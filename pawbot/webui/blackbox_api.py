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
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast


class BlackboxActionError(Exception):
    """Raised for invalid blackbox actions; carries an HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


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
    turns_path = directory / "turns.jsonl"
    if not turns_path.exists():
        return {
            "status": "invalid",
            "message": "录制文件缺失，无法回放",
            "turns": 0,
        }

    valid_turns = 0
    malformed_lines = 0
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
    except (OSError, UnicodeError):
        return {
            "status": "invalid",
            "message": "录制文件无法读取",
            "turns": 0,
        }

    if malformed_lines:
        return {
            "status": "invalid",
            "message": "录制文件格式损坏，无法回放",
            "turns": valid_turns,
        }
    if valid_turns == 0:
        return {
            "status": "invalid",
            "message": "没有有效的 Turn 记录，无法回放",
            "turns": 0,
        }
    return {
        "status": "ready",
        "message": "可回放",
        "turns": valid_turns,
    }


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


def _turn_diagnostics(turn: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a compact explanation layer above the raw replay rails."""
    tool_events = [event for event in events if event.get("kind") == "tool"]
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
    return {
        "stop_reason": turn.get("stop_reason") or "unknown",
        "failed_tools": failed_tools,
        "unknown_side_effects": unknown_side_effects,
        "budget": turn.get("budget"),
        "message": (
            "本轮正常结束"
            if not failed_tools and not unknown_side_effects
            else "本轮包含失败工具或未确认副作用，请先查看对应工具记录"
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
    cassette = _cassette_name(turn_id)
    return {
        "directory": str(directory),
        "turn_id": turn_id,
        "turn": _json_safe(turn),
        "events": _json_safe(events),
        "diagnostics": _json_safe(_turn_diagnostics(turn, events)),
        "counts": {
            "llm_responses": sum(1 for event in events if event.get("kind") == "llm"),
            "tool_calls": sum(1 for event in events if event.get("kind") == "tool"),
        },
        "files": {
            "turns": "turns.jsonl",
            "tools": "tools.jsonl" if (directory / "tools.jsonl").exists() else None,
            "cassette": cassette if (directory / cassette).exists() else None,
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
    directory = _resolve_directory(agent, name)
    agent.blackbox = BlackboxController(str(directory))
    return {"recording": True, "directory": str(directory)}


async def _stop(agent: Any) -> dict[str, Any]:
    agent.blackbox = None
    return {"recording": False}


async def _list(agent: Any) -> dict[str, Any]:
    root = _blackbox_root(agent)
    recordings: list[dict[str, Any]] = []
    if root.exists():
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            summary = _recording_summary(entry)
            recordings.append({
                "directory": str(entry),
                "name": entry.name,
                **summary,
            })
    return {"recordings": recordings, "root": str(root)}


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
    from pawbot.agent.blackbox import ReplayBreakpoint, ReplayController

    directory_name = str(payload.get("directory") or "")
    if not directory_name:
        raise BlackboxActionError(400, "directory is required for replay")
    directory = str(_resolve_directory(agent, directory_name))
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
            f"这个录制无法回放：{exc}",
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
    rows = [
        {
            "turn_id": turn_id,
            "ok": ok,
            "diffs": _json_safe(diffs),
            "summary": (
                "可见回答、工具调用和消息结构一致"
                if ok
                else f"发现 {len(diffs)} 处可观察差异，请展开查看"
            ),
        }
        for turn_id, ok, diffs in results
    ]
    total = len(rows)
    deterministic = sum(1 for row in rows if row["ok"])
    return {
        "directory": directory,
        "total_turns": total,
        "deterministic_turns": deterministic,
        "all_deterministic": total > 0 and deterministic == total,
        "summary": (
            f"{deterministic}/{total} 个回合的可观察行为一致"
            if total
            else "没有可回放的回合"
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
        raise BlackboxActionError(400, f"unknown blackbox action {action!r}")

    return dispatch
