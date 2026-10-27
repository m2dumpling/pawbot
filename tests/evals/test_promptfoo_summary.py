from __future__ import annotations

import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest

path = Path(__file__).resolve().parents[2] / "evals/promptfoo/summarize.py"
spec = importlib.util.spec_from_file_location("promptfoo_summary", path)
assert spec is not None and spec.loader is not None
summary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summary)


def document(statuses: list[str]) -> dict:
    rows = [{
        "vars": {"case_id": "case"}, "success": status == "passed",
        "failureReason": 2 if status == "error" else 0 if status == "passed" else 1,
        "latencyMs": 100, "tokenUsage": {"total": 10},
        "response": {"metadata": {
            "case_id": "case", "case_sha256": "case-sha", "suite_sha256": "suite-sha",
            "agent_sha256": "agent-sha", "provider": "fake", "model": "demo",
            "generation": {}, "limits": {}, "cost_estimated_usd": None,
            "execution_status": "error" if status == "error" else "completed",
        }},
    } for status in statuses]
    return {"config": {"evaluateOptions": {"repeat": 3}}, "results": {"results": rows}}


def test_one_success_does_not_imply_consistent_success() -> None:
    result = summary.summarize(document(["failed", "passed", "failed"]))
    assert result["pass_rate"] == pytest.approx(1 / 3)
    assert result["empirical_pass_at_k"] == 1
    assert result["empirical_pass_power_k"] == 0
    assert result["cost_estimated_usd"] is None


def test_infrastructure_errors_are_separate_and_incomplete_groups_excluded() -> None:
    result = summary.summarize(document(["error", "passed", "failed"]))
    assert result["errors"] == 1
    assert result["evaluable"] == 2
    assert result["pass_rate"] == 0.5
    assert result["empirical_pass_at_k"] is None
    assert result["incomplete_case_groups"] == 1


def test_report_comparison_refuses_changed_oracles_or_execution_errors() -> None:
    baseline = summary.summarize(document(["passed"] * 3))
    candidate = deepcopy(baseline)
    candidate["fingerprints"]["cases"] = ["different-oracle"]
    with pytest.raises(ValueError, match="cases differs"):
        summary.compare(baseline, candidate)
    candidate = summary.summarize(document(["passed", "error", "passed"]))
    with pytest.raises(ValueError, match="execution errors"):
        summary.compare(baseline, candidate)


def test_same_source_comparison_does_not_claim_agent_improvement() -> None:
    result = summary.summarize(document(["passed"] * 3))
    comparison = summary.compare(result, result)
    assert comparison["agent_changed"] is False
    assert "not evidence of an Agent improvement" in comparison["interpretation"]


def test_changed_assertion_configuration_cannot_masquerade_as_improvement() -> None:
    original = document(["passed"] * 3)
    original["config"]["defaultTest"] = {"assert": [{"type": "javascript", "weight": 1}]}
    modified = deepcopy(original)
    modified["config"]["defaultTest"]["assert"][0]["weight"] = 0
    with pytest.raises(ValueError, match="evaluation_config differs"):
        summary.compare(summary.summarize(original), summary.summarize(modified))


def test_cli_repeat_override_takes_precedence_over_yaml_default() -> None:
    exported = document(["passed"])
    exported["runtimeOptions"] = {"repeat": 1}
    result = summary.summarize(exported)
    assert result["repeat"] == 1
    assert result["complete_case_groups"] == 1


def test_judge_raw_scores_and_unavailability_do_not_change_hard_pass_rate() -> None:
    exported = document(["passed"] * 3)
    for row, score, unavailable in zip(exported["results"]["results"], [1, 0, 0], [False, False, True], strict=True):
        row["namedScores"] = {"explanation_quality": 0}
        row["tokenUsage"]["assertions"] = {"total": 5}
        row["gradingResult"] = {"componentResults": [{
            "assertion": {"type": "llm-rubric", "weight": 0},
            "pass": True, "score": score, "reason": "judge signal",
            "metadata": {"graderError": unavailable},
        }], "tokensUsed": {"total": 5}}
    result = summary.summarize(exported)
    assert result["pass_rate"] == 1
    assert result["judge"] == {
        "evaluated": 2, "unavailable": 1, "mean_score": 0.5, "flagged": 1, "advisory": True,
    }
    assert result["cases"][0]["judge"][-1]["score"] is None
    assert result["provider_tokens"] == 30
    assert result["judge_tokens"] == 15  # Export duplicates this value; count it once.
    assert result["total_tokens"] == 45


def test_workspace_summary_keeps_splits_usage_sources_and_deduplicates_tool_events() -> None:
    exported = document(["passed", "failed", "error"])
    for index, row in enumerate(exported["results"]["results"]):
        row["vars"]["split"] = "dev" if index < 2 else "holdout"
        m = row["response"]["metadata"]
        m.update({
            "evaluation_scope": "workspace", "trial_id": f"trial-{index}",
            "split": row["vars"]["split"], "planned_splits": {"dev": 4, "holdout": 2},
            "model_requests": [{"duration_ms": 10, "usage": {
                "source": "reported" if index == 0 else "estimated",
                "input_tokens": 20, "output_tokens": 5,
            }}] if index != 2 else [{"duration_ms": 2, "usage": None}],
            "physical_model_requests": [{"duration_ms": 10}] if index != 2 else None,
            "tool_events": [
                {"id": "call", "name": "read_file", "duration_ms": 4, "status": "ok", "iteration": 0, "index": 0},
                {"id": "call", "name": "read_file", "duration_ms": 4, "status": "ok", "iteration": 0, "index": 0},
                {"id": "call", "name": "read_file", "duration_ms": 4, "status": "ok", "iteration": 1, "index": 0},
            ],
            "contexts": [{"estimated_prompt_tokens": 100, "estimated_tool_schema_tokens": 20,
                          "estimated_tool_result_tokens": 10, "token_estimation_source": "test"}],
        })
    result = summary.summarize(exported)["workspace"]
    assert result["planned_trials"] == 18
    assert result["splits"]["dev"]["passed"] == result["splits"]["dev"]["failed"] == 1
    assert result["splits"]["holdout"]["errors"] == 1
    assert result["tools"]["read_file"]["calls"] == 6
    assert result["tools"]["read_file"]["mean_ms"] == 4
    assert result["usage"]["reported_input_tokens"] == 20
    assert result["usage"]["estimated_input_tokens"] == 20
    assert result["usage"]["requests_with_missing_or_mixed_usage"] == 1
    assert result["physical_request_counts_unavailable_trials"] == 1
    assert result["context_estimates"]["mean_tool_schema_tokens"] == 20


def test_workspace_oracle_unavailable_is_not_reported_as_quality_failure() -> None:
    exported = document(["error"])
    m = exported["results"]["results"][0]["response"]["metadata"]
    m.update({"evaluation_scope": "workspace", "diagnostic_class": "oracle_unavailable"})
    result = summary.summarize(exported)["workspace"]
    assert result["splits"]["unknown"]["not_evaluable"] == 1
    assert result["splits"]["unknown"]["failed"] == 0
