from __future__ import annotations

import pytest

from pawbot.agent.evaluation import TaskContract, evaluate_task
from pawbot.agent.hook import AgentHook
from pawbot.agent.runner import AgentRunner, AgentRunResult, AgentRunSpec
from pawbot.agent.tools.base import ToolResult
from pawbot.agent.tools.registry import ToolRegistry
from pawbot.harness import HarnessTool, ScriptedProvider
from pawbot.providers.base import LLMResponse, ToolCallRequest
from pawbot.utils.llm_runtime import LLMRuntime


def test_task_evaluation_requires_a_completed_agent_result() -> None:
    evaluation = evaluate_task(
        TaskContract(id="demo", final_content_contains=("done",)),
        AgentRunResult(
            final_content="provider failed",
            messages=[],
            stop_reason="error",
            error="provider failed",
        ),
        execution_status="completed",
    )

    assert evaluation.status == "not_evaluable"
    assert evaluation.completed is False
    assert "provider failed" in (evaluation.reason or "")


def test_task_evaluation_checks_final_result_and_successful_tools() -> None:
    evaluation = evaluate_task(
        TaskContract(
            id="demo",
            final_content_contains=("done",),
            required_tools=("write_file",),
        ),
        AgentRunResult(
            final_content="wrong answer",
            messages=[],
            tools_used=["read_file"],
            stop_reason="completed",
        ),
        execution_status="completed",
    )

    assert evaluation.status == "failed"
    assert evaluation.completed is False
    assert len(evaluation.failures) == 2


@pytest.mark.asyncio
async def test_task_evaluation_marks_cancelled_execution_not_evaluable() -> None:
    evaluation = evaluate_task(
        TaskContract(id="demo"),
        None,
        execution_status="cancelled",
    )

    assert evaluation.status == "not_evaluable"
    assert evaluation.reason == "execution was cancelled"


def test_task_contract_round_trip_caps_untrusted_declarative_values() -> None:
    contract = TaskContract.from_dict({
        "id": "  demo  ",
        "description": "x" * 2_000,
        "final_content_contains": ["done", "x" * 2_000],
        "tool_result_contains": [["read", "ready"]],
    })

    assert contract is not None
    assert contract.id == "demo"
    assert len(contract.description) == 1_000
    assert contract.final_content_contains == ("done", "x" * 1_000)
    assert contract.tool_result_contains == (("read", "ready"),)
    assert TaskContract.from_dict({"id": ""}) is None


def test_task_evaluation_checks_tool_results_and_custom_validator(tmp_path) -> None:
    result = AgentRunResult(
        final_content="done",
        messages=[{
            "role": "tool",
            "name": "read",
            "tool_call_id": "call-1",
            "content": "ready",
        }],
        tools_used=["read"],
        stop_reason="completed",
    )
    evaluation = evaluate_task(
        TaskContract(
            id="demo",
            tool_result_contains=(("read", "ready"),),
            validator=lambda candidate, workspace: candidate.final_content == "done",
        ),
        result,
        execution_status="completed",
        workspace=tmp_path,
    )

    assert evaluation.status == "passed"


@pytest.mark.asyncio
async def test_runner_retries_after_failed_task_verification(tmp_path) -> None:
    result_file = tmp_path / "result.txt"

    def write_result(_arguments):
        result_file.write_text("verified", encoding="utf-8")
        return ToolResult("wrote result", is_error=False)

    tool = HarnessTool(
        "write_result",
        "Write a verification file.",
        {"type": "object", "properties": {}},
        write_result,
        read_only=False,
        capabilities=("write",),
    )
    registry = ToolRegistry()
    registry.register(tool)
    provider = ScriptedProvider([
        LLMResponse(
            content=None,
            finish_reason="tool_calls",
            tool_calls=[ToolCallRequest(id="call-1", name="write_result", arguments={})],
        ),
        LLMResponse(content="I finished, but this answer is incomplete."),
        LLMResponse(content="done: verified"),
    ])
    contract = TaskContract(
        id="write-result",
        description="write and verify the result",
        final_content_contains=("done",),
        required_tools=("write_result",),
        required_files=("result.txt",),
        file_contains=(("result.txt", "verified"),),
    )

    result = await AgentRunner().run(AgentRunSpec(
        initial_messages=[{"role": "user", "content": "write the result"}],
        tools=registry,
        runtime=LLMRuntime.capture(provider, "harness-model", context_window_tokens=8_192),
        max_iterations=4,
        max_tool_result_chars=4_096,
        workspace=tmp_path,
        session_key="test:verification",
        hook=AgentHook(),
        task_contract=contract,
    ))

    assert result.final_content == "done: verified"
    assert result.task_evaluation is not None
    assert result.task_evaluation.status == "passed"
    assert provider.request_count == 3
    assert any(
        message.get("_hidden_history", {}).get("kind") == "task_verification"
        for message in result.messages
        if isinstance(message.get("_hidden_history"), dict)
    )


@pytest.mark.asyncio
async def test_runner_keeps_failed_task_verification_at_iteration_limit(tmp_path) -> None:
    provider = ScriptedProvider([LLMResponse(content="incomplete")])
    result = await AgentRunner().run(AgentRunSpec(
        initial_messages=[{"role": "user", "content": "finish"}],
        tools=ToolRegistry(),
        runtime=LLMRuntime.capture(provider, "harness-model", context_window_tokens=8_192),
        max_iterations=1,
        max_tool_result_chars=4_096,
        workspace=tmp_path,
        session_key="test:verification-limit",
        task_contract=TaskContract(id="must-finish", final_content_contains=("done",)),
    ))

    assert result.stop_reason == "task_verification_failed"
    assert result.task_evaluation is not None
    assert result.task_evaluation.status == "failed"
