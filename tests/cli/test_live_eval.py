from __future__ import annotations

import json
import re
from pathlib import Path

from typer.testing import CliRunner

from pawbot.agent.live_eval import LIVE_EVAL_VERSION, load_live_eval_cases
from pawbot.cli.commands import app

runner = CliRunner()


def test_live_eval_list_shows_current_catalog_version() -> None:
    result = runner.invoke(app, ["eval", "live", "list"])

    assert result.exit_code == 0, result.output
    assert f"Live Eval · v{LIVE_EVAL_VERSION}" in result.output


def test_live_eval_compare_reports_per_case_and_metric_deltas(tmp_path: Path) -> None:
    baseline = {
        "benchmark": "pawbot-incident-triage-live",
        "label": "baseline",
        "metadata": {"dataset_sha256": "same-catalog"},
        "summary": {"pass_at_1": 0.5, "estimated_cost_usd": 0.2, "mean_latency_ms": 1_500},
        "cases": [
            {"case_id": "case-a", "status": "failed"},
            {"case_id": "case-b", "status": "passed"},
        ],
    }
    candidate = {
        "benchmark": "pawbot-incident-triage-live",
        "label": "candidate",
        "metadata": {"dataset_sha256": "same-catalog"},
        "summary": {"pass_at_1": 1.0, "estimated_cost_usd": 0.25, "mean_latency_ms": 1_200},
        "cases": [
            {"case_id": "case-a", "status": "passed"},
            {"case_id": "case-b", "status": "passed"},
        ],
    }
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

    result = runner.invoke(app, [
        "eval", "live", "compare",
        "--baseline", str(baseline_path),
        "--candidate", str(candidate_path),
        "--json",
    ])

    assert result.exit_code == 0, result.output
    comparison = json.loads(result.stdout)
    assert comparison["pass_at_1"]["delta"] == 0.5
    assert comparison["estimated_cost_usd"]["delta"] == 0.05
    assert comparison["mean_latency_ms"]["delta"] == -300
    assert comparison["cases"][0]["changed"] is True


def test_live_eval_compare_preserves_repeated_trial_statuses(tmp_path: Path) -> None:
    baseline = {
        "benchmark": "pawbot-incident-triage-live",
        "label": "baseline",
        "metadata": {"dataset_sha256": "same-catalog"},
        "summary": {"trials_requested_per_case": 2},
        "cases": [
            {"case_id": "case-a", "trial": 1, "status": "passed"},
            {"case_id": "case-a", "trial": 2, "status": "failed"},
        ],
    }
    candidate = {
        "benchmark": "pawbot-incident-triage-live",
        "label": "candidate",
        "metadata": {"dataset_sha256": "same-catalog"},
        "summary": {"trials_requested_per_case": 2},
        "cases": [
            {"case_id": "case-a", "trial": 1, "status": "passed"},
            {"case_id": "case-a", "trial": 2, "status": "passed"},
        ],
    }
    baseline_path = tmp_path / "baseline-multiple.json"
    candidate_path = tmp_path / "candidate-multiple.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

    result = runner.invoke(app, [
        "eval", "live", "compare",
        "--baseline", str(baseline_path),
        "--candidate", str(candidate_path),
        "--json",
    ])

    assert result.exit_code == 0, result.output
    comparison = json.loads(result.stdout)
    row = comparison["cases"][0]
    assert row["baseline"]["trial_statuses"] == ["passed", "failed"]
    assert row["candidate"]["trial_statuses"] == ["passed", "passed"]
    assert row["baseline"]["pass_rate"] == 0.5
    assert row["candidate"]["pass_rate"] == 1.0
    assert row["changed"] is True


def test_live_eval_run_requires_explicit_cost_limit() -> None:
    result = runner.invoke(app, ["eval", "live", "run"], env={"FORCE_COLOR": "1"})

    assert result.exit_code != 0
    plain_output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", result.output)
    assert "--max-total-cost-usd" in plain_output


def test_cli_candidate_export_writes_only_reviewed_case_payload(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"agents": {"defaults": {"workspace": str(tmp_path / "workspace")}}}),
        encoding="utf-8",
    )
    candidate_id = "candidate-cli-export"
    candidate_dir = tmp_path / "blackbox" / "candidates" / candidate_id
    candidate_dir.mkdir(parents=True)
    (candidate_dir / "candidate.json").write_text(
        json.dumps({"candidate_id": candidate_id, "status": "candidate"}),
        encoding="utf-8",
    )
    private_prompt = "Private customer name: Asha Example, email asha@example.com"
    (candidate_dir / "turns.jsonl").write_text(
        json.dumps({"input": private_prompt}) + "\n",
        encoding="utf-8",
    )
    case = next(
        case
        for case in load_live_eval_cases()
        if case.id == "general-token-estimate-policy"
    ).model_dump(mode="json")
    case["privacy"] = "sanitized"
    case["input"] = "Explain why generic tokenization is an estimate."
    case_path = tmp_path / "reviewed-case.json"
    case_path.write_text(json.dumps(case), encoding="utf-8")

    result = runner.invoke(app, [
        "record", "export-live-eval", candidate_id,
        "--case-json", str(case_path),
        "--review-reference", "ticket-42",
        "--config", str(config_path),
    ])

    assert result.exit_code == 0, result.output
    catalog_path = tmp_path / "blackbox" / "samples" / case["id"] / "live-eval-catalog.v1.json"
    assert catalog_path.exists()
    catalog_text = catalog_path.read_text(encoding="utf-8")
    assert private_prompt not in catalog_text
    assert "asha@example.com" not in catalog_text
    assert private_prompt in (tmp_path / "blackbox" / "samples" / case["id"] / "turns.jsonl").read_text(encoding="utf-8")
    exported = load_live_eval_cases(catalog_path)
    assert exported[0].privacy == "sanitized"
    assert exported[0].source == f"rolling_candidate_review:{candidate_id}"
