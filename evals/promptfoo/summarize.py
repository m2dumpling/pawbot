"""Read Promptfoo exports; distinguish quality failures from execution errors."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


def _judge_signals(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Weight-zero namedScores are weighted zeros, not the Judge's raw score."""
    return [{
        "available": not bool(part.get("metadata", {}).get("graderError")),
        "score": None if part.get("metadata", {}).get("graderError") else part.get("score"),
        "reason": part.get("reason"),
    } for part in row.get("gradingResult", {}).get("componentResults", [])
        if part.get("assertion", {}).get("type") == "llm-rubric"]


def summarize(document: dict[str, Any]) -> dict[str, Any]:
    rows = document["results"]["results"]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    repeat = int(document.get("runtimeOptions", {}).get("repeat")
                 or document["config"].get("evaluateOptions", {}).get("repeat", 1))
    for row in rows:
        grouped[row["vars"]["case_id"]].append(row)

    def is_error(row: dict[str, Any]) -> bool:
        m = row.get("response", {}).get("metadata", {})
        return bool(row.get("response", {}).get("error")) or m.get("execution_status") == "error" or row.get("failureReason") == 2

    def is_pass(row: dict[str, Any]) -> bool:
        return row.get("success") is True and not is_error(row)

    valid = [row for row in rows if not is_error(row)]
    complete_groups = [trials for trials in grouped.values() if len(trials) == repeat and not any(is_error(row) for row in trials)]
    metadata = [row.get("response", {}).get("metadata", {}) for row in rows]
    cases = [{
        "id": case_id,
        "passed": sum(is_pass(row) for row in trials),
        "failed": sum(not is_pass(row) and not is_error(row) for row in trials),
        "errors": sum(is_error(row) for row in trials),
        "trials": len(trials),
        "failure_reasons": [row.get("error") or row.get("gradingResult", {}).get("reason") for row in trials if not is_pass(row)],
        "transcripts": [row.get("response", {}).get("metadata", {}).get("transcript") for row in trials],
        "judge": [signal for row in trials for signal in _judge_signals(row)],
    } for case_id, trials in sorted(grouped.items())]
    judge_signals = [signal for row in rows for signal in _judge_signals(row)]
    judge_scores = [signal["score"] for signal in judge_signals
                    if signal["available"] and isinstance(signal["score"], (int, float))]
    provider_tokens = sum(row.get("tokenUsage", {}).get("total", 0) for row in rows)
    judge_tokens = sum(row.get("tokenUsage", {}).get("assertions", {}).get("total", 0)
                       or row.get("gradingResult", {}).get("tokensUsed", {}).get("total", 0)
                       for row in rows)
    known_costs = [m.get("cost_estimated_usd") for m in metadata]
    fingerprints = {
        key: sorted({json.dumps(m[key], sort_keys=True) for m in metadata if key in m})
        for key in ("suite_sha256", "agent_sha256", "model", "provider", "generation", "limits",
                    "context_window_tokens", "python_version", "sdk_versions", "dependencies_sha256",
                    "cost_limit_usd")
    }
    # Grader weights and prompt templates belong to the experiment, even when
    # assertions.cjs and cases.yaml themselves have not changed.
    fingerprints["evaluation_config"] = {
        key: document["config"].get(key) for key in ("providers", "prompts", "defaultTest")
    }
    fingerprints["cases"] = sorted({
        f"{m.get('case_id')}:{m.get('case_sha256')}" for m in metadata
    })
    return {
        "eval_id": document.get("evalId"), "repeat": repeat,
        "total": len(rows), "evaluable": len(valid), "errors": len(rows) - len(valid),
        "passed": sum(is_pass(row) for row in valid),
        "pass_rate": sum(is_pass(row) for row in valid) / len(valid) if valid else None,
        "complete_case_groups": len(complete_groups),
        "incomplete_case_groups": len(grouped) - len(complete_groups),
        # Empirical case fractions from complete groups, not model probabilities.
        "empirical_pass_at_k": mean(any(is_pass(row) for row in trials) for trials in complete_groups) if complete_groups else None,
        "empirical_pass_power_k": mean(all(is_pass(row) for row in trials) for trials in complete_groups) if complete_groups else None,
        "mean_latency_ms": mean(row["latencyMs"] for row in valid) if valid else None,
        "provider_tokens": provider_tokens,
        "judge_tokens": judge_tokens,
        "total_tokens": provider_tokens + judge_tokens,
        "cost_estimated_usd": sum(known_costs) if known_costs and all(isinstance(v, (int, float)) for v in known_costs) else None,
        "cost_scope": "agent_provider_only",
        "simulation": any(m.get("simulation", False) for m in metadata),
        "judge": {
            "evaluated": len(judge_scores),
            "unavailable": sum(not signal["available"] for signal in judge_signals),
            "mean_score": mean(judge_scores) if judge_scores else None,
            "flagged": sum(score < 0.5 for score in judge_scores),
            "advisory": True,
        },
        "fingerprints": fingerprints, "cases": cases,
    }


def compare(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    for key in ("suite_sha256", "cases", "model", "provider", "generation", "limits",
                "context_window_tokens", "python_version", "sdk_versions", "dependencies_sha256",
                "cost_limit_usd", "evaluation_config"):
        if baseline["fingerprints"][key] != candidate["fingerprints"][key]:
            raise ValueError(f"Reports are not comparable: {key} differs")
    if baseline["repeat"] != candidate["repeat"] or baseline["simulation"] != candidate["simulation"]:
        raise ValueError("Reports are not comparable: trial count or simulation differs")
    if baseline["errors"] or candidate["errors"] or baseline["incomplete_case_groups"] or candidate["incomplete_case_groups"]:
        raise ValueError("Resolve execution errors/missing trials before claiming a quality delta")
    changed = baseline["fingerprints"]["agent_sha256"] != candidate["fingerprints"]["agent_sha256"]
    return {
        "agent_changed": changed,
        "interpretation": "Agent source changed; inspect the change and transcripts." if changed else "Same Agent source: repeated measurement, not evidence of an Agent improvement.",
        "pass_rate_delta": candidate["pass_rate"] - baseline["pass_rate"],
        "baseline": baseline, "candidate": candidate,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path, nargs="?")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    first = summarize(json.loads(args.baseline.read_text(encoding="utf-8")))
    try:
        result = compare(first, summarize(json.loads(args.candidate.read_text(encoding="utf-8")))) if args.candidate else first
    except ValueError as exc:
        parser.error(str(exc))
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
