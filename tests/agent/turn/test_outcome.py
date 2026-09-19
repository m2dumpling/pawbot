from __future__ import annotations

from dataclasses import dataclass, field

from pawbot.agent.turn.outcome import TurnOutcome


@dataclass
class _Evaluation:
    status: str


@dataclass
class _Result:
    stop_reason: str = "completed"
    error: str | None = None
    task_evaluation: _Evaluation | None = None
    tool_states: list[dict[str, object]] = field(default_factory=list)


def test_round_trip_keeps_all_independent_axes() -> None:
    original = TurnOutcome(
        execution_status="completed",
        stop_reason="completed",
        task_status="failed",
        side_effect_status="confirmed",
        recovery_status="not_needed",
        replay_status="divergent",
        error_code="TASK_VERIFICATION_FAILED",
        error_message="expected file is missing",
    )

    assert TurnOutcome.from_dict(original.to_dict()) == original


def test_untrusted_payload_uses_conservative_defaults() -> None:
    outcome = TurnOutcome.from_dict({"execution_status": "completed", "task_status": "unknown"})

    assert outcome.execution_status == "completed"
    assert outcome.task_status == "not_evaluable"
    assert outcome.side_effect_status == "unknown"
    assert outcome.recovery_status == "resumable"
    assert outcome.replay_status == "not_run"


def test_contract_failure_does_not_change_successful_execution() -> None:
    outcome = TurnOutcome.from_run_result(
        _Result(task_evaluation=_Evaluation("failed"))
    )

    assert outcome.execution_status == "completed"
    assert outcome.task_status == "failed"
    assert outcome.replay_status == "not_run"


def test_cancelled_side_effect_requires_confirmation() -> None:
    outcome = TurnOutcome.from_run_result(
        _Result(
            stop_reason="cancelled",
            task_evaluation=_Evaluation("not_evaluable"),
            tool_states=[
                {
                    "state": "unknown",
                    "side_effect": "may_have_occurred",
                    "recovery_required": True,
                    "recovery_resolution": "pending_confirmation",
                }
            ],
        )
    )

    assert outcome.execution_status == "cancelled"
    assert outcome.task_status == "not_evaluable"
    assert outcome.side_effect_status == "unknown"
    assert outcome.recovery_status == "awaiting_confirmation"


def test_budget_limit_is_not_reported_as_success() -> None:
    outcome = TurnOutcome.from_run_result(_Result(stop_reason="max_tool_calls"))

    assert outcome.execution_status == "limited"
    assert outcome.task_status == "not_requested"
    assert outcome.recovery_status == "resumable"


def test_axis_helpers_only_change_requested_axis() -> None:
    original = TurnOutcome(
        execution_status="failed",
        stop_reason="error",
        task_status="not_evaluable",
        side_effect_status="unknown",
        recovery_status="awaiting_confirmation",
    )

    replayed = original.with_replay("consistent")
    recovered = replayed.merge_recovery("recovered")

    assert replayed.execution_status == "failed"
    assert replayed.task_status == "not_evaluable"
    assert replayed.replay_status == "consistent"
    assert recovered.recovery_status == "recovered"
    assert recovered.side_effect_status == "unknown"
