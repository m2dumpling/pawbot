"""Tool-call execution boundary.

Executes one model response's tool calls, scheduling concurrent batches and
classifying every outcome (success / recoverable error / security violation)
into a model-facing observation.

Design (ADR-005): the public entry point :func:`execute_tool_calls` is frozen
for backward compatibility (``AgentRunner`` depends on it). Internally,
execution is a three-stage pipeline with one focused collaborator per stage:

* :class:`BatchPlanner` — partitions calls into concurrency-safe batches.
* :class:`CallExecutor` — runs one call through the replay rail, the security
  classifier, and the hook lifecycle.
* :class:`ViolationClassifier` — maps a failure to a boundary category and
  shapes the model-facing payload.

Unlike the upstream implementation this pipeline has no dead code and no
``getattr``-based duck typing: ``tools`` is a real ``ToolRegistry`` and every
stage is called through its static interface.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, cast

from loguru import logger

from pawbot.agent.approval import (
    DEFAULT_APPROVAL_CAPABILITIES,
    ToolApprovalCallback,
    ToolApprovalRequest,
    ToolApprovalResult,
)
from pawbot.agent.hook import AgentHook, AgentHookContext
from pawbot.agent.tools.base import ToolExecutionPolicy
from pawbot.agent.tools.registry import ToolRegistry, ToolResult, is_tool_error_result
from pawbot.providers.base import ToolCallRequest
from pawbot.utils.runtime import (
    repeated_external_lookup_error,
    repeated_workspace_violation_error,
)
from pawbot.utils.tool_operation import (
    TOOL_EXECUTION_METADATA_KEY,
    operation_id_for_tool_call,
)

_RETRY_HINT = "\n\n[Analyze the error above and try a different approach.]"
# SSRF is a hard security block at the tool boundary, but the agent turn
# should recover conversationally instead of aborting the runtime.
_SSRF_MARKERS: tuple[str, ...] = (
    "internal/private url detected",
    "private/internal address",
    "private address",
)
_SSRF_BOUNDARY_NOTE = (
    "This is a non-bypassable security boundary. Stop trying to access "
    "private/internal URLs. Do not retry with curl, wget, encoded IPs, "
    "alternate DNS, redirects, proxies, or another tool. Ask the user for "
    "local files, logs, screenshots, or an explicit safe public URL instead. "
    "If the user explicitly trusts this private URL, ask them to whitelist "
    "the exact IP/CIDR via tools.ssrfWhitelist."
)
# Non-SSRF boundary markers returned to the model as recoverable tool errors.
_WORKSPACE_VIOLATION_MARKERS: tuple[str, ...] = (
    "outside the configured workspace",
    "outside allowed directory",
    "working_dir is outside",
    "working_dir could not be resolved",
    "path outside working dir",
    "path traversal detected",
)


def is_ssrf_violation(text: str) -> bool:
    """Return whether a tool error describes a blocked private-network request."""
    if not text:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in _SSRF_MARKERS)


def _is_workspace_violation(text: str) -> bool:
    """Return whether text describes any workspace or network boundary rejection."""
    if not text:
        return False
    lowered = text.lower()
    if is_ssrf_violation(lowered):
        return True
    return any(marker in lowered for marker in _WORKSPACE_VIOLATION_MARKERS)


def _ssrf_soft_payload(raw_text: str) -> str:
    text = raw_text.strip() or "Error: request blocked by SSRF guard"
    return f"{text}\n\n{_SSRF_BOUNDARY_NOTE}"


def _event_detail(prefix: str, text: str, limit: int = 160) -> str:
    return (prefix + text.replace("\n", " ").strip())[:limit]


def _ok_detail(result: Any) -> str:
    detail = "" if result is None else str(result)
    detail = detail.replace("\n", " ").strip()
    if not detail:
        return "(empty)"
    if len(detail) > 120:
        return detail[:120] + "..."
    return detail


def execution_policy_for_tool(tool: Any) -> ToolExecutionPolicy:
    """Normalize the optional policy exposed by built-in or plugin Tools."""
    candidate = getattr(tool, "execution_policy", None)
    if isinstance(candidate, ToolExecutionPolicy):
        return candidate
    return ToolExecutionPolicy(
        side_effect="none" if bool(getattr(tool, "read_only", False)) else "unknown",
        idempotency="not_applicable" if bool(getattr(tool, "read_only", False)) else "unknown",
        recovery_strategy="none" if bool(getattr(tool, "read_only", False)) else "manual_confirmation",
    )


def _lookup_tool(tools: Any, name: str) -> Any | None:
    """Resolve a tool while keeping compatibility with lightweight embedders."""
    getter = getattr(tools, "get", None)
    if not callable(getter):
        return None
    try:
        return getter(name)
    except Exception:
        return None


def recovery_confirmation_required(policy: ToolExecutionPolicy) -> bool:
    """Return whether an operation cannot be retried blindly after uncertainty."""
    return (
        policy.side_effect != "none"
        and policy.idempotency != "idempotent"
        and policy.recovery_strategy not in {"none", "safe_retry"}
    )


def tool_execution_metadata(
    tools: ToolRegistry,
    session_key: str | None,
    tool_call: ToolCallRequest,
) -> dict[str, Any]:
    """Return safe policy metadata for a pending checkpoint entry."""
    tool = _lookup_tool(tools, tool_call.name)
    policy = execution_policy_for_tool(tool) if tool is not None else ToolExecutionPolicy(
        side_effect="unknown",
        idempotency="unknown",
        recovery_strategy="manual_confirmation",
    )
    return {
        "call_id": tool_call.id,
        "name": tool_call.name,
        "operation_id": operation_id_for_tool_call(
            session_key,
            tool_call.name,
            tool_call.arguments,
        ),
        **policy.to_dict(),
    }


def _recovered_operation(messages: list[dict[str, Any]], operation_id: str) -> dict[str, Any] | None:
    """Find an interrupted operation marker, if it is still unresolved."""
    for message in reversed(messages):
        message_data = message
        message_operation_id = message_data.get("operation_id")
        if not isinstance(message_operation_id, str):
            execution_metadata = message_data.get(TOOL_EXECUTION_METADATA_KEY)
            if isinstance(execution_metadata, dict):
                message_operation_id = cast(dict[str, Any], execution_metadata).get(
                    "operation_id"
                )
        if message_operation_id != operation_id:
            continue
        # A later normal tool result resolves the earlier interruption marker.
        if (
            message_data.get("_recovery_interrupted") is True
            or message_data.get("_recovery_pending") is True
        ):
            return message
        return None
    return None


class ViolationClassifier:
    """Map a tool failure to a boundary category and shape the payload."""

    def __init__(self, workspace_violation_counts: dict[str, int]) -> None:
        self._workspace_violation_counts = workspace_violation_counts

    def classify(
        self,
        *,
        raw_text: str,
        soft_payload: str,
        event: dict[str, str],
        tool_call: ToolCallRequest,
    ) -> tuple[Any, dict[str, str]] | None:
        """Return ``(result, event)`` for a boundary violation, else ``None``.

        ``None`` signals the caller to fall through to the plain error payload.
        """
        if is_ssrf_violation(raw_text):
            logger.warning(
                "Tool {} blocked by SSRF guard; returning non-retryable tool error: {}",
                tool_call.name,
                raw_text.replace("\n", " ").strip()[:200],
            )
            event["detail"] = _event_detail("ssrf_violation: ", raw_text)
            return _ssrf_soft_payload(raw_text), event

        if _is_workspace_violation(raw_text):
            escalation = repeated_workspace_violation_error(
                tool_call.name,
                tool_call.arguments,
                self._workspace_violation_counts,
            )
            event["detail"] = _event_detail("workspace_violation: ", raw_text)
            if escalation is not None:
                logger.warning(
                    "Tool {} hit workspace boundary repeatedly; escalating hint",
                    tool_call.name,
                )
                event["detail"] = _event_detail(
                    "workspace_violation_escalated: ",
                    raw_text,
                )
                return escalation, event
            return soft_payload, event

        return None


class BatchPlanner:
    """Partition tool calls into concurrently-executable batches."""

    @staticmethod
    def plan(
        tools: ToolRegistry,
        tool_calls: list[ToolCallRequest],
        *,
        concurrent: bool,
    ) -> list[list[ToolCallRequest]]:
        if not concurrent:
            return [[tool_call] for tool_call in tool_calls]

        batches: list[list[ToolCallRequest]] = []
        current: list[ToolCallRequest] = []
        for tool_call in tool_calls:
            tool = _lookup_tool(tools, tool_call.name)
            if tool is not None and tool.concurrency_safe:
                current.append(tool_call)
                continue
            if current:
                batches.append(current)
                current = []
            batches.append([tool_call])
        if current:
            batches.append(current)
        return batches


class CallExecutor:
    """Run one tool call through the replay rail, then classify the outcome.

    The execution sequence is: repeated-lookup guard → parameter preparation
    → replay short-circuit (ADR-004) → real execution → boundary/error
    classification. Hooks observe the start, completion, and error of every
    attempt.
    """

    def __init__(
        self,
        tools: ToolRegistry,
        *,
        external_lookup_counts: dict[str, int],
        workspace_violation_counts: dict[str, int],
        hook: AgentHook,
        context: AgentHookContext,
        denied_tool_capabilities: frozenset[str] = frozenset(),
        tool_approval_callback: ToolApprovalCallback | None = None,
        recovery_approval_callback: ToolApprovalCallback | None = None,
        approval_capabilities: frozenset[str] = DEFAULT_APPROVAL_CAPABILITIES,
        channel: str = "",
        chat_id: str | None = None,
    ) -> None:
        self._tools = tools
        self._external_lookup_counts = external_lookup_counts
        self._hook = hook
        self._context = context
        self._classifier = ViolationClassifier(workspace_violation_counts)
        self._denied_tool_capabilities = denied_tool_capabilities
        self._tool_approval_callback = tool_approval_callback
        self._recovery_approval_callback = recovery_approval_callback
        self._approval_capabilities = approval_capabilities
        self._channel = channel
        self._chat_id = chat_id
        self._recovery_operation_ids_seen: set[str] = set()

    def _state_record(self, tool_call: ToolCallRequest) -> dict[str, Any]:
        tool = _lookup_tool(self._tools, tool_call.name)
        policy = execution_policy_for_tool(tool) if tool is not None else ToolExecutionPolicy(
            side_effect="unknown",
            idempotency="unknown",
            recovery_strategy="manual_confirmation",
        )
        record: dict[str, Any] = {
            "call_id": tool_call.id,
            "name": tool_call.name,
            "operation_id": operation_id_for_tool_call(
                self._context.session_key,
                tool_call.name,
                tool_call.arguments,
            ),
            "state": "planned",
            "side_effect": policy.side_effect,
            **policy.to_dict(),
            "recovery_resolution": None,
            "started_at": None,
            "finished_at": None,
            "duration_ms": None,
        }
        self._context.tool_states.append(record)
        return record

    @staticmethod
    def _finish_state(
        state: dict[str, Any],
        *,
        lifecycle: str,
        side_effect: str,
        started_at: float,
    ) -> None:
        finished_at = time.time()
        state.update({
            "state": lifecycle,
            "side_effect": side_effect,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": max(0, int((finished_at - started_at) * 1000)),
        })

    @staticmethod
    def _side_effect_class(tool: Any) -> str:
        policy = execution_policy_for_tool(tool)
        return "none" if policy.side_effect == "none" else "may_have_occurred"

    @staticmethod
    def _receipt_from_result(result: Any) -> Any:
        if isinstance(result, dict):
            return cast(dict[str, Any], result).get("receipt")
        return getattr(result, "receipt", None)

    @classmethod
    def _receipt_from_tool(cls, tool: Any, result: Any) -> Any:
        receipt_reader = getattr(tool, "execution_receipt", None)
        if callable(receipt_reader):
            try:
                return receipt_reader(result)
            except Exception:
                logger.debug(
                    "Tool {} returned an invalid execution receipt",
                    getattr(tool, "name", "unknown"),
                    exc_info=True,
                )
        return cls._receipt_from_result(result)

    @staticmethod
    def _bounded_receipt(value: Any) -> str | None:
        """Keep only a small receipt snapshot in session state and traces."""
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
        else:
            try:
                text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            except (TypeError, ValueError):
                text = str(value)
        if not text:
            return None
        return text[:512] + ("..." if len(text) > 512 else "")

    @staticmethod
    def _recovery_error(
        tool_name: str,
        operation_id: str,
        *,
        reason: str = (
            "A human must confirm before retrying because the previous side effect "
            "could not be confirmed."
        ),
    ) -> ToolResult:
        return ToolResult.error(
            f"Error: tool '{tool_name}' was interrupted and may already have caused "
            f"side effects (operation {operation_id}). {reason}"
        )

    @staticmethod
    def _needs_approval(tool: Any, approval_capabilities: frozenset[str]) -> bool:
        if bool(getattr(tool, "read_only", False)):
            return False
        raw_capabilities = getattr(tool, "capabilities", frozenset({"write"}))
        capabilities = frozenset(str(value) for value in raw_capabilities)
        return bool(capabilities & approval_capabilities) or not capabilities

    async def run(self, tool_call: ToolCallRequest) -> tuple[Any, dict[str, str]]:
        from pawbot.agent.blackbox import replay_is_active

        lifecycle = self._state_record(tool_call)
        lookup_error = repeated_external_lookup_error(
            tool_call.name,
            tool_call.arguments,
            self._external_lookup_counts,
        )
        if lookup_error:
            self._finish_state(
                lifecycle,
                lifecycle="blocked",
                side_effect="not_started",
                started_at=time.time(),
            )
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": "repeated external lookup blocked",
            }
            return lookup_error + _RETRY_HINT, event

        tool, params, prep_error = self._tools.prepare_call(
            tool_call.name,
            tool_call.arguments,
        )
        if prep_error:
            self._finish_state(
                lifecycle,
                lifecycle="failed",
                side_effect="not_started",
                started_at=time.time(),
            )
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": prep_error.split(": ", 1)[-1][:120],
            }
            handled = self._classifier.classify(
                raw_text=prep_error,
                soft_payload=prep_error + _RETRY_HINT,
                event=event,
                tool_call=tool_call,
            )
            if handled is not None:
                return handled
            return prep_error + _RETRY_HINT, event

        policy = execution_policy_for_tool(tool)
        operation_id = cast(str, lifecycle["operation_id"])
        lifecycle.update(policy.to_dict())
        recovery_approved = False
        default_capabilities: frozenset[str] = frozenset()
        raw_capabilities = cast(Any, getattr(tool, "capabilities", default_capabilities))
        tool_capabilities: frozenset[str] = frozenset(
            str(value) for value in raw_capabilities
        )
        denied = sorted(tool_capabilities & self._denied_tool_capabilities)
        if denied:
            detail = f"blocked by tool capability policy: {', '.join(denied)}"
            self._finish_state(
                lifecycle,
                lifecycle="blocked",
                side_effect="not_started",
                started_at=time.time(),
            )
            event = {"name": tool_call.name, "status": "error", "detail": detail}
            return ToolResult.error(
                f"Error: tool '{tool_call.name}' requires denied capabilities: {', '.join(denied)}"
            ), event

        recovered = _recovered_operation(self._context.messages, operation_id)
        if recovered is not None and policy.side_effect != "none" and not replay_is_active():
            lifecycle["recovery_required"] = True
            if operation_id in self._recovery_operation_ids_seen:
                lifecycle["recovery_resolution"] = "duplicate_blocked"
                self._finish_state(
                    lifecycle,
                    lifecycle="blocked",
                    side_effect="may_have_occurred",
                    started_at=time.time(),
                )
                return ToolResult.error(
                    f"Error: duplicate retry for interrupted operation {operation_id} "
                    "was blocked; inspect the first retry result before trying again."
                ), {
                    "name": tool_call.name,
                    "status": "error",
                    "detail": "duplicate interrupted operation blocked",
                }
            self._recovery_operation_ids_seen.add(operation_id)
            if policy.recovery_strategy == "never_retry":
                lifecycle["recovery_resolution"] = "never_retry"
                self._finish_state(
                    lifecycle,
                    lifecycle="blocked",
                    side_effect="may_have_occurred",
                    started_at=time.time(),
                )
                return self._recovery_error(
                    tool_call.name,
                    operation_id,
                    reason="The Tool policy forbids retrying this operation.",
                ), {
                    "name": tool_call.name,
                    "status": "error",
                    "detail": "recovery blocked by tool policy: never_retry",
                }
            if not recovery_confirmation_required(policy):
                lifecycle["recovery_resolution"] = "auto_retry"
            else:
                recovery_callback = self._recovery_approval_callback or self._tool_approval_callback
                request = ToolApprovalRequest.create(
                    call_id=str(tool_call.id or ""),
                    name=tool_call.name,
                    arguments=cast(dict[str, Any], params) if isinstance(params, dict) else {},
                    capabilities=tuple(sorted(tool_capabilities)) or ("write",),
                    session_key=self._context.session_key,
                    iteration=self._context.iteration,
                    channel=self._channel,
                    chat_id=self._chat_id,
                    operation_id=operation_id,
                    recovery_required=True,
                    recovery_reason=(
                        "The previous attempt was interrupted and its external side effect "
                        "could not be confirmed; review the operation before retrying."
                    ),
                )
                await self._hook.on_tool_approval_requested(self._context, request)
                if recovery_callback is None:
                    decision = ToolApprovalResult.deny(
                        "recovery confirmation is unavailable"
                    )
                else:
                    try:
                        decision = await recovery_callback(request)
                    except asyncio.CancelledError:
                        decision = ToolApprovalResult.deny("turn cancelled")
                        await self._hook.on_tool_approval_resolved(
                            self._context,
                            request,
                            decision,
                        )
                        self._finish_state(
                            lifecycle,
                            lifecycle="blocked",
                            side_effect="may_have_occurred",
                            started_at=time.time(),
                        )
                        raise
                    except Exception as exc:
                        decision = ToolApprovalResult.deny(
                            f"recovery approval failed: {type(exc).__name__}"
                        )
                await self._hook.on_tool_approval_resolved(self._context, request, decision)
                if not decision.approved:
                    reason = decision.reason or "recovery retry was not approved"
                    lifecycle["recovery_resolution"] = "denied"
                    self._finish_state(
                        lifecycle,
                        lifecycle="blocked",
                        side_effect="may_have_occurred",
                        started_at=time.time(),
                    )
                    return self._recovery_error(tool_call.name, operation_id), {
                        "name": tool_call.name,
                        "status": "error",
                        "detail": f"recovery confirmation denied: {reason}",
                    }
                lifecycle["recovery_resolution"] = "approved"
                recovery_approved = True

        if (
            self._tool_approval_callback is not None
            and not recovery_approved
            and self._needs_approval(tool, self._approval_capabilities)
        ):
            capabilities = tuple(
                sorted(str(value) for value in getattr(tool, "capabilities", {"write"}))
            )
            request = ToolApprovalRequest.create(
                call_id=str(tool_call.id or ""),
                name=tool_call.name,
                arguments=cast(dict[str, Any], params) if isinstance(params, dict) else {},
                capabilities=capabilities,
                session_key=self._context.session_key,
                iteration=self._context.iteration,
                channel=self._channel,
                chat_id=self._chat_id,
            )
            await self._hook.on_tool_approval_requested(self._context, request)
            try:
                decision = await self._tool_approval_callback(request)
            except asyncio.CancelledError:
                cancelled = ToolApprovalResult.deny("turn cancelled")
                await self._hook.on_tool_approval_resolved(
                    self._context,
                    request,
                    cancelled,
                )
                self._finish_state(
                    lifecycle,
                    lifecycle="blocked",
                    side_effect="not_started",
                    started_at=time.time(),
                )
                raise
            except Exception as exc:
                decision = ToolApprovalResult.deny(
                    f"approval callback failed: {type(exc).__name__}"
                )
            await self._hook.on_tool_approval_resolved(self._context, request, decision)
            if not decision.approved:
                reason = decision.reason or "user denied approval"
                self._finish_state(
                    lifecycle,
                    lifecycle="blocked",
                    side_effect="not_started",
                    started_at=time.time(),
                )
                denial = ToolResult.error(
                    f"Error: tool '{tool_call.name}' was not approved: {reason}"
                )
                await self._hook.on_execute_tool_error(
                    self._context,
                    tool_call,
                    tool,
                    params,
                    denial,
                )
                return denial, {
                    "name": tool_call.name,
                    "status": "error",
                    "detail": f"approval denied: {reason}",
                }

        started_at = time.time()
        lifecycle["state"] = "running"
        lifecycle["started_at"] = started_at
        await self._hook.before_execute_tool(self._context, tool_call, tool, params)
        try:
            # ADR-004 replay rail: when a recorded turn is re-executed
            # offline, every tool call is served from the JSONL snapshot by
            # stable key instead of touching the real system (side-effect
            # isolation).
            from pawbot.agent.blackbox import lookup_replay_result

            replay_args = cast(dict[str, Any], params) if isinstance(params, dict) else {}
            found, replayed = lookup_replay_result(tool_call.name, replay_args)
            if found:
                if is_tool_error_result(replayed) and recovery_confirmation_required(policy):
                    lifecycle["recovery_required"] = True
                    lifecycle["recovery_resolution"] = "pending_confirmation"
                self._finish_state(
                    lifecycle,
                    lifecycle="failed" if is_tool_error_result(replayed) else "succeeded",
                    side_effect="not_executed",
                    started_at=started_at,
                )
                if is_tool_error_result(replayed):
                    await self._hook.on_execute_tool_error(
                        self._context,
                        tool_call,
                        tool,
                        params,
                        replayed,
                    )
                    event = {
                        "name": tool_call.name,
                        "status": "error",
                        "detail": str(replayed).replace("\n", " ").strip()[:120],
                    }
                    return replayed, event
                event = {
                    "name": tool_call.name,
                    "status": "ok",
                    "detail": "(replayed from blackbox snapshot)",
                }
                await self._hook.after_execute_tool(
                    self._context, tool_call, tool, params, replayed
                )
                return replayed, event
            if replay_is_active():
                # A replay fixture that is missing a tool observation is
                # malformed. Never fall through to the real executor: doing
                # so would turn a debugging operation into a side-effecting
                # live run and could make the replay appear green.
                replay_error = ToolResult.error(
                    f"Error: missing replay observation for tool '{tool_call.name}'; "
                    "tool execution was blocked"
                )
                self._finish_state(
                    lifecycle,
                    lifecycle="failed",
                    side_effect="not_executed",
                    started_at=started_at,
                )
                await self._hook.on_execute_tool_error(
                    self._context,
                    tool_call,
                    tool,
                    params,
                    replay_error,
                )
                event = {
                    "name": tool_call.name,
                    "status": "error",
                    "detail": "missing replay observation; execution blocked",
                }
                return replay_error, event
            if tool is not None:
                result = await tool.execute(**params)
            else:
                result = await self._tools.execute(tool_call.name, params)
        except asyncio.CancelledError:
            policy = execution_policy_for_tool(tool)
            lifecycle["recovery_required"] = policy.side_effect != "none"
            lifecycle["recovery_resolution"] = (
                "pending_confirmation" if policy.side_effect != "none" else "not_required"
            )
            self._finish_state(
                lifecycle,
                lifecycle="unknown",
                side_effect=self._side_effect_class(tool),
                started_at=started_at,
            )
            await self._hook.on_execute_tool_cancelled(
                self._context,
                tool_call,
                tool,
                params,
            )
            raise
        except Exception as exc:
            policy = execution_policy_for_tool(tool)
            if recovery_confirmation_required(policy):
                lifecycle["recovery_required"] = True
                lifecycle["recovery_resolution"] = "pending_confirmation"
            self._finish_state(
                lifecycle,
                lifecycle="failed",
                side_effect=self._side_effect_class(tool),
                started_at=started_at,
            )
            await self._hook.on_execute_tool_error(
                self._context, tool_call, tool, params, exc
            )
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": str(exc),
            }
            payload = f"Error: {type(exc).__name__}: {exc}"
            handled = self._classifier.classify(
                raw_text=str(exc),
                # Preserve legacy exception payloads without the retry hint.
                soft_payload=payload,
                event=event,
                tool_call=tool_call,
            )
            if handled is not None:
                return handled
            return payload, event

        if is_tool_error_result(result):
            policy = execution_policy_for_tool(tool)
            if recovery_confirmation_required(policy):
                lifecycle["recovery_required"] = True
                lifecycle["recovery_resolution"] = "pending_confirmation"
            self._finish_state(
                lifecycle,
                lifecycle="failed",
                side_effect=self._side_effect_class(tool),
                started_at=started_at,
            )
            await self._hook.on_execute_tool_error(
                self._context, tool_call, tool, params, result
            )
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": result.replace("\n", " ").strip()[:120],
            }
            handled = self._classifier.classify(
                raw_text=result,
                soft_payload=result + _RETRY_HINT,
                event=event,
                tool_call=tool_call,
            )
            if handled is not None:
                return handled
            return result + _RETRY_HINT, event

        await self._hook.after_execute_tool(self._context, tool_call, tool, params, result)
        receipt = self._receipt_from_tool(tool, result)
        if receipt is not None:
            lifecycle["receipt"] = self._bounded_receipt(receipt)
        self._finish_state(
            lifecycle,
            lifecycle="succeeded",
            side_effect=self._side_effect_class(tool),
            started_at=started_at,
        )
        return result, {"name": tool_call.name, "status": "ok", "detail": _ok_detail(result)}


async def execute_tool_calls(
    tools: ToolRegistry,
    tool_calls: list[ToolCallRequest],
    *,
    concurrent: bool,
    external_lookup_counts: dict[str, int],
    workspace_violation_counts: dict[str, int],
    hook: AgentHook,
    context: AgentHookContext,
    denied_tool_capabilities: frozenset[str] = frozenset(),
    tool_approval_callback: ToolApprovalCallback | None = None,
    recovery_approval_callback: ToolApprovalCallback | None = None,
    approval_capabilities: frozenset[str] = DEFAULT_APPROVAL_CAPABILITIES,
    channel: str = "",
    chat_id: str | None = None,
) -> tuple[list[Any], list[dict[str, str]]]:
    """Execute one model response's tool calls in stable result order.

    Frozen public entry point (ADR-005). The pipeline is: plan batches →
    run each call through a single :class:`CallExecutor` → collect results.
    """
    executor = CallExecutor(
        tools,
        external_lookup_counts=external_lookup_counts,
        workspace_violation_counts=workspace_violation_counts,
        hook=hook,
        context=context,
        denied_tool_capabilities=denied_tool_capabilities,
        tool_approval_callback=tool_approval_callback,
        recovery_approval_callback=recovery_approval_callback,
        approval_capabilities=approval_capabilities,
        channel=channel,
        chat_id=chat_id,
    )
    results: list[Any] = []
    events: list[dict[str, str]] = []
    for batch in BatchPlanner.plan(tools, tool_calls, concurrent=concurrent):
        if concurrent and len(batch) > 1:
            batch_results = await asyncio.gather(*(executor.run(c) for c in batch))
        else:
            batch_results = [await executor.run(c) for c in batch]
        for result, event in batch_results:
            results.append(result)
            events.append(event)
    return results, events
