from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pawbot.webui.blackbox_api import (
    BlackboxActionError,
    _delete,
    _detail,
    _list,
)


def _agent(workspace: Path) -> SimpleNamespace:
    return SimpleNamespace(workspace=workspace, blackbox=None)


@pytest.mark.asyncio
async def test_list_marks_malformed_recordings_invalid(tmp_path: Path) -> None:
    root = tmp_path / "blackbox"
    ready = root / "ready"
    invalid = root / "invalid"
    ready.mkdir(parents=True)
    invalid.mkdir(parents=True)
    (ready / "turns.jsonl").write_text(
        json.dumps({"kind": "turn", "turn_id": "turn-1"}) + "\n",
        encoding="utf-8",
    )
    (invalid / "turns.jsonl").write_text('{"x": 1}\n', encoding="utf-8")

    result = await _list(_agent(tmp_path))
    rows = {row["name"]: row for row in result["recordings"]}

    assert rows["ready"]["status"] == "ready"
    assert rows["ready"]["turns"] == 1
    assert rows["invalid"]["status"] == "invalid"
    assert rows["invalid"]["turns"] == 0


@pytest.mark.asyncio
async def test_delete_only_removes_recordings_under_blackbox_root(tmp_path: Path) -> None:
    root = tmp_path / "blackbox"
    recording = root / "session-1"
    recording.mkdir(parents=True)
    (recording / "turns.jsonl").write_text("", encoding="utf-8")

    result = await _delete(_agent(tmp_path), {"directory": str(recording)})
    assert result["deleted"] is True
    assert not recording.exists()

    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(BlackboxActionError, match="workspace/blackbox"):
        await _delete(_agent(tmp_path), {"directory": str(outside)})


@pytest.mark.asyncio
async def test_detail_returns_raw_turn_and_execution_rail(tmp_path: Path) -> None:
    root = tmp_path / "blackbox" / "session-1"
    root.mkdir(parents=True)
    (root / "turns.jsonl").write_text(
        json.dumps(
            {
                "kind": "turn",
                "turn_id": "turn-1",
                "session_key": "websocket:chat-1",
                "model": "fake-model",
                "initial_messages": [{"role": "user", "content": "hello"}],
                "final_messages": [{"role": "assistant", "content": "done"}],
                "final_content": "done",
                "tools": [{"name": "echo", "parameters": {}}],
                "usage": {"input_tokens": 10, "output_tokens": 2},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "tools.jsonl").write_text(
        "\n".join(
            [
                json.dumps({
                    "kind": "llm",
                    "turn_id": "turn-1",
                    "iteration": 0,
                    "response_index": 0,
                    "response": {
                        "content": "",
                        "tool_calls": [{"name": "echo", "arguments": {"text": "hi"}}],
                        "finish_reason": "tool_calls",
                    },
                }),
                json.dumps({
                    "kind": "tool",
                    "turn_id": "turn-1",
                    "iteration": 0,
                    "invocation_index": 0,
                    "name": "echo",
                    "args": {"text": "hi"},
                    "status": "ok",
                    "result": "echo:hi",
                }),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = await _detail(
        _agent(tmp_path),
        {"directory": str(root), "turn_id": "turn-1"},
    )

    assert result["turn"]["initial_messages"][0]["content"] == "hello"
    assert [event["kind"] for event in result["events"]] == ["llm", "tool"]
    assert result["events"][1]["result"] == "echo:hi"
    assert result["counts"] == {"llm_responses": 1, "tool_calls": 1}
    assert result["diagnostics"]["stop_reason"] == "unknown"
    assert result["diagnostics"]["failed_tools"] == []
