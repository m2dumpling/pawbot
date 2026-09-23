"""CLI commands for the opt-in real-provider incident-triage evaluation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import typer
from rich.console import Console
from rich.table import Table

from pawbot.agent.live_eval import LIVE_EVAL_VERSION, load_live_eval_cases, run_live_eval_sync
from pawbot.config.loader import load_config
from pawbot.providers.base import GenerationSettings
from pawbot.providers.factory import load_provider_snapshot

live_eval_app = typer.Typer(
    help="Run versioned live-model evaluations against isolated local fixtures",
    no_args_is_help=True,
)
console = Console()


@live_eval_app.command("list")
def live_eval_list(
    json_output: bool = typer.Option(False, "--json", help="Print the catalog as JSON"),
    catalog: Path | None = typer.Option(None, "--catalog", exists=True, readable=True),
) -> None:
    """List the public synthetic live-evaluation task catalog."""
    cases = load_live_eval_cases(catalog)
    if json_output:
        console.print_json(json.dumps([case.model_dump(mode="json") for case in cases], ensure_ascii=False))
        return
    table = Table(title=f"Pawbot Incident Triage Live Eval · v{LIVE_EVAL_VERSION}")
    table.add_column("Case", style="cyan", no_wrap=True)
    table.add_column("Category", style="magenta")
    table.add_column("Tools")
    table.add_column("Privacy")
    table.add_column("Task")
    for case in cases:
        table.add_row(case.id, case.category, ", ".join(case.allowed_tools) or "none", case.privacy, case.title)
    console.print(table)


@live_eval_app.command("run")
def live_eval_run(
    case: list[str] = typer.Option([], "--case", help="Select case IDs; repeat to select multiple"),
    trials: int = typer.Option(1, "--trials", min=1, max=20, help="Independent trials per case"),
    seed: int = typer.Option(0, "--seed", help="Deterministic case-order seed"),
    label: str = typer.Option("candidate", "--label", help="Report label, such as baseline or candidate"),
    preset: str | None = typer.Option(None, "--preset", help="Configured model preset to evaluate"),
    catalog: Path | None = typer.Option(None, "--catalog", exists=True, readable=True, help="Versioned EvalCase catalog, including reviewed candidate exports"),
    temperature: float | None = typer.Option(None, "--temperature", min=0.0, max=2.0),
    max_tokens: int | None = typer.Option(None, "--max-tokens", min=64, max=32768),
    max_total_cost_usd: float = typer.Option(
        ...,
        "--max-total-cost-usd",
        min=0.000001,
        help="Required estimated spend cap; live provider calls only run with this explicit budget",
    ),
    input_cost_per_million: float | None = typer.Option(
        None,
        "--input-cost-per-million",
        min=0,
        help="Override configured input token price; required if config has no price",
    ),
    output_cost_per_million: float | None = typer.Option(
        None,
        "--output-cost-per-million",
        min=0,
        help="Override configured output token price; required if config has no price",
    ),
    judge: bool = typer.Option(False, "--judge", help="Run the optional same-provider subjective rubric; adds model calls"),
    fail_fast: bool = typer.Option(False, "--fail-fast"),
    config_path: Path | None = typer.Option(None, "--config", help="Pawbot config JSON path"),
    output: Path | None = typer.Option(None, "--output", help="Report JSON path; defaults to a unique file under ~/.pawbot/eval-reports"),
    trace_root: Path | None = typer.Option(None, "--trace-root", help="Trace directory; defaults beside the report"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace an existing report file"),
) -> None:
    """Run real-model trials. Every trial uses a fresh AgentRunner and temporary workspace."""
    report_path = output or (
        Path.home()
        / ".pawbot"
        / "eval-reports"
        / f"live-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    if report_path.exists() and not overwrite:
        console.print(f"[red]Report already exists:[/red] {report_path}; use --overwrite to replace it")
        raise typer.Exit(2)

    try:
        config = load_config(config_path)
        snapshot = load_provider_snapshot(config_path, preset_name=preset)
        if snapshot.provider.__class__.__name__ == "UnconfiguredProvider":
            console.print("[red]Live eval needs a configured provider credential.[/red]")
            raise typer.Exit(2)
        defaults = config.agents.defaults
        input_rate = input_cost_per_million
        if input_rate is None:
            input_rate = defaults.input_cost_per_million_usd
        output_rate = output_cost_per_million
        if output_rate is None:
            output_rate = defaults.output_cost_per_million_usd
        if input_rate is None or output_rate is None:
            console.print(
                "[red]Set input/output token prices in agent defaults or pass both "
                "--input-cost-per-million and --output-cost-per-million.[/red]"
            )
            raise typer.Exit(2)

        base_generation = snapshot.generation or GenerationSettings()
        generation = GenerationSettings(
            temperature=base_generation.temperature if temperature is None else temperature,
            max_tokens=base_generation.max_tokens if max_tokens is None else max_tokens,
            reasoning_effort=base_generation.reasoning_effort,
        )
        from pawbot.agent.otel import configure_otlp_export

        configure_otlp_export(
            enabled=config.observability.otel_enabled,
            service_name=config.observability.otel_service_name,
            sample_ratio=config.observability.otel_sample_ratio,
        )
        trace_directory = trace_root or report_path.with_suffix("").with_name(report_path.stem + ".traces")
        report = run_live_eval_sync(
            provider_factory=lambda: load_provider_snapshot(config_path, preset_name=preset).provider,
            model=snapshot.model,
            context_window_tokens=snapshot.context_window_tokens,
            generation=generation,
            catalog_path=catalog,
            case_ids=case or None,
            trials=trials,
            seed=seed,
            label=label,
            trace_root=trace_directory,
            input_cost_per_million_usd=input_rate,
            output_cost_per_million_usd=output_rate,
            max_total_cost_usd=max_total_cost_usd,
            fail_fast=fail_fast,
            judge_enabled=judge,
        )
    except typer.Exit:
        raise
    except (ValueError, OSError, json.JSONDecodeError, RuntimeError) as exc:
        console.print(f"[red]Live eval error:[/red] {exc}")
        raise typer.Exit(2) from exc

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report["report_path"] = report_path.resolve(strict=False).as_posix()
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _print_live_report(report)
    console.print(f"Report: {report_path.resolve(strict=False)}")
    if (
        report["summary"]["failed_trials"]
        or report["summary"]["not_evaluable_trials"]
        or report["summary"]["cases_run"] < report["summary"]["cases_requested"]
        or report["summary"]["trials_run"] < report["summary"]["cases_requested"] * trials
    ):
        raise typer.Exit(1)


@live_eval_app.command("compare")
def live_eval_compare(
    baseline: Path = typer.Option(..., "--baseline", exists=True, readable=True),
    candidate: Path = typer.Option(..., "--candidate", exists=True, readable=True),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Compare two report files from the same versioned dataset."""
    try:
        before = _read_report(baseline)
        after = _read_report(candidate)
    except (OSError, json.JSONDecodeError) as exc:
        console.print(f"[red]Could not load report:[/red] {exc}")
        raise typer.Exit(2) from exc
    before_meta = _report_mapping(before.get("metadata"))
    after_meta = _report_mapping(after.get("metadata"))
    if (
        before.get("benchmark") != after.get("benchmark")
        or before_meta.get("dataset_sha256") != after_meta.get("dataset_sha256")
    ):
        console.print("[red]Reports must use the same benchmark and dataset hash.[/red]")
        raise typer.Exit(2)
    before_cases = _group_case_trials(_report_rows(before.get("cases")))
    after_cases = _group_case_trials(_report_rows(after.get("cases")))
    if before_cases.keys() != after_cases.keys():
        console.print("[red]Reports must contain the same case IDs.[/red]")
        raise typer.Exit(2)
    before_summary = _report_mapping(before.get("summary"))
    after_summary = _report_mapping(after.get("summary"))
    before_trials = int(before_summary.get("trials_requested_per_case") or 1)
    after_trials = int(after_summary.get("trials_requested_per_case") or 1)
    per_case: list[dict[str, Any]] = []
    for case_id in sorted(before_cases.keys() & after_cases.keys()):
        baseline_case = _case_trial_summary(before_cases[case_id], before_trials)
        candidate_case = _case_trial_summary(after_cases[case_id], after_trials)
        changed = any(
            baseline_case[key] != candidate_case[key]
            for key in ("pass_at_1", "pass_rate", "pass_at_k", "all_trials_passed")
        )
        per_case.append({
            "case_id": case_id,
            "baseline": baseline_case,
            "candidate": candidate_case,
            "changed": changed,
        })
    comparison: dict[str, Any] = {
        "benchmark": before.get("benchmark"),
        "baseline_label": before.get("label"),
        "candidate_label": after.get("label"),
        "dataset_sha256": before_meta.get("dataset_sha256"),
        "pass_at_1": {
            "baseline": before_summary.get("pass_at_1"),
            "candidate": after_summary.get("pass_at_1"),
            "delta": _difference(after_summary.get("pass_at_1"), before_summary.get("pass_at_1")),
        },
        "estimated_cost_usd": {
            "baseline": before_summary.get("estimated_cost_usd"),
            "candidate": after_summary.get("estimated_cost_usd"),
            "delta": _difference(after_summary.get("estimated_cost_usd"), before_summary.get("estimated_cost_usd")),
        },
        "mean_latency_ms": {
            "baseline": before_summary.get("mean_latency_ms"),
            "candidate": after_summary.get("mean_latency_ms"),
            "delta": _difference(after_summary.get("mean_latency_ms"), before_summary.get("mean_latency_ms")),
        },
        "cases": per_case,
    }
    if json_output:
        console.print_json(json.dumps(comparison, ensure_ascii=False))
        return
    console.print(f"{comparison['baseline_label']} → {comparison['candidate_label']}")
    table = Table(title="Live Eval Comparison")
    table.add_column("Metric")
    table.add_column("Baseline", justify="right")
    table.add_column("Candidate", justify="right")
    table.add_column("Change", justify="right")
    for name, metric_value in (
        ("pass@1", comparison["pass_at_1"]),
        ("Estimated cost USD", comparison["estimated_cost_usd"]),
        ("Mean latency ms", comparison["mean_latency_ms"]),
    ):
        metric = _report_mapping(metric_value)
        table.add_row(name, str(metric["baseline"]), str(metric["candidate"]), str(metric["delta"]))
    console.print(table)
    for item in per_case:
        if item["changed"]:
            before_case = _report_mapping(item["baseline"])
            after_case = _report_mapping(item["candidate"])
            console.print(
                f"[yellow]{item['case_id']}:[/yellow] "
                f"pass@1 {before_case['pass_at_1']} → {after_case['pass_at_1']}; "
                f"pass@k {before_case['pass_at_k']} → {after_case['pass_at_k']}; "
                f"trial pass rate {before_case['pass_rate']} → {after_case['pass_rate']}"
            )


