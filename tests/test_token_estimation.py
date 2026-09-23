"""Token estimation tests: precision, structural overhead, budget math."""

from __future__ import annotations

from pawbot.agent.loop import AgentLoop
from pawbot.agent.token_estimation import (
    count_message_tokens,
    count_prompt_tokens,
    count_tokens,
    count_tokens_with_source,
    system_prompt_tokens,
    token_estimation_source,
    tokenizer_encoding_name,
)


class _FakeRuntime:
    context_window_tokens = 100_000

    class _Generation:
        max_tokens = 4096

    generation = _Generation()


def test_count_tokens_fallback_and_tiktoken():
    assert count_tokens("") == 0
    assert count_tokens("hello world", "fake-model") > 0
    assert tokenizer_encoding_name("gpt-4o-mini") == "o200k_base"
    assert tokenizer_encoding_name("provider-unknown") == "cl100k_base"


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


def test_token_counter_reports_the_fallback_used(monkeypatch):
    monkeypatch.setattr(
        "pawbot.agent.token_estimation._encoding",
        lambda _model: (_ for _ in ()).throw(RuntimeError("encoding unavailable")),
    )

    tokens, source = count_tokens_with_source("你好", "unknown-model")

    assert tokens == (len("你好".encode("utf-8")) + 3) // 4
    assert source == "heuristic:utf8_4_bytes_per_token"
    assert token_estimation_source("unknown-model") == source
