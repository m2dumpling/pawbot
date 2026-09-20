"""Small task-level evaluation set built on the deterministic Agent Harness.

This is intentionally not a general LLM-judge platform.  The built-in set is
provider-free and deterministic, so it can run in CI and compare task outcome,
trajectory safety, and execution telemetry without real tools or network I/O.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Sequence

from pawbot.harness import HarnessReport, builtin_cases, run_benchmark

EVAL_SET_NAME = "pawbot-task-eval"
EVAL_SET_VERSION = 1


@dataclass(frozen=True, slots=True)
class EvalCaseSpec:
    id: str
    title: str
    category: str
    description: str
    source: str = "sanitized_runtime_fixture"

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "description": self.description,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class EvalReport:
    eval_set: str
    version: int
    harness: HarnessReport
    elapsed_ms: int

    @property
    def passed(self) -> bool:
        return self.harness.passed

    def to_dict(self) -> dict[str, Any]:
        payload = self.harness.to_dict()
        metadata = dict(payload.get("metadata") or {})
        metadata["benchmark"] = self.eval_set
        metadata["eval_set_version"] = self.version
        metadata["evaluation_kind"] = "task_and_trajectory"
        payload["metadata"] = metadata
        summary = dict(payload.get("summary") or {})
        summary.update({
            "eval_set": self.eval_set,
            "eval_set_version": self.version,
            "elapsed_ms": self.elapsed_ms,
            "status": "passed" if self.passed else "failed",
            "trajectory_pass_rate": (
                sum(
                    result.trajectory_status == "passed"
                    for result in self.harness.results
                ) / len(self.harness.results)
                if self.harness.results
                else None
            ),
            "unknown_side_effects": sum(
                1
                for result in self.harness.results
                if (result.outcome or {}).get("side_effect_status") == "unknown"
            ),
        })
        payload["benchmark"] = self.eval_set
        payload["version"] = self.version
        payload["summary"] = summary
        payload["cases"] = [
            {
                "id": result.case_id,
                "title": result.title,
                "category": result.category,
                "task_status": result.task_status,
                "trajectory_status": result.trajectory_status,
                "execution_status": result.execution_status,
                "elapsed_ms": result.elapsed_ms,
                "model_requests": result.model_requests,
                "tool_attempts": result.tool_attempts,
                "tool_failures": result.tool_failures,
                "failures": list(result.failures),
                "task_assertions": [dict(item) for item in result.task_assertions],
            }
            for result in self.harness.results
        ]
        return payload


def eval_cases() -> tuple[EvalCaseSpec, ...]:
    """Return the stable, sanitized task-evaluation catalog."""
    catalog = {case.id: case for case in builtin_cases()}
    selected_ids = (
        "basic-tool-call",
        "tool-failure-recovery",
        "workspace-change-and-verify",
        "investigate-and-summarize",
        "approval-gated-tool",
        "turn-budget",
    )
    return tuple(
        EvalCaseSpec(
            id=case_id,
            title=catalog[case_id].title,
            category=catalog[case_id].category,
            description=catalog[case_id].description,
        )
        for case_id in selected_ids
    )


async def run_eval(
    *,
    case_ids: Sequence[str] | None = None,
    fail_fast: bool = False,
) -> EvalReport:
    catalog = {case.id: case for case in eval_cases()}
    selected = list(catalog) if case_ids is None else [str(case_id) for case_id in case_ids]
    unknown = [case_id for case_id in selected if case_id not in catalog]
    if unknown:
        raise ValueError(f"unknown eval case(s): {', '.join(unknown)}")
    started = time.perf_counter()
    harness = await run_benchmark(case_ids=selected, fail_fast=fail_fast)
    elapsed_ms = max(0, round((time.perf_counter() - started) * 1000))
    return EvalReport(
        eval_set=EVAL_SET_NAME,
        version=EVAL_SET_VERSION,
        harness=harness,
        elapsed_ms=elapsed_ms,
    )


def run_eval_sync(
    *,
    case_ids: Sequence[str] | None = None,
    fail_fast: bool = False,
) -> EvalReport:
    return asyncio.run(run_eval(case_ids=case_ids, fail_fast=fail_fast))


__all__ = [
    "EVAL_SET_NAME",
    "EVAL_SET_VERSION",
    "EvalCaseSpec",
    "EvalReport",
    "eval_cases",
    "run_eval",
    "run_eval_sync",
]
