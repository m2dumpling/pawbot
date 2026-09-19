from __future__ import annotations

import pytest

from pawbot.harness import REQUIRED_CASE_IDS, builtin_cases, run_benchmark


@pytest.mark.asyncio
async def test_builtin_benchmark_passes_all_failure_boundaries() -> None:
    report = await run_benchmark()

    assert report.passed
    assert report.failed_count == 0
    assert report.passed_count == len(REQUIRED_CASE_IDS)
    assert {result.case_id for result in report.results} == REQUIRED_CASE_IDS

    by_id = {result.case_id: result for result in report.results}
    assert by_id["tool-failure-recovery"].tool_failures == 1
    assert by_id["provider-error"].stop_reason == "error"
    assert by_id["llm-timeout"].stop_reason == "error"
    assert by_id["cancelled-turn"].execution_status == "cancelled"
    assert by_id["turn-budget"].stop_reason == "max_tool_calls"
    assert by_id["basic-tool-call"].trajectory_status == "passed"
    assert by_id["basic-tool-call"].task_status == "passed"
    assert by_id["tool-failure-recovery"].task_status == "passed"
    assert by_id["workspace-change-and-verify"].task_status == "passed"
    assert by_id["investigate-and-summarize"].task_status == "passed"
    assert by_id["provider-error"].task_status == "not_evaluable"
    assert by_id["cancelled-turn"].task_status == "not_evaluable"
    assert by_id["basic-tool-call"].outcome["task_status"] == "passed"
    assert by_id["provider-error"].outcome["execution_status"] == "failed"
    assert by_id["cancelled-turn"].outcome["execution_status"] == "cancelled"
    assert by_id["turn-budget"].outcome["execution_status"] == "limited"

    metadata = report.to_dict()["metadata"]
    assert metadata["benchmark_version"] == "1.0"
    assert metadata["provider_mode"] == "scripted"
    assert metadata["network"] == "disabled"
    assert metadata["workspace_io"] is False
    assert isinstance(metadata["git_revision"], str)
    summary = report.to_dict()["summary"]
    assert summary["task_evaluable"] == 5
    assert summary["task_failed"] == 0
    assert summary["task_not_evaluable"] == 4
    assert summary["task_pass_rate"] == 1.0
    assert summary["tool_attempts"] == 10
    assert summary["tool_failures"] == 3


@pytest.mark.asyncio
async def test_benchmark_can_run_a_selected_case() -> None:
    report = await run_benchmark(case_ids=["tool-failure-recovery"])

    assert report.passed
    assert [result.case_id for result in report.results] == ["tool-failure-recovery"]
    assert report.results[0].tool_statuses == ("error", "ok")
    assert report.results[0].task_status == "passed"


@pytest.mark.asyncio
async def test_unknown_benchmark_case_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown harness case"):
        await run_benchmark(case_ids=["does-not-exist"])


def test_catalog_has_a_failure_or_boundary_case_for_each_required_category() -> None:
    cases = builtin_cases()
    assert {case.id for case in cases} == REQUIRED_CASE_IDS
    assert {case.category for case in cases} >= {
        "success",
        "recovery",
        "provider",
        "timeout",
        "cancellation",
        "governance",
    }
