#!/usr/bin/env python
"""Calibrate token estimation against provider-reported usage.

Compares ``pawbot.agent.token_estimation.count_prompt_tokens`` with the
provider-reported ``usage.prompt_tokens`` recorded in a blackbox directory
(``turns.jsonl``), and prints per-turn and aggregate error.

Usage:
    python scripts/calibrate_tokens.py --dir <blackbox-dir>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pawbot.agent.token_estimation import count_prompt_tokens


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", required=True, help="blackbox directory with turns.jsonl")
    args = parser.parse_args()

    path = Path(args.dir) / "turns.jsonl"
    if not path.exists():
        print(f"no turns.jsonl found at {path}")
        return 2

    total_error = 0.0
    total_actual = 0
    compared = 0
    print(f"{'turn_id':<28} {'est':>8} {'actual':>8} {'err%':>7}")
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("kind") != "turn":
            continue
        usage = record.get("usage") or {}
        actual = usage.get("input_tokens") or usage.get("prompt_tokens")
        if not actual:
            continue
        # Calibration is only meaningful on a one-request turn: `usage` is the
        # *cumulative* prompt across all requests, while our estimator is a
        # single-request snapshot. Multi-request turns are reported but not
        # counted toward the MAPE acceptance bar.
        request_count = usage.get("request_count", 1)
        messages = (
            record.get("final_messages") or record.get("initial_messages") or []
        )
        estimated = count_prompt_tokens(
            messages,
            tools=record.get("tools"),
            model=record.get("model"),
        )
        error_pct = (estimated - actual) / actual * 100
        counted = request_count == 1
        if counted:
            total_error += abs(error_pct)
            total_actual += actual
            compared += 1
        marker = "" if counted else " (skipped: multi-request turn)"
        print(f"{record.get('turn_id','?'):<28} {estimated:>8} {actual:>8} {error_pct:>7.1f}{marker}")

    if compared:
        mape = total_error / compared
        print(f"\n{compared} turns compared | MAPE {mape:.1f}% | total actual tokens {total_actual}")
        print("MAPE < 10% is the acceptance bar for the project (ADR-002).")
        return 0 if mape < 10 else 1
    print("no turns with recorded usage; record a session with --record first")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
