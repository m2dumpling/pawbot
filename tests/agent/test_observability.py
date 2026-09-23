"""Tests for the local structured Agent execution trace."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from pawbot.agent.approval import ToolApprovalRequest, ToolApprovalResult
from pawbot.agent.blackbox.recorder import BlackboxController
from pawbot.agent.blackbox.rolling import RollingBlackboxController
from pawbot.agent.hook import AgentHookContext, AgentRunHookContext
from pawbot.agent.loop import AgentLoop
from pawbot.agent.observability import TraceStore
from pawbot.bus.events import InboundMessage
from pawbot.bus.queue import MessageBus
from pawbot.providers.base import GenerationSettings, LLMProvider, LLMResponse, ToolCallRequest


@pytest.mark.asyncio
async def test_trace_records_lifecycle_without_raw_payloads(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "traces")
    trace = store.start_turn(
        session_key="websocket:chat-1",
        turn_id="websocket:chat-1:turn-1",
        channel="websocket",
        chat_id="chat-1",
        model="fake-model",
        provider="fake",
    )
    assert trace is not None
    hook = trace.hook(initial_messages=[{"role": "user", "content": "secret prompt"}], tools_count=1)

    await hook.before_run(AgentRunHookContext(messages=[]))
    context = AgentHookContext(
        iteration=0,
        messages=[{"role": "user", "content": "secret prompt"}],
        model_message_count=1,
        context_window_tokens=100_000,
        budget={"iterations": 1},
    )
    await hook.before_iteration(context)
    context.response = LLMResponse(
        content="",
        tool_calls=[ToolCallRequest(id="call-1", name="exec", arguments={"command": "echo secret"})],
        finish_reason="tool_calls",
    )
    context.tool_calls = list(context.response.tool_calls)
    await hook.on_model_response(context)
    context.tool_events = [{"name": "exec", "status": "error", "detail": "exit code 1"}]
    context.tool_states = [{
        "call_id": "call-1",
        "name": "exec",
        "state": "failed",
        "side_effect": "may_have_occurred",
        "duration_ms": 42,
    }]
    await hook.after_iteration(context)
    trace.finish(status="error", stop_reason="error", error="api_key=top-secret")

    path = tmp_path / "traces" / "websocket_chat-1" / "websocket_chat-1_turn-1.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    names = [row["event"] for row in rows]
    assert names[0] == "turn.accepted"
    assert "llm.response" in names
    assert "tool.finished" in names
    assert names[-1] == "turn.failed"
    assert all("secret prompt" not in json.dumps(row, ensure_ascii=False) for row in rows)
    assert "<redacted>" in json.dumps(rows[-1]["error"], ensure_ascii=False)
    assert rows[names.index("tool.finished")]["duration_ms"] == 42
    trace = store.start_turn(
        session_key="websocket:chat-1",
        turn_id="turn-redaction",
        channel="websocket",
        chat_id="chat-1",
    )
    assert trace is not None
    trace.emit(
        "diagnostic.payload",
        authorization="Bearer nested-secret",
        nested={"api_key": "another-secret", "safe": "visible"},
        long_text="x" * 2_000,
        big_list=list(range(200)),
    )
    trace.finish(status="completed", stop_reason="completed")
    _, redacted_events = store.detail("websocket_chat-1/turn-redaction.jsonl")
    payload = next(event for event in redacted_events if event["event"] == "diagnostic.payload")
    assert payload["authorization"] == "<redacted>"
    assert payload["nested"]["api_key"] == "<redacted>"
    assert payload["nested"]["safe"] == "visible"
    assert len(payload["long_text"]) == 1_001
    assert len(payload["big_list"]) == 129
    summary, _ = store.detail("websocket_chat-1/websocket_chat-1_turn-1.jsonl")
    assert summary["tool_failure_count"] == 1
    assert summary["provider_error_count"] == 0


@pytest.mark.asyncio
async def test_trace_reports_prompt_estimate_source_without_prompt_text(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "traces")
    trace = store.start_turn(
        session_key="cli:token-estimate",
        turn_id="turn-token-estimate",
        channel="cli",
        chat_id="direct",
        model="test-model",
    )
    assert trace is not None
    counter = SimpleNamespace(
        estimate_prompt_tokens=lambda messages, tools, model: (321, "test-provider-counter")
    )
    hook = trace.hook(
        initial_messages=[{"role": "user", "content": "private prompt text"}],
        tools_count=1,
        token_counter=counter,
        tool_definitions=[{"type": "function", "function": {"name": "lookup"}}],
    )

    await hook.on_model_request_started(AgentHookContext(
        iteration=0,
        messages=[{"role": "user", "content": "private prompt text"}],
        model="test-model",
    ))
    trace.finish(status="completed", stop_reason="completed")

    _summary, events = store.detail("cli_token-estimate/turn-token-estimate.jsonl")
    request = next(event for event in events if event["event"] == "llm.request_started")
    assert request["estimated_prompt_tokens"] == 321
    assert request["token_estimation_source"] == "test-provider-counter"
    assert "private prompt text" not in json.dumps(request, ensure_ascii=False)


@pytest.mark.asyncio
async def test_trace_records_task_verification_as_a_separate_outcome(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "traces")
    trace = store.start_turn(
        session_key="cli:verification",
        turn_id="turn-verification",
        channel="cli",
        chat_id="direct",
    )
    assert trace is not None
    hook = trace.hook(initial_messages=[{"role": "user", "content": "finish"}], tools_count=0)

    evaluation = {
        "status": "failed",
        "completed": False,
        "reason": "task assertions failed",
        "failures": ["required file is missing: result.txt"],
    }
    await hook.on_task_verification(
        AgentRunHookContext(messages=[], task_evaluation=evaluation),
        evaluation,
        attempt=1,
    )
    trace.finish(status="completed", stop_reason="task_verification_failed")

    summary, events = store.detail("cli_verification/turn-verification.jsonl")
    verification = next(event for event in events if event["event"] == "task.verification")
    assert verification["status"] == "failed"
    assert verification["verification_status"] == "failed"
    assert verification["verification_attempt"] == 1
    assert verification["verification_failures"] == ["required file is missing: result.txt"]
    assert summary["verification_status"] == "failed"
    assert summary["verification_completed"] is False
    assert summary["outcome"] is None


def test_recording_trace_uses_recording_directory(tmp_path: Path) -> None:
    recording = tmp_path / "blackbox" / "sample"
    store = TraceStore(tmp_path / "traces")
    trace = store.start_turn(
        session_key="cli:direct",
        turn_id="turn-1",
        channel="cli",
        chat_id="direct",
        recording_directory=recording,
    )

    assert trace is not None
    trace.finish(status="completed", stop_reason="completed")
    assert (recording / "events.jsonl").exists()
    assert not (tmp_path / "traces" / "cli_direct" / "turn-1.jsonl").exists()


def test_rolling_recording_mirrors_trace_into_lightweight_store(tmp_path: Path) -> None:
    recording = tmp_path / "blackbox" / "rolling" / "session" / "turn-1"
    store = TraceStore(tmp_path / "traces")
    trace = store.start_turn(
        session_key="cli:direct",
        turn_id="turn-1",
        channel="cli",
        chat_id="direct",
        recording_directory=recording,
        mirror_recording=True,
    )

    assert trace is not None
    trace.finish(status="completed", stop_reason="completed")
    assert (recording / "events.jsonl").exists()
    assert (tmp_path / "traces" / "cli_direct" / "turn-1.jsonl").exists()


@pytest.mark.asyncio
async def test_agent_loop_trace_covers_outer_stages_and_react_response(tmp_path: Path) -> None:
    provider = MagicMock(spec=LLMProvider)
    provider.provider_name = "fake"
    provider.get_default_model.return_value = "fake-model"
    provider.generation = GenerationSettings(max_tokens=512)
    provider.chat_with_retry = AsyncMock(
        return_value=LLMResponse(content="done", finish_reason="stop"),
    )
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="fake-model",
        trace_store=TraceStore(tmp_path / "traces"),
    )

    result = await loop._process_message(
        InboundMessage(
            channel="cli",
            sender_id="user",
            chat_id="direct",
            content="hello",
        ),
    )

    assert result is not None
    trace_files = [
        path
        for path in (tmp_path / "traces").rglob("*.jsonl")
        if path.name != "index.jsonl"
    ]
    assert len(trace_files) == 1
    rows = [json.loads(line) for line in trace_files[0].read_text(encoding="utf-8").splitlines()]
    names = [row["event"] for row in rows]
    assert "stage.completed" in names
    assert "llm.response" in names
    assert "agent.completed" in names
    assert names[-1] == "turn.completed"
    assert {row.get("stage") for row in rows if row["event"] == "stage.completed"} >= {
        "restore",
        "compact",
        "build",
        "run",
        "save",
        "respond",
    }


@pytest.mark.asyncio
async def test_recorded_agent_turn_places_trace_next_to_blackbox_rails(tmp_path: Path) -> None:
    provider = MagicMock(spec=LLMProvider)
    provider.provider_name = "fake"
    provider.get_default_model.return_value = "fake-model"
    provider.generation = GenerationSettings(max_tokens=512)
    provider.chat_with_retry = AsyncMock(
        return_value=LLMResponse(content="done", finish_reason="stop"),
    )
    recording = tmp_path / "blackbox" / "sample"
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="fake-model",
        blackbox=BlackboxController(str(recording)),
        trace_store=TraceStore(tmp_path / "traces"),
    )

    await loop._process_message(
        InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="hello"),
    )

    assert (recording / "events.jsonl").exists()
    assert not list((tmp_path / "traces").rglob("*.jsonl"))
    event_names = [
        json.loads(line)["event"]
        for line in (recording / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert "llm.response" in event_names
    assert event_names[-1] == "turn.completed"


@pytest.mark.asyncio
async def test_rolling_agent_turn_keeps_replay_rails_and_light_trace(tmp_path: Path) -> None:
    provider = MagicMock(spec=LLMProvider)
    provider.provider_name = "fake"
    provider.get_default_model.return_value = "fake-model"
    provider.generation = GenerationSettings(max_tokens=512)
    provider.chat_with_retry = AsyncMock(
        return_value=LLMResponse(content="done", finish_reason="stop"),
    )
    rolling = RollingBlackboxController(tmp_path / "runtime" / "blackbox")
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="fake-model",
        blackbox=rolling,
        rolling_blackbox=rolling,
        trace_store=TraceStore(tmp_path / "traces"),
    )

    await loop._process_message(
        InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="hello"),
    )

    rolling_turns = list((tmp_path / "runtime" / "blackbox" / "rolling").rglob("turns.jsonl"))
    assert len(rolling_turns) == 1
    assert (rolling_turns[0].parent / "events.jsonl").exists()
    assert list((tmp_path / "traces").rglob("*.jsonl"))


@pytest.mark.asyncio
async def test_trace_classifies_provider_failure_with_duration(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "traces")
    trace = store.start_turn(
        session_key="cli:direct",
        turn_id="turn-provider-error",
        channel="cli",
        chat_id="direct",
        model="fake-model",
        provider="fake",
    )
    assert trace is not None
    hook = trace.hook(initial_messages=[{"role": "user", "content": "hello"}], tools_count=0)
    context = AgentHookContext(
        iteration=0,
        messages=[{"role": "user", "content": "hello"}],
        model="fake-model",
        provider="fake",
        model_message_count=1,
    )
    await hook.before_iteration(context)
    await hook.on_model_request_started(context)
    await hook.on_model_error(context, RuntimeError("api_key=top-secret"))
    trace.finish(status="error", stop_reason="error")

    summary, events = store.detail("cli_direct/turn-provider-error.jsonl")
    failed = next(event for event in events if event["event"] == "llm.request_failed")
    assert failed["duration_ms"] >= 0
    assert failed["error"]["type"] == "provider_error"
    assert failed["error"]["code"] == "RUNTIMEERROR"
    assert "top-secret" not in json.dumps(failed, ensure_ascii=False)
    assert summary["provider_error_count"] == 1


@pytest.mark.asyncio
async def test_trace_records_planned_tools_and_unknown_side_effects(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "traces")
    trace = store.start_turn(
        session_key="cli:direct",
        turn_id="turn-tool-lifecycle",
        channel="cli",
        chat_id="direct",
    )
    assert trace is not None
    hook = trace.hook(initial_messages=[], tools_count=1)
    call = ToolCallRequest(id="call-1", name="exec", arguments={"command": "echo hi"})
    context = AgentHookContext(
        iteration=0,
        messages=[],
        tool_calls=[call],
        tool_events=[{"name": "exec", "status": "cancelled"}],
        tool_states=[{
            "call_id": "call-1",
            "name": "exec",
            "state": "unknown",
            "side_effect": "may_have_occurred",
            "duration_ms": 5,
        }],
        tool_results=["cancelled"],
    )
    class ToolInfo:
        capabilities = frozenset({"execute"})
        read_only = False
        concurrency_safe = False
        exclusive = True

    await hook.before_execute_tools(context)
    await hook.before_execute_tool(context, call, ToolInfo(), {"command": "echo hi"})
    await hook.on_execute_tool_cancelled(context, call, None, {})
    await hook.after_iteration(context)
    trace.finish(status="cancelled", stop_reason="cancelled")

    summary, events = store.detail("cli_direct/turn-tool-lifecycle.jsonl")
    planned = next(event for event in events if event["event"] == "tool.planned")
    assert planned["argument_keys"] == ["command"]
    started = next(event for event in events if event["event"] == "tool.started")
    assert started["tool_capabilities"] == ["execute"]
    assert started["exclusive"] is True
    finished = next(event for event in events if event["event"] == "tool.finished")
    assert finished["read_only"] is False
    assert any(event["event"] == "tool.cancelled" for event in events)
    assert summary["tool_failure_count"] >= 1
    assert summary["unknown_side_effect_count"] >= 1


def test_trace_store_filters_and_deletes_session_traces(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "traces", retention_days=0)

    healthy = store.start_turn(
        session_key="cli:healthy",
        turn_id="turn-healthy",
        channel="cli",
        chat_id="direct",
    )
    assert healthy is not None
    healthy.finish(status="completed", stop_reason="completed")

    failed = store.start_turn(
        session_key="cli:failed",
        turn_id="turn-failed",
        channel="cli",
        chat_id="direct",
    )
    assert failed is not None
    failed.emit("tool.finished", status="failed", duration_ms=2_500, tool_name="exec")
    failed.finish(status="error", stop_reason="error")

    assert {row["session_key"] for row in store.list_summaries()} == {
        "cli:failed",
        "cli:healthy",
    }
    assert len(store.list_summaries(issues_only=True)) == 1
    assert len(store.list_summaries(slow_only=True)) == 1

    store.delete_session("cli:failed")
    remaining = store.list_summaries()
    assert len(remaining) == 1
    assert remaining[0]["session_key"] == "cli:healthy"


def test_trace_store_can_filter_unified_sessions_by_chat_id(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "traces", retention_days=0)
    for chat_id in ("chat-a", "chat-b"):
        trace = store.start_turn(
            session_key="unified:default",
            turn_id=f"turn-{chat_id}",
            channel="websocket",
            chat_id=chat_id,
        )
        assert trace is not None
        trace.finish(status="completed", stop_reason="completed")

    rows = store.list_summaries(session_key="unified:default", chat_id="chat-b")

    assert len(rows) == 1
    assert rows[0]["chat_id"] == "chat-b"


def test_reopened_store_marks_crashed_trace_incomplete(tmp_path: Path) -> None:
    root = tmp_path / "traces"
    store = TraceStore(root, retention_days=0)
    trace = store.start_turn(
        session_key="cli:crashed",
        turn_id="turn-crashed",
        channel="cli",
        chat_id="direct",
    )
    assert trace is not None
    trace.emit("stage.started", status="running", stage="run")

    reopened = TraceStore(root, retention_days=0)
    summaries = reopened.list_summaries()
    assert summaries[0]["status"] == "incomplete"
    _, events = reopened.detail("cli_crashed/turn-crashed.jsonl")
    assert events[-1]["event"] == "turn.incomplete"
    assert events[-1]["error"]["code"] == "INCOMPLETE_TRACE"
    assert events[-1]["outcome"]["execution_status"] == "incomplete"


def test_trace_inspection_does_not_close_a_live_foreign_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "traces"
    trace_file = root / "cli_live" / "turn-live.jsonl"
    trace_file.parent.mkdir(parents=True)
    trace_file.write_text(
        json.dumps({
            "schema_version": 1,
            "sequence": 1,
            "event": "turn.accepted",
            "trace_id": "trace:turn-live",
            "session_key": "cli:live",
            "turn_id": "turn-live",
            "channel": "cli",
            "chat_id": "direct",
            "owner_pid": 987654,
            "status": "accepted",
            "timestamp_ms": 1,
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("pawbot.process_runtime.process_is_running", lambda _pid: True)

    store = TraceStore(root, retention_days=0)
    summaries = store.list_summaries()
    assert summaries[0]["status"] == "accepted"
    assert len(store.detail("cli_live/turn-live.jsonl")[1]) == 1


def test_session_delete_cascades_to_lightweight_traces(tmp_path: Path) -> None:
    provider = MagicMock(spec=LLMProvider)
    provider.provider_name = "fake"
    provider.get_default_model.return_value = "fake-model"
    store = TraceStore(tmp_path / "traces", retention_days=0)
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="fake-model",
        trace_store=store,
    )
    session_key = "cli:deleted"
    session = loop.sessions.get_or_create(session_key)
    loop.sessions.save(session)
    trace = store.start_turn(
        session_key=session_key,
        turn_id="turn-deleted",
        channel="cli",
        chat_id="direct",
    )
    assert trace is not None
    trace.finish(status="completed", stop_reason="completed")
    assert loop.sessions.delete_session(session_key) is True
    assert not list((tmp_path / "traces").rglob("*.jsonl"))


def test_trace_store_enforces_count_and_size_retention(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "traces", retention_days=0, max_traces=2, max_bytes=1_000_000)
    for index in range(3):
        trace = store.start_turn(
            session_key="cli:direct",
            turn_id=f"turn-{index}",
            channel="cli",
            chat_id="direct",
        )
        assert trace is not None
        trace.finish(status="completed", stop_reason="completed")

    store.cleanup(force=True)
    assert len(store.list_summaries()) == 2
    index_rows = [
        json.loads(line)
        for line in (tmp_path / "traces" / "index.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(index_rows) == 2


@pytest.mark.asyncio
async def test_trace_records_approval_decision_without_raw_arguments(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "traces")
    trace = store.start_turn(
        session_key="websocket:approval",
        turn_id="turn-approval",
        channel="websocket",
        chat_id="approval",
    )
    assert trace is not None
    hook = trace.hook(initial_messages=[], tools_count=1)
    context = AgentHookContext(iteration=0, messages=[])
    request = ToolApprovalRequest.create(
        call_id="call-1",
        name="write_file",
        arguments={"value": "secret value"},
        capabilities=("write",),
        session_key="websocket:approval",
        iteration=0,
    )

    await hook.on_tool_approval_requested(context, request)
    await hook.on_tool_approval_resolved(
        context,
        request,
        ToolApprovalResult.deny("operator rejected"),
    )
    trace.finish(status="completed", stop_reason="completed")

    _, events = store.detail("websocket_approval/turn-approval.jsonl")
    requested = next(event for event in events if event["event"] == "tool.approval_requested")
    resolved = next(event for event in events if event["event"] == "tool.approval_resolved")
    assert requested["status"] == "waiting"
    assert requested["argument_keys"] == ["value"]
    assert resolved["status"] == "denied"
    assert resolved["reason"] == "operator rejected"
    assert "secret value" not in json.dumps(events, ensure_ascii=False)
