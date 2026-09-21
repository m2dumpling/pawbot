from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from pawbot.agent.observability import TraceStore
from pawbot.webui import blackbox_api
from pawbot.webui.blackbox_api import (
    BlackboxActionError,
    _delete,
    _detail,
    _list,
    _replay,
    _trace_detail,
    _trace_list,
    _turn_diagnostics,
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
    assert result["counts"] == {
        "llm_responses": 1,
        "tool_calls": 1,
        "provider_tool_events": 0,
    }
    assert result["diagnostics"]["stop_reason"] == "unknown"
    assert result["diagnostics"]["failed_tools"] == []
    assert result["diagnostics"]["original_execution"]["status"] == "unknown"


@pytest.mark.asyncio
async def test_detail_returns_structured_trace_events_when_available(tmp_path: Path) -> None:
    root = tmp_path / "blackbox" / "trace-sample"
    root.mkdir(parents=True)
    (root / "turns.jsonl").write_text(
        json.dumps({
            "kind": "turn",
            "complete": True,
            "turn_id": "turn-trace",
            "session_key": "cli:direct",
            "model": "fake-model",
            "initial_messages": [{"role": "user", "content": "hello"}],
            "final_messages": [{"role": "assistant", "content": "done"}],
            "final_content": "done",
            "stop_reason": "completed",
        }) + "\n",
        encoding="utf-8",
    )
    (root / "events.jsonl").write_text(
        json.dumps({
            "schema_version": 1,
            "sequence": 1,
            "event": "stage.completed",
            "turn_id": "turn-trace",
            "stage": "build",
            "status": "completed",
            "duration_ms": 12,
        }) + "\n",
        encoding="utf-8",
    )

    result = await _detail(
        _agent(tmp_path),
        {"directory": str(root), "turn_id": "turn-trace"},
    )

    assert result["trace_events"] == [{
        "schema_version": 1,
        "sequence": 1,
        "event": "stage.completed",
        "turn_id": "turn-trace",
        "stage": "build",
        "status": "completed",
        "duration_ms": 12,
    }]
    assert result["files"]["events"] == "events.jsonl"


@pytest.mark.asyncio
async def test_trace_list_and_detail_read_default_lightweight_traces(tmp_path: Path) -> None:
    trace_root = tmp_path / "traces"
    trace_file = trace_root / "cli_direct" / "turn-1.jsonl"
    trace_file.parent.mkdir(parents=True)
    trace_file.write_text(
        "\n".join([
            json.dumps({
                "event": "turn.accepted",
                "trace_id": "trace:turn-1",
                "session_key": "cli:direct",
                "turn_id": "turn-1",
                "timestamp_ms": 1,
            }),
            json.dumps({
                "event": "turn.completed",
                "trace_id": "trace:turn-1",
                "session_key": "cli:direct",
                "turn_id": "turn-1",
                "status": "completed",
                "duration_ms": 25,
                "timestamp_ms": 2,
            }),
        ]) + "\n",
        encoding="utf-8",
    )
    agent = SimpleNamespace(
        workspace=tmp_path,
        blackbox=None,
        trace_store=TraceStore(trace_root),
    )

    listed = await _trace_list(agent, {})
    assert listed["traces"][0]["id"] == "cli_direct/turn-1.jsonl"
    assert listed["traces"][0]["duration_ms"] == 25
    assert listed["traces"][0]["failure_count"] == 0

    detail = await _trace_detail(agent, {"id": "cli_direct/turn-1.jsonl"})
    assert detail["summary"]["trace_id"] == "trace:turn-1"
    assert len(detail["events"]) == 2

    with pytest.raises(BlackboxActionError, match="traces"):
        await _trace_detail(agent, {"id": "../outside.jsonl"})


@pytest.mark.asyncio
async def test_trace_surfaces_the_webui_conversation_name(monkeypatch, tmp_path: Path) -> None:
    trace_root = tmp_path / "traces"
    trace_file = trace_root / "websocket_chat-1" / "turn-1.jsonl"
    trace_file.parent.mkdir(parents=True)
    trace_file.write_text(
        json.dumps({
            "event": "turn.completed",
            "trace_id": "trace:turn-1",
            "session_key": "websocket:chat-1",
            "turn_id": "turn-1",
            "status": "completed",
            "duration_ms": 25,
            "timestamp_ms": 2,
        }) + "\n",
        encoding="utf-8",
    )
    sessions = SimpleNamespace(
        list_sessions=lambda: [{
            "key": "websocket:chat-1",
            "title": "",
            "preview": "洛杉矶天气与本地新闻",
        }],
    )
    monkeypatch.setattr(blackbox_api, "list_webui_sessions", lambda _sessions: [])
    monkeypatch.setattr(blackbox_api, "read_webui_sidebar_state", lambda: {"title_overrides": {}})
    agent = SimpleNamespace(
        workspace=tmp_path,
        blackbox=None,
        sessions=sessions,
        trace_store=TraceStore(trace_root),
    )

    listed = await _trace_list(agent, {})

    assert listed["traces"][0]["session_name"] == "洛杉矶天气与本地新闻"
    trace_detail = await _trace_detail(agent, {"id": "websocket_chat-1/turn-1.jsonl"})
    assert trace_detail["summary"]["session_name"] == "洛杉矶天气与本地新闻"

    recording = tmp_path / "blackbox" / "sample-1788960000000"
    recording.mkdir(parents=True)
    (recording / "turns.jsonl").write_text(
        json.dumps({
            "kind": "turn",
            "complete": True,
            "turn_id": "turn-1",
            "session_key": "websocket:chat-1",
        }) + "\n",
        encoding="utf-8",
    )
    recordings = await _list(agent)
    assert recordings["recordings"][0]["session_names"] == ["洛杉矶天气与本地新闻"]


@pytest.mark.asyncio
async def test_token_estimation_runs_off_the_gateway_event_loop(monkeypatch) -> None:
    calling_thread = threading.get_ident()
    worker_threads: list[int] = []

    def count_tokens(*_args: Any, **_kwargs: Any) -> int:
        worker_threads.append(threading.get_ident())
        return 12

    monkeypatch.setattr("pawbot.agent.token_estimation.count_prompt_tokens", count_tokens)
    agent = SimpleNamespace(
        model="fake-model",
        tools=SimpleNamespace(get_definitions=lambda: []),
        llm_runtime=lambda: SimpleNamespace(context_window_tokens=100),
    )
    sessions = SimpleNamespace(
        get_or_create=lambda _key: SimpleNamespace(messages=[{"role": "user", "content": "hi"}]),
    )

    result = await blackbox_api._tokens(agent, sessions, {"session_key": "websocket:chat-1"})

    assert result["estimated_tokens"] == 12
    assert worker_threads and worker_threads[0] != calling_thread


def test_turn_diagnostics_separates_replay_health_from_original_errors() -> None:
    diagnostics = _turn_diagnostics(
        {"complete": True, "stop_reason": "completed"},
        [
            {
                "kind": "llm",
                "iteration": 0,
                "response": {
                    "finish_reason": "error",
                    "error_status_code": 429,
                    "error_kind": "http",
                    "error_type": "rate_limit_exceeded",
                    "error_code": "rate_limit_exceeded",
                    "content": "provider rejected the request",
                },
            },
            {
                "kind": "tool",
                "name": "write_file",
                "status": "error",
                "detail": "permission denied",
            },
        ],
    )

    assert diagnostics["original_execution"] == {
        "status": "multiple_errors",
        "ok": False,
        "failed_tool_count": 1,
        "provider_error_count": 1,
        "unknown_side_effect_count": 0,
    }
    assert diagnostics["provider_errors"][0]["status_code"] == 429
    assert diagnostics["failed_tools"][0]["name"] == "write_file"
    assert diagnostics["original_outcome"]["execution_status"] == "failed"
    assert diagnostics["original_outcome"]["task_status"] == "not_requested"
    assert diagnostics["original_outcome"]["replay_status"] == "not_run"


@pytest.mark.asyncio
async def test_replay_reports_original_execution_separately(tmp_path: Path) -> None:
    root = tmp_path / "blackbox" / "sample"
    root.mkdir(parents=True)
    (root / "turns.jsonl").write_text(
        json.dumps({
            "kind": "turn",
            "complete": True,
            "turn_id": "turn-1",
            "stop_reason": "completed",
        }) + "\n",
        encoding="utf-8",
    )
    (root / "tools.jsonl").write_text(
        json.dumps({
            "kind": "tool",
            "turn_id": "turn-1",
            "name": "exec",
            "status": "error",
            "result": "exit code 1",
        }) + "\n",
        encoding="utf-8",
    )

    async def replay_all(_controller: object) -> list[tuple[str, bool, list[str]]]:
        controller = cast(Any, _controller)
        controller.last_replay_details = [{
            "turn_id": "turn-1",
            "message_diffs": [],
            "trace_diffs": ["trace event[0] differs"],
            "trace_comparable": True,
        }]
        controller.last_benchmark = [{
            "turn_id": "turn-1",
            "elapsed_ms": 12,
            "messages": 2,
            "diffs": 1,
            "trace_diffs": 1,
            "trace_comparable": True,
            "tool_calls": 0,
        }]
        controller.last_benchmark_summary = {
            "turns": 1,
            "total_elapsed_ms": 12,
            "average_elapsed_ms": 12,
            "fastest_elapsed_ms": 12,
            "slowest_elapsed_ms": 12,
            "trace_comparable_turns": 1,
            "trace_diff_turns": 1,
        }
        return [("turn-1", True, [])]

    agent = SimpleNamespace(workspace=tmp_path, blackbox=None, replay_all=replay_all)
    result = await _replay(agent, {"directory": str(root)})

    assert result["all_deterministic"] is True
    assert result["deterministic_turns"] == 1
    assert result["original_issue_turns"] == 1
    assert result["original_failed_tool_calls"] == 1
    assert result["original_unknown_side_effects"] == 0
    assert result["results"][0]["ok"] is True
    assert result["results"][0]["original_execution"]["status"] == "tool_error"
    assert result["results"][0]["original_outcome"]["execution_status"] == "failed"
    assert result["trace_comparable_turns"] == 1
    assert result["trace_diff_turns"] == 1
    assert result["results"][0]["trace_comparable"] is True
    assert result["results"][0]["trace_diffs"] == ["trace event[0] differs"]
    assert result["benchmark"]["slowest_elapsed_ms"] == 12
