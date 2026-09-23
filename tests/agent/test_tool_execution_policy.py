from __future__ import annotations

from typing import Any

import pytest

from pawbot.agent.approval import ToolApprovalRequest, ToolApprovalResult
from pawbot.agent.hook import AgentHook, AgentHookContext
from pawbot.agent.tools.base import ToolExecutionContext, ToolExecutionPolicy, ToolResult
from pawbot.agent.tools.execution import CallExecutor, operation_id_for_tool_call
from pawbot.agent.tools.registry import ToolRegistry
from pawbot.harness import HarnessTool
from pawbot.providers.base import ToolCallRequest


class _PolicyTool(HarnessTool):
    def __init__(self, policy: ToolExecutionPolicy, action: Any) -> None:
        super().__init__(
            "mutate",
            "Perform a test operation.",
            {"type": "object", "properties": {"value": {"type": "string"}}},
            action,
            read_only=False,
            capabilities=("write",),
        )
        self._policy = policy

    @property
    def execution_policy(self) -> ToolExecutionPolicy:
        return self._policy


class _ContextAwarePolicyTool(_PolicyTool):
    def __init__(self, policy: ToolExecutionPolicy, action: Any) -> None:
        super().__init__(policy, action)
        self.execution_contexts: list[ToolExecutionContext] = []

    async def execute_with_context(
        self,
        context: ToolExecutionContext,
        **kwargs: Any,
    ) -> Any:
        self.execution_contexts.append(context)
        return await self.execute(**kwargs)


def _executor(
    registry: ToolRegistry,
    context: AgentHookContext,
    *,
    recovery_callback: Any = None,
) -> CallExecutor:
    return CallExecutor(
        registry,
        external_lookup_counts={},
        workspace_violation_counts={},
        hook=AgentHook(),
        context=context,
        recovery_approval_callback=recovery_callback,
        channel="test",
        chat_id="chat",
    )


def _interrupted_context(call: ToolCallRequest) -> AgentHookContext:
    return AgentHookContext(
        iteration=1,
        session_key="test:recovery",
        messages=[{
            "role": "tool",
            "tool_call_id": "old-call",
            "name": call.name,
            "content": "Error: Task interrupted before this tool finished.",
            "operation_id": operation_id_for_tool_call(
                "test:recovery",
                call.name,
                call.arguments,
            ),
            "_recovery_interrupted": True,
        }],
    )


def test_operation_identity_is_stable_without_collapsing_string_arguments() -> None:
    assert operation_id_for_tool_call(
        "session",
        "mutate",
        {"a": 1, "b": [2, 3]},
    ) == operation_id_for_tool_call(
        "session",
        "mutate",
        {"b": [2, 3], "a": 1},
    )
    assert operation_id_for_tool_call(
        "session",
        "mutate",
        {"value": "{}"},
    ) != (
        operation_id_for_tool_call("session", "mutate", {"value": {}})
    )


@pytest.mark.asyncio
async def test_idempotent_operation_is_retried_without_confirmation() -> None:
    calls = 0

    def action(_arguments: dict[str, Any]) -> str:
        nonlocal calls
        calls += 1
        return "ok"

    registry = ToolRegistry()
    registry.register(_PolicyTool(
        ToolExecutionPolicy(
            side_effect="reversible",
            idempotency="idempotent",
            recovery_strategy="safe_retry",
            reversible=True,
            receipt_supported=True,
        ),
        action,
    ))
    call = ToolCallRequest(id="new-call", name="mutate", arguments={"value": "x"})
    context = _interrupted_context(call)

    result, event = await _executor(registry, context).run(call)

    assert result == "ok"
    assert event["status"] == "ok"
    assert calls == 1
    assert context.tool_states[-1]["recovery_resolution"] == "auto_retry"
    assert context.tool_states[-1]["recovery_required"] is True


@pytest.mark.asyncio
async def test_idempotent_adapter_receives_stable_remote_idempotency_key() -> None:
    registry = ToolRegistry()
    tool = _ContextAwarePolicyTool(
        ToolExecutionPolicy(
            side_effect="reversible",
            idempotency="idempotent",
            recovery_strategy="safe_retry",
            reversible=True,
        ),
        lambda _arguments: "ok",
    )
    registry.register(tool)
    call = ToolCallRequest(id="call-new", name="mutate", arguments={"value": "x"})
    context = _interrupted_context(call)

    result, event = await _executor(registry, context).run(call)

    assert result == "ok"
    assert event["status"] == "ok"
    assert len(tool.execution_contexts) == 1
    execution = tool.execution_contexts[0]
    expected_id = operation_id_for_tool_call("test:recovery", "mutate", {"value": "x"})
    assert execution.operation_id == expected_id
    assert execution.idempotency_key == expected_id
    assert context.tool_states[-1]["operation_id"] == expected_id


