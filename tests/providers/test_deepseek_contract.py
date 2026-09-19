from __future__ import annotations

from unittest.mock import patch

from pawbot.providers.openai_compat_provider import OpenAICompatProvider
from pawbot.providers.registry import (
    context_window_tokens_for,
    find_by_name,
    reasoning_effort_values_for,
)


def _provider() -> OpenAICompatProvider:
    with patch("pawbot.providers.openai_compat_provider.AsyncOpenAI"):
        return OpenAICompatProvider(
            api_key="test-key",
            default_model="deepseek-v4-flash",
            spec=find_by_name("deepseek"),
        )


def test_deepseek_v4_flash_capability_contract() -> None:
    spec = find_by_name("deepseek")
    assert spec is not None
    assert context_window_tokens_for("deepseek", "deepseek-v4-flash", 128_000) == 1_048_576
    assert reasoning_effort_values_for("deepseek", "deepseek-v4-flash") == [
        "",
        "low",
        "high",
        "max",
    ]
    model = next(item for item in spec.model_capabilities if item.id == "deepseek-v4-flash")
    assert model.supports_tools is True
    assert model.supports_reasoning is True
    assert model.max_output_tokens == 384_000


def test_deepseek_request_contract_keeps_tools_reasoning_and_long_context_history() -> None:
    provider = _provider()
    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "read_file", "arguments": '{"path":"README.md"}'},
    }
    kwargs = provider._build_kwargs(
        messages=[
            {"role": "system", "content": "You are an agent."},
            {"role": "user", "content": "inspect the readme"},
            {"role": "assistant", "content": "", "tool_calls": [tool_call]},
            {"role": "tool", "tool_call_id": "call_1", "name": "read_file", "content": "ok"},
            {"role": "user", "content": "continue"},
        ],
        tools=[{
            "type": "function",
            "function": {
                "name": "read_file",
                "parameters": {"type": "object", "properties": {}},
            },
        }],
        model="deepseek-v4-flash",
        max_tokens=1024,
        temperature=0.1,
        reasoning_effort="high",
        tool_choice=None,
    )

    assert kwargs["model"] == "deepseek-v4-flash"
    assert kwargs["tools"]
    assert kwargs["tool_choice"] == "auto"
    assert kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
    assert kwargs["messages"][2]["reasoning_content"] == ""


def test_deepseek_response_contract_normalizes_reasoning_tool_call_and_usage() -> None:
    provider = _provider()
    response = provider._parse({
        "choices": [{
            "message": {
                "content": None,
                "reasoning_content": "I should inspect the file first.",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path":"README.md"}',
                    },
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
        },
    })

    assert response.finish_reason == "tool_calls"
    assert response.reasoning_content == "I should inspect the file first."
    assert response.tool_calls[0].name == "read_file"
    assert response.tool_calls[0].arguments == {"path": "README.md"}
    assert response.usage is not None
    assert response.usage.total_tokens == 120


def test_deepseek_stream_contract_accumulates_tool_arguments_and_reasoning() -> None:
    response = OpenAICompatProvider._parse_chunks([
        {"choices": [{"delta": {"reasoning_content": "inspect "}}]},
        {"choices": [{"delta": {"reasoning_content": "the file"}}]},
        {"choices": [{"delta": {"tool_calls": [{
            "index": 0,
            "id": "call_1",
            "function": {"name": "read_file", "arguments": '{"path":'},
        }]}}]},
        {"choices": [{"delta": {"tool_calls": [{
            "index": 0,
            "function": {"arguments": '"README.md"}'},
        }]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}},
    ])

    assert response.reasoning_content == "inspect the file"
    assert response.finish_reason == "tool_calls"
    assert response.tool_calls[0].id == "call_1"
    assert response.tool_calls[0].arguments == {"path": "README.md"}
    assert response.usage is not None
    assert response.usage.total_tokens == 7
