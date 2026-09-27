from __future__ import annotations

import json
from pathlib import Path

import pytest

from pawbot.agent.blackbox.manifest import finalize_recording_manifest
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


def test_saved_recording_can_be_added_and_removed_from_task_eval(tmp_path: Path) -> None:
    controller = RollingBlackboxController(tmp_path / "runtime" / "blackbox")
    sample = controller.samples_directory / "kept-run"
    sample.mkdir()
    (sample / "turns.jsonl").write_text(
        json.dumps({
            "kind": "turn",
            "complete": True,
            "turn_id": "turn-1",
            "task_contract": {"expected_final": "done"},
        }) + "\n",
        encoding="utf-8",
    )
    (sample / "tools.jsonl").write_text(
        json.dumps({"kind": "llm", "turn_id": "turn-1"}) + "\n",
        encoding="utf-8",
    )
    finalize_recording_manifest(sample)

    case = controller.add_recording_to_eval(
        sample,
        title="会话摘要标题",
        allowed_roots=(controller.samples_directory,),
    )

    assert case["title"] == "会话摘要标题"
    assert case["task_contract"] == {"expected_final": "done"}
    assert controller.list_eval_cases() == [case]
    assert controller.remove_eval_case(str(case["id"])) is True
    assert controller.list_eval_cases() == []
    assert sample.is_dir()


def test_eval_case_removal_by_sample_drops_only_the_index_reference(tmp_path: Path) -> None:
    controller = RollingBlackboxController(tmp_path / "runtime" / "blackbox")
    sample = controller.samples_directory / "kept-run"
    sample.mkdir()
    (sample / "turns.jsonl").write_text(
        json.dumps({"kind": "turn", "complete": True, "turn_id": "turn-1"}) + "\n",
        encoding="utf-8",
    )
    (sample / "tools.jsonl").write_text(
        json.dumps({"kind": "llm", "turn_id": "turn-1"}) + "\n",
        encoding="utf-8",
    )
    finalize_recording_manifest(sample)
    controller.add_recording_to_eval(
        sample,
        allowed_roots=(controller.samples_directory,),
    )

    assert controller.remove_eval_cases_for_sample(sample) == 1
    assert controller.list_eval_cases() == []
    assert sample.exists()


def test_candidate_add_to_eval_promotes_only_after_replayability_check(tmp_path: Path) -> None:
    controller = RollingBlackboxController(tmp_path / "runtime" / "blackbox")
    candidate = controller.candidates_directory / "candidate-ready"
    candidate.mkdir()
    (candidate / "candidate.json").write_text(
        json.dumps({"candidate_id": candidate.name, "status": "candidate"}),
        encoding="utf-8",
    )
    (candidate / "turns.jsonl").write_text(
        json.dumps({"kind": "turn", "complete": True, "turn_id": "turn-1"}) + "\n",
        encoding="utf-8",
    )
    (candidate / "tools.jsonl").write_text(
        json.dumps({"kind": "llm", "turn_id": "turn-1"}) + "\n",
        encoding="utf-8",
    )
    finalize_recording_manifest(candidate)

    case = controller.add_candidate_to_eval(candidate.name, title="Readable title")

    sample = controller.samples_directory / candidate.name
    assert case["title"] == "Readable title"
    assert sample.is_dir()
    assert not candidate.exists()
    assert json.loads((sample / "candidate.json").read_text(encoding="utf-8"))["eval_case_id"] == case["id"]


def test_incomplete_candidate_is_not_moved_when_eval_registration_fails(tmp_path: Path) -> None:
    controller = RollingBlackboxController(tmp_path / "runtime" / "blackbox")
    candidate = controller.candidates_directory / "candidate-incomplete"
    candidate.mkdir()
    (candidate / "candidate.json").write_text(
        json.dumps({"candidate_id": candidate.name, "status": "candidate"}),
        encoding="utf-8",
    )
    (candidate / "turns.jsonl").write_text(
        json.dumps({"kind": "turn", "complete": True, "turn_id": "turn-1"}) + "\n",
        encoding="utf-8",
    )
    finalize_recording_manifest(candidate)

    with pytest.raises(ValueError, match="not replayable"):
        controller.add_candidate_to_eval(candidate.name)

    assert candidate.is_dir()
    assert not (controller.samples_directory / candidate.name).exists()
