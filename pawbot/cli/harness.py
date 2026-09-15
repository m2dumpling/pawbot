"""CLI commands for the deterministic Agent Harness Benchmark."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from pawbot.harness import builtin_cases, run_benchmark

harness_app = typer.Typer(
    help="Run deterministic Agent Harness Benchmark cases without provider or workspace I/O",
    no_args_is_help=True,
)
console = Console()


@harness_app.command("list")
def harness_list(
    json_output: bool = typer.Option(False, "--json", help="Print the case catalog as JSON"),
) -> None:
    """List the deterministic cases covered by the benchmark."""
    cases = builtin_cases()
    if json_output:
        payload = [
            {
                "case_id": case.id,
                "title": case.title,
                "category": case.category,
                "description": case.description,
                "tags": list(case.tags),
            }
            for case in cases
        ]
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return

    table = Table(title="Agent Harness Benchmark")
    table.add_column("Case", style="cyan", no_wrap=True)
    table.add_column("Category", style="magenta")
    table.add_column("What it proves")
    for case in cases:
        table.add_row(case.id, case.category, case.description)
    console.print(table)


@harness_app.command("run")
def harness_run(
    case: list[str] = typer.Option(
        [],
        "--case",
        help="Run one or more case IDs; repeat the option to select multiple cases",
    ),
    fail_fast: bool = typer.Option(False, "--fail-fast", help="Stop after the first failed case"),
    json_output: bool = typer.Option(False, "--json", help="Print the machine-readable report"),
) -> None:
    """Run benchmark cases against the real AgentRunner."""
    try:
        report = asyncio.run(run_benchmark(case_ids=case or None, fail_fast=fail_fast))
    except ValueError as exc:
        console.print(f"[red]Harness error:[/red] {exc}")
        raise typer.Exit(2) from exc

    payload: dict[str, Any] = report.to_dict()
    if json_output:
        console.print_json(json.dumps(payload, ensure_ascii=False))
    else:
        table = Table(title="Agent Harness Benchmark")
        table.add_column("Case", style="cyan", no_wrap=True)
        table.add_column("Result")
        table.add_column("Trajectory")
        table.add_column("Task")
        table.add_column("Execution")
        table.add_column("Model", justify="right")
        table.add_column("Tools", justify="right")
        table.add_column("Tool errors", justify="right")
        table.add_column("Time", justify="right")
        for result in report.results:
            status = "[green]PASS[/green]" if result.status == "passed" else "[red]FAIL[/red]"
            task_status = (
                "[green]completed[/green]"
                if result.task_status == "passed"
                else "[yellow]not evaluable[/yellow]"
                if result.task_status == "not_evaluable"
                else "[red]failed[/red]"
            )
            table.add_row(
                result.case_id,
                status,
                result.trajectory_status,
                task_status,
                result.execution_status,
                str(result.model_requests),
                str(result.tool_attempts),
                str(result.tool_failures),
                f"{result.elapsed_ms}ms",
            )
        console.print(table)
        console.print(
            f"Summary: {report.passed_count}/{len(report.results)} passed · "
            f"{report.elapsed_ms}ms"
        )
        for result in report.results:
            for failure in result.failures:
                console.print(f"[red]• {result.case_id}:[/red] {failure}")

    if report.failed_count:
        raise typer.Exit(1)


__all__ = ["harness_app"]
