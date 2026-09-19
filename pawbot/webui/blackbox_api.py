"""WebUI integration for the Record & Replay blackbox and token governance.

These are the ADR-002 (token estimation) and ADR-004 (record/replay)
enhancements surfaced as an async action dispatcher so the embedded WebUI can
control recording/replay and inspect token estimates without the CLI.

The dispatcher is deliberately self-contained: it takes an ``agent_getter``
(so the AgentLoop instance is resolved lazily, matching how the gateway
composition layer wires the rest of the WebUI surface) and an optional
``session_manager`` for per-session token inspection.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

from pawbot.webui.session_list_index import list_webui_sessions
from pawbot.webui.sidebar_state import read_webui_sidebar_state


class BlackboxActionError(Exception):
    """Raised for invalid blackbox actions; carries an HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _compact_session_label(value: Any) -> str:
    """Return the same compact, human-readable label used by the WebUI list."""
    if not isinstance(value, str):
        return ""
    label = re.sub(r"\s+", " ", value).strip()
    if not label or label.startswith("/"):
        return ""
    return label[:60].rstrip() + ("…" if len(label) > 60 else "")


def _session_display_names(agent: Any) -> dict[str, str]:
    """Build session-key-to-title mappings for trace and replay surfaces."""
    sessions = getattr(agent, "sessions", None)
    list_sessions = getattr(sessions, "list_sessions", None)
    if not callable(list_sessions):
        return {}
    rows: list[Any] = []
    # Keep the two indexes independent. A malformed legacy session should not
    # erase the titles that can still be read from the WebUI index.
    try:
        raw_sessions = list_sessions()
        if isinstance(raw_sessions, list):
            rows.extend(cast(list[Any], raw_sessions))
    except Exception:
        pass
    try:
        raw_webui_sessions = list_webui_sessions(cast(Any, sessions))
        rows.extend(cast(list[Any], raw_webui_sessions))
    except Exception:
        pass

    names: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_mapping = cast(dict[str, Any], row)
        key = row_mapping.get("key")
        if not isinstance(key, str) or not key:
            continue
        title = _compact_session_label(row_mapping.get("title"))
        preview = _compact_session_label(row_mapping.get("preview"))
        if title or preview:
            names[key] = title or preview
    try:
        sidebar_state = read_webui_sidebar_state()
    except Exception:
        sidebar_state = {}
    overrides = sidebar_state.get("title_overrides")
    if isinstance(overrides, dict):
        for raw_key, raw_title in cast(dict[Any, Any], overrides).items():
            key = raw_key if isinstance(raw_key, str) else ""
            title = _compact_session_label(raw_title)
            if key and title:
                names[key] = title

    # Unified-session mode records the technical key ``unified:default`` while
    # the WebUI title belongs to the last concrete WebSocket route. Project the
    # title back to the unified key so the inspection surface remains readable.
    read_metadata = getattr(sessions, "read_session_metadata", None)
    if callable(read_metadata):
        for row in rows:
            row_mapping = cast(dict[str, Any], row) if isinstance(row, dict) else {}
            key = row_mapping.get("key")
            if not isinstance(key, str) or key in names:
                continue
            try:
                metadata = read_metadata(key)
            except Exception:
                continue
            if isinstance(metadata, dict):
                route = cast(dict[str, Any], metadata).get("last_channel")
                if isinstance(route, str) and route in names:
                    names[key] = names[route]
    return names


def _session_display_name(agent: Any, session_key: Any) -> str | None:
    if not isinstance(session_key, str) or not session_key:
        return None
    return _session_display_names(agent).get(session_key)


