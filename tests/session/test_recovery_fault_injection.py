from __future__ import annotations

from pawbot.agent.turn.outcome import TurnOutcome
from pawbot.session.manager import Session
from pawbot.session.recovery import RUNTIME_CHECKPOINT_KEY, restore_runtime_checkpoint


def test_interrupted_pending_tool_is_materialized_without_execution() -> None:
    session = Session(key="cli:fault-injection")
    session.metadata[RUNTIME_CHECKPOINT_KEY] = {
        "phase": "awaiting_tools",
        "assistant_message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call-1",
                "function": {"name": "write_file", "arguments": "{}"},
            }],
        },
        "pending_tool_calls": [{
            "id": "call-1",
            "function": {"name": "write_file", "arguments": "{}"},
        }],
        "completed_tool_results": [],
        "turn_outcome": TurnOutcome(
            execution_status="incomplete",
            stop_reason="checkpoint:awaiting_tools",
            task_status="not_evaluable",
            side_effect_status="not_applicable",
            recovery_status="resumable",
        ).to_dict(),
    }

    assert restore_runtime_checkpoint(session) is True
    interrupted = [
        message
        for message in session.messages
        if message.get("_recovery_interrupted") is True
    ]
    assert len(interrupted) == 1
    assert interrupted[0]["operation_id"]
    assert session.metadata["_last_turn_outcome"]["execution_status"] == "incomplete"
