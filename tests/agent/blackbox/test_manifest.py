from __future__ import annotations

from pathlib import Path

from pawbot.agent.blackbox.manifest import (
    finalize_recording_manifest,
    initialize_recording_manifest,
    recording_health,
    validate_recording_manifest,
)
from pawbot.agent.blackbox.writer import append_jsonl


def _complete_turn(turn_id: str = "turn-1") -> dict[str, object]:
    return {
        "kind": "turn",
        "complete": True,
        "turn_id": turn_id,
        "initial_messages": [{"role": "user", "content": "hello"}],
        "final_messages": [{"role": "assistant", "content": "done"}],
    }


def test_finalize_marks_a_complete_sample_ready_and_hashes_rails(tmp_path: Path) -> None:
    directory = tmp_path / "sample"
    initialize_recording_manifest(directory)
    append_jsonl(directory / "tools.jsonl", {"kind": "llm", "turn_id": "turn-1"})
    append_jsonl(directory / "turns.jsonl", _complete_turn())

    manifest = finalize_recording_manifest(directory)

    assert manifest["sample_status"] == "ready"
    assert manifest["turns"][0]["llm_response_count"] == 1
    assert validate_recording_manifest(directory) == "ready"
    assert recording_health(directory)["sample_health"] == "ready"


def test_incomplete_turn_cannot_be_replayed_as_a_ready_sample(tmp_path: Path) -> None:
    directory = tmp_path / "sample"
    initialize_recording_manifest(directory)
    append_jsonl(directory / "turns.jsonl", {**_complete_turn(), "complete": False})

    manifest = finalize_recording_manifest(directory)

    assert manifest["sample_status"] == "incomplete"
    assert validate_recording_manifest(directory) == "incomplete"


def test_changed_rail_is_detected_after_a_ready_manifest(tmp_path: Path) -> None:
    directory = tmp_path / "sample"
    initialize_recording_manifest(directory)
    append_jsonl(directory / "tools.jsonl", {"kind": "llm", "turn_id": "turn-1"})
    append_jsonl(directory / "turns.jsonl", _complete_turn())
    assert finalize_recording_manifest(directory)["sample_status"] == "ready"

    append_jsonl(directory / "turns.jsonl", _complete_turn("turn-2"))

    assert validate_recording_manifest(directory) == "corrupted"


def test_directory_without_manifest_remains_a_compatible_legacy_sample(tmp_path: Path) -> None:
    directory = tmp_path / "legacy"
    directory.mkdir()
    append_jsonl(directory / "turns.jsonl", _complete_turn())

    assert recording_health(directory)["sample_health"] == "legacy_unverified"
    assert validate_recording_manifest(directory) == "legacy_unverified"
