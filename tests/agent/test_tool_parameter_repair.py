from __future__ import annotations

import pytest

from pawbot.agent.runner import AgentRunner
from pawbot.agent.tools.registry import ToolRegistry
from pawbot.harness import HarnessScenario, HarnessTool, ScriptedProvider
from pawbot.providers.base import LLMResponse, ToolCallRequest


def _call(count: int) -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[ToolCallRequest(
            id=f"call-{count}",
            name="record_count",
            arguments={"count": count},
        )],
        finish_reason="tool_calls",
    )


def _final(text: str = "done") -> LLMResponse:
    return LLMResponse(content=text, finish_reason="stop")


def _scenario(responses: list[LLMResponse], *, max_iterations: int = 8):
    calls: list[int] = []
    registry = ToolRegistry()
    registry.register(HarnessTool(
        "record_count",
        "Record a positive count.",
        {
            "type": "object",
            "properties": {"count": {"type": "integer", "minimum": 1}},
            "required": ["count"],
            "additionalProperties": False,
        },
        lambda arguments: calls.append(arguments["count"]) or "recorded",
    ))
    scenario = HarnessScenario(
        initial_messages=[{"role": "user", "content": "Record a count."}],
        provider=ScriptedProvider(responses),
        tools=registry,
        session_key="test:parameter-repair",
        max_iterations=max_iterations,
    )
    return scenario, calls


@pytest.mark.asyncio
async def test_invalid_tool_arguments_are_model_visible_and_repairable() -> None:
    scenario, calls = _scenario([_call(-1), _call(2), _final("recorded 2")])

    result = await AgentRunner().run(scenario.build_spec())

    assert result.final_content == "recorded 2"
    assert calls == [2]
    assert result.tool_events[0]["status"] == "error"
    assert "count must be >= 1" in str(result.messages[2]["content"])
    assert result.tool_events[1]["status"] == "ok"
    assert scenario.provider.request_count == 3


@pytest.mark.asyncio
async def test_repeated_invalid_tool_arguments_stop_at_iteration_budget() -> None:
    # The runner may issue one final response-only request after the iteration
    # loop; that finalizer is not allowed to execute another tool call.
    scenario, calls = _scenario([_call(-1), _call(0), _final("cannot continue")], max_iterations=2)

    result = await AgentRunner().run(scenario.build_spec())

    assert calls == []
    assert scenario.provider.request_count == 3
    assert len(result.tool_events) == 2
    assert all(event["status"] == "error" for event in result.tool_events)
    assert result.stop_reason == "max_iterations"
