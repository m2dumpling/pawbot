"""Token estimation tests: precision, structural overhead, budget math."""

from __future__ import annotations

from pawbot.agent.loop import AgentLoop
from pawbot.agent.token_estimation import (
    count_message_tokens,
    count_prompt_tokens,
    count_tokens,
    system_prompt_tokens,
)


class _FakeRuntime:
    context_window_tokens = 100_000

    class _Generation:
        max_tokens = 4096

    generation = _Generation()


def test_count_tokens_fallback_and_tiktoken():
    assert count_tokens("") == 0
    assert count_tokens("hello world", "fake-model") > 0


def test_structural_overhead_counts_tool_calls():
    plain = count_message_tokens({"role": "user", "content": "hi"})
    with_tool = count_message_tokens(
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "function": {"name": "x", "arguments": "{}"}}]}
    )
    assert with_tool > plain


def test_prompt_tokens_include_tools_schema():
    base = count_prompt_tokens([{"role": "user", "content": "hi"}])
    with_tools = count_prompt_tokens(
        [{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "t", "description": "d", "parameters": {}}}],
    )
    assert with_tools > base


def test_replay_budget_reserves_system_prompt():
    runtime = _FakeRuntime()
    budget_plain = AgentLoop._replay_token_budget(runtime)
    budget_with_system = AgentLoop._replay_token_budget(
        runtime, reserved_system_tokens=20_000
    )
    assert budget_plain > budget_with_system
    assert budget_with_system > 0


def test_system_prompt_tokens_positive():
    assert system_prompt_tokens("You are a lean coding agent.") > 0
