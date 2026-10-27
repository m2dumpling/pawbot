"""Promptfoo adapter for isolated local projects using the real AgentLoop."""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import sys
import tempfile
import time
import uuid
from copy import deepcopy
from functools import wraps
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = Path(__file__).resolve().parent
for directory in (REPO_ROOT, EVAL_ROOT):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import provider as shared  # noqa: E402
from workspace_tasks import ALLOWED_CHANGES, SEEDS, changes, seed_workspace, snapshot  # noqa: E402

from pawbot.agent.hook import AgentHook, AgentHookContext, SDKCaptureHook  # noqa: E402
from pawbot.agent.loop import AgentLoop  # noqa: E402
from pawbot.agent.observability import TraceStore, redact_text  # noqa: E402
from pawbot.agent.tools.apply_patch import ApplyPatchTool  # noqa: E402
from pawbot.agent.tools.base import ToolResult  # noqa: E402
from pawbot.agent.tools.filesystem import (  # noqa: E402
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)
from pawbot.agent.tools.registry import ToolRegistry  # noqa: E402
from pawbot.agent.tools.search import FindFilesTool, GrepTool  # noqa: E402
from pawbot.bus.queue import MessageBus  # noqa: E402
from pawbot.config.schema import ToolsConfig  # noqa: E402
from pawbot.harness import HarnessTool, ScriptedProvider  # noqa: E402
from pawbot.providers.base import GenerationSettings, LLMResponse, ToolCallRequest  # noqa: E402
from pawbot.session.manager import SessionManager  # noqa: E402
from pawbot.utils.helpers import estimate_prompt_tokens_chain  # noqa: E402
from pawbot.utils.llm_runtime import LLMRuntime  # noqa: E402

ALLOWED_TOOLS = frozenset({
    "read_file", "list_dir", "find_files", "grep", "edit_file", "write_file", "apply_patch",
    "run_project_tests",
})
WRITE_TOOLS = frozenset({"edit_file", "write_file", "apply_patch"})
CHECK_SCRIPT = EVAL_ROOT / "workspace_tasks.py"
MAX_OUTPUT = 16_000


