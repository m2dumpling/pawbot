"""Deterministic Record & Replay tests.

A scripted FakeProvider stands in for the LLM (no network), and a deterministic
FakeTool records call counts. We record one turn, then replay it offline and
assert the message sequence matches structurally — while proving the tool was
*not* actually executed during replay (side-effect isolation).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from pawbot.agent.blackbox.keys import tool_key
from pawbot.agent.blackbox.recorder import BlackboxController
from pawbot.agent.blackbox.replayer import (
    ReplayController,
    ReplayProvider,
    compare_messages,
)
from pawbot.agent.hook import AgentHookContext, AgentRunHookContext
from pawbot.agent.runner import AgentRunner, AgentRunSpec
from pawbot.agent.tools import ToolResult
from pawbot.agent.tools.base import Tool, tool_parameters
from pawbot.agent.tools.registry import ToolRegistry
from pawbot.providers.base import (
    GenerationSettings,
    LLMProvider,
    LLMResponse,
    LLMUsage,
    ToolCallRequest,
)
from pawbot.utils.llm_runtime import LLMRuntime


class FakeTool(Tool):
    """Deterministic side-effecting tool: records how many times it really runs."""

    name = "echo"
    description = "Echo text back (deterministic)"
    calls: list[dict] = []

    async def execute(self, **kwargs):
        FakeTool.calls.append(kwargs)
        return f"echo:{kwargs.get('text', '')}"


FakeTool = tool_parameters({
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
})(FakeTool)


class SequenceTool(Tool):
    name = "sequence"
    description = "Return an incrementing deterministic result"
    calls: list[dict] = []

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        return f"result-{len(self.calls)}"


SequenceTool = tool_parameters({
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
})(SequenceTool)


class ErrorTool(Tool):
    name = "error_tool"
    description = "Return a deterministic tool error"

    async def execute(self, **_kwargs):
        return ToolResult.error("Error: deterministic failure")


ErrorTool = tool_parameters({
    "type": "object",
    "properties": {},
})(ErrorTool)


class FakeProvider(LLMProvider):
    """Scripted LLM provider: no network, fixed response sequence."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        super().__init__(provider_name="fake")
        self.responses = list(responses)
        self.generation = GenerationSettings(max_tokens=1024)

    def get_default_model(self) -> str:
        return "fake-model"

    async def chat(self, *args, **kwargs) -> LLMResponse:
        return self.responses.pop(0)

    async def stream(self, *args, **kwargs):
        yield self.responses.pop(0)

    async def chat_with_retry(self, *args, **kwargs) -> LLMResponse:
        return await self.chat(*args, **kwargs)

    async def chat_stream_with_retry(self, *args, **kwargs):
        async for item in self.stream(*args, **kwargs):
            yield item


def _usage(prompt: int, completion: int) -> LLMUsage:
    total = prompt + completion
    return LLMUsage(
        input_tokens=prompt,
        output_tokens=completion,
        total_tokens=total,
        reported_tokens=total,
    )


def _build_spec(tools: ToolRegistry, runtime: LLMRuntime, hook, initial):
    return AgentRunSpec(
        initial_messages=initial,
        tools=tools,
        runtime=runtime,
        max_iterations=8,
        max_tool_result_chars=4096,
        hook=hook,
        concurrent_tools=False,
        workspace=Path("."),
        session_key="test",
        context_block_limit=80_000,
    )


@pytest.fixture()
def scripted_turn():
    """One tool-call iteration followed by a final response."""
    responses = [
        LLMResponse(
            content="",
            tool_calls=[ToolCallRequest(id="call_1", name="echo", arguments={"text": "hi"})],
            finish_reason="tool_calls",
            usage=_usage(120, 8),
        ),
        LLMResponse(content="done", finish_reason="stop", usage=_usage(200, 12)),
    ]
    return responses