def _attach_session_name(agent: Any, row: dict[str, Any], names: dict[str, str] | None = None) -> dict[str, Any]:
    """Add a display title without replacing the stable technical session key."""
    session_key = row.get("session_key")
    name_mapping = names if names is not None else _session_display_names(agent)
    name = name_mapping.get(session_key) if isinstance(session_key, str) else None
    if not name:
        channel = row.get("channel")
        chat_id = row.get("chat_id")
        route = f"{channel}:{chat_id}" if isinstance(channel, str) and isinstance(chat_id, str) else ""
        name = name_mapping.get(route) if route else None
    if name:
        row = {**row, "session_name": name}
    return row


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        return {str(k): _json_safe(v) for k, v in mapping.items()}
    if isinstance(value, (list, tuple)):
        sequence = cast(list[Any] | tuple[Any, ...], value)
        return [_json_safe(v) for v in sequence]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _recording_summary(directory: Path) -> dict[str, Any]:
    """Return a user-facing summary without trusting directory shape alone."""
    from pawbot.agent.blackbox.manifest import recording_health

    health = recording_health(directory)
    sample_health = str(health["sample_health"])
    if sample_health in {"recording", "incomplete", "corrupted"}:
        return {
            "status": "invalid",
            "sample_health": sample_health,
            "reason": sample_health,
            "message": "样本尚未完整保存，当前只能查看诊断，不能离线验证",
            "turns": 0,
        }
    turns_path = directory / "turns.jsonl"
    if not turns_path.exists():
        return {
            "status": "invalid",
            "reason": "missing_turn_file",
            "message": "找不到录制文件，无法检查",
            "turns": 0,
            "sample_health": sample_health,
        }

    valid_turns = 0
    malformed_lines = 0
    trace_events = 0
    try:
        with turns_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    raw_record = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    malformed_lines += 1
                    continue
                if (
                    isinstance(raw_record, dict)
                    and cast(dict[str, Any], raw_record).get("kind") == "turn"
                    and cast(dict[str, Any], raw_record).get("complete") is not False
                ):
                    valid_turns += 1
                else:
                    malformed_lines += 1

        # ``tools.jsonl`` is optional for a no-tool turn, but when present it
        # must be readable because ReplayController indexes every line before
        # it can start replaying turns.
        tools_path = directory / "tools.jsonl"
        if tools_path.exists():
            with tools_path.open(encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        raw_record = json.loads(line)
                    except (json.JSONDecodeError, TypeError):
                        malformed_lines += 1
                        continue
                    if not isinstance(raw_record, dict):
                        malformed_lines += 1

        # Newer recordings include a structured lifecycle timeline. Keep this
        # file optional so recordings created before the trace layer remain
        # replayable and visible.
        events_path = directory / "events.jsonl"
        if events_path.exists():
            with events_path.open(encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        raw_record = json.loads(line)
                    except (json.JSONDecodeError, TypeError):
                        malformed_lines += 1
                        continue
                    if isinstance(raw_record, dict):
                        trace_events += 1
                    else:
                        malformed_lines += 1
    except (OSError, UnicodeError):
        return {
            "status": "invalid",
            "reason": "unreadable",
            "message": "录制文件无法读取",
            "turns": 0,
            "sample_health": sample_health,
        }

    if malformed_lines:
        return {
            "status": "invalid",
            "reason": "malformed",
            "message": "录制文件格式有问题，无法检查",
            "turns": valid_turns,
            "sample_health": sample_health,
        }
    if valid_turns == 0:
        return {
            "status": "invalid",
            "reason": "no_valid_turns",
            "message": "没有找到有效的任务记录，无法检查",
            "turns": 0,
            "sample_health": sample_health,
        }
    return {
        "status": "ready",
        "message": "记录完整",
        "turns": valid_turns,
        "trace_events": trace_events,
        "sample_health": sample_health,
    }


def _recording_session_names(directory: Path, names: dict[str, str]) -> list[str]:
    """Return unique WebUI titles referenced by a detailed recording."""
    try:
        records = _read_jsonl(directory / "turns.jsonl")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for record in records:
        key = record.get("session_key")
        title = names.get(key) if isinstance(key, str) else None
        if title and title not in seen:
            seen.add(title)
            result.append(title)
    return result


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read JSONL records for the authenticated local inspection surface."""
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(f"非对象 JSON 记录：{path.name}")
            records.append(cast(dict[str, Any], record))
    return records


def _cassette_name(turn_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in turn_id) + ".yaml"


def _llm_error_details(event: dict[str, Any]) -> dict[str, Any] | None:
    """Extract provider error metadata from one recorded LLM response."""
    response = event.get("response")
    if not isinstance(response, dict):
        return None
    response_mapping = cast(dict[str, Any], response)
    error_fields = (
        "error_status_code",
        "error_kind",
        "error_type",
        "error_code",
    )
    finish_reason = str(response_mapping.get("finish_reason") or "").lower()
    if finish_reason != "error" and not any(
        response_mapping.get(field) not in (None, "") for field in error_fields
    ):
        return None
    content = response_mapping.get("content")
    if isinstance(content, str):
        message = content.strip()
    else:
        message = ""
    return {
        "iteration": event.get("iteration"),
        "response_index": event.get("response_index"),
        "status_code": response_mapping.get("error_status_code"),
        "kind": response_mapping.get("error_kind"),
        "type": response_mapping.get("error_type"),
        "code": response_mapping.get("error_code"),
        "message": message[:1000],
    }


def _original_execution_status(
    *,
    turn: dict[str, Any],
    failed_tools: list[dict[str, Any]],
    provider_errors: list[dict[str, Any]],
    unknown_side_effects: list[dict[str, Any]],
) -> str:
    """Classify the original run independently from replay determinism."""
    stop_reason = str(turn.get("stop_reason") or "").lower()
    if provider_errors and failed_tools:
        return "multiple_errors"
    if provider_errors:
        return "model_error"
    if failed_tools:
        return "tool_error"
    if stop_reason in {"cancelled", "canceled"}:
        return "cancelled"
    if stop_reason == "error" or turn.get("complete") is False or turn.get("error"):
        return "execution_error"
    if unknown_side_effects:
        return "unknown_side_effect"
    if str(turn.get("stop_reason") or "").lower() in {
        "completed",
        "stop",
        "task_verification_failed",
        "task_verification_not_evaluable",
    }:
        return "success"
    return "unknown"


def _original_turn_outcome(
    turn: dict[str, Any],
    *,
    original_status: str,
    task_evaluation: dict[str, Any] | None,
    unknown_side_effects: list[dict[str, Any]],
) -> dict[str, Any]:
    """Prefer stored Outcome while keeping old recordings readable."""
    from pawbot.agent.turn.outcome import TurnOutcome

    stored = turn.get("outcome")
    if isinstance(stored, dict):
        return TurnOutcome.from_dict(stored).to_dict()
    stop_reason = str(turn.get("stop_reason") or "") or None
    if original_status == "success":
        execution_status = "completed"
    elif original_status == "cancelled":
        execution_status = "cancelled"
    elif stop_reason in {
        "max_iterations",
        "max_tool_calls",
        "max_wall_time",
        "max_input_tokens",
        "max_output_tokens",
        "max_cost_usd",
    }:
        execution_status = "limited"
    elif original_status == "unknown_side_effect":
        execution_status = "incomplete"
    else:
        execution_status = "failed"
    raw_task_status = task_evaluation.get("status") if task_evaluation is not None else None
    task_status = (
        raw_task_status
        if raw_task_status in {"passed", "failed", "not_evaluable"}
        else "not_requested"
    )
    side_effect_status = "unknown" if unknown_side_effects else "not_applicable"
    recovery_status = "awaiting_confirmation" if unknown_side_effects else (
        "not_needed" if execution_status == "completed" else "resumable"
    )
    return TurnOutcome(
        execution_status=cast(Any, execution_status),
        stop_reason=stop_reason,
        task_status=cast(Any, task_status),
        side_effect_status=cast(Any, side_effect_status),
        recovery_status=cast(Any, recovery_status),
    ).to_dict()


def _turn_diagnostics(turn: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a compact explanation layer above the raw replay rails."""
    tool_events = [event for event in events if event.get("kind") == "tool"]
    provider_tool_events = [
        event for event in events if event.get("kind") == "provider_tool"
    ]
    failed_tools = [
        {
            "name": event.get("name"),
            "status": event.get("status"),
            "detail": event.get("detail", ""),
        }
        for event in tool_events
        if event.get("status") not in {None, "ok"}
    ]
    unknown_side_effects = [
        {
            "name": event.get("name"),
            "call_id": (
                event.get("execution", {}).get("call_id")
                if isinstance(event.get("execution"), dict)
                else None
            ),
            "detail": "工具被取消或执行状态未知，请人工确认副作用",
        }
        for event in tool_events
        if isinstance(event.get("execution"), dict)
        and event["execution"].get("state") == "unknown"
    ]
    provider_errors = [
        error
        for event in events
        if event.get("kind") == "llm"
        for error in [_llm_error_details(event)]
        if error is not None
    ]
    original_status = _original_execution_status(
        turn=turn,
        failed_tools=failed_tools,
        provider_errors=provider_errors,
        unknown_side_effects=unknown_side_effects,
    )
    task_evaluation_value = turn.get("task_evaluation")
    task_evaluation = (
        cast(dict[str, Any], task_evaluation_value)
        if isinstance(task_evaluation_value, dict)
        else None
    )
    original_execution: dict[str, Any] = {
        "status": original_status,
        "ok": original_status == "success",
        "failed_tool_count": len(failed_tools),
        "provider_error_count": len(provider_errors),
        "unknown_side_effect_count": len(unknown_side_effects),
    }
    if task_evaluation is not None:
        original_execution.update({
            "task_status": task_evaluation.get("status"),
            "task_completed": task_evaluation.get("completed"),
        })
    original_outcome = _original_turn_outcome(
        turn,
        original_status=original_status,
        task_evaluation=task_evaluation,
        unknown_side_effects=unknown_side_effects,
    )
    return {
        "stop_reason": turn.get("stop_reason") or "unknown",
        "failed_tools": failed_tools,
        "provider_errors": provider_errors,
        "unknown_side_effects": unknown_side_effects,
        "provider_tool_events": _json_safe(provider_tool_events),
        "original_execution": original_execution,
        "original_outcome": original_outcome,
        "task_evaluation": task_evaluation,
        "budget": turn.get("budget"),
        "message": (
            "本轮正常结束"
            if original_status == "success"
            else "原始执行包含异常，请分别查看回放结果和原始执行状态"
        ),
    }


async def _detail(agent: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Return the raw rails for one turn without re-running the agent."""
    directory_name = str(payload.get("directory") or "")
    turn_id = str(payload.get("turn_id") or "")
    if not directory_name or not turn_id:
        raise BlackboxActionError(400, "directory and turn_id are required for detail")

    directory = _resolve_directory(agent, directory_name)
    if not directory.exists():
        raise BlackboxActionError(404, "录制目录不存在")
    try:
        turns = _read_jsonl(directory / "turns.jsonl")
        turn = next(
            (
                record
                for record in turns
                if record.get("kind") == "turn" and str(record.get("turn_id")) == turn_id
            ),
            None,
        )
        if turn is None:
            raise BlackboxActionError(404, f"回合不存在：{turn_id}")
        events = [
            record
            for record in _read_jsonl(directory / "tools.jsonl")
            if str(record.get("turn_id")) == turn_id
        ]
        trace_events = [
            record
            for record in _read_jsonl(directory / "events.jsonl")
            if str(record.get("turn_id")) == turn_id
        ]
    except BlackboxActionError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise BlackboxActionError(422, f"无法读取这个回合的原始数据：{exc}") from exc

    events.sort(
        key=lambda record: (
            int(record.get("iteration") or 0),
            0 if record.get("kind") == "llm" else 1,
            int(record.get("response_index", record.get("invocation_index", 0)) or 0),
        )
    )
    session_name = _session_display_name(agent, turn.get("session_key"))
    cassette = _cassette_name(turn_id)
    return {
        "directory": str(directory),
        "turn_id": turn_id,
        "session_name": session_name,
        "turn": _json_safe(turn),
        "events": _json_safe(events),
        "trace_events": _json_safe(trace_events),
        "diagnostics": _json_safe(_turn_diagnostics(turn, events)),
        "counts": {
            "llm_responses": sum(1 for event in events if event.get("kind") == "llm"),
            "tool_calls": sum(1 for event in events if event.get("kind") == "tool"),
            "provider_tool_events": sum(
                1 for event in events if event.get("kind") == "provider_tool"
            ),
        },
        "files": {
            "turns": "turns.jsonl",
            "tools": "tools.jsonl" if (directory / "tools.jsonl").exists() else None,
            "cassette": cassette if (directory / cassette).exists() else None,
            "events": "events.jsonl" if (directory / "events.jsonl").exists() else None,
        },
    }


def _blackbox_root(agent: Any) -> Path:
    """The single root directory all recordings live under (matches _list)."""
    workspace = Path(getattr(agent, "workspace", "") or ".")
    return (workspace / "blackbox").resolve()


def _resolve_directory(agent: Any, name: str) -> Path:
    """Resolve a recording name to an absolute path under the blackbox root."""
    root = _blackbox_root(agent)
    path = Path(name)
    resolved = (path if path.is_absolute() else root / name).resolve()
    if resolved == root or root not in resolved.parents:
        raise BlackboxActionError(400, "录制目录必须位于当前 workspace/blackbox 下")
    return resolved


async def _status(agent: Any) -> dict[str, Any]:
    from pawbot.agent.blackbox import BlackboxController

    sync_policy = getattr(agent, "sync_recording_policy", None)
    if callable(sync_policy):
        sync_policy()
    bb = agent.blackbox
    recording = isinstance(bb, BlackboxController)
    runtime = agent.llm_runtime()
    return {
        "recording": recording,
        "directory": str(bb.directory) if recording else "",
        "model": agent.model,
        "context_window_tokens": runtime.context_window_tokens,
        "tool_count": len(agent.tools),
    }


async def _start(agent: Any, payload: dict[str, Any]) -> dict[str, Any]:
    from pawbot.agent.blackbox import BlackboxController

    name = str(payload.get("directory") or "session")
    start_recording = getattr(agent, "start_detailed_recording", None)
    if callable(start_recording):
        directory = Path(str(start_recording(name)))
    else:
        directory = _resolve_directory(agent, name)
        agent.blackbox = BlackboxController(str(directory))
    return {"recording": True, "directory": str(directory)}


async def _stop(agent: Any) -> dict[str, Any]:
    stop_recording = getattr(agent, "stop_detailed_recording", None)
    if callable(stop_recording):
        stop_recording()
    else:
        active = getattr(agent, "blackbox", None)
        directory = getattr(active, "directory", None)
        if isinstance(directory, Path):
            from pawbot.agent.blackbox import finalize_recording_manifest

            finalize_recording_manifest(directory)
        agent.blackbox = None
    return {"recording": False}


async def _list(agent: Any) -> dict[str, Any]:
    root = _blackbox_root(agent)
    recordings: list[dict[str, Any]] = []
    names = _session_display_names(agent)
    if root.exists():
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            summary = _recording_summary(entry)
            session_names = _recording_session_names(entry, names)
            recordings.append({
                "directory": str(entry),
                "name": entry.name,
                "session_names": session_names,
                **summary,
            })
    return {"recordings": recordings, "root": str(root)}


def _trace_store(agent: Any) -> Any:
    """Return the configured local TraceStore without exposing its implementation."""
    store = getattr(agent, "trace_store", None)
    if not callable(getattr(store, "list_summaries", None)):
        raise BlackboxActionError(503, "执行追踪不可用")
    return store


async def _trace_list(agent: Any, payload: dict[str, Any]) -> dict[str, Any]:
    store = _trace_store(agent)
    raw_limit = payload.get("limit", 50)
    limit = raw_limit if isinstance(raw_limit, int) and not isinstance(raw_limit, bool) else 50
    session_key = payload.get("session_key")
    if not isinstance(session_key, str) or not session_key:
        session_key = None
    chat_id = payload.get("chat_id")
    if not isinstance(chat_id, str) or not chat_id:
        chat_id = None
    summaries = store.list_summaries(
        limit=limit,
        session_key=session_key,
        chat_id=chat_id,
        issues_only=payload.get("filter") == "issues",
        slow_only=payload.get("filter") == "slow",
    )
    names = _session_display_names(agent)
    return {
        "traces": _json_safe([_attach_session_name(agent, row, names) for row in summaries]),
        "root": str(store.root),
    }


async def _trace_detail(agent: Any, payload: dict[str, Any]) -> dict[str, Any]:
    identifier = str(payload.get("id") or "")
    if not identifier:
        raise BlackboxActionError(400, "id is required for trace detail")
    store = _trace_store(agent)
    try:
        summary, events = store.detail(identifier)
    except FileNotFoundError as exc:
        raise BlackboxActionError(404, "执行追踪不存在") from exc
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise BlackboxActionError(422, f"无法读取执行追踪：{exc}") from exc
    return {
        "summary": _json_safe(_attach_session_name(agent, summary)),
        "events": _json_safe(events),
    }


async def _delete(agent: Any, payload: dict[str, Any]) -> dict[str, Any]:
    directory_name = str(payload.get("directory") or "")
    if not directory_name:
        raise BlackboxActionError(400, "directory is required for delete")
    directory = _resolve_directory(agent, directory_name)
    if not directory.exists():
        raise BlackboxActionError(404, "录制目录不存在")

    active = getattr(agent, "blackbox", None)
    active_directory = getattr(active, "directory", None)
    if active_directory is not None:
        if Path(active_directory).resolve() == directory:
            raise BlackboxActionError(409, "请先停止正在进行的录制")

    shutil.rmtree(directory)
    return {"deleted": True, "directory": str(directory)}


async def _replay(agent: Any, payload: dict[str, Any]) -> dict[str, Any]:
    from pawbot.agent.blackbox import (
        ReplayBreakpoint,
        ReplayController,
        validate_recording_manifest,
    )

    directory_name = str(payload.get("directory") or "")
    if not directory_name:
        raise BlackboxActionError(400, "directory is required for replay")
    directory_path = _resolve_directory(agent, directory_name)
    sample_health = validate_recording_manifest(directory_path)
    if sample_health not in {"ready", "legacy_unverified"}:
        raise BlackboxActionError(409, f"样本状态为 {sample_health}，请先停止并完整保存后再验证")
    directory = str(directory_path)
    break_at = payload.get("break_at")
    if break_at is not None and not isinstance(break_at, int):
        raise BlackboxActionError(400, "break_at must be an integer")
    try:
        controller = ReplayController(
            directory,
            break_at=int(break_at) if break_at is not None else None,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise BlackboxActionError(
            422,
            f"这个样本无法检查：{exc}",
        ) from exc
    if not controller.turns:
        raise BlackboxActionError(404, f"no recorded turns in {directory!r}")
    try:
        results = await agent.replay_all(controller)
    except ReplayBreakpoint as bp:
        info = controller.last_breakpoint or {}
        return {
            "directory": directory,
            "breakpoint": True,
            "iteration": bp.iteration,
            "turn_id": info.get("turn_id"),
            "messages": _json_safe(info.get("messages") or []),
        }
    try:
        raw_turns = _read_jsonl(Path(directory) / "turns.jsonl")
        raw_events = _read_jsonl(Path(directory) / "tools.jsonl")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise BlackboxActionError(422, f"无法读取原始执行状态：{exc}") from exc
    turns_by_id = {
        str(record.get("turn_id")): record
        for record in raw_turns
        if record.get("kind") == "turn" and record.get("turn_id")
    }
    events_by_id: dict[str, list[dict[str, Any]]] = {}
    for event in raw_events:
        turn_id = str(event.get("turn_id") or "")
        if turn_id:
            events_by_id.setdefault(turn_id, []).append(event)

    raw_replay_details: Any = getattr(controller, "last_replay_details", [])
    replay_detail_by_id: dict[str, dict[str, Any]] = {}
    for detail in (
        cast(list[Any], raw_replay_details)
        if isinstance(raw_replay_details, list)
        else []
    ):
        if isinstance(detail, dict):
            detail_mapping = cast(dict[str, Any], detail)
            if isinstance(detail_mapping.get("turn_id"), str):
                replay_detail_by_id[detail_mapping["turn_id"]] = detail_mapping
    raw_benchmark_rows: Any = getattr(controller, "last_benchmark", [])
    benchmark_by_id: dict[str, dict[str, Any]] = {}
    for benchmark in (
        cast(list[Any], raw_benchmark_rows)
        if isinstance(raw_benchmark_rows, list)
        else []
    ):
        if isinstance(benchmark, dict):
            benchmark_mapping = cast(dict[str, Any], benchmark)
            if isinstance(benchmark_mapping.get("turn_id"), str):
                benchmark_by_id[benchmark_mapping["turn_id"]] = benchmark_mapping

    rows: list[dict[str, Any]] = []
    session_names = _session_display_names(agent)
    for turn_id, ok, diffs in results:
        turn_record = turns_by_id.get(turn_id, {"turn_id": turn_id})
        diagnostics = _turn_diagnostics(
            turn_record,
            events_by_id.get(turn_id, []),
        )
        replay_detail = replay_detail_by_id.get(turn_id, {})
        message_diffs = replay_detail.get("message_diffs")
        trace_diffs = replay_detail.get("trace_diffs")
        trace_comparable = replay_detail.get("trace_comparable")
        if not isinstance(message_diffs, list):
            message_diffs = list(diffs)
        if not isinstance(trace_diffs, list):
            trace_diffs = []
        if not isinstance(trace_comparable, bool):
            trace_comparable = False
        replay_task = {
            "status": replay_detail.get("task_status"),
            "completed": replay_detail.get("task_completed"),
        }
        replay_outcome = replay_detail.get("outcome")
        turn_session_key = turn_record.get("session_key")
        turn_session_name = (
            session_names.get(turn_session_key)
            if isinstance(turn_session_key, str)
            else None
        )
        rows.append({
            "turn_id": turn_id,
            "session_name": turn_session_name,
            "ok": ok,
            "diffs": _json_safe(diffs),
            "message_diffs": _json_safe(message_diffs),
            "trace_diffs": _json_safe(trace_diffs),
            "trace_comparable": trace_comparable,
            "benchmark": _json_safe(benchmark_by_id.get(turn_id)),
            "summary": (
                "未发现可观察差异"
                if ok
                else f"当前执行与原样本有 {len(diffs)} 处差异"
            ),
            "original_execution": _json_safe(diagnostics["original_execution"]),
            "original_outcome": _json_safe(diagnostics["original_outcome"]),
            "original_task": _json_safe(diagnostics.get("task_evaluation")),
            "replay_task": _json_safe(replay_task),
            "replay_outcome": _json_safe(replay_outcome),
        })
    total = len(rows)
    deterministic = sum(1 for row in rows if row["ok"])
    original_issue_turns = sum(
        1
        for row in rows
        if not bool(row["original_execution"].get("ok"))
    )
    original_failed_tool_calls = sum(
        int(row["original_execution"].get("failed_tool_count") or 0)
        for row in rows
    )
    original_provider_errors = sum(
        int(row["original_execution"].get("provider_error_count") or 0)
        for row in rows
    )
    original_unknown_side_effects = sum(
        int(row["original_execution"].get("unknown_side_effect_count") or 0)
        for row in rows
    )
    original_task_failures = sum(
        1
        for row in rows
        if isinstance(row.get("original_task"), dict)
        and row["original_task"].get("status") == "failed"
    )
    replay_task_failures = sum(
        1
        for row in rows
        if isinstance(row.get("replay_task"), dict)
        and row["replay_task"].get("status") == "failed"
    )
    trace_comparable_turns = sum(1 for row in rows if row["trace_comparable"])
    trace_diff_turns = sum(1 for row in rows if row["trace_diffs"])
    benchmark_summary = getattr(controller, "last_benchmark_summary", None)
    if not isinstance(benchmark_summary, dict):
        benchmark_rows = [
            row["benchmark"] for row in rows if isinstance(row.get("benchmark"), dict)
        ]
        elapsed = [int(row.get("elapsed_ms") or 0) for row in benchmark_rows]
        total_elapsed = sum(elapsed)
        benchmark_summary = {
            "turns": len(benchmark_rows),
            "total_elapsed_ms": total_elapsed,
            "average_elapsed_ms": round(total_elapsed / len(elapsed), 1) if elapsed else 0,
            "fastest_elapsed_ms": min(elapsed, default=0),
            "slowest_elapsed_ms": max(elapsed, default=0),
            "trace_comparable_turns": trace_comparable_turns,
            "trace_diff_turns": trace_diff_turns,
        }
    return {
        "directory": directory,
        "total_turns": total,
        "deterministic_turns": deterministic,
        "all_deterministic": total > 0 and deterministic == total,
        "original_issue_turns": original_issue_turns,
        "original_failed_tool_calls": original_failed_tool_calls,
        "original_provider_errors": original_provider_errors,
        "original_unknown_side_effects": original_unknown_side_effects,
        "original_task_failures": original_task_failures,
        "replay_task_failures": replay_task_failures,
        "trace_comparable_turns": trace_comparable_turns,
        "trace_diff_turns": trace_diff_turns,
        "benchmark": _json_safe(benchmark_summary),
        "summary": (
            f"{deterministic}/{total} 个回合回放一致；"
            f"{original_issue_turns} 个回合原始执行包含问题；"
            f"{original_task_failures} 个回合任务验收未通过"
            if total
            else "没有可以检查的回合"
        ),
        "results": rows,
    }


async def _tokens(
    agent: Any,
    session_manager: Any,
    payload: dict[str, Any],
) -> dict[str, Any]:
    from pawbot.agent.token_estimation import count_prompt_tokens

    session_key = payload.get("session_key")
    messages: list[dict[str, Any]] = []
    if session_key and session_manager is not None:
        session = session_manager.get_or_create(session_key)
        messages = list(getattr(session, "messages", None) or [])
    tools: list[dict[str, Any]] = []
    tool_registry = getattr(agent, "tools", None)
    get_definitions = getattr(tool_registry, "get_definitions", None)
    if callable(get_definitions):
        raw_tools = get_definitions()
        if isinstance(raw_tools, list):
            typed_tools = cast(list[Any], raw_tools)
            tools = [
                cast(dict[str, Any], item)
                for item in typed_tools
                if isinstance(item, dict)
            ]
    estimated = count_prompt_tokens(messages, tools=tools, model=agent.model)
    runtime = agent.llm_runtime()
    context_window = runtime.context_window_tokens
    return {
        "session_key": session_key,
        "model": agent.model,
        "message_count": len(messages),
        "tool_count": len(tools),
        "estimated_tokens": estimated,
        "context_window_tokens": context_window,
        "usage_ratio": round(estimated / context_window, 4) if context_window > 0 else None,
    }


def blackbox_action_factory(
    *,
    agent_getter: Callable[[], Any],
    session_manager: Any,
) -> Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Build the async ``(action, payload) -> dict`` dispatcher."""

    async def dispatch(action: str, payload: dict[str, Any]) -> dict[str, Any]:
        agent = agent_getter()
        if agent is None:
            raise BlackboxActionError(503, "agent is not available")
        if action == "status":
            return await _status(agent)
        if action == "start":
            return await _start(agent, payload)
        if action == "stop":
            return await _stop(agent)
        if action == "list":
            return await _list(agent)
        if action == "detail":
            return await _detail(agent, payload)
        if action == "delete":
            return await _delete(agent, payload)
        if action == "replay":
            return await _replay(agent, payload)
        if action == "tokens":
            return await _tokens(agent, session_manager, payload)
        if action == "trace.list":
            return await _trace_list(agent, payload)
        if action == "trace.detail":
            return await _trace_detail(agent, payload)
        raise BlackboxActionError(400, f"unknown blackbox action {action!r}")

    return dispatch
