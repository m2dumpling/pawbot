"""Human-in-the-loop approval primitives for high-risk Tool calls."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from loguru import logger

ApprovalDecision = Literal["approved", "denied"]
DEFAULT_APPROVAL_CAPABILITIES = frozenset({"write", "execute", "network"})
ToolApprovalNotifier = Callable[["ToolApprovalRequest"], None | Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ToolApprovalRequest:
    """The exact Tool call a human must approve before side effects begin."""

    request_id: str
    call_id: str
    name: str
    arguments: dict[str, Any]
    capabilities: tuple[str, ...]
    session_key: str | None
    iteration: int
    created_at_ms: int
    channel: str = ""
    chat_id: str | None = None
    operation_id: str | None = None
    recovery_required: bool = False
    recovery_reason: str | None = None

    @classmethod
    def create(
        cls,
        *,
        call_id: str,
        name: str,
        arguments: dict[str, Any],
        capabilities: tuple[str, ...],
        session_key: str | None,
        iteration: int,
        channel: str = "",
        chat_id: str | None = None,
        operation_id: str | None = None,
        recovery_required: bool = False,
        recovery_reason: str | None = None,
    ) -> ToolApprovalRequest:
        return cls(
            request_id=uuid4().hex,
            call_id=call_id,
            name=name,
            arguments=deepcopy(arguments),
            capabilities=tuple(capabilities),
            session_key=session_key,
            iteration=iteration,
            created_at_ms=int(time.time() * 1000),
            channel=channel,
            chat_id=chat_id,
            operation_id=operation_id,
            recovery_required=recovery_required,
            recovery_reason=recovery_reason,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a transport-safe snapshot for UI, audit, or test adapters."""
        return {
            "request_id": self.request_id,
            "call_id": self.call_id,
            "name": self.name,
            "arguments": deepcopy(self.arguments),
            "capabilities": list(self.capabilities),
            "session_key": self.session_key,
            "iteration": self.iteration,
            "created_at_ms": self.created_at_ms,
            "channel": self.channel,
            "chat_id": self.chat_id,
            "operation_id": self.operation_id,
            "recovery_required": self.recovery_required,
            "recovery_reason": self.recovery_reason,
        }


@dataclass(frozen=True, slots=True)
class ToolApprovalResult:
    """A human or policy decision for one pending Tool call."""

    decision: ApprovalDecision
    reason: str | None = None

    @property
    def approved(self) -> bool:
        return self.decision == "approved"

    @classmethod
    def approve(cls, reason: str | None = None) -> ToolApprovalResult:
        return cls("approved", reason)

    @classmethod
    def deny(cls, reason: str | None = None) -> ToolApprovalResult:
        return cls("denied", reason)


ToolApprovalCallback = Callable[[ToolApprovalRequest], Awaitable[ToolApprovalResult]]


@dataclass(slots=True)
class _PendingApproval:
    request: ToolApprovalRequest
    future: asyncio.Future[ToolApprovalResult]


class ToolApprovalManager:
    """Coordinate pending approvals without coupling the runner to a UI.

    A transport registers ``on_request`` to display a request and calls
    :meth:`resolve` when the user selects Approve or Deny.  The runner waits
    on the manager's callback; timeout, notification failure, and unknown
    request IDs all fail closed and never execute the Tool.
    """

    def __init__(
        self,
        *,
        timeout_s: float = 300.0,
        on_request: ToolApprovalNotifier | None = None,
    ) -> None:
        self.timeout_s = max(0.0, float(timeout_s))
        self._on_request = on_request
        self._pending: dict[str, _PendingApproval] = {}

    async def request(self, request: ToolApprovalRequest) -> ToolApprovalResult:
        """Wait for one decision, failing closed when the wait cannot continue."""
        if request.request_id in self._pending:
            return ToolApprovalResult.deny("duplicate approval request")
        future = asyncio.get_running_loop().create_future()
        self._pending[request.request_id] = _PendingApproval(request, future)
        try:
            if self._on_request is not None:
                notification = self._on_request(request)
                if inspect.isawaitable(notification):
                    await notification
            if self.timeout_s <= 0:
                return ToolApprovalResult.deny("approval timed out")
            try:
                return await asyncio.wait_for(asyncio.shield(future), self.timeout_s)
            except asyncio.TimeoutError:
                return ToolApprovalResult.deny("approval timed out")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("tool approval request {} could not be displayed", request.request_id)
            return ToolApprovalResult.deny("approval request could not be displayed")
        finally:
            self._pending.pop(request.request_id, None)

    def resolve(
        self,
        request_id: str,
        decision: ApprovalDecision,
        *,
        reason: str | None = None,
    ) -> bool:
        """Resolve a pending request; return False for stale/unknown IDs."""
        pending = self._pending.get(request_id)
        if pending is None or pending.future.done():
            return False
        pending.future.set_result(ToolApprovalResult(decision, reason))
        return True

    def resolve_for_chat(
        self,
        request_id: str,
        chat_id: str,
        decision: ApprovalDecision,
        *,
        reason: str | None = None,
    ) -> bool:
        """Resolve only when the request belongs to the authenticated chat."""
        pending = self._pending.get(request_id)
        if pending is None or pending.request.chat_id != chat_id:
            return False
        return self.resolve(request_id, decision, reason=reason)

    def deny(self, request_id: str, *, reason: str | None = None) -> bool:
        return self.resolve(request_id, "denied", reason=reason)

    def pending_requests(self, *, session_key: str | None = None) -> list[ToolApprovalRequest]:
        """Return a stable snapshot for a UI or operator inspection endpoint."""
        requests = [
            pending.request
            for pending in self._pending.values()
            if session_key is None or pending.request.session_key == session_key
        ]
        return sorted(requests, key=lambda request: request.created_at_ms)

    def cancel_session(self, session_key: str) -> int:
        """Deny all outstanding requests for a session during cancellation."""
        cancelled = 0
        for request in self.pending_requests(session_key=session_key):
            if self.deny(request.request_id, reason="session cancelled"):
                cancelled += 1
        return cancelled


__all__ = [
    "ApprovalDecision",
    "DEFAULT_APPROVAL_CAPABILITIES",
    "ToolApprovalCallback",
    "ToolApprovalManager",
    "ToolApprovalNotifier",
    "ToolApprovalRequest",
    "ToolApprovalResult",
]
