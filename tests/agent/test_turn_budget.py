from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from agent.runner_helpers import make_run_spec
from pawbot.agent.budget import TurnBudget
from pawbot.agent.hook import AgentHook, AgentHookContext
from pawbot.agent.runner import AgentRunner
from pawbot.agent.tools.base import Tool
from pawbot.agent.tools.execution import execute_tool_calls
from pawbot.agent.tools.registry import ToolRegistry
from pawbot.providers.base import LLMResponse, LLMUsage, ToolCallRequest


class _CountingTool(Tool):
    calls = 0

    @property
    def name(self) -> str:
        return "count"

    @property
    def description(self) -> str:
        return "count calls"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset({"execute"})

    async def execute(self, **kwargs):
        del kwargs
        type(self).calls += 1
        return "ok"


class _CancelledTool(_CountingTool):
    async def execute(self, **kwargs):
        del kwargs
        raise asyncio.CancelledError


def _provider(*responses: LLMResponse) -> MagicMock:
    provider = MagicMock()
    provider.chat_with_retry = MagicMock()
    pending = list(responses)

    async def chat_with_retry(*args, **kwargs):
        del args, kwargs
        return pending.pop(0)

    provider.chat_with_retry = chat_with_retry
    return provider


@pytest.mark.asyncio
async def test_turn_budget_blocks_tool_calls_before_side_effects() -> None:
    _CountingTool.calls = 0
    provider = _provider(
        LLMResponse(
            content="",
            tool_calls=[ToolCallRequest(id="call-1", name="count", arguments={})],
            finish_reason="tool_calls",
            usage=LLMUsage.reported(input_tokens=10, output_tokens=2),
        ),
    )
    tools = ToolRegistry()
    tools.register(_CountingTool())

    result = await AgentRunner().run(make_run_spec(
        provider,
        initial_messages=[{"role": "user", "content": "run"}],
        tools=tools,
        model="test-model",
        max_iterations=3,
        max_tool_result_chars=16_000,
        budget=TurnBudget(max_iterations=3, max_tool_calls=0),
    ))

    assert _CountingTool.calls == 0
    assert result.stop_reason == "max_tool_calls"
    assert result.tool_states[0]["state"] == "blocked"
    assert result.budget["usage"]["tool_calls"] == 1


@pytest.mark.asyncio
async def test_tool_capability_policy_blocks_without_execution() -> None:
    _CountingTool.calls = 0
    provider = _provider(
        LLMResponse(
            content="",
            tool_calls=[ToolCallRequest(id="call-1", name="count", arguments={})],
            finish_reason="tool_calls",
        ),
        LLMResponse(content="done", finish_reason="stop"),
    )
    tools = ToolRegistry()
    tools.register(_CountingTool())

    result = await AgentRunner().run(make_run_spec(
        provider,
        initial_messages=[{"role": "user", "content": "run"}],
        tools=tools,
        model="test-model",
        max_iterations=3,
        max_tool_result_chars=16_000,
        denied_tool_capabilities=frozenset({"execute"}),
    ))

    assert _CountingTool.calls == 0
    assert result.final_content == "done"
    assert result.tool_events[0]["status"] == "error"
    assert result.tool_states[0]["state"] == "blocked"


@pytest.mark.asyncio
async def test_cancelled_mutating_tool_is_marked_unknown_side_effect() -> None:
    tools = ToolRegistry()
    tools.register(_CancelledTool())
    context = AgentHookContext(iteration=0, messages=[])

    with pytest.raises(asyncio.CancelledError):
        await execute_tool_calls(
            tools,
            [ToolCallRequest(id="call-1", name="count", arguments={})],
            concurrent=False,
            external_lookup_counts={},
            workspace_violation_counts={},
            hook=AgentHook(),
            context=context,
        )

    assert context.tool_states[0]["state"] == "unknown"
    assert context.tool_states[0]["side_effect"] == "may_have_occurred"


def test_turn_budget_tracks_cost_when_rates_are_configured() -> None:
    budget = TurnBudget(
        max_iterations=3,
        input_cost_per_million_usd=1.0,
        output_cost_per_million_usd=2.0,
    )
    budget.start()
    budget.observe_usage(LLMUsage.reported(input_tokens=1_000, output_tokens=500))

    assert budget.cost_usd == pytest.approx(0.002)
    assert budget.snapshot()["usage"]["input_tokens"] == 1_000
