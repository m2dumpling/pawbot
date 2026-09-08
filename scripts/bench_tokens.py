#!/usr/bin/env python
"""Quantified token-estimation benchmark for the resume.

Compares three estimators against the provider-reported ``usage.input_tokens``
recorded in a blackbox directory, on single-request turns (where the estimate
is one request and the usage is that request's input):

- ``byte``   : a 4-bytes/token character heuristic (upstream's non-tiktoken fallback)
- ``cl100k`` : tiktoken ``cl100k_base`` over the serialized messages, no structural
               overhead and no tool-schema accounting (upstream's default path)
- ``full``   : model-aware tiktoken + structural overhead + tool schemas
               (``pawbot.agent.token_estimation``, ADR-002)

Outputs the per-estimator mean absolute percentage error (MAPE), which is the
number to cite on a resume: "estimation error reduced from X% to Y%".
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pawbot.agent.token_estimation import count_prompt_tokens


def _byte_estimate(messages: list[dict]) -> int:
    total = 0
    for msg in messages:
        total += (len(json.dumps(msg, ensure_ascii=False, default=repr).encode("utf-8")) + 3) // 4
    return total


def _cl100k_estimate(messages: list[dict]) -> int:
    import tiktoken

    enc = tiktoken.get_encoding("cl100k_base")
    total = 0
    for msg in messages:
        total += len(enc.encode(json.dumps(msg, ensure_ascii=False, default=repr)))
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", required=True, help="blackbox directory with turns.jsonl")
    args = parser.parse_args()

    path = Path(args.dir) / "turns.jsonl"
    if not path.exists():
        print(f"no turns.jsonl at {path}")
        return 2

    sums = {"byte": 0.0, "cl100k": 0.0, "full": 0.0}
    counted = 0
    total_actual = 0
    print(f"{'turn':<26}{'actual':>8}{'byte%':>8}{'cl100k%':>9}{'full%':>8}")
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("kind") != "turn":
            continue
        usage = rec.get("usage") or {}
        if usage.get("request_count", 1) != 1:
            continue
        actual = usage.get("input_tokens")
        if not actual:
            continue
        messages = rec.get("final_messages") or rec.get("initial_messages") or []
        tools = rec.get("tools")
        estimates = {
            "byte": _byte_estimate(messages),
            "cl100k": _cl100k_estimate(messages),
            "full": count_prompt_tokens(messages, tools=tools, model=rec.get("model")),
        }
        errs = {k: (v - actual) / actual * 100 for k, v in estimates.items()}
        for k in sums:
            sums[k] += abs(errs[k])
        counted += 1
        total_actual += actual
        print(
            f"{rec.get('turn_id','?')[:26]:<26}{actual:>8}{errs['byte']:>8.1f}{errs['cl100k']:>9.1f}{errs['full']:>8.1f}"
        )

    if not counted:
        print("no single-request turns with usage; record with --record first")
        return 2

    print("\n=== MAPE (lower is better) ===")
    for k in ("byte", "cl100k", "full"):
        print(f"{k:>6}: {sums[k] / counted:5.1f}%")
    print(f"\n{counted} single-request turns | total actual input tokens {total_actual}")
    print("Resume line: 'token estimation error reduced from "
          f"{sums['cl100k'] / counted:.0f}% (cl100k) to {sums['full'] / counted:.1f}% (model-aware+tools)'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
