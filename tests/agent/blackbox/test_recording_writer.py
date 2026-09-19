from __future__ import annotations

import json
from pathlib import Path

from pawbot.agent.blackbox.writer import append_jsonl, write_json_atomic


def test_append_jsonl_writes_ordered_durable_records(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    append_jsonl(path, {"sequence": 1})
    append_jsonl(path, {"sequence": 2})

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows == [{"sequence": 1}, {"sequence": 2}]


def test_write_json_atomic_replaces_previous_metadata(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    write_json_atomic(path, {"sample_status": "recording"})
    write_json_atomic(path, {"sample_status": "ready", "version": 1})

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "sample_status": "ready",
        "version": 1,
    }
    assert list(tmp_path.glob("*.tmp")) == []
