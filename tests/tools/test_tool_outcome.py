from __future__ import annotations

from pawbot.agent.tools.outcome import ToolOutcome


def test_successful_state_becomes_a_confirmed_structured_outcome() -> None:
    outcome = ToolOutcome.from_state({
        "name": "write_file",
        "call_id": "call-1",
        "operation_id": "op-1",
        "state": "succeeded",
        "side_effect": "confirmed",
        "idempotency": "idempotent",
        "recovery_strategy": "safe_retry",
        "receipt": "write-123",
        "duration_ms": 12,
    })

    assert outcome.status == "succeeded"
    assert outcome.error_code is None
    assert outcome.side_effect == "confirmed"
    assert outcome.receipt == "write-123"


def test_unknown_side_effect_is_never_reported_as_success() -> None:
    outcome = ToolOutcome.from_state({
        "name": "send_email",
        "operation_id": "op-1",
        "state": "unknown",
        "side_effect": "may_have_occurred",
        "idempotency": "non_idempotent",
        "recovery_strategy": "manual_confirmation",
        "recovery_required": True,
        "error_code": "UNKNOWN_SIDE_EFFECT",
    })

    assert outcome.status == "unknown"
    assert outcome.error_code == "UNKNOWN_SIDE_EFFECT"
    assert outcome.recovery_required is True


def test_failed_legacy_state_has_a_safe_fallback_code() -> None:
    outcome = ToolOutcome.from_state({
        "name": "unknown_tool",
        "operation_id": "op-1",
        "state": "failed",
        "side_effect": "not_started",
    })

    assert outcome.status == "failed"
    assert outcome.error_code == "TOOL_EXECUTION_FAILED"
    assert outcome.side_effect == "not_started"
