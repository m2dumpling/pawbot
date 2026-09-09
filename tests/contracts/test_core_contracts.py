"""Small contract checks for the public agent building blocks.

The larger integration suite exercises these components in combination. These
tests intentionally keep the boundaries visible so a provider, tool plugin, or
session storage change fails close to the contract it violates.
"""

from __future__ import annotations

from typing import Any

from pawbot.agent.tools.base import Tool
from pawbot.agent.tools.registry import ToolRegistry
from pawbot.providers.base import LLMResponse, ToolCallRequest
from pawbot.session.manager import Session


class _ContractTool(Tool):
    @property
    def name(self) -> str:
        return "contract_tool"

    @property
    def description(self) -> str:
        return "A minimal contract tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        }

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        return str(kwargs["value"])


def test_provider_contract_preserves_tool_calls_and_terminal_semantics() -> None:
    call = ToolCallRequest(id="call-1", name="contract_tool", arguments={"value": "ok"})
    response = LLMResponse(content=None, tool_calls=[call], finish_reason="tool_calls")

    assert response.should_execute_tools is True
    wire = call.to_openai_tool_call()
    assert wire["function"]["name"] == "contract_tool"
    assert wire["function"]["arguments"] == '{"value": "ok"}'


def test_tool_contract_exposes_schema_capability_and_validation_boundary() -> None:
    registry = ToolRegistry()
    registry.register(_ContractTool())

    tool, params, error = registry.prepare_call("contract_tool", {"value": "ok"})
    assert tool is not None
    assert params == {"value": "ok"}
    assert error is None
    assert tool.capabilities == frozenset({"read"})
    assert registry.get_definitions()[0]["function"]["name"] == "contract_tool"


def test_session_contract_persists_order_and_replayable_roles() -> None:
    session = Session(key="contract:session")
    session.add_message("user", "hello")
    session.add_message("assistant", "world")

    history = session.get_history()

    assert [message["role"] for message in history] == ["user", "assistant"]
    assert [message["content"] for message in history] == ["hello", "world"]
