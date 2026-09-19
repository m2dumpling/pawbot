"""Canonical, serializable facts about one Agent turn.

``TurnOutcome`` deliberately keeps execution, task verification, side effects,
recovery, and replay on separate axes.  A replay that is structurally
consistent, for example, does not make an originally failed Tool call become a
successful task.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal, cast

ExecutionStatus = Literal["completed", "failed", "cancelled", "limited", "incomplete"]
TaskStatus = Literal["passed", "failed", "not_evaluable", "not_requested"]
SideEffectStatus = Literal["not_applicable", "confirmed", "unknown"]
RecoveryStatus = Literal["not_needed", "resumable", "awaiting_confirmation", "recovered"]
ReplayStatus = Literal["not_run", "consistent", "divergent", "unavailable"]

_EXECUTION_STATUSES = frozenset({"completed", "failed", "cancelled", "limited", "incomplete"})
_TASK_STATUSES = frozenset({"passed", "failed", "not_evaluable", "not_requested"})
_SIDE_EFFECT_STATUSES = frozenset({"not_applicable", "confirmed", "unknown"})
_RECOVERY_STATUSES = frozenset({"not_needed", "resumable", "awaiting_confirmation", "recovered"})
_REPLAY_STATUSES = frozenset({"not_run", "consistent", "divergent", "unavailable"})
_LIMIT_STOP_REASONS = frozenset({
    "max_iterations",
    "max_tool_calls",
    "max_wall_time",
    "max_input_tokens",
    "max_output_tokens",
    "max_cost_usd",
})


def _text(value: Any, *, limit: int = 1_000) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


def _enum(value: Any, allowed: frozenset[str], fallback: str) -> str:
    return value if isinstance(value, str) and value in allowed else fallback


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    """Independent outcome axes for one execution unit.

    The object is intentionally small so it can live in Session metadata,
    Trace terminal events, recording envelopes, and API payloads without
    forcing callers to understand the full AgentRunResult internals.
    """

    execution_status: ExecutionStatus
    stop_reason: str | None
    task_status: TaskStatus
    side_effect_status: SideEffectStatus
    recovery_status: RecoveryStatus
    replay_status: ReplayStatus = "not_run"
    error_code: str | None = None
    error_message: str | None = None
    schema_version: int = 1

    @property
    def is_successful_execution(self) -> bool:
        return self.execution_status == "completed"

    @property
    def is_task_accepted(self) -> bool:
        return self.task_status == "passed"

    @property
    def requires_human_recovery(self) -> bool:
        return self.recovery_status == "awaiting_confirmation"

    def to_dict(self) -> dict[str, Any]:
        """Return the stable JSON form used across Pawbot evidence surfaces."""
        return {
            "schema_version": self.schema_version,
            "execution_status": self.execution_status,
            "stop_reason": self.stop_reason,
            "task_status": self.task_status,
            "side_effect_status": self.side_effect_status,
            "recovery_status": self.recovery_status,
            "replay_status": self.replay_status,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, value: Any) -> TurnOutcome:
        """Decode an untrusted payload using conservative, non-success defaults."""
        mapping: dict[str, Any] = cast(dict[str, Any], value) if isinstance(value, dict) else {}
        version = mapping.get("schema_version")
        return cls(
            execution_status=cast(
                ExecutionStatus,
                _enum(mapping.get("execution_status"), _EXECUTION_STATUSES, "incomplete"),
            ),
            stop_reason=_text(mapping.get("stop_reason")),
            task_status=cast(
                TaskStatus,
                _enum(mapping.get("task_status"), _TASK_STATUSES, "not_evaluable"),
            ),
            side_effect_status=cast(
                SideEffectStatus,
                _enum(mapping.get("side_effect_status"), _SIDE_EFFECT_STATUSES, "unknown"),
            ),
            recovery_status=cast(
                RecoveryStatus,
                _enum(mapping.get("recovery_status"), _RECOVERY_STATUSES, "resumable"),
            ),
            replay_status=cast(
                ReplayStatus,
                _enum(mapping.get("replay_status"), _REPLAY_STATUSES, "not_run"),
            ),
            error_code=_text(mapping.get("error_code"), limit=160),
            error_message=_text(mapping.get("error_message")),
            schema_version=version if isinstance(version, int) and version > 0 else 1,
        )

    @classmethod
    def from_run_result(cls, result: Any) -> TurnOutcome:
        """Project a runner result into outcome axes without mutating it.

        The helper accepts ``Any`` deliberately: importing ``AgentRunResult``
        here would create a cycle because the runner itself owns this model.
        """
        stop_reason = _text(getattr(result, "stop_reason", None))
        error = _text(getattr(result, "error", None))
        if stop_reason == "cancelled":
            execution_status: ExecutionStatus = "cancelled"
        elif error is not None or stop_reason == "error":
            execution_status = "failed"
        elif stop_reason in _LIMIT_STOP_REASONS:
            execution_status = "limited"
        elif stop_reason in {None, "", "completed"}:
            execution_status = "completed"
        else:
            execution_status = "incomplete"

        evaluation = getattr(result, "task_evaluation", None)
        evaluation_mapping: dict[str, Any] | None = (
            cast(dict[str, Any], evaluation) if isinstance(evaluation, dict) else None
        )
        raw_task_status: Any = (
            evaluation_mapping.get("status")
            if evaluation_mapping is not None
            else getattr(cast(Any, evaluation), "status", None)
        )
        task_status = cast(
            TaskStatus,
            _enum(
                raw_task_status,
                frozenset({"passed", "failed", "not_evaluable"}),
                "not_requested",
            ),
        )

        raw_tool_states = getattr(result, "tool_states", None)
        states: list[dict[str, Any]] = []
        if isinstance(raw_tool_states, list):
            states = [
                cast(dict[str, Any], state)
                for state in cast(list[Any], raw_tool_states)
                if isinstance(state, dict)
            ]
        structured_tool_outcomes = [
            cast(dict[str, Any], state["outcome"])
            for state in states
            if isinstance(state.get("outcome"), dict)
        ]
        has_unknown_side_effect = any(
            state.get("state") == "unknown"
            or (
                state.get("side_effect") == "may_have_occurred"
                and state.get("state") != "succeeded"
            )
            for state in states
        ) or any(
            outcome.get("side_effect") == "may_have_occurred"
            for outcome in structured_tool_outcomes
        )
        has_confirmed_side_effect = any(
            outcome.get("side_effect") == "confirmed"
            for outcome in structured_tool_outcomes
        ) or any(
            state.get("state") == "succeeded"
            and state.get("side_effect_class") not in {None, "none"}
            for state in states
        )
        side_effect_status: SideEffectStatus = (
            "unknown"
            if has_unknown_side_effect
            else "confirmed"
            if has_confirmed_side_effect
            else "not_applicable"
        )

        needs_confirmation = any(
            state.get("recovery_required") is True
            and state.get("recovery_resolution") not in {"approved", "auto_retry"}
            for state in states
        )
        recovered = any(
            state.get("recovery_resolution") in {"approved", "auto_retry"}
            for state in states
        )
        recovery_status: RecoveryStatus = (
            "awaiting_confirmation"
            if needs_confirmation
            else "recovered"
            if recovered
            else "resumable"
            if execution_status in {"failed", "cancelled", "limited", "incomplete"}
            else "not_needed"
        )

        error_code = (
            "TASK_VERIFICATION_FAILED"
            if task_status == "failed"
            else "TURN_CANCELLED"
            if execution_status == "cancelled"
            else "BUDGET_EXHAUSTED"
            if execution_status == "limited"
            else "AGENT_RUN_ERROR"
            if execution_status == "failed"
            else None
        )
        error_message = error
        if error_message is None and evaluation_mapping is not None:
            error_message = _text(evaluation_mapping.get("reason"))

        return cls(
            execution_status=execution_status,
            stop_reason=stop_reason,
            task_status=task_status,
            side_effect_status=side_effect_status,
            recovery_status=recovery_status,
            error_code=error_code,
            error_message=error_message,
        )

    def with_replay(self, status: ReplayStatus) -> TurnOutcome:
        """Return a copy whose replay axis changed and nothing else did."""
        if status not in _REPLAY_STATUSES:
            raise ValueError(f"unsupported replay status: {status}")
        return replace(self, replay_status=status)

    def merge_recovery(self, status: RecoveryStatus) -> TurnOutcome:
        """Return a copy whose recovery axis changed and nothing else did."""
        if status not in _RECOVERY_STATUSES:
            raise ValueError(f"unsupported recovery status: {status}")
        return replace(self, recovery_status=status)


__all__ = [
    "ExecutionStatus",
    "RecoveryStatus",
    "ReplayStatus",
    "SideEffectStatus",
    "TaskStatus",
    "TurnOutcome",
]
