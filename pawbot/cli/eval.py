"""CLI for the deterministic task-level evaluation set."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from pawbot.evals import eval_cases, run_eval_sync

eval_app = typer.Typer(
    help="Run the provider-free Pawbot task evaluation set",
    no_args_is_help=True,
)
console = Console()


@eval_app.command("list")
def eval_list(
    json_output: bool = typer.Option(False, "--json", help="Print the case catalog as JSON"),
) -> None:
    """List the stable task-evaluation cases."""
    cases = eval_cases()
    if json_output:
        console.print_json(json.dumps([case.to_dict() for case in cases], ensure_ascii=False))
        return
    table = Table(title="Pawbot Task Eval Set")
    table.add_column("Case", style="cyan", no_wrap=True)
    table.add_column("Category", style="magenta")
    table.add_column("What it checks")
    for case in cases:
        table.add_row(case.id, case.category, case.description)
    console.print(table)


@eval_app.command("run")
def eval_run(
    case: list[str] = typer.Option(
        [],
        "--case",
        help="Run one or more case IDs; repeat the option to select multiple cases",
    ),
    fail_fast: bool = typer.Option(False, "--fail-fast", help="Stop after the first failed case"),
    json_output: bool = typer.Option(False, "--json", help="Print the machine-readable report"),
) -> None:
    """Run task, trajectory, and execution checks without provider or workspace I/O."""
    try:
        report = run_eval_sync(case_ids=case or None, fail_fast=fail_fast)
    except ValueError as exc:
        console.print(f"[red]Eval error:[/red] {exc}")
        raise typer.Exit(2) from exc

    payload = report.to_dict()
    if json_output:
        console.print_json(json.dumps(payload, ensure_ascii=False))
    else:
        table = Table(title="Pawbot Task Eval Set")
        table.add_column("Case", style="cyan", no_wrap=True)
        table.add_column("Task")
        table.add_column("Trajectory")
        table.add_column("Execution")
        table.add_column("Tools", justify="right")
        table.add_column("Time", justify="right")
        for result in report.harness.results:
            table.add_row(
                result.case_id,
                result.task_status,
                result.trajectory_status,
                result.execution_status,
                str(result.tool_attempts),
                f"{result.elapsed_ms}ms",
            )
        console.print(table)
        summary = payload["summary"]
        console.print(
            "Summary: "
            f"{summary['task_passed']}/{summary['task_evaluable']} evaluable tasks passed · "
            f"{summary['trajectory_passed']}/{summary['total']} trajectories passed · "
            f"{summary['elapsed_ms']}ms"
        )
        for result in report.harness.results:
            for failure in result.failures:
                console.print(f"[red]• {result.case_id}:[/red] {failure}")

    if not report.passed:
        raise typer.Exit(1)


__all__ = ["eval_app"]
