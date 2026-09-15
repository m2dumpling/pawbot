from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from pawbot.agent.approval import ToolApprovalManager, ToolApprovalRequest, ToolApprovalResult
from pawbot.agent.hook import AgentHook, AgentHookContext
from pawbot.agent.runner import AgentRunner
from pawbot.agent.tools.base import ToolResult
from pawbot.agent.tools.registry import ToolRegistry
from pawbot.harness import HarnessScenario, HarnessTool, ScriptedProvider
from pawbot.providers.base import LLMResponse, ToolCallRequest


def _tool_response(name: str = "write_file") -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[ToolCallRequest(id="call-1", name=name, arguments={"value": "x"})],
        finish_reason="tool_calls",
    )


def _final_response(content: str) -> LLMResponse:
    return LLMResponse(content=content, finish_reason="stop")


def _side_effect_tool(calls: list[dict[str, str]]) -> HarnessTool:
    return HarnessTool(
        "write_file",
        "Write a value to an external system.",
        {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        lambda arguments: calls.append(arguments) or "written",
        read_only=False,
        capabilities=("write",),
    )


class ApprovalHook(AgentHook):
    def __init__(self) -> None:
        super().__init__(reraise=True)
        self.requests: list[ToolApprovalRequest] = []
        self.decisions: list[ToolApprovalResult] = []

    async def on_tool_approval_requested(
        self,
        context: AgentHookContext,
        request: ToolApprovalRequest,
    ) -> None:
        del context
        self.requests.append(request)

    async def on_tool_approval_resolved(
        self,
        context: AgentHookContext,
        request: ToolApprovalRequest,
        decision: ToolApprovalResult,
    ) -> None:
        del context, request
        self.decisions.append(decision)


@pytest.mark.asyncio
async def test_approval_allows_a_high_risk_tool_before_execution() -> None:
    calls: list[dict[str, str]] = []
    scenario = HarnessScenario(
        initial_messages=[{"role": "user", "content": "write it"}],
        provider=ScriptedProvider([_tool_response(), _final_response("done")]),
        tools=ToolRegistry(),
        session_key="approval:allow",
    )
    scenario.tools.register(_side_effect_tool(calls))
    hook = ApprovalHook()

    async def approve(request: ToolApprovalRequest) -> ToolApprovalResult:
        assert request.name == "write_file"
        assert request.arguments == {"value": "x"}
        return ToolApprovalResult.approve("operator approved")

    result = await AgentRunner().run(
        replace(scenario.build_spec(), hook=hook, tool_approval_callback=approve)
    )

    assert result.final_content == "done"
    assert calls == [{"value": "x"}]
    assert hook.requests[0].capabilities == ("write",)
    assert hook.decisions == [ToolApprovalResult.approve("operator approved")]


@pytest.mark.asyncio
async def test_denial_is_model_visible_and_never_runs_the_tool() -> None:
    calls: list[dict[str, str]] = []
    scenario = HarnessScenario(
        initial_messages=[{"role": "user", "content": "write it"}],
        provider=ScriptedProvider([_tool_response(), _final_response("not written")]),
        tools=ToolRegistry(),
        session_key="approval:deny",
    )
    scenario.tools.register(_side_effect_tool(calls))
    hook = ApprovalHook()

    async def deny(_request: ToolApprovalRequest) -> ToolApprovalResult:
        return ToolApprovalResult.deny("user rejected the write")

    result = await AgentRunner().run(
        replace(scenario.build_spec(), hook=hook, tool_approval_callback=deny)
    )

    assert result.final_content == "not written"
    assert calls == []
    assert result.tool_events == [{
        "name": "write_file",
        "status": "error",
        "detail": "approval denied: user rejected the write",
    }]
    assert result.tool_states[0]["state"] == "blocked"
    assert result.tool_states[0]["side_effect"] == "not_started"
    assert hook.decisions == [ToolApprovalResult.deny("user rejected the write")]
    assert isinstance(result.messages[2]["content"], ToolResult)
    assert "was not approved" in result.messages[2]["content"]


@pytest.mark.asyncio
async def test_approval_manager_resolves_and_expires_fail_closed() -> None:
    notified: list[str] = []
    manager = ToolApprovalManager(
        timeout_s=1,
        on_request=lambda request: notified.append(request.request_id),
    )
    request = ToolApprovalRequest.create(
        call_id="call-1",
        name="write_file",
        arguments={"value": "x"},
        capabilities=("write",),
        session_key="approval:manager",
        iteration=0,
        chat_id="chat-1",
    )
    pending = asyncio.create_task(manager.request(request))
    await asyncio.sleep(0)
    assert [item.request_id for item in manager.pending_requests()] == [request.request_id]
    assert notified == [request.request_id]
    assert manager.resolve_for_chat(request.request_id, "other-chat", "approved") is False
    assert manager.resolve_for_chat(request.request_id, "chat-1", "approved", reason="human") is True
    assert await pending == ToolApprovalResult.approve("human")
    assert manager.pending_requests() == []

    expiring = ToolApprovalManager(timeout_s=0)
    expired_request = ToolApprovalRequest.create(
        call_id="call-2",
        name="write_file",
        arguments={},
        capabilities=("write",),
        session_key="approval:manager",
        iteration=0,
    )
    assert await expiring.request(expired_request) == ToolApprovalResult.deny("approval timed out")


@pytest.mark.asyncio
async def test_read_only_tools_do_not_trigger_approval_callback() -> None:
    called = False

    async def unexpected(_request: ToolApprovalRequest) -> ToolApprovalResult:
        nonlocal called
        called = True
        return ToolApprovalResult.deny()

    read_only = HarnessTool(
        "read_value",
        "Read a value.",
        {"type": "object", "properties": {}},
        lambda _arguments: "value",
        read_only=True,
        capabilities=("read",),
    )
    scenario = HarnessScenario(
        initial_messages=[{"role": "user", "content": "read it"}],
        provider=ScriptedProvider([
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="call-1", name="read_value", arguments={})],
                finish_reason="tool_calls",
            ),
            _final_response("value"),
        ]),
        tools=ToolRegistry(),
        session_key="approval:read-only",
    )
    scenario.tools.register(read_only)

    result = await AgentRunner().run(
        replace(scenario.build_spec(), tool_approval_callback=unexpected)
    )

    assert result.final_content == "value"
    assert called is False
