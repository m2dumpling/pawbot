"""Structured Tool execution facts kept alongside legacy string results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast

from pawbot.agent.tools.base import ToolIdempotency, ToolRecoveryStrategy

ToolOutcomeStatus = Literal["succeeded", "failed", "blocked", "cancelled", "unknown"]
ToolSideEffectResult = Literal[
    "not_started", "not_executed", "confirmed", "may_have_occurred"
]


def _string(value: Any, *, limit: int = 512) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """Machine-readable execution result for one Tool call.

    ``ToolResult`` stays string-compatible for model and plugin compatibility.
    This companion model is persisted in ``tool_states`` for Trace, recovery,
    replay, and task evaluation surfaces.
    """

    tool_name: str
    call_id: str | None
    operation_id: str
    status: ToolOutcomeStatus
    error_code: str | None
    retryable: bool
    side_effect: ToolSideEffectResult
    idempotency: ToolIdempotency
    recovery_strategy: ToolRecoveryStrategy
    recovery_required: bool
    receipt: str | None
    duration_ms: int | None
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "tool_name": self.tool_name,
            "call_id": self.call_id,
            "operation_id": self.operation_id,
            "status": self.status,
            "error_code": self.error_code,
            "retryable": self.retryable,
            "side_effect": self.side_effect,
            "idempotency": self.idempotency,
            "recovery_strategy": self.recovery_strategy,
            "recovery_required": self.recovery_required,
            "receipt": self.receipt,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> ToolOutcome:
        """Project legacy lifecycle metadata into the stable result contract."""
        lifecycle = str(state.get("state") or "failed")
        status = cast(
            ToolOutcomeStatus,
            {
                "succeeded": "succeeded",
                "blocked": "blocked",
                "unknown": "unknown",
                "cancelled": "cancelled",
            }.get(lifecycle, "failed"),
        )
        raw_side_effect = str(state.get("side_effect") or "not_started")
        side_effect = cast(
            ToolSideEffectResult,
            "confirmed"
            if status == "succeeded" and raw_side_effect == "may_have_occurred"
            else raw_side_effect
            if raw_side_effect in {
                "not_started",
                "not_executed",
                "confirmed",
                "may_have_occurred",
            }
            else "may_have_occurred"
            if raw_side_effect != "none"
            else "not_started",
        )
        raw_idempotency = str(state.get("idempotency") or "unknown")
        raw_recovery = str(state.get("recovery_strategy") or "manual_confirmation")
        return cls(
            tool_name=str(state.get("name") or "unknown"),
            call_id=_string(state.get("call_id"), limit=256),
            operation_id=str(state.get("operation_id") or "unknown"),
            status=status,
            error_code=(
                None
                if status == "succeeded"
                else _string(state.get("error_code"), limit=160)
                or "TOOL_EXECUTION_FAILED"
            ),
            retryable=state.get("retryable") is True,
            side_effect=side_effect,
            idempotency=cast(
                ToolIdempotency,
                raw_idempotency
                if raw_idempotency in {"not_applicable", "idempotent", "non_idempotent", "unknown"}
                else "unknown",
            ),
            recovery_strategy=cast(
                ToolRecoveryStrategy,
                raw_recovery
                if raw_recovery
                in {"none", "safe_retry", "retry_with_receipt", "manual_confirmation", "never_retry"}
                else "manual_confirmation",
            ),
            recovery_required=state.get("recovery_required") is True,
            receipt=_string(state.get("receipt")),
            duration_ms=(
                state["duration_ms"]
                if isinstance(state.get("duration_ms"), int)
                and not isinstance(state.get("duration_ms"), bool)
                else None
            ),
        )


__all__ = ["ToolOutcome", "ToolOutcomeStatus", "ToolSideEffectResult"]
