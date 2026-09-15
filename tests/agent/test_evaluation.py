from __future__ import annotations

import pytest

from pawbot.agent.evaluation import TaskContract, evaluate_task
from pawbot.agent.runner import AgentRunResult


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
