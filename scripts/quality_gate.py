#!/usr/bin/env python3
"""Run the repeatable quality gate used after an AI-assisted code change.

The gate intentionally combines static checks with the Agent Harness
Benchmark.  The benchmark is the behavioral check: a patch is not accepted
merely because it formats and type-checks if it breaks recovery, cancellation,
provider-error handling, or turn budgets.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    passed: bool
    elapsed_ms: int
    command: tuple[str, ...] = ()
    detail: str = ""


def _external_command(tool: str, *args: str) -> list[str]:
    """Resolve a project tool through uv when available, otherwise PATH."""
    uv = shutil.which("uv")
    if uv is not None:
        return [uv, "run", "--no-sync", tool, *args]
    executable = shutil.which(tool)
    if executable is not None:
        return [executable, *args]
    if tool in {"pytest", "ruff"}:
        return [sys.executable, "-m", tool, *args]
    return [tool, *args]


def _run_command(name: str, command: list[str]) -> CheckResult:
    started_at = time.perf_counter()
    environment = os.environ.copy()
    environment["PYTHONHASHSEED"] = "0"
    try:
        completed = subprocess.run(
            command,
            cwd=REPOSITORY_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        output = "\n".join(
            value for value in (completed.stdout.strip(), completed.stderr.strip()) if value
        )
        passed = completed.returncode == 0
        detail = output[-4_000:] if output else ""
    except OSError as exc:
        passed = False
        detail = f"could not start command: {exc}"
        command = list(command)
    elapsed_ms = max(0, round((time.perf_counter() - started_at) * 1000))
    marker = "PASS" if passed else "FAIL"
    print(f"[{marker}] {name} ({elapsed_ms}ms)")
    if detail:
        print(detail)
    return CheckResult(
        name=name,
        passed=passed,
        elapsed_ms=elapsed_ms,
        command=tuple(command),
        detail=detail,
    )


def _run_harness() -> CheckResult:
    started_at = time.perf_counter()
    try:
        from pawbot.harness import run_benchmark_sync

        report = run_benchmark_sync()
        payload = report.to_dict()
        summary = payload["summary"]
        detail = json.dumps(summary, ensure_ascii=False)
        passed = report.passed
    except Exception as exc:
        passed = False
        detail = f"harness crashed: {type(exc).__name__}: {exc}"
    elapsed_ms = max(0, round((time.perf_counter() - started_at) * 1000))
    marker = "PASS" if passed else "FAIL"
    print(f"[{marker}] Agent Harness Benchmark ({elapsed_ms}ms)")
    print(detail)
    return CheckResult(
        name="Agent Harness Benchmark",
        passed=passed,
        elapsed_ms=elapsed_ms,
        detail=detail,
    )


def _build_checks(*, full: bool) -> list[CheckResult]:
    checks = [
        _run_command("Working-tree whitespace", ["git", "diff", "--check"]),
        _run_command("Staged whitespace", ["git", "diff", "--cached", "--check"]),
        _run_command("Ruff", _external_command("ruff", "check", "pawbot", "tests", "scripts")),
        _run_command("basedpyright", _external_command("basedpyright")),
        _run_harness(),
        _run_command(
            "Agent contract tests",
            _external_command(
                "pytest",
                "-q",
                "tests/harness",
                "tests/agent/test_evaluation.py",
                "tests/agent/test_tool_approval.py",
                "tests/agent/test_provenance.py",
                "tests/agent/test_observability.py",
            ),
        ),
        _run_command(
            "Record & Replay fixture tests",
            _external_command(
                "pytest",
                "-q",
                "tests/test_blackbox.py::test_public_sanitized_fixture_is_loadable",
                "tests/test_blackbox.py::test_record_then_replay_is_deterministic",
            ),
        ),
    ]
    if full:
        checks.append(
            _run_command("Full Python tests", _external_command("pytest", "-q"))
        )
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--full",
        action="store_true",
        help="Also run the complete Python test suite; CI already runs this separately",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the machine-readable gate report to this path",
    )
    args = parser.parse_args()

    print("pawbot Agent quality gate")
    print(f"Repository: {REPOSITORY_ROOT}")
    checks = _build_checks(full=args.full)
    passed = all(check.passed for check in checks)
    report: dict[str, Any] = {
        "schema_version": 1,
        "gate": "pawbot-agent-quality-gate",
        "status": "passed" if passed else "failed",
        "checks": [asdict(check) for check in checks],
    }
    if args.output is not None:
        output = args.output.expanduser()
        if not output.is_absolute():
            output = REPOSITORY_ROOT / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Report: {output}")
    print(f"\nQuality gate: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
