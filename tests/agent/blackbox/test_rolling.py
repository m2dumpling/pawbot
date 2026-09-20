from __future__ import annotations

import json
from pathlib import Path

import pytest

from pawbot.agent.blackbox.rolling import RollingBlackboxController
from pawbot.agent.hook import AgentRunHookContext


def _context(*, status: str = "completed", tool_error: bool = False) -> AgentRunHookContext:
    return AgentRunHookContext(
        messages=[{"role": "user", "content": "demo"}],
        final_content="done",
        stop_reason=status,
        tool_events=[{"name": "demo", "status": "error" if tool_error else "ok"}],
        outcome={
            "execution_status": status,
            "task_status": "not_requested",
            "side_effect_status": "not_applicable",
        },
    )


@pytest.mark.parametrize("turn_number", [1, 2, 3])
def test_rolling_controller_creates_isolated_turn_directories(
    tmp_path: Path,
    turn_number: int,
) -> None:
    controller = RollingBlackboxController(
        tmp_path / "blackbox",
        max_turns_per_session=20,
    )
    directory = controller.recording_directory_for_turn(
        f"turn-{turn_number}",
        "websocket:session-1",
    )

    assert directory.parent.name.endswith("-" + directory.parent.name.split("-")[-1])
    assert directory.exists()
    assert (directory / "manifest.json").exists()


def test_tool_failure_is_promoted_to_candidate_after_outer_trace_finishes(tmp_path: Path) -> None:
    controller = RollingBlackboxController(tmp_path / "blackbox")
    directory = controller.recording_directory_for_turn("turn-1", "session-1")
    (directory / "turns.jsonl").write_text(
        json.dumps({"kind": "turn", "complete": True, "turn_id": "turn-1"}) + "\n",
        encoding="utf-8",
    )
    (directory / "tools.jsonl").write_text(
        json.dumps({"kind": "llm", "turn_id": "turn-1"}) + "\n",
        encoding="utf-8",
    )

    context = _context(tool_error=True)
    controller._on_turn_finished(directory, context)  # pyright: ignore[reportPrivateUsage]
    assert not list((tmp_path / "blackbox" / "candidates").iterdir())

    controller.finalize_turn("turn-1")

    candidates = list((tmp_path / "blackbox" / "candidates").iterdir())
    assert len(candidates) == 1
    candidate = json.loads((candidates[0] / "candidate.json").read_text(encoding="utf-8"))
    assert candidate["status"] == "candidate"
    assert "tool_error" in candidate["reasons"]


def test_successful_rolling_turn_is_not_promoted(tmp_path: Path) -> None:
    controller = RollingBlackboxController(tmp_path / "blackbox")
    directory = controller.recording_directory_for_turn("turn-1", "session-1")
    (directory / "turns.jsonl").write_text(
        json.dumps({"kind": "turn", "complete": True, "turn_id": "turn-1"}) + "\n",
        encoding="utf-8",
    )
    (directory / "tools.jsonl").write_text(
        json.dumps({"kind": "llm", "turn_id": "turn-1"}) + "\n",
        encoding="utf-8",
    )

    controller._on_turn_finished(directory, _context())  # pyright: ignore[reportPrivateUsage]
    controller.finalize_turn("turn-1")

    assert not list((tmp_path / "blackbox" / "candidates").iterdir())
    assert directory.exists()
