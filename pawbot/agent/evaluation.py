"""Task-level evaluation kept separate from Agent trajectory checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pawbot.agent.runner import AgentRunResult

TaskEvaluationStatus = Literal["passed", "failed", "not_evaluable"]


@dataclass(frozen=True, slots=True)
class TaskContract:
    """Declarative assertions for the outcome of one Agent task."""

    id: str
    description: str = ""
    final_content_equals: str | None = None
    final_content_contains: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskEvaluation:
    """Result of evaluating the task outcome, not the internal trajectory."""

    status: TaskEvaluationStatus
    completed: bool
    reason: str | None = None
    failures: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "completed": self.completed,
            "reason": self.reason,
            "failures": list(self.failures),
        }


def evaluate_task(
    contract: TaskContract,
    result: AgentRunResult | None,
    *,
    execution_status: str,
) -> TaskEvaluation:
    """Evaluate final task completion without treating trace shape as quality."""
    if result is None or execution_status != "completed":
        return TaskEvaluation(
            status="not_evaluable",
            completed=False,
            reason=(
                "execution was cancelled"
                if execution_status == "cancelled"
                else "execution did not produce an Agent result"
            ),
        )
    if result.stop_reason != "completed" or result.error:
        return TaskEvaluation(
            status="not_evaluable",
            completed=False,
            reason=(result.error or f"stop_reason={result.stop_reason}"),
        )
    if not result.final_content or not result.final_content.strip():
        return TaskEvaluation(
            status="not_evaluable",
            completed=False,
            reason="the Agent did not produce a final answer",
        )

    failures: list[str] = []
    if (
        contract.final_content_equals is not None
        and result.final_content != contract.final_content_equals
    ):
        failures.append("final answer does not equal the task contract")
    for expected in contract.final_content_contains:
        if expected not in result.final_content:
            failures.append(f"final answer is missing {expected!r}")
    missing_tools = [
        name for name in contract.required_tools if name not in result.tools_used
    ]
    if missing_tools:
        failures.append("required successful tools missing: " + ", ".join(missing_tools))
    return TaskEvaluation(
        status="failed" if failures else "passed",
        completed=not failures,
        reason=None if not failures else "task assertions failed",
        failures=tuple(failures),
    )


__all__ = [
    "TaskContract",
    "TaskEvaluation",
    "TaskEvaluationStatus",
    "evaluate_task",
]