@pytest.mark.asyncio
async def test_unknown_tool_idempotency_is_not_advertised_to_adapter() -> None:
    registry = ToolRegistry()
    tool = _ContextAwarePolicyTool(
        ToolExecutionPolicy(
            side_effect="irreversible",
            idempotency="unknown",
            recovery_strategy="manual_confirmation",
        ),
        lambda _arguments: "accepted",
    )
    registry.register(tool)
    call = ToolCallRequest(id="call-unknown", name="mutate", arguments={"value": "x"})
    context = AgentHookContext(iteration=0, session_key="test:normal", messages=[])

    result, event = await _executor(registry, context).run(call)

    assert result == "accepted"
    assert event["status"] == "ok"
    assert len(tool.execution_contexts) == 1
    assert tool.execution_contexts[0].operation_id
    assert tool.execution_contexts[0].idempotency_key is None


@pytest.mark.asyncio
async def test_unknown_side_effect_waits_for_recovery_confirmation() -> None:
    requests: list[ToolApprovalRequest] = []

    async def approve(request: ToolApprovalRequest) -> ToolApprovalResult:
        requests.append(request)
        return ToolApprovalResult.approve("operator checked the receipt")

    registry = ToolRegistry()
    registry.register(_PolicyTool(
        ToolExecutionPolicy(
            side_effect="irreversible",
            idempotency="unknown",
            recovery_strategy="manual_confirmation",
            receipt_supported=True,
        ),
        lambda _arguments: "sent",
    ))
    call = ToolCallRequest(id="new-call", name="mutate", arguments={"value": "x"})
    context = _interrupted_context(call)

    result, event = await _executor(
        registry,
        context,
        recovery_callback=approve,
    ).run(call)

    assert result == "sent"
    assert event["status"] == "ok"
    assert len(requests) == 1
    assert requests[0].recovery_required is True
    assert requests[0].operation_id == context.tool_states[-1]["operation_id"]
    assert context.tool_states[-1]["recovery_resolution"] == "approved"


@pytest.mark.asyncio
async def test_unknown_side_effect_fails_closed_without_confirmation() -> None:
    registry = ToolRegistry()
    registry.register(_PolicyTool(
        ToolExecutionPolicy(
            side_effect="unknown",
            idempotency="unknown",
            recovery_strategy="manual_confirmation",
        ),
        lambda _arguments: "must not run",
    ))
    call = ToolCallRequest(id="new-call", name="mutate", arguments={"value": "x"})
    context = _interrupted_context(call)

    result, event = await _executor(registry, context).run(call)

    assert isinstance(result, ToolResult)
    assert result.is_error is True
    assert event["status"] == "error"
    assert context.tool_states[-1]["state"] == "blocked"
    assert context.tool_states[-1]["side_effect"] == "may_have_occurred"


@pytest.mark.asyncio
async def test_failed_side_effect_marks_an_exact_retry_for_confirmation() -> None:
    attempts = 0

    def action(_arguments: dict[str, Any]) -> Any:
        nonlocal attempts
        attempts += 1
        return ToolResult.error("remote system timed out") if attempts == 1 else "sent"

    registry = ToolRegistry()
    registry.register(_PolicyTool(
        ToolExecutionPolicy(
            side_effect="irreversible",
            idempotency="non_idempotent",
            recovery_strategy="manual_confirmation",
        ),
        action,
    ))
    call = ToolCallRequest(id="call-1", name="mutate", arguments={"value": "x"})
    first_context = AgentHookContext(
        iteration=0,
        session_key="test:recovery",
        messages=[],
    )
    first_result, first_event = await _executor(registry, first_context).run(call)
    assert first_event["status"] == "error"
    assert "timed out" in str(first_result)
    assert first_context.tool_states[-1]["recovery_resolution"] == "pending_confirmation"
    first_context.messages.append({
        "role": "tool",
        "name": "mutate",
        "tool_call_id": "call-1",
        "content": str(first_result),
        "operation_id": first_context.tool_states[-1]["operation_id"],
        "_recovery_pending": True,
    })

    requests: list[ToolApprovalRequest] = []

    async def approve(request: ToolApprovalRequest) -> ToolApprovalResult:
        requests.append(request)
        return ToolApprovalResult.approve()

    second_result, second_event = await _executor(
        registry,
        first_context,
        recovery_callback=approve,
    ).run(call)
    assert second_result == "sent"
    assert second_event["status"] == "ok"
    assert attempts == 2
    assert len(requests) == 1
    assert requests[0].recovery_required is True