def _fingerprint(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        digest.update(path.relative_to(REPO_ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _fingerprints(variables: dict[str, Any]) -> dict[str, str]:
    suite_files = [
        EVAL_ROOT / name for name in (
            "workspace_provider.py", "workspace_tasks.py", "workspace_cases.yaml",
            "workspace_assertions.cjs", "promptfooconfig.workspace.yaml",
            "provider.py", "fixtures.py", "cli.cjs", "package.json",
            "summarize.py", "assertions.test.cjs",
        )
    ]
    suite_files.extend([
        REPO_ROOT / "tests/evals/test_workspace_eval.py",
        REPO_ROOT / "tests/evals/test_promptfoo_summary.py",
    ])
    source_files = [
        path for path in (REPO_ROOT / "pawbot").rglob("*.py")
        if "__pycache__" not in path.parts and "tests" not in path.parts
    ]
    runtime_keys = {"__evalId", "__evalStepId", "__repeatIndex", "script", "allow_simulation"}
    case = {key: value for key, value in variables.items() if key not in runtime_keys}
    suite_hash = _fingerprint(suite_files)
    return {
        "suite_sha256": suite_hash,
        "case_sha256": hashlib.sha256(
            (suite_hash + json.dumps(case, sort_keys=True, ensure_ascii=False)).encode("utf-8")
        ).hexdigest(),
        "agent_sha256": _fingerprint(source_files),
        "dependencies_sha256": _fingerprint([
            REPO_ROOT / "pyproject.toml", REPO_ROOT / "uv.lock", EVAL_ROOT / "package-lock.json",
        ]),
    }


def _text_files(workspace: Path, tree: dict[str, str]) -> dict[str, str]:
    contents: dict[str, str] = {}
    for name in tree:
        if name.endswith("/"):
            continue
        path = workspace / name
        if path.stat().st_size > 64_000:
            contents[name] = "<file exceeds diff limit>\n"
            continue
        try:
            contents[name] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            contents[name] = "<binary file>\n"
    return contents


def _write_diff(trial_root: Path, before: dict[str, str], after: dict[str, str], changed: list[str]) -> str:
    lines: list[str] = []
    for name in changed:
        if name.endswith("/"):
            continue
        lines.extend(difflib.unified_diff(
            before.get(name, "").splitlines(keepends=True),
            after.get(name, "").splitlines(keepends=True),
            fromfile=f"before/{name}", tofile=f"after/{name}",
        ))
    path = trial_root / "file-diff.txt"
    path.write_text("".join(lines)[:MAX_OUTPUT], encoding="utf-8")
    return str(path)


def _child_env(scratch: Path) -> dict[str, str]:
    # Do not pass provider credentials, personal configuration, or proxies to
    # the Python process that evaluates candidate code.
    allowed = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATH")
    env = {name: os.environ[name] for name in allowed if name in os.environ}
    env.update({
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMP": str(scratch),
        "TEMP": str(scratch),
        "TMPDIR": str(scratch),
    })
    return env


async def _check(
    case_id: str, workspace: Path, scratch: Path, *, final: bool, answer: str = "",
) -> dict[str, Any]:
    argv = [
        sys.executable, "-I", "-B", "-X", "utf8", str(CHECK_SCRIPT),
        "--case", case_id, "--workspace", str(workspace),
    ]
    if final:
        argv.append("--final")
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=str(workspace), env=_child_env(scratch),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    async def read_capped(stream: Any) -> bytes:
        parts: list[bytes] = []
        size = 0
        while chunk := await stream.read(4096):
            size += len(chunk)
            if size > MAX_OUTPUT:
                raise RuntimeError("Verifier output exceeded its limit")
            parts.append(chunk)
        return b"".join(parts)

    async def communicate() -> tuple[bytes, bytes]:
        assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
        proc.stdin.write(answer.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
        _, stdout, stderr = await asyncio.gather(
            proc.wait(), read_capped(proc.stdout), read_capped(proc.stderr),
        )
        return stdout, stderr

    try:
        stdout, stderr = await asyncio.wait_for(communicate(), timeout=20)
    except (Exception, asyncio.CancelledError):
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        raise
    if proc.returncode != 0:
        raise RuntimeError(f"Verifier failed to start or exited unexpectedly: {stderr.decode('utf-8', 'replace')[:500]}")
    if len(stdout) > MAX_OUTPUT:
        raise RuntimeError("Verifier output exceeded its limit")
    try:
        value = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Verifier did not return valid JSON") from exc
    if not isinstance(value, dict) or not isinstance(value.get("passed"), bool):
        raise RuntimeError("Verifier returned an invalid result")
    return value


def _tools(case_id: str, workspace: Path, scratch: Path) -> ToolRegistry:
    registry = ToolRegistry()
    for cls in (
        ReadFileTool, ListDirTool, FindFilesTool, GrepTool, EditFileTool, WriteFileTool,
        ApplyPatchTool,
    ):
        registry.register(cls(workspace=workspace, allowed_dir=workspace, restrict_to_workspace=True))

    async def run_tests(_arguments: dict[str, Any]) -> str:
        try:
            result = await _check(case_id, workspace, scratch, final=False)
        except asyncio.TimeoutError:
            return ToolResult.error("The fixed project test timed out.")
        except Exception as exc:
            return ToolResult.error(f"Project test unavailable: {type(exc).__name__}")
        text = json.dumps(result, ensure_ascii=False)
        return text if result["passed"] else ToolResult.error(text)

    registry.register(HarnessTool(
        "run_project_tests",
        "Run the fixed public checks for this local project; no command arguments are accepted.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        run_tests,
        read_only=True,
    ))
    if set(registry.tool_names) != ALLOWED_TOOLS:
        raise ValueError("The workspace tool whitelist is incomplete or contains extra tools")
    return registry


class WorkspaceHook(AgentHook):
    """Record tool attempts and per-request data without modifying the runner."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict[str, Any]] = []
        self.observations: list[dict[str, Any]] = []
        self.tool_timings: list[dict[str, Any]] = []
        self.model_requests: list[dict[str, Any]] = []
        self._tool_ids: set[tuple[int, str, int]] = set()
        self._started: int | None = None

    async def on_model_request_started(self, context: AgentHookContext) -> None:
        self._started = time.monotonic_ns()

    async def on_model_response(self, context: AgentHookContext) -> None:
        response = context.response
        self.model_requests.append({
            "iteration": context.iteration,
            "duration_ms": (time.monotonic_ns() - self._started) // 1_000_000 if self._started else None,
            "usage": context.usage.to_dict() if context.usage else None,
            "finish_reason": getattr(response, "finish_reason", None),
            "ttft_ms": getattr(response, "ttft_ms", None),
        })
        self._started = None

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        self.calls.extend({
            "id": call.id, "name": call.name, "arguments": deepcopy(call.arguments),
            "iteration": context.iteration, "index": index,
        } for index, call in enumerate(context.tool_calls))

    async def after_iteration(self, context: AgentHookContext) -> None:
        for index, call in enumerate(context.tool_calls):
            call_id = str(call.id)
            key = (context.iteration, call_id, index)
            if key in self._tool_ids:
                continue
            self._tool_ids.add(key)
            event = context.tool_events[index] if index < len(context.tool_events) else {}
            state = next((row for row in context.tool_states if row.get("call_id") == call_id), {})
            result = context.tool_results[index] if index < len(context.tool_results) else None
            status = str(event.get("status") or state.get("state") or "unknown")
            self.observations.append({
                "id": call_id, "name": call.name, "status": status,
                "iteration": context.iteration, "index": index,
                "result": str(result)[:MAX_OUTPUT] if result is not None else "",
            })
            self.tool_timings.append({
                "id": call_id, "name": call.name, "status": status,
                "iteration": context.iteration, "index": index,
                "duration_ms": state.get("duration_ms"),
            })


def _instrument_provider(provider: Any, model: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Inspect only this trial's provider and preserve the original call."""
    contexts: list[dict[str, Any]] = []
    physical: list[dict[str, Any]] = []

    def observe(record: Any) -> None:
        physical.append({
            "duration_ms": record.duration_ms, "finish_reason": record.finish_reason,
            "error_kind": record.error_kind, "usage": record.usage.to_dict() if record.usage else None,
        })

    provider.set_llm_call_observer(observe)
    for method_name in ("chat_with_retry", "chat_stream_with_retry"):
        original = getattr(provider, method_name)

        @wraps(original)
        async def wrapped(*args: Any, __original: Any = original, **kwargs: Any) -> Any:
            messages = kwargs.get("messages")
            tools = kwargs.get("tools")
            if isinstance(messages, list) and isinstance(tools, list):
                names = {
                    str(item.get("function", {}).get("name"))
                    for item in tools if isinstance(item, dict)
                }
                if not names.issubset(ALLOWED_TOOLS):
                    raise RuntimeError("Unexpected tool exposed to the workspace evaluation")
                try:
                    total, source = estimate_prompt_tokens_chain(provider, model, messages, tools)
                    empty, _ = estimate_prompt_tokens_chain(provider, model, [], [])
                    schema, _ = estimate_prompt_tokens_chain(provider, model, [], tools)
                    tool_messages = [row for row in messages if row.get("role") == "tool"]
                    observations, _ = estimate_prompt_tokens_chain(provider, model, tool_messages, [])
                    contexts.append({
                        "estimated_prompt_tokens": total,
                        "estimated_tool_schema_tokens": max(0, schema - empty),
                        "estimated_tool_result_tokens": max(0, observations - empty),
                        "token_estimation_source": source,
                        "tool_names": sorted(names),
                        "logical_input_sha256": hashlib.sha256(json.dumps(
                            {"messages": messages, "tools": tools}, sort_keys=True,
                            ensure_ascii=False, default=str,
                        ).encode("utf-8")).hexdigest(),
                    })
                except Exception:
                    contexts.append({"token_estimation_source": "unavailable", "tool_names": sorted(names)})
            return await __original(*args, **kwargs)

        setattr(provider, method_name, wrapped)
    return contexts, physical


def _observed_sources(observations: list[dict[str, Any]]) -> list[str]:
    text = "\n".join(
        row["result"] for row in observations
        if row.get("status") == "ok" and row.get("name") in {"read_file", "grep"}
    )
    return [identifier for identifier in ("CONFIG-DEMO", "RUNTIME-DEMO") if identifier in text]


def _halt_path(trace_root: Path) -> Path | None:
    run_id = os.environ.get("PAWBOT_WORKSPACE_RUN_ID")
    if not run_id:
        return None
    safe = hashlib.sha256(run_id.encode()).hexdigest()[:24]
    return trace_root / "_workspace_runs" / safe / "halt.json"


async def _execute_inner(
    prompt: str, config: dict[str, Any], variables: dict[str, Any], context: dict[str, Any],
    provider_owners: list[Any],
) -> dict[str, Any]:
    case_id = str(variables.get("case_id") or "")
    if case_id not in SEEDS:
        raise ValueError(f"Unknown workspace case: {case_id}")
    mode = config.get("mode", "live")
    if mode not in {"live", "smoke"}:
        raise ValueError("workspace provider mode must be live or smoke")
    simulation = mode == "smoke"
    trace_root = Path(os.environ.get("PAWBOT_EVAL_TRACE_ROOT", str(Path.home() / ".pawbot/promptfoo-traces"))).expanduser().resolve()
    halt_path = _halt_path(trace_root)
    if not simulation and halt_path and halt_path.exists():
        return {"error": "Workspace run halted after an earlier infrastructure error",
                "metadata": {"case_id": case_id, "execution_status": "error", "diagnostic_class": "run_halted"}}
    if simulation:
        script = variables.get("script")
        if not isinstance(script, list) or not script:
            raise ValueError("workspace smoke requires a response script")
        provider = ScriptedProvider([
            LLMResponse(
                content=row.get("content"),
                tool_calls=[ToolCallRequest(**call) for call in row.get("tool_calls", [])],
                finish_reason="tool_calls" if row.get("tool_calls") else "stop",
            ) for row in script
        ])
        model, window, input_rate, output_rate = "harness-model", 128_000, None, None
    else:
        snapshot_provider, input_rate, output_rate = shared._snapshot()
        provider, model, window = snapshot_provider.provider, snapshot_provider.model, snapshot_provider.context_window_tokens
    provider_owners.append(provider)
    cost_limit = shared._number("PAWBOT_EVAL_MAX_COST_USD")
    if cost_limit is not None and input_rate is None and not simulation:
        raise ValueError("A USD budget requires both token prices")
    generation = GenerationSettings(
        temperature=float(config.get("temperature", 0.1)),
        max_tokens=int(config.get("max_tokens", 2048)),
        reasoning_effort=config.get("reasoning_effort", "none"),
    )
    runtime = LLMRuntime.capture(provider, model, context_window_tokens=window).with_generation_overrides(
        temperature=generation.temperature, max_tokens=generation.max_tokens,
        reasoning_effort=generation.reasoning_effort,
    )
    contexts, physical = _instrument_provider(provider, model)
    trial_id = uuid.uuid4().hex
    trial_root = trace_root / trial_id
    trial_root.mkdir(parents=True, exist_ok=False)
    metadata: dict[str, Any] = {
        "case_id": case_id, "split": variables.get("split"), "trial_id": trial_id,
        "evaluation_id": context.get("evaluationId"), "repeat_index": context.get("repeatIndex"),
        "simulation": simulation, "model": model, "provider": getattr(provider, "provider_name", "unknown"),
        "generation": {"temperature": generation.temperature, "max_tokens": generation.max_tokens,
                       "reasoning_effort": generation.reasoning_effort},
        "limits": {
            "max_iterations": int(config.get("max_iterations", 8)),
            "max_tool_calls": int(config.get("max_tool_calls", 12)),
            "max_wall_seconds": float(config.get("max_wall_seconds", 120)),
            "max_input_tokens": int(config.get("max_input_tokens", 32_000)),
            "max_output_tokens": int(config.get("max_output_tokens", 8_192)),
        },
        "context_window_tokens": window, "cost_limit_usd": cost_limit,
        "python_version": sys.version.split()[0], "platform": platform.platform(),
        "sdk_versions": {
            package: importlib.metadata.version(package)
            for package in ("openai", "httpx", "pydantic")
        },
        "git_commit": shared._git_revision(),
        "evaluation_scope": "AgentLoop, real local file tools, fixed public tests and external checks",
        "environment": {"timezone": "UTC", "workspace": "independent temporary directory",
                        "clock": "live wall clock"},
        "tool_concurrency": "runner-default-concurrent", "cost_source": "not_configured",
        "planned_splits": {"dev": 4, "holdout": 2} if not simulation else {"smoke": 1},
        "transcript": str(trial_root / "transcript.json"),
        **_fingerprints(variables),
    }
    hook = WorkspaceHook()
    capture = SDKCaptureHook()
    output, error = "", None
    started = time.monotonic_ns()
    with tempfile.TemporaryDirectory(prefix="project-", dir=trial_root) as temporary:
        temp_root = Path(temporary)
        workspace, scratch = temp_root / "workspace", temp_root / "scratch"
        scratch.mkdir()
        seed_workspace(case_id, workspace)
        tools = _tools(case_id, workspace, scratch)
        store = TraceStore(trial_root / "traces", capture_prompts=True, capture_tool_results=True)
        readonly = case_id == "workspace-readonly-audit"
        loop = AgentLoop(
            bus=MessageBus(), provider=provider, workspace=workspace, model=model,
            max_iterations=metadata["limits"]["max_iterations"],
            max_tool_calls=metadata["limits"]["max_tool_calls"],
            max_turn_seconds=metadata["limits"]["max_wall_seconds"],
            max_input_tokens=metadata["limits"]["max_input_tokens"],
            max_output_tokens=metadata["limits"]["max_output_tokens"],
            max_turn_cost_usd=cost_limit, input_cost_per_million_usd=input_rate,
            output_cost_per_million_usd=output_rate, context_window_tokens=window,
            restrict_to_workspace=True, timezone="UTC",
            tools_config=ToolsConfig(restrict_to_workspace=True, denied_capabilities=["write"] if readonly else []),
            session_manager=SessionManager(workspace, sessions_root=temp_root / "sessions"),
            memory_data_root=temp_root / "memory", trace_store=store,
        )
        # Do not expose local user skills or personal memory during an eval.
        loop.context.skills.builtin_skills = temp_root / "no-skills"
        # SessionManager and MemoryStore create runtime markers in the
        # workspace during construction. Treat these as protected initial
        # state rather than blaming the agent for creating them.
        before = snapshot(workspace)
        before_content = _text_files(workspace, before)
        try:
            response = await asyncio.wait_for(loop.process_direct(
                prompt, session_key=f"sdk:workspace-eval-{trial_id}", channel="cli",
                chat_id=trial_id, sender_id="eval", hooks=[capture, hook], tools=tools, runtime=runtime,
            ), timeout=180)
            output = response.content if response and isinstance(response.content, str) else ""
            execution_status = str((capture.outcome or {}).get("execution_status") or (
                "completed" if capture.stop_reason == "completed" else
                "error" if capture.stop_reason == "error" or capture.error else "limited"
            ))
            if execution_status in {"failed", "error"}:
                error = capture.error or f"Agent execution failed: {capture.stop_reason}"
                execution_status = "error"
        except asyncio.TimeoutError:
            execution_status, error = "error", "Workspace trial exceeded its outer timeout"
        except Exception as exc:
            execution_status, error = "error", f"{type(exc).__name__}: {exc}"
        finally:
            metadata["latency_ms"] = (time.monotonic_ns() - started) // 1_000_000
            await loop.aclose()
        metadata.update({
            "execution_status": execution_status, "stop_reason": capture.stop_reason,
            "tool_calls": hook.calls, "tool_events": hook.tool_timings,
            "observations": hook.observations, "model_requests": hook.model_requests,
            "physical_model_requests": physical or None, "contexts": contexts,
            "budget": capture.budget, "outcome": capture.outcome,
        })
        summaries = store.list_summaries(limit=2, session_key=f"sdk:workspace-eval-{trial_id}")
        metadata["trace"] = str(store.resolve(summaries[0]["id"])) if summaries else None
        validation_start = time.monotonic_ns()
        try:
            after = snapshot(workspace)
            changed = changes(before, after)
            metadata["file_diff"] = _write_diff(trial_root, before_content, _text_files(workspace, after), changed)
            unexpected = [name for name in changed if name not in ALLOWED_CHANGES[case_id]]
            with tempfile.TemporaryDirectory(prefix="verify-", dir=temp_root) as verification:
                copy = Path(verification) / "workspace"
                shutil.copytree(workspace, copy)
                verdict = await _check(case_id, copy, scratch, final=True, answer=output)
            boundary_failures: list[str] = []
            forbidden = [call["name"] for call in hook.calls if call["name"] not in ALLOWED_TOOLS]
            if forbidden:
                boundary_failures.append(f"Unregistered or forbidden tool attempted: {forbidden}")
            if unexpected:
                boundary_failures.append(f"Unexpected changed files: {unexpected}")
            if readonly:
                attempts = [call["name"] for call in hook.calls if call["name"] in WRITE_TOOLS]
                if attempts:
                    boundary_failures.append(f"Read-only task attempted write tools: {attempts}")
                if _observed_sources(hook.observations) != ["CONFIG-DEMO", "RUNTIME-DEMO"]:
                    boundary_failures.append("Both source IDs must be observed from successful tool results")
            elif not any(row["name"] == "run_project_tests" and row["status"] == "ok" for row in hook.observations):
                boundary_failures.append("No successful public project test was observed")
            metadata["validation"] = verdict
            metadata["boundary_failures"] = boundary_failures
            metadata["changed_files"] = changed
        except ValueError as exc:
            # A model-created symlink or unsupported file is an observable
            # boundary violation, not a provider/infrastructure outage.
            metadata["validation"] = {"passed": False, "failures": [str(exc)], "scope": "final"}
            metadata["boundary_failures"] = [str(exc)]
            metadata["changed_files"] = []
        except Exception as exc:
            metadata["validation"] = {"passed": False, "failures": [], "status": "unavailable"}
            error = error or f"External verifier unavailable: {type(exc).__name__}: {exc}"
            metadata["execution_status"] = "error"
            metadata["diagnostic_class"] = "oracle_unavailable"
        metadata["validation_ms"] = (time.monotonic_ns() - validation_start) // 1_000_000
        usage = capture.usage
        response_data: dict[str, Any] = {"output": output, "metadata": metadata, "latencyMs": metadata["latency_ms"]}
        if usage is not None:
            response_data["tokenUsage"] = {
                "prompt": usage.input_tokens, "completion": usage.output_tokens,
                "total": usage.total_tokens, "numRequests": usage.request_count,
            }
            metadata["usage_source"] = usage.source
            if input_rate is not None and output_rate is not None:
                response_data["cost"] = (usage.input_tokens * input_rate + usage.output_tokens * output_rate) / 1_000_000
                metadata["cost_estimated_usd"] = response_data["cost"]
                metadata["cost_source"] = "configured_rates_estimate"
        if error:
            safe_error = redact_text(str(error), limit=500)
            response_data["error"] = safe_error
            metadata.setdefault("diagnostic_class", "provider_or_execution_error")
            if not simulation and halt_path:
                halt_path.parent.mkdir(parents=True, exist_ok=True)
                halt_path.write_text(json.dumps({"reason": safe_error}, ensure_ascii=False), encoding="utf-8")
        transcript = {
            "metadata": metadata, "messages": capture.messages, "output": output,
            "error": redact_text(str(error), limit=500) if error else None,
            "tool_definitions": tools.get_definitions(),
        }
        Path(metadata["transcript"]).write_text(json.dumps(transcript, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return response_data


async def _execute(prompt: str, config: dict[str, Any], variables: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    providers: list[Any] = []
    try:
        return await _execute_inner(prompt, config, variables, context, providers)
    finally:
        for provider in providers:
            await shared._close_provider(provider)


def call_api(prompt: str, options: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = context or {}
    variables = context.get("vars") or {}
    try:
        return asyncio.run(_execute(prompt, options.get("config") or {}, variables, context))
    except Exception as exc:
        safe_error = redact_text(f"{type(exc).__name__}: {exc}", limit=500)
        if (options.get("config") or {}).get("mode", "live") != "smoke":
            trace_root = Path(os.environ.get(
                "PAWBOT_EVAL_TRACE_ROOT", str(Path.home() / ".pawbot/promptfoo-traces"),
            )).expanduser().resolve()
            halt_path = _halt_path(trace_root)
            if halt_path:
                try:
                    halt_path.parent.mkdir(parents=True, exist_ok=True)
                    halt_path.write_text(json.dumps({"reason": safe_error}, ensure_ascii=False), encoding="utf-8")
                except OSError:
                    pass
        return {"error": safe_error,
                "metadata": {"case_id": variables.get("case_id"), "execution_status": "error",
                             "diagnostic_class": "provider_or_execution_error"}}
