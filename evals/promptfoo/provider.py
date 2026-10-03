"""Execute one Pawbot AgentRunner trial. Promptfoo owns cases and grading."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import inspect
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = Path(__file__).resolve().parent
for directory in (REPO_ROOT, EVAL_ROOT):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from fixtures import FixtureTraceHook, build_tools  # noqa: E402

from pawbot.agent.budget import TurnBudget  # noqa: E402
from pawbot.agent.observability import TraceRun, TraceWriter, redact_text  # noqa: E402
from pawbot.agent.runner import AgentRunner, AgentRunSpec  # noqa: E402
from pawbot.config.loader import load_config, resolve_config_env_vars  # noqa: E402
from pawbot.harness import ScriptedProvider  # noqa: E402
from pawbot.providers.base import GenerationSettings, LLMResponse, ToolCallRequest  # noqa: E402
from pawbot.providers.factory import build_provider_snapshot  # noqa: E402
from pawbot.utils.llm_runtime import LLMRuntime  # noqa: E402


def _sha256(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _case_fingerprint(variables: dict[str, Any]) -> str:
    # Promptfoo injects these positional identifiers into provider vars, but
    # omits them from exported cases. They must not change the task fingerprint.
    runtime_keys = {"__evalId", "__evalStepId", "__repeatIndex"}
    payload = {key: value for key, value in variables.items() if key not in runtime_keys}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _number(name: str, fallback: float | None = None) -> float | None:
    raw = os.environ.get(name)
    value = float(raw) if raw is not None else fallback
    if value is not None and (not math.isfinite(value) or value < 0):
        raise ValueError(f"{name} must be a finite nonnegative number")
    return value


def _snapshot(*, judge: bool = False) -> tuple[Any, float | None, float | None]:
    path_text = os.environ.get("PAWBOT_EVAL_CONFIG")
    path = Path(path_text).expanduser() if path_text else None
    if path is not None and not path.is_file():
        raise ValueError("PAWBOT_EVAL_CONFIG does not identify an existing file")
    config = resolve_config_env_vars(load_config(path), config_path=path)
    # Evaluate one model instead of silently mixing a configured fallback chain.
    config.agents.defaults.fallback_models = []
    preset = os.environ.get("PAWBOT_EVAL_JUDGE_PRESET") if judge else None
    snapshot = build_provider_snapshot(
        config, preset_name=preset or os.environ.get("PAWBOT_EVAL_PRESET") or None
    )
    if snapshot.provider.__class__.__name__ == "UnconfiguredProvider":
        raise ValueError("Configure a usable Pawbot provider before running live evals")
    defaults = config.agents.defaults
    input_rate = _number("PAWBOT_EVAL_INPUT_COST_PER_MILLION", defaults.input_cost_per_million_usd)
    output_rate = _number("PAWBOT_EVAL_OUTPUT_COST_PER_MILLION", defaults.output_cost_per_million_usd)
    if input_rate is None or output_rate is None:
        input_rate = output_rate = None
    return snapshot, input_rate, output_rate


def _git_revision() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True,
            text=True, timeout=5, check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else "unavailable"
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"


async def _close_provider(provider: Any) -> None:
    # Each worker call owns its provider. Close SDK transports on the same event
    # loop before asyncio.run disposes it (important for repeated Windows trials).
    client = getattr(provider, "_client", None)
    close = getattr(client, "close", None) or getattr(client, "aclose", None)
    if callable(close):
        pending = close()
        if inspect.isawaitable(pending):
            await asyncio.wait_for(pending, timeout=5)


async def _execute(
    prompt: str, config: dict[str, Any], variables: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    case_id = variables["case_id"]
    mode = config.get("mode", "live")
    if mode not in {"live", "smoke"}:
        raise ValueError("provider mode must be live or smoke")
    simulation = mode == "smoke"
    if simulation:
        script = variables.get("script")
        if not isinstance(script, list) or not script:
            raise ValueError("smoke tests require an explicit response script")
        provider = ScriptedProvider([
            LLMResponse(
                content=row.get("content"),
                tool_calls=[ToolCallRequest(**call) for call in row.get("tool_calls", [])],
                finish_reason="tool_calls" if row.get("tool_calls") else "stop",
            ) for row in script
        ])
        model, window = "harness-model", 128_000
        input_rate = output_rate = None
        generation = GenerationSettings(temperature=0, max_tokens=1024)
    else:
        snapshot, input_rate, output_rate = _snapshot()
        provider, model, window = snapshot.provider, snapshot.model, snapshot.context_window_tokens
        generation = GenerationSettings(
            temperature=float(config.get("temperature", 0.1)),
            max_tokens=int(config.get("max_tokens", 1024)),
            reasoning_effort=config.get("reasoning_effort", "none"),
        )
    cost_limit = _number("PAWBOT_EVAL_MAX_COST_USD")
    if cost_limit is not None and input_rate is None and not simulation:
        raise ValueError("A USD budget requires both token prices; otherwise use token/time limits")
    runtime = LLMRuntime.capture(provider, model, context_window_tokens=window).with_generation_overrides(
        temperature=generation.temperature, max_tokens=generation.max_tokens,
        reasoning_effort=generation.reasoning_effort,
    )
    trial_id = uuid.uuid4().hex
    trace_root = Path(os.environ.get("PAWBOT_EVAL_TRACE_ROOT", str(Path.home() / ".pawbot/promptfoo-traces"))).expanduser()
    trial_root = trace_root / trial_id
    trial_root.mkdir(parents=True, exist_ok=False)
    calls: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    tools = build_tools(variables.get("fixture", []), observations)
    messages = [
        {"role": "system", "content": (EVAL_ROOT / "system.txt").read_text(encoding="utf-8")},
        {"role": "user", "content": prompt},
    ]
    trace = TraceRun(
        writer=TraceWriter(trial_root / "events.jsonl"), trace_id=f"promptfoo:{trial_id}",
        session_key=f"promptfoo:{trial_id}", turn_id=trial_id, channel="promptfoo",
        chat_id=case_id, model=model, provider=getattr(provider, "provider_name", "unknown"),
    )
    trace.emit("turn.accepted", status="accepted", eval_case_id=case_id)
    hook = FixtureTraceHook(
        trace, calls=calls, initial_message_count=len(messages), tools_count=len(tools.tool_names),
        tool_definitions=tools.get_definitions(), capture_prompts=True, capture_tool_results=True,
    )
    limits = {
        "max_iterations": int(config.get("max_iterations", 8)),
        "max_tool_calls": int(config.get("max_tool_calls", 6)),
        "max_wall_seconds": float(config.get("max_wall_seconds", 90)),
        "max_input_tokens": int(config.get("max_input_tokens", 16_000)),
        "max_output_tokens": int(config.get("max_output_tokens", 4_096)),
    }
    budget = TurnBudget(
        **limits, max_cost_usd=cost_limit, input_cost_per_million_usd=input_rate,
        output_cost_per_million_usd=output_rate,
    )
    metadata = {
        "case_id": case_id, "trial_id": trial_id, "simulation": simulation,
        "evaluation_id": context.get("evaluationId"), "repeat_index": context.get("repeatIndex"),
        "model": model, "provider": getattr(provider, "provider_name", "unknown"),
        "context_window_tokens": window,
        "python_version": platform.python_version(),
        "sdk_versions": {
            package: importlib.metadata.version(package)
            for package in ("openai", "httpx", "pydantic")
        },
        "git_commit": _git_revision(),
        "agent_sha256": _sha256(sorted((REPO_ROOT / "pawbot/agent").rglob("*.py"))
                                + sorted((REPO_ROOT / "pawbot/providers").rglob("*.py"))
                                + [REPO_ROOT / "pawbot/utils/llm_runtime.py", REPO_ROOT / "pawbot/harness.py"]),
        "dependencies_sha256": _sha256([REPO_ROOT / "uv.lock", REPO_ROOT / "pyproject.toml"]),
        "suite_sha256": _sha256([EVAL_ROOT / name for name in (
            "cases.yaml", "system.txt", "assertions.cjs", "fixtures.py", "provider.py",
        )]),
        "case_sha256": _case_fingerprint(variables),
        "generation": {"temperature": generation.temperature, "max_tokens": generation.max_tokens, "reasoning_effort": generation.reasoning_effort},
        "limits": limits, "cost_limit_usd": cost_limit,
        "cost_estimated_usd": None, "cost_source": "not_configured",
        "trace": (trial_root / "events.jsonl").as_posix(),
        "transcript": (trial_root / "transcript.json").as_posix(),
    }
    started = time.perf_counter()
    result = None
    error = None
    try:
        with tempfile.TemporaryDirectory(prefix="pawbot-promptfoo-") as directory:
            result = await asyncio.wait_for(AgentRunner().run(AgentRunSpec(
                initial_messages=messages, tools=tools, runtime=runtime,
                max_iterations=limits["max_iterations"], max_tool_result_chars=8_000,
                hook=hook, workspace=Path(directory), session_key=f"promptfoo:{trial_id}",
                denied_tool_capabilities=frozenset({"fixture_write"}), budget=budget,
                llm_timeout_s=30, channel="promptfoo", chat_id=case_id,
            )), timeout=limits["max_wall_seconds"] + 5)
    except Exception as exc:
        error = redact_text(f"{type(exc).__name__}: {exc}", limit=500)
    finally:
        try:
            await _close_provider(provider)
        except Exception as exc:
            metadata["cleanup_warning"] = redact_text(str(exc), limit=200)
    output = (result.final_content or "") if result is not None else ""
    stop_reason = result.stop_reason if result is not None else "execution_error"
    error = error or (result.error if result is not None else None)
    execution_error = stop_reason in {"error", "execution_error"}
    metadata.update({
        "execution_status": "error" if execution_error else "completed" if stop_reason == "completed" else "limited",
        "stop_reason": stop_reason, "tool_calls": calls, "observations": observations,
        "error_code": result.error_code if result is not None else None,
        "tool_events": result.tool_events if result else [],
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "budget": budget.snapshot(),
    })
    trace.finish(status=metadata["execution_status"], stop_reason=stop_reason, error=error)
    response: dict[str, Any] = {"output": output, "metadata": metadata, "latencyMs": metadata["latency_ms"]}
    usage = result.usage if result is not None else None
    if usage is not None:
        response["tokenUsage"] = {
            "prompt": usage.input_tokens, "completion": usage.output_tokens,
            "total": usage.total_tokens, "numRequests": usage.request_count,
        }
        metadata["usage_source"] = usage.source
        if input_rate is not None and output_rate is not None:
            response["cost"] = (usage.input_tokens * input_rate + usage.output_tokens * output_rate) / 1_000_000
            metadata["cost_estimated_usd"] = response["cost"]
            metadata["cost_source"] = "configured_rates_estimate"
    if execution_error:
        response["error"] = redact_text(str(error), limit=500)
        metadata["diagnostic_class"] = "provider_or_execution_error"
    transcript = {
        "metadata": metadata, "messages": result.messages if result is not None else messages,
        "tool_definitions": tools.get_definitions(), "observations": observations,
        "output": output, "error": error,
    }
    Path(metadata["transcript"]).write_text(json.dumps(transcript, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return response


def call_api(prompt: str, options: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = context or {}
    variables = context.get("vars") or {}
    if not isinstance(variables.get("case_id"), str) or not variables["case_id"]:
        return {"error": "missing test variable: case_id"}
    try:
        return asyncio.run(_execute(prompt, options.get("config") or {}, variables, context))
    except Exception as exc:
        return {"error": redact_text(f"{type(exc).__name__}: {exc}", limit=500)}


def judge_api(prompt: str, options: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Optional Promptfoo llm-rubric provider; never overrides hard assertions."""
    del options, context
    try:
        snapshot, _, _ = _snapshot(judge=True)
        async def request() -> LLMResponse:
            try:
                return await asyncio.wait_for(snapshot.provider.chat_with_retry(
                    messages=[{"role": "user", "content": prompt}], tools=None,
                    model=snapshot.model, temperature=0, max_tokens=400, reasoning_effort="none",
                ), timeout=45)
            finally:
                await _close_provider(snapshot.provider)

        response = asyncio.run(request())
        result: dict[str, Any] = {"output": response.content or ""}
        if response.usage:
            result["tokenUsage"] = {
                "prompt": response.usage.input_tokens, "completion": response.usage.output_tokens,
                "total": response.usage.total_tokens, "numRequests": 1,
            }
        return result
    except Exception as exc:
        return {"error": redact_text(f"Judge unavailable: {type(exc).__name__}: {exc}", limit=500)}
