from __future__ import annotations

from pawbot.agent.turn.outcome import TurnOutcome


def test_replay_axis_never_changes_original_execution_or_task_facts() -> None:
    original = TurnOutcome(
        execution_status="completed",
        stop_reason="completed",
        task_status="failed",
        side_effect_status="unknown",
        recovery_status="awaiting_confirmation",
    )

    replay = original.with_replay("consistent")

    assert replay.execution_status == "completed"
    assert replay.task_status == "failed"
    assert replay.side_effect_status == "unknown"
    assert replay.recovery_status == "awaiting_confirmation"
    assert replay.replay_status == "consistent"


def test_conservative_decode_never_invents_task_success() -> None:
    decoded = TurnOutcome.from_dict({"execution_status": "completed"})

    assert decoded.task_status == "not_evaluable"
    assert decoded.side_effect_status == "unknown"
    assert decoded.recovery_status == "resumable"
