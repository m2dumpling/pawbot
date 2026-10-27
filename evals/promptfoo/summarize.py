"""Read Promptfoo exports; distinguish quality failures from execution errors."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any


def _judge_signals(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Weight-zero namedScores are weighted zeros, not the Judge's raw score."""
    return [{
        "available": not bool(part.get("metadata", {}).get("graderError")),
        "score": None if part.get("metadata", {}).get("graderError") else part.get("score"),
        "reason": part.get("reason"),
    } for part in row.get("gradingResult", {}).get("componentResults", [])
        if part.get("assertion", {}).get("type") == "llm-rubric"]


def _workspace_metrics(rows: list[dict[str, Any]], repeat: int) -> dict[str, Any] | None:
    if not any(
        row.get("response", {}).get("metadata", {}).get("evaluation_scope")
        or str(row.get("vars", {}).get("case_id", "")).startswith("workspace-")
        for row in rows
    ):
        return None
    splits: dict[str, dict[str, int]] = {}
    trials: list[dict[str, Any]] = []
    tools: dict[str, dict[str, Any]] = {}
    requests: list[dict[str, Any]] = []
    contexts: list[dict[str, Any]] = []
    source_counts: dict[str, int] = defaultdict(int)
    physical_count, physical_unavailable = 0, 0
    reported_input, reported_output, estimated_input, estimated_output, missing_usage = 0, 0, 0, 0, 0
    planned: dict[str, int] = {"smoke": 1} if any(
        row.get("vars", {}).get("split") == "smoke" for row in rows
    ) else {"dev": 4, "holdout": 2}
    seen_tools: set[tuple[str, str, str, str, str]] = set()

    for row in rows:
        m = row.get("response", {}).get("metadata", {})
        split = str(m.get("split") or row.get("vars", {}).get("split") or "unknown")
        for key, count in m.get("planned_splits", {}).items():
            planned[str(key)] = int(count)
        group = splits.setdefault(split, {"total": 0, "passed": 0, "failed": 0, "errors": 0, "not_evaluable": 0})
        group["total"] += 1
        if m.get("diagnostic_class") == "oracle_unavailable":
            status = "not_evaluable"
        elif row.get("response", {}).get("error") or m.get("execution_status") == "error" or row.get("failureReason") == 2:
            status = "errors"
        else:
            status = "passed" if row.get("success") is True else "failed"
        group[status] += 1
        trial_id = str(m.get("trial_id") or "")
        trials.append({
            "case_id": m.get("case_id") or row.get("vars", {}).get("case_id"),
            "split": split, "trial_id": trial_id or None, "status": status,
            "stop_reason": m.get("stop_reason"), "diagnostic_class": m.get("diagnostic_class"),
            "latency_ms": m.get("latency_ms"), "validation_ms": m.get("validation_ms"),
            "token_usage": row.get("tokenUsage"), "usage_source": m.get("usage_source"),
            "transcript": m.get("transcript"), "trace": m.get("trace"),
            "failure_reason": row.get("response", {}).get("error") or row.get("gradingResult", {}).get("reason"),
        })
        model_rows = m.get("model_requests") or []
        requests.extend(model_rows)
        for request in model_rows:
            usage = request.get("usage")
            if not isinstance(usage, dict):
                missing_usage += 1
                continue
            source = str(usage.get("source") or "unknown")
            source_counts[source] += 1
            if source == "reported":
                reported_input += int(usage.get("input_tokens") or 0)
                reported_output += int(usage.get("output_tokens") or 0)
            elif source == "estimated":
                estimated_input += int(usage.get("input_tokens") or 0)
                estimated_output += int(usage.get("output_tokens") or 0)
            else:
                missing_usage += 1
        physical = m.get("physical_model_requests")
        if isinstance(physical, list):
            physical_count += len(physical)
        elif model_rows:
            physical_unavailable += 1
        contexts.extend(m.get("contexts") or [])
        for index, event in enumerate(m.get("tool_events") or []):
            name = str(event.get("name") or "unknown")
            call_id = str(event.get("id") or f"missing-{index}")
            key = (trial_id, str(event.get("iteration", "")), call_id, name, str(event.get("index", "")))
            if key in seen_tools:
                continue
            seen_tools.add(key)
            item = tools.setdefault(name, {"calls": 0, "errors": 0, "durations": []})
            item["calls"] += 1
            if event.get("status") != "ok":
                item["errors"] += 1
            duration = event.get("duration_ms")
            if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration >= 0:
                item["durations"].append(duration)
    for name, item in tools.items():
        durations = sorted(item.pop("durations"))
        item.update({
            "timed_calls": len(durations), "mean_ms": mean(durations) if durations else None,
            "median_ms": median(durations) if durations else None,
            "p95_ms": durations[math.ceil(len(durations) * 0.95) - 1] if durations else None,
            "max_ms": max(durations) if durations else None,
        })
    for split, group in splits.items():
        group["planned_cases"] = planned.get(split, 0)
        group["planned_trials"] = planned.get(split, 0) * repeat
    for split, count in planned.items():
        splits.setdefault(split, {
            "total": 0, "passed": 0, "failed": 0, "errors": 0, "not_evaluable": 0,
            "planned_cases": count, "planned_trials": count * repeat,
        })

    def average(field: str, items: list[dict[str, Any]]) -> float | None:
        values = [row[field] for row in items
                  if isinstance(row.get(field), (int, float)) and not isinstance(row.get(field), bool)]
        return mean(values) if values else None

    return {
        "planned_trials": sum(planned.values()) * repeat if planned else None,
        "splits": splits, "trials": trials, "tools": tools,
        "logical_model_requests": len(requests),
        "physical_model_requests_observed": physical_count,
        "physical_request_counts_unavailable_trials": physical_unavailable,
        "mean_model_request_ms": average("duration_ms", requests),
        "mean_ttft_ms": average("ttft_ms", requests),
        "usage": {
            "reported_input_tokens": reported_input, "reported_output_tokens": reported_output,
            "estimated_input_tokens": estimated_input, "estimated_output_tokens": estimated_output,
            "requests_with_missing_or_mixed_usage": missing_usage, "source_counts": dict(source_counts),
        },
        "context_estimates": {
            "requests": len(contexts),
            "mean_prompt_tokens": average("estimated_prompt_tokens", contexts),
            "mean_tool_schema_tokens": average("estimated_tool_schema_tokens", contexts),
            "mean_tool_result_tokens": average("estimated_tool_result_tokens", contexts),
            "sources": sorted({str(row.get("token_estimation_source")) for row in contexts
                               if row.get("token_estimation_source")}),
            "interpretation": "Estimates of logical model input; not exact billed or additive token counts.",
        },
        "timing_interpretation": "Tool durations may overlap and are not wall-clock fractions; p95 is a small-sample observation.",
    }


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
                    "context_window_tokens", "python_version", "platform", "sdk_versions",
                    "dependencies_sha256", "tool_concurrency", "environment", "cost_limit_usd")
    }
    # Grader weights and prompt templates belong to the experiment, even when
    # assertions.cjs and cases.yaml themselves have not changed.
    fingerprints["evaluation_config"] = {
        key: document["config"].get(key) for key in ("providers", "prompts", "defaultTest")
    }
    fingerprints["cases"] = sorted({
        f"{m.get('case_id')}:{m.get('case_sha256')}" for m in metadata
    })
    result = {
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
    workspace = _workspace_metrics(rows, repeat)
    if workspace is not None:
        result["workspace"] = workspace
    return result


def compare(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    for key in ("suite_sha256", "cases", "model", "provider", "generation", "limits",
                "context_window_tokens", "python_version", "platform", "sdk_versions",
                "dependencies_sha256", "tool_concurrency", "environment", "cost_limit_usd", "evaluation_config"):
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