async def test_record_then_replay_is_deterministic(tmp_path: Path, scripted_turn):
    FakeTool.calls = []
    provider = FakeProvider(scripted_turn)
    runtime = LLMRuntime.capture(provider, "fake-model", context_window_tokens=100_000)
    tools = ToolRegistry()
    tools.register(FakeTool())

    initial = [{"role": "user", "content": "hello"}]
    bb_dir = tmp_path / "bb"
    controller = BlackboxController(str(bb_dir))
    recorder = controller.turn_hook("turn_1", initial, session_key="test", model="fake-model")
    runner = AgentRunner()

    with controller.turn_scope("turn_1"):
        recorded = await runner.run(_build_spec(tools, runtime, recorder, initial))

    assert len(FakeTool.calls) == 1, "record mode must really execute the tool"
    assert recorded.final_content == "done"

    # Replay: the tool must NOT run again (short-circuited from the JSONL rail),
    # and the LLM rail must be served from recorded provider responses.
    FakeTool.calls = []
    replay = ReplayController(str(bb_dir))
    turn = replay.turns[0]
    assert len(replay.turns) == 1

    replay_provider = ReplayProvider(runtime.provider, replay.store, turn.turn_id)
    replay_runtime = LLMRuntime.capture(
        replay_provider, "fake-model", context_window_tokens=100_000
    )
    probe = replay.probe_hook(turn)
    with replay.turn_scope(turn):
        replayed = await runner.run(
            _build_spec(tools, replay_runtime, probe, turn.initial_messages)
        )

    assert FakeTool.calls == [], "replay must short-circuit tool execution (side-effect isolation)"
    assert not compare_messages(replayed.messages, turn.final_messages), (
        "replayed message sequence must match the recorded one structurally"
    )


async def test_replay_consumes_repeated_identical_tool_calls_in_order(
    tmp_path: Path,
    monkeypatch,
):
    """The same tool+arguments twice must consume two recorded observations."""
    monkeypatch.setattr(
        "pawbot.agent.blackbox.recorder._safe_vcr_import",
        lambda: None,
    )
    provider = FakeProvider([
        LLMResponse(
            content="",
            tool_calls=[ToolCallRequest(id="c1", name="sequence", arguments={"value": "x"})],
            finish_reason="tool_calls",
        ),
        LLMResponse(
            content="",
            tool_calls=[ToolCallRequest(id="c2", name="sequence", arguments={"value": "x"})],
            finish_reason="tool_calls",
        ),
        LLMResponse(content="done", finish_reason="stop"),
    ])
    runtime = LLMRuntime.capture(provider, "fake-model", context_window_tokens=100_000)
    tools = ToolRegistry()
    SequenceTool.calls = []
    tools.register(SequenceTool())
    initial = [{"role": "user", "content": "repeat"}]
    bb_dir = tmp_path / "repeated"
    controller = BlackboxController(str(bb_dir))
    recorder = controller.turn_hook("turn_repeat", initial, model="fake-model")

    with controller.turn_scope("turn_repeat"):
        recorded = await AgentRunner().run(_build_spec(tools, runtime, recorder, initial))

    assert SequenceTool.calls == [{"value": "x"}, {"value": "x"}]
    replay = ReplayController(str(bb_dir))
    turn = replay.turns[0]
    replay_provider = ReplayProvider(runtime.provider, replay.store, turn.turn_id)
    replay_runtime = LLMRuntime.capture(
        replay_provider,
        "fake-model",
        context_window_tokens=100_000,
    )
    SequenceTool.calls = []
    with replay.turn_scope(turn):
        replayed = await AgentRunner().run(
            _build_spec(tools, replay_runtime, replay.probe_hook(turn), initial)
        )

    assert SequenceTool.calls == []
    assert not compare_messages(replayed.messages, recorded.messages)
    tool_results = [
        message["content"]
        for message in replayed.messages
        if message.get("role") == "tool"
    ]
    assert tool_results == ["result-1", "result-2"]


async def test_replay_preserves_classified_tool_errors(tmp_path: Path):
    provider = FakeProvider([
        LLMResponse(
            content="",
            tool_calls=[ToolCallRequest(id="error-1", name="error_tool", arguments={})],
            finish_reason="tool_calls",
        ),
        LLMResponse(content="recovered", finish_reason="stop"),
    ])
    runtime = LLMRuntime.capture(provider, "fake-model", context_window_tokens=100_000)
    tools = ToolRegistry()
    tools.register(ErrorTool())
    initial = [{"role": "user", "content": "fail once"}]
    bb_dir = tmp_path / "error"
    controller = BlackboxController(str(bb_dir))
    recorder = controller.turn_hook("turn_error", initial, model="fake-model")

    with controller.turn_scope("turn_error"):
        recorded = await AgentRunner().run(_build_spec(tools, runtime, recorder, initial))

    replay = ReplayController(str(bb_dir))
    turn = replay.turns[0]
    replay_provider = ReplayProvider(runtime.provider, replay.store, turn.turn_id)
    replay_runtime = LLMRuntime.capture(
        replay_provider,
        "fake-model",
        context_window_tokens=100_000,
    )
    with replay.turn_scope(turn):
        replayed = await AgentRunner().run(
            _build_spec(tools, replay_runtime, replay.probe_hook(turn), initial)
        )

    assert recorded.final_content == "recovered"
    assert replayed.final_content == "recovered"
    assert replayed.tool_events[0]["status"] == "error"
    assert not compare_messages(replayed.messages, recorded.messages)