def _difference(candidate: Any, baseline: Any) -> float | None:
    if not isinstance(candidate, (int, float)) or not isinstance(baseline, (int, float)):
        return None
    return round(float(candidate) - float(baseline), 6)


def _read_report(path: Path) -> dict[str, Any]:
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"report must be a JSON object: {path}")
    return cast(dict[str, Any], payload)


def _report_mapping(value: Any) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _report_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items = cast(list[Any], value)
    return [cast(dict[str, Any], item) for item in items if isinstance(item, dict)]


def _group_case_trials(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        case_id = row.get("case_id")
        if isinstance(case_id, str):
            grouped.setdefault(case_id, []).append(row)
    return grouped


def _case_trial_summary(rows: list[dict[str, Any]], requested_trials: int) -> dict[str, Any]:
    statuses = [str(row.get("status") or "not_evaluable") for row in rows]
    evaluable = [status for status in statuses if status != "not_evaluable"]
    passed = sum(status == "passed" for status in evaluable)
    return {
        "trial_statuses": statuses,
        "passed_trials": passed,
        "failed_trials": sum(status == "failed" for status in evaluable),
        "not_evaluable_trials": len(statuses) - len(evaluable),
        "pass_at_1": statuses[0] if statuses else "not_run",
        "pass_rate": passed / len(evaluable) if evaluable else None,
        "pass_at_k": any(status == "passed" for status in evaluable) if evaluable else None,
        "all_trials_passed": (
            len(statuses) == requested_trials
            and all(status == "passed" for status in statuses)
        ),
    }


def _print_live_report(report: dict[str, Any]) -> None:
    table = Table(title=f"Live Eval · {report['label']} · {report['metadata']['model']}")
    table.add_column("Case", style="cyan", no_wrap=True)
    table.add_column("Trial", justify="right")
    table.add_column("Outcome")
    table.add_column("Trajectory")
    table.add_column("Safety")
    table.add_column("Tools", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Cost USD", justify="right")
    table.add_column("Latency", justify="right")
    for item in report["cases"]:
        table.add_row(
            item["case_id"],
            str(item["trial"]),
            str(item["outcome_status"]),
            str(item["trajectory_status"]),
            str(item["safety_status"]),
            str(item["tool_calls"] and len(item["tool_calls"]) or 0),
            f"{item['input_tokens']} / {item['output_tokens']}",
            str(item["estimated_cost_usd"]),
            f"{item['latency_ms']}ms",
        )
    console.print(table)
    summary = report["summary"]
    console.print(
        "Summary: "
        f"pass@1={summary['pass_at_1']} · pass@k={summary['pass_at_k']} · "
        f"outcome={summary['outcome_pass_rate']} · trajectory={summary['trajectory_pass_rate']} · "
        f"safety={summary['safety_pass_rate']} · estimated cost=${summary['estimated_cost_usd']}"
    )
    for item in report["cases"]:
        for failure in item["failures"]:
            console.print(f"[red]• {item['case_id']} trial {item['trial']}:[/red] {failure}")


__all__ = ["live_eval_app"]