async def test_one_recording_accumulates_turns_from_multiple_sessions(tmp_path: Path) -> None:
    """The WebUI recording window is agent-wide, not tied to one session key."""
    directory = tmp_path / "multi-session"
    controller = BlackboxController(str(directory))

    for index, session_key in enumerate(("websocket:chat-a", "websocket:chat-b"), start=1):
        initial = [{"role": "user", "content": f"message-{index}"}]
        recorder = controller.turn_hook(
            f"turn-{index}",
            initial,
            session_key=session_key,
            model="fake-model",
        )
        await recorder.after_run(
            AgentRunHookContext(
                messages=[*initial, {"role": "assistant", "content": f"reply-{index}"}],
                final_content=f"reply-{index}",
                stop_reason="stop",
            )
        )

    replay = ReplayController(str(directory))
    assert [turn.session_key for turn in replay.turns] == [
        "websocket:chat-a",
        "websocket:chat-b",
    ]


async def test_stable_key_is_order_independent():
    key_a = tool_key("echo", {"a": 1, "b": [2, 3]})
    key_b = tool_key("echo", {"b": [2, 3], "a": 1})
    assert key_a == key_b
    assert key_a != tool_key("echo", {"a": 1})


def test_compare_messages_ignores_volatile_fields():
    a = [{"role": "user", "content": "x", "timestamp": "t1"}]
    b = [{"role": "user", "content": "x", "timestamp": "t2"}]
    assert compare_messages(a, b) == []
    c = [{"role": "user", "content": "y"}]
    assert compare_messages(a, c) != []


def test_compare_messages_ignores_provider_reasoning_and_reports_counts_correctly():
    recorded = [{
        "role": "assistant",
        "content": "same answer",
        "reasoning_content": "provider-private trace",
    }]
    replayed = [{"role": "assistant", "content": "same answer"}]
    assert compare_messages(replayed, recorded) == []

    different = [{"role": "assistant", "content": "changed answer"}]
    diffs = compare_messages(different, recorded)
    assert any("message[0] differs" in diff for diff in diffs)
    assert not any("message count differs: 1 != 1" in diff for diff in diffs)


@pytest.mark.asyncio
async def test_public_sanitized_fixture_is_loadable() -> None:
    fixture = Path(__file__).parent / "fixtures" / "blackbox" / "basic-turn"
    replay = ReplayController(str(fixture))
    turn = replay.turns[0]

    found, result = replay.store.for_turn(turn.turn_id).lookup(
        tool_key("echo", {"text": "hi"}),
    )
    assert found is True
    assert result == "echo:hi"

    provider = ReplayProvider(object(), replay.store, turn.turn_id)
    first = await provider.chat_with_retry([])
    second = await provider.chat_with_retry([])
    assert first.tool_calls[0].name == "echo"
    assert second.content == "done"


async def test_cancelled_recording_keeps_unknown_side_effect_evidence(tmp_path: Path) -> None:
    directory = tmp_path / "cancelled"
    controller = BlackboxController(str(directory))
    recorder = controller.turn_hook(
        "cancelled-turn",
        [{"role": "user", "content": "run"}],
        session_key="test",
        model="fake-model",
    )
    context = AgentHookContext(
        iteration=0,
        messages=[],
        tool_states=[{
            "call_id": "call-1",
            "name": "echo",
            "state": "unknown",
            "side_effect": "may_have_occurred",
        }],
    )
    await recorder.on_execute_tool_cancelled(
        context,
        ToolCallRequest(id="call-1", name="echo", arguments={"text": "hi"}),
        object(),
        {"text": "hi"},
    )
    await recorder.on_finally(AgentRunHookContext(
        messages=[],
        stop_reason="cancelled",
        exception=asyncio.CancelledError(),
        tool_states=context.tool_states,
    ))

    records = [
        json.loads(line)
        for line in (directory / "tools.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert records[0]["status"] == "unknown"
    assert records[0]["execution"]["side_effect"] == "may_have_occurred"
    assert json.loads((directory / "turns.jsonl").read_text(encoding="utf-8"))["complete"] is False
