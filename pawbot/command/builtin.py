"""Built-in slash command handlers."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from loguru import logger

from pawbot import __version__
from pawbot.bus.events import INBOUND_META_USER_SHELL, OutboundMessage
from pawbot.command.router import CommandContext, CommandRouter, normalize_command_text
from pawbot.providers.base import LLMUsage
from pawbot.utils.helpers import build_status_content
from pawbot.utils.restart import set_restart_notice_to_env
from pawbot.utils.workspace_prompts import initialize_workspace_prompt

if TYPE_CHECKING:
    from pawbot.agent.loop import AgentLoop
    from pawbot.session.manager import Session
    from pawbot.utils.gitstore import CommitInfo

# WebUI protocol contract for how a slash command participates in turn state:
# - side_channel: returns control text without starting or ending an agent turn.
# - finalize_active_turn: side-channel command that also closes the active UI turn.
# - stop_active_turn: cancels the active turn; WebUI may intercept exact submits.
# - agent_turn: always enters the normal agent path.
# - agent_turn_with_args: no args is side-channel usage; args enter the agent path.
CommandLifecycle = Literal[
    "side_channel",
    "finalize_active_turn",
    "stop_active_turn",
    "agent_turn",
    "agent_turn_with_args",
]

USER_SHELL_COMMAND = "/__shell"


@dataclass(frozen=True)
class BuiltinCommandSpec:
    command: str
    title: str
    description: str
    icon: str
    arg_hint: str = ""
    lifecycle: CommandLifecycle = "side_channel"
    accepts_args: bool = False

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "command": self.command,
            "title": self.title,
            "description": self.description,
            "icon": self.icon,
            "arg_hint": self.arg_hint,
            "lifecycle": self.lifecycle,
            "accepts_args": self.accepts_args,
        }


BUILTIN_COMMAND_SPECS: tuple[BuiltinCommandSpec, ...] = (
    BuiltinCommandSpec(
        "/new",
        "New chat",
        "Reset this chat and start a fresh conversation.",
        "square-pen",
        lifecycle="finalize_active_turn",
    ),
    BuiltinCommandSpec(
        "/stop",
        "Stop current task",
        "Cancel the active agent turn for this chat.",
        "square",
        lifecycle="stop_active_turn",
    ),
    BuiltinCommandSpec(
        "/restart",
        "Restart pawbot",
        "Restart the bot process.",
        "rotate-cw",
    ),
    BuiltinCommandSpec(
        "/status",
        "Show status",
        "Display runtime, provider, and channel status.",
        "activity",
    ),
    BuiltinCommandSpec(
        "/model",
        "Show or switch model",
        "Show available models or switch the model for this session.",
        "brain",
        "[model or preset]",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/models",
        "List available models",
        "Refresh the current provider's model catalogue.",
        "list",
        lifecycle="side_channel",
    ),
    BuiltinCommandSpec(
        "/effort",
        "Set thinking effort",
        "Show or set the thinking-effort level for this chat (e.g. low, high, max).",
        "brain-cog",
        "[level]",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/history",
        "Show conversation history",
        "Print the last N persisted conversation messages.",
        "history",
        "[n]",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/trace",
        "Inspect execution traces",
        "Show recent, failed, slow, or one specific execution trace.",
        "activity",
        "[errors|slow|all|trace-id]",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/record",
        "Save a regression sample",
        "Show, start, stop, or keep rolling execution evidence.",
        "radio",
        "[status|start [name]|stop|candidates|keep <id>|reject <id>]",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/eval",
        "Run task evaluation",
        "List or run the provider-free task evaluation set.",
        "file-check-2",
        "[list|run]",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/goal",
        "Start long-running goal",
        "Tell the agent to treat the request as a long-running goal.",
        "activity",
        "<goal>",
        lifecycle="agent_turn_with_args",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/trigger",
        "Create named local trigger",
        "Create a named CLI trigger bound to this chat session.",
        "zap",
        "<name>",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/dream",
        "Run Dream",
        "Manually trigger memory consolidation.",
        "sparkles",
    ),
    BuiltinCommandSpec(
        "/dream-log",
        "Show Dream log",
        "Show what the last Dream consolidation changed.",
        "book-open",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/dream-restore",
        "Restore memory",
        "Revert memory to a previous Dream snapshot.",
        "undo-2",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/dream-prompt",
        "Dream memory",
        "Tell Dream how to organize this workspace's memory.",
        "file-text",
        "[init]",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/remember",
        "Save a memory",
        "Save an explicit global or workspace preference.",
        "bookmark-plus",
        "[global|workspace] <key=value or preference>",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/memory",
        "Manage memories",
        "List confirmed memories or review automatic candidates.",
        "brain",
        "[list|candidates|show <id>|clear]",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/memories",
        "Memory controls",
        "Control memory use and memory generation for this chat.",
        "brain-circuit",
        "[show|use on|off|default|generate on|off|default]",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/forget",
        "Forget a memory",
        "Remove one explicit memory by its id.",
        "bookmark-minus",
        "<memory-id>",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/evaluator-prompt",
        "Heartbeat evaluator",
        "Customize the heartbeat notification gate prompt for this workspace.",
        "file-text",
        "[init]",
        accepts_args=True,
    ),
    BuiltinCommandSpec(
        "/skill",
        "List skills",
        "List all enabled skills available to the agent.",
        "wrench",
    ),
    BuiltinCommandSpec(
        "/help",
        "Show help",
        "List available slash commands.",
        "circle-help",
    ),
    BuiltinCommandSpec(
        "/pairing",
        "Manage pairing",
        "List, approve, deny or revoke pairing requests.",
        "shield",
        "[list|approve <code>|deny <code>|revoke <user_id>]",
        accepts_args=True,
    ),
)


def builtin_command_palette() -> list[dict[str, str | bool]]:
    """Return structured command metadata for UI command palettes."""
    return [spec.as_dict() for spec in BUILTIN_COMMAND_SPECS]


def builtin_command_starts_agent_turn(text: str) -> bool:
    """Return whether WebUI ingress should expect a normal agent lifecycle."""
    normalized = normalize_command_text(text)
    command, separator, args = normalized.partition(" ")
    spec = next(
        (item for item in BUILTIN_COMMAND_SPECS if item.command == command.lower()),
        None,
    )
    if spec is None or (separator and not spec.accepts_args):
        return True
    if spec.lifecycle == "agent_turn":
        return True
    return spec.lifecycle == "agent_turn_with_args" and bool(args.strip())


async def cmd_stop(ctx: CommandContext) -> OutboundMessage:
    """Cancel all active tasks and subagents for the session."""
    loop = ctx.loop
    msg = ctx.msg
    total = await loop._cancel_active_tasks(ctx.key)  # pyright: ignore[reportPrivateUsage]
    # Also drain pending queue to prevent mid-turn injection deadlock
    pending = loop._pending_queues.pop(ctx.key, None)  # pyright: ignore[reportPrivateUsage]
    if pending is not None:
        while not pending.empty():
            try:
                pending.get_nowait()
                total += 1
            except Exception:
                break
    content = f"Stopped {total} task(s)." if total else "No active task to stop."
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content=content,
        metadata=dict(msg.metadata or {})
    )


async def cmd_restart(ctx: CommandContext) -> OutboundMessage:
    """Restart the process."""
    msg = ctx.msg
    set_restart_notice_to_env(
        channel=msg.channel,
        chat_id=msg.chat_id,
        metadata=dict(msg.metadata or {}),
    )

    async def _do_restart():
        await asyncio.sleep(1)
        argv = [sys.executable, "-m", "pawbot"] + sys.argv[1:]
        mode = ctx.loop.restart_mode or "auto"
        if mode == "auto":
            mode = "spawn" if sys.platform == "win32" else "exec"
        if mode == "exec":
            os.execv(sys.executable, argv)
            return
        if mode == "spawn":
            kwargs: dict[str, Any] = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            subprocess.Popen(argv, **kwargs)
        os._exit(0)

    asyncio.create_task(_do_restart())
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content="Restarting...",
        metadata=dict(msg.metadata or {})
    )


async def cmd_effort(ctx: CommandContext) -> OutboundMessage:
    """Show or set the thinking-effort level for this chat."""
    from pawbot.providers.registry import reasoning_effort_values_for

    loop = ctx.loop
    args = ctx.args.strip()
    metadata = {**dict(ctx.msg.metadata or {}), "render_as": "text"}
    session = ctx.session or loop.sessions.get_or_create(ctx.key)

    runtime = loop.runtime_for_session(session, recover_removed=False)
    model = runtime.model
    provider = getattr(runtime.provider, "provider_name", "") or ""
    supported = reasoning_effort_values_for(provider, model)
    current = runtime.generation.reasoning_effort or ""

    if not args:
        lines = [
            "## Thinking effort",
            f"- Current model: `{model}`",
            f"- Current effort: {current or '(provider default)'}",
            "- Supported levels: "
            + (", ".join(f"`{value}`" for value in supported) or "(none)"),
            "- Set with `/effort <level>`, or `/effort default` to reset.",
        ]
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content="\n".join(lines),
            metadata=metadata,
        )

    requested = "default" if args.lower() == "default" else args
    if requested != "default" and requested not in supported:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=(
                f"Unsupported thinking effort `{args}`.\n\n"
                f"Supported levels: "
                + (", ".join(f"`{value}`" for value in supported) or "(none)")
            ),
            metadata=metadata,
        )

    try:
        updated = loop.set_session_reasoning_effort(
            ctx.key,
            "" if requested == "default" else requested,
        )
    except (KeyError, ValueError) as exc:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=f"Could not set thinking effort: {_command_error_message(exc)}",
            metadata=metadata,
        )

    effective = updated.generation.reasoning_effort or ""
    lines = [
        f"Thinking effort set to `{effective or 'provider default'}` for this chat.",
        "- Scope: current session",
        f"- Model: `{model}`",
    ]
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content="\n".join(lines),
        metadata=metadata,
    )


async def cmd_status(ctx: CommandContext) -> OutboundMessage:
    """Build an outbound status message for a session."""
    loop = ctx.loop
    session = ctx.session or loop.sessions.get_or_create(ctx.key)
    runtime = ctx.runtime or loop.runtime_for_session(session)
    ctx_est = 0
    with suppress(Exception):
        ctx_est, _ = loop.consolidator.estimate_session_prompt_tokens(
            session,
            runtime=runtime,
        )
    last_usage = LLMUsage.from_dict(session.metadata.get("_last_usage"))
    if ctx_est <= 0:
        ctx_est = last_usage.input_tokens if last_usage is not None else 0

    # Fetch web search provider usage (best-effort, never blocks the response)
    search_usage_text: str | None = None
    # Never let usage fetch break /status
    with suppress(Exception):
        from pawbot.utils.searchusage import fetch_search_usage
        search_cfg = loop.web_config.search
        usage = await fetch_search_usage(
            provider=search_cfg.provider,
            api_key=search_cfg.api_key or None,
        )
        search_usage_text = usage.format()
    active_tasks = loop._active_tasks.get(ctx.key, [])  # pyright: ignore[reportPrivateUsage]
    task_count = sum(1 for t in active_tasks if not t.done())
    with suppress(Exception):
        task_count += loop.subagents.get_running_count_by_session(ctx.key)
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=build_status_content(
            version=__version__, model=runtime.model,
            start_time=loop._start_time, last_usage=last_usage,  # pyright: ignore[reportPrivateUsage]
            context_window_tokens=runtime.context_window_tokens,
            session_msg_count=len(session.get_history(max_messages=0)),
            context_tokens_estimate=ctx_est,
            search_usage_text=search_usage_text,
            active_task_count=task_count,
            max_completion_tokens=runtime.generation.max_tokens,
        ),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


def _trace_row_text(row: dict[str, Any]) -> str:
    """Render one compact trace row without exposing raw recorded payloads."""
    turn_id = str(row.get("turn_id") or "unknown")
    status = str(row.get("status") or "unknown")
    duration = row.get("duration_ms")
    duration_text = _format_trace_duration(duration)
    tools = int(row.get("tool_count") or 0)
    failures = int(row.get("failure_count") or 0)
    suffix = f" · {tools} tool(s)"
    if failures:
        suffix += f" · {failures} issue(s)"
    return f"- `{turn_id}` — {status} · {duration_text}{suffix}"


def _format_trace_duration(value: Any) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "running"
    milliseconds = max(0, int(value))
    return f"{milliseconds}ms" if milliseconds < 1_000 else f"{milliseconds / 1000:.1f}s"


async def cmd_trace(ctx: CommandContext) -> OutboundMessage:
    """Inspect lightweight execution traces for the active session."""
    store = cast(Any, getattr(ctx.loop, "trace_store", None))
    metadata = {**dict(ctx.msg.metadata or {}), "render_as": "text"}
    if not callable(getattr(store, "list_summaries", None)):
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content="Execution tracing is unavailable for this pawbot instance.",
            metadata=metadata,
        )

    args = ctx.args.strip()
    lowered = args.lower()
    if args and lowered not in {"all", "errors", "slow"}:
        try:
            summary, events = store.detail(args)
        except FileNotFoundError:
            content = f"Trace `{args}` was not found. Run `/trace` to list this chat's traces."
        except (OSError, ValueError) as exc:
            content = f"Could not inspect trace `{args}`: {_command_error_message(exc)}"
        else:
            lines = [
                "## Execution trace",
                _trace_row_text(summary),
                "",
                "### Steps",
            ]
            for event in events:
                event_name = str(event.get("event") or "event")
                status = str(event.get("status") or "")
                duration = event.get("duration_ms")
                duration_text = (
                    f" · {_format_trace_duration(duration)}"
                    if isinstance(duration, (int, float)) and not isinstance(duration, bool)
                    else ""
                )
                tool = event.get("tool_name")
                detail = f" · {tool}" if isinstance(tool, str) and tool else ""
                lines.append(f"- `{event_name}`{detail}{duration_text} {status}".rstrip())
            content = "\n".join(lines)
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=content,
            metadata=metadata,
        )

    traces = store.list_summaries(
        limit=10,
        session_key=None if lowered == "all" else ctx.key,
        issues_only=lowered == "errors",
        slow_only=lowered == "slow",
    )
    scope = "all sessions" if lowered == "all" else "this chat"
    if lowered == "errors":
        scope += " with issues"
    elif lowered == "slow":
        scope += " slower than 2s"
    lines = [f"## Execution traces · {scope}"]
    if traces:
        lines.extend(_trace_row_text(row) for row in traces)
        lines.append("")
        lines.append("Use `/trace <trace-id>` for the structured event list.")
    else:
        lines.append("No matching traces yet.")
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content="\n".join(lines),
        metadata=metadata,
    )


def _recording_name(value: str) -> str:
    compact = "".join(character if character.isalnum() or character in "-_" else "-" for character in value)
    return compact.strip("-_")[:80] or f"session-{int(time.time() * 1000)}"


async def cmd_record(ctx: CommandContext) -> OutboundMessage:
    """Manage the global regression-sample capture window."""
    from pawbot.agent.blackbox import (
        BlackboxController,
        clear_recording_policy,
        finalize_recording_manifest,
        read_recording_policy,
        write_recording_policy,
    )

    loop = ctx.loop
    metadata = {**dict(ctx.msg.metadata or {}), "render_as": "text"}
    args = ctx.args.strip()
    action, _, raw_name = args.partition(" ")
    action = action.lower() or "status"
    active = getattr(loop, "blackbox", None)
    recording = isinstance(active, BlackboxController)
    workspace = Path(loop.workspace)
    policy_directory = read_recording_policy(workspace)

    if action == "status":
        if recording:
            content = (
                f"Regression sample capture is active: `{active.directory.name}`. "
                "Every session is captured until `/record stop`."
            )
        elif policy_directory is not None:
            content = (
                f"Regression sample capture is armed: `{policy_directory.name}`. "
                "The running gateway will capture every subsequent session."
            )
        else:
            content = "Regression sample capture is off. Automatic live execution records remain on for every new turn."
    elif action == "start":
        if recording:
            content = (
                f"Regression sample capture is already active: `{active.directory.name}`. "
                "Run `/record stop` before starting another sample."
            )
        else:
            name = _recording_name(raw_name.strip() or f"session-{int(time.time() * 1000)}")
            start_recording = getattr(loop, "start_detailed_recording", None)
            if callable(start_recording):
                directory = Path(str(start_recording(name)))
            else:
                directory = write_recording_policy(workspace, name)
                loop.blackbox = BlackboxController(str(directory))
            content = (
                f"Started regression sample capture: `{name}`. Every session is included until `/record stop`. "
                "This sample stores prompts, model responses, and tool results."
            )
    elif action == "stop":
        if not recording:
            if policy_directory is None:
                content = "Regression sample capture is already off."
            else:
                finalize_recording_manifest(policy_directory)
                clear_recording_policy(workspace)
                content = "Regression sample capture is now off. Existing samples were kept."
        else:
            name = active.directory.name
            stop_recording = getattr(loop, "stop_detailed_recording", None)
            if callable(stop_recording):
                stop_recording()
            else:
                finalize_recording_manifest(active.directory)
                clear_recording_policy(workspace)
                loop.blackbox = None
            content = f"Stopped regression sample capture: `{name}`. You can validate it offline with `pawbot replay`."
    elif action == "candidates":
        rolling = getattr(loop, "rolling_blackbox", None) or getattr(loop, "_rolling_blackbox", None)
        list_candidates = getattr(rolling, "list_candidates", None)
        candidate_lister = (
            cast(Callable[[], list[dict[str, Any]]], list_candidates)
            if callable(list_candidates)
            else None
        )
        candidates = candidate_lister() if candidate_lister is not None else []
        if candidates:
            lines = ["Candidate problem runs:"]
            for candidate in candidates:
                reasons = ", ".join(str(item) for item in candidate.get("reasons", []))
                lines.append(f"- `{candidate.get('candidate_id', 'unknown')}` · {reasons or 'execution issue'}")
            lines.append("Use `/record keep <candidate-id>` to preserve one as a regression sample.")
            content = "\n".join(lines)
        else:
            content = "No candidate problem runs are waiting for review."
    elif action == "keep":
        candidate_id = raw_name.strip()
        rolling = getattr(loop, "rolling_blackbox", None) or getattr(loop, "_rolling_blackbox", None)
        promote = getattr(rolling, "promote_candidate", None)
        if not candidate_id:
            content = "Usage: `/record keep <candidate-id>`."
        elif not callable(promote):
            content = "Automatic rolling replay is unavailable for this instance."
        else:
            try:
                directory = cast(Path, promote(candidate_id))
            except (OSError, ValueError) as exc:
                content = f"Could not keep candidate: {_command_error_message(exc)}"
            else:
                content = f"Kept `{candidate_id}` as a regression sample: `{directory.name}`."
    elif action == "reject":
        candidate_id = raw_name.strip()
        rolling = getattr(loop, "rolling_blackbox", None) or getattr(loop, "_rolling_blackbox", None)
        reject = getattr(rolling, "reject_candidate", None)
        if not candidate_id:
            content = "Usage: `/record reject <candidate-id>`."
        elif not callable(reject):
            content = "Automatic rolling replay is unavailable for this instance."
        else:
            try:
                directory = cast(Path, reject(candidate_id))
            except (OSError, ValueError) as exc:
                content = f"Could not ignore candidate: {_command_error_message(exc)}"
            else:
                content = f"Ignored `{candidate_id}` and moved it to reviewed evidence: `{directory.name}`."
    else:
        content = "Usage: `/record status`, `/record start [name]`, `/record stop`, `/record candidates`, `/record keep <id>`, or `/record reject <id>`."

    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata=metadata,
    )


async def cmd_eval(ctx: CommandContext) -> OutboundMessage:
    """List or run the provider-free task evaluation set."""
    from pawbot.evals import eval_cases, run_eval

    action = ctx.args.strip().lower() or "list"
    metadata = {**dict(ctx.msg.metadata or {}), "render_as": "text"}
    if action == "list":
        lines = ["## Agent task evaluation set"]
        lines.extend(
            f"- `{case.id}` · {case.category} · {case.description}"
            for case in eval_cases()
        )
        lines.append("Run `/eval run` to execute the fixed provider-free checks.")
        content = "\n".join(lines)
    elif action == "run":
        report = await run_eval()
        summary = report.to_dict()["summary"]
        content = (
            f"Task eval: {summary['task_passed']}/{summary['task_evaluable']} "
            f"evaluable tasks passed · {summary['trajectory_passed']}/{summary['total']} "
            f"trajectories passed · {summary['elapsed_ms']}ms."
        )
    else:
        content = "Usage: `/eval list` or `/eval run`."
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata=metadata,
    )


def _memory_preference_from_text(text: str) -> tuple[str, str] | None:
    """Resolve the small set of deterministic natural-language preferences.

    The explicit command must remain useful without another model request.  A
    later extractor can expand this table for free-form candidates, but an
    unrecognized phrase is rejected instead of being guessed and persisted.
    """

    normalized = " ".join(text.strip().split())
    if not normalized:
        return None
    if "=" in normalized:
        key, value = normalized.split("=", 1)
        if key.strip() and value.strip():
            return key.strip(), value.strip()

    lowered = normalized.lower()
    if any(token in lowered for token in ("简体中文", "中文回复", "用中文")):
        return "reply_language", "zh-CN"
    if any(token in lowered for token in ("英文回复", "英语回复", "用英文")):
        return "reply_language", "en"
    if any(token in lowered for token in ("简洁", "简短", "不要太长")):
        return "response_style", "concise"
    if any(token in lowered for token in ("详细", "展开讲", "讲详细")):
        return "response_style", "detailed"
    return None


async def cmd_remember(ctx: CommandContext) -> OutboundMessage:
    """Persist one explicit preference without waiting for Dream."""

    from pawbot.agent.memory_preferences import MemoryPolicyError

    args = ctx.args.strip()
    first, _, rest = args.partition(" ")
    if first.lower() in {"global", "workspace"}:
        scope = cast(Literal["global", "workspace"], first.lower())
        text = rest.strip()
    else:
        scope = "global"
        text = args

    parsed = _memory_preference_from_text(text)
    extracted = None
    if parsed is None and text and ctx.runtime is not None:
        from pawbot.agent.memory_extractor import extract_memory_candidate

        try:
            extracted = await extract_memory_candidate(ctx.runtime, text, scope_hint=scope)
        except Exception:
            extracted = None
        if extracted is not None:
            parsed = (extracted.key, extracted.value)
    if parsed is None:
        content = (
            "Usage: `/remember [global|workspace] <key=value or preference>`\n\n"
            "Examples:\n"
            "- `/remember global reply_language=zh-CN`\n"
            "- `/remember global 默认使用简体中文回复`\n"
            "- `/remember workspace response_style=concise`"
        )
    else:
        key, value = parsed
        try:
            record = ctx.loop.context.explicit_memory.remember(
                scope=scope,
                kind=extracted.kind if extracted is not None else "preference",
                key=key,
                value=value,
                source="explicit",
                status="confirmed",
                origin_session=ctx.key,
                origin_turn=ctx.msg.metadata.get("turn_id"),
            )
        except MemoryPolicyError as exc:
            content = f"Memory was not saved: {exc}"
        else:
            content = (
                f"已保存为{('全局' if scope == 'global' else '当前工作区')}偏好：\n"
                f"{record.key} = {record.value}\n"
                f"记忆编号：`{record.memory_id}`"
            )
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


async def cmd_memory(ctx: CommandContext) -> OutboundMessage:
    """List or inspect structured explicit and candidate memories."""

    args = ctx.args.strip().split()
    action = args[0].lower() if args else "list"
    store = ctx.loop.context.explicit_memory
    if action in {"list", "confirmed"}:
        records = store.list_records(status="confirmed")
    elif action in {"candidates", "candidate"}:
        records = store.list_records(status="candidate")
    elif action in {"confirm", "promote", "reject", "ignore"} and len(args) == 2:
        candidate_id = args[1]
        record = (
            store.promote(candidate_id)
            if action in {"confirm", "promote"}
            else store.reject(candidate_id)
        )
        content = (
            f"已确认记忆 `{candidate_id}`。"
            if action in {"confirm", "promote"} and record is not None
            else f"已忽略候选记忆 `{candidate_id}`。"
            if action in {"reject", "ignore"} and record is not None
            else f"未找到候选记忆 `{candidate_id}`。"
        )
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=content,
            metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
        )
    elif action == "show" and len(args) == 2:
        records = [
            record
            for record in store.list_records()
            if record.memory_id == args[1]
        ]
    else:
        records = []

    if action == "clear":
        deleted = store.clear_all()
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=f"已清空 {deleted} 条记忆。",
            metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
        )

    if action == "show" and len(args) == 2 and not records:
        content = f"No memory found for `{args[1]}`."
    elif not records:
        if action not in {"list", "confirmed", "candidates", "candidate", "show"}:
            content = (
                "Usage: `/memory list`, `/memory candidates`, `/memory confirm <id>`, "
                "`/memory reject <id>`, or `/memory show <memory-id>`."
            )
        else:
            content = "No matching memories."
    else:
        title = "Confirmed memories" if action in {"list", "confirmed"} else "Memory candidates"
        lines = [f"## {title}"]
        lines.extend(
            f"- `{record.memory_id}` · {record.scope} · {record.key} = {record.value}"
            + (
                f" · confidence={record.confidence:.2f}"
                if action in {"candidates", "candidate"} and record.confidence is not None
                else ""
            )
            for record in records
        )
        content = "\n".join(lines)
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


async def cmd_memories(ctx: CommandContext) -> OutboundMessage:
    """Control memory behavior for the current conversation."""

    from pawbot.agent.personalization import (
        MEMORY_GENERATE_OVERRIDE_METADATA_KEY,
        MEMORY_USE_OVERRIDE_METADATA_KEY,
        memory_generate_override_from_metadata,
        memory_use_override_from_metadata,
    )

    session = ctx.session or ctx.loop.sessions.get_or_create(ctx.key)
    args = ctx.args.strip().lower().split()
    if not args or args[0] == "show":
        state = ctx.loop.context.personalization.read()
        use_override = memory_use_override_from_metadata(session.metadata)
        generate_override = memory_generate_override_from_metadata(session.metadata)
        use_value = state.use_memories if use_override is None else use_override
        generate_value = state.generate_memories if generate_override is None else generate_override
        inherited = "（继承全局）" if use_override is None else "（当前会话覆盖）"
        generated_inherited = "（继承全局）" if generate_override is None else "（当前会话覆盖）"
        content = (
            "当前会话记忆设置：\n"
            f"- 使用已保存记忆：{'开启' if use_value else '关闭'} {inherited}\n"
            f"- 允许生成记忆候选：{'开启' if generate_value else '关闭'} {generated_inherited}\n\n"
            "用法：`/memories use on|off|default`，"
            "`/memories generate on|off|default`"
        )
    elif len(args) == 2 and args[0] in {"use", "generate"} and args[1] in {
        "on", "off", "default"
    }:
        key = (
            MEMORY_USE_OVERRIDE_METADATA_KEY
            if args[0] == "use"
            else MEMORY_GENERATE_OVERRIDE_METADATA_KEY
        )
        if args[1] == "default":
            session.metadata.pop(key, None)
            value = "已恢复全局设置"
        else:
            session.metadata[key] = args[1] == "on"
            value = f"已{'开启' if args[1] == 'on' else '关闭'}当前会话设置"
        ctx.loop.sessions.save(session)
        content = f"{('记忆使用' if args[0] == 'use' else '记忆生成')}：{value}。"
    else:
        content = (
            "用法：`/memories show`、`/memories use on|off|default`、"
            "`/memories generate on|off|default`"
        )
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


async def cmd_forget(ctx: CommandContext) -> OutboundMessage:
    """Tombstone one explicit memory without rewriting its audit history."""

    memory_id = ctx.args.strip()
    if not memory_id:
        content = "Usage: `/forget <memory-id>`"
    elif ctx.loop.context.explicit_memory.forget(memory_id):
        content = f"已删除记忆 `{memory_id}`。"
    else:
        content = f"未找到记忆 `{memory_id}`。"
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


async def cmd_new(ctx: CommandContext) -> OutboundMessage:
    """Stop active task and start a fresh session."""
    loop = ctx.loop
    await loop._cancel_active_tasks(ctx.key)  # pyright: ignore[reportPrivateUsage]
    loop.discard_session_file_state(ctx.key)
    session = ctx.session or loop.sessions.get_or_create(ctx.key)
    snapshot = list(session.messages)
    archive_snapshot = None
    runtime = None
    if session.last_archived < len(snapshot):
        runtime = ctx.runtime or loop.runtime_for_session(session)
        archive_snapshot = replace(
            session,
            messages=snapshot,
            metadata=dict(session.metadata),
            provider_state=None,
        )
    session.clear()
    loop.sessions.save(session)
    loop.sessions.invalidate(session.key)
    if archive_snapshot is not None and runtime is not None:
        loop.schedule_background(
            loop.consolidator.archive_session(  # pyright: ignore[reportUnknownMemberType]
                archive_snapshot,
                archive_end=len(snapshot),
                runtime=runtime,
            )
        )
    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content="New session started.",
        metadata=dict(ctx.msg.metadata or {})
    )


def _format_preset_names(names: list[str]) -> str:
    return ", ".join(f"`{name}`" for name in names) if names else "(none configured)"


def _model_preset_names(loop: AgentLoop) -> list[str]:
    names = set(loop.model_presets)
    names.add("default")
    return ["default", *sorted(name for name in names if name != "default")]


def _command_error_message(exc: Exception) -> str:
    return str(exc.args[0]) if isinstance(exc, KeyError) and exc.args else str(exc)


def _model_command_status(loop: AgentLoop, session: Session) -> str:
    names = _model_preset_names(loop)
    try:
        runtime = loop.runtime_for_session(session, recover_removed=False)
    except (KeyError, ValueError) as exc:
        return "\n".join([
            "## Model",
            f"- Current selection error: {_command_error_message(exc)}",
            f"- Available presets: {_format_preset_names(names)}",
            "- Switch with `/model <preset>`.",
        ])
    active = runtime.model_preset or "default"
    return "\n".join([
        "## Model",
        f"- Current model: `{runtime.model}`",
        f"- Current preset: `{active}`",
        f"- Available presets: {_format_preset_names(names)}",
    ])


def _catalog_model_info(catalog: dict[str, Any], model: str) -> dict[str, Any] | None:
    """Find a live catalogue row without assuming a provider-specific schema."""
    rows_value = catalog.get("models")
    if not isinstance(rows_value, list):
        return None
    rows = cast(list[object], rows_value)
    wanted = model.strip().casefold()
    for row_value in rows:
        if not isinstance(row_value, dict):
            continue
        row = cast(dict[str, Any], row_value)
        model_id = row.get("id")
        if isinstance(model_id, str) and model_id.strip().casefold() == wanted:
            return row
    return None


def _catalog_model_label(row: dict[str, Any]) -> str:
    model_id = str(row.get("id") or "").strip()
    label = row.get("label")
    return str(label).strip() if isinstance(label, str) and label.strip() else model_id


def _format_live_models(catalog: dict[str, Any]) -> list[str]:
    rows_value = catalog.get("models")
    if not isinstance(rows_value, list):
        return []
    rows = cast(list[object], rows_value)
    lines: list[str] = []
    for row_value in rows[:30]:
        if not isinstance(row_value, dict):
            continue
        row = cast(dict[str, Any], row_value)
        model_id = row.get("id")
        if not isinstance(model_id, str) or not model_id.strip():
            continue
        label = _catalog_model_label(row)
        details: list[str] = []
        context = row.get("context_window")
        if isinstance(context, int) and context > 0:
            details.append(f"{context:,} ctx")
        reasoning = row.get("reasoning_effort_values")
        if isinstance(reasoning, list):
            levels = [
                str(value) or "default"
                for value in cast(list[object], reasoning)
                if isinstance(value, str)
            ]
            if levels:
                details.append("thinking: " + "/".join(levels))
        suffix = f" — {' · '.join(details)}" if details else ""
        lines.append(f"- `{model_id}`{f' ({label})' if label != model_id else ''}{suffix}")
    total = catalog.get("model_count")
    if isinstance(total, int) and total > len(lines):
        lines.append(f"- … and {total - len(lines)} more")
    return lines


async def cmd_model(ctx: CommandContext) -> OutboundMessage:
    """Show or switch configured presets and live provider models."""
    loop = ctx.loop
    args = ctx.args.strip()
    metadata = {**dict(ctx.msg.metadata or {}), "render_as": "text"}
    session = ctx.session or loop.sessions.get_or_create(ctx.key)

    if args.casefold() == "default":
        try:
            runtime = loop.set_session_model_preset(ctx.key, "default")
        except (KeyError, ValueError) as exc:
            return OutboundMessage(
                channel=ctx.msg.channel,
                chat_id=ctx.msg.chat_id,
                content=f"Could not switch model preset: {_command_error_message(exc)}",
                metadata=metadata,
            )
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content="\n".join([
                "Switched model preset to `default`.",
                "- Scope: current session",
                f"- Model: `{runtime.model}`",
                f"- Context window: {runtime.context_window_tokens}",
                f"- Max output tokens: {runtime.generation.max_tokens}",
            ]),
            metadata=metadata,
        )

    try:
        current_runtime = loop.runtime_for_session(session, recover_removed=False)
    except (KeyError, ValueError) as exc:
        if not args:
            return OutboundMessage(
                channel=ctx.msg.channel,
                chat_id=ctx.msg.chat_id,
                content=_model_command_status(loop, session),
                metadata=metadata,
            )
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=f"Could not resolve the current model: {_command_error_message(exc)}",
            metadata=metadata,
        )

    provider = getattr(current_runtime.provider, "provider_name", "") or None

    if not args:
        catalog = await loop.discover_models(provider)
        lines = [_model_command_status(loop, session)]
        if catalog.get("status") == "available":
            live_models = _format_live_models(catalog)
            if live_models:
                lines.extend([
                    "",
                    f"## Models from {catalog.get('provider') or provider or 'current provider'}",
                    *live_models,
                    "",
                    "Switch for this session with `/model <model id>`. Use `/model default` to return to the configured model.",
                ])
        elif catalog.get("message"):
            lines.extend(["", f"Live catalogue: {catalog['message']}"])
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content="\n".join(lines),
            metadata=metadata,
        )

    name = args
    preset_names = _model_preset_names(loop)
    is_preset = any(name.casefold() == candidate.casefold() for candidate in preset_names)
    if not is_preset:
        catalog = await loop.discover_models(provider)
        if catalog.get("status") == "unavailable":
            return OutboundMessage(
                channel=ctx.msg.channel,
                chat_id=ctx.msg.chat_id,
                content=(
                    f'Could not switch model preset: model_preset {name!r} not found.\n\n'
                    f"Available presets: {_format_preset_names(preset_names)}"
                ),
                metadata=metadata,
            )
        catalog_row = _catalog_model_info(catalog, name)
        selected_model = (
            str(catalog_row.get("id")).strip()
            if catalog_row is not None and isinstance(catalog_row.get("id"), str)
            else name
        )
        context_window = (
            catalog_row.get("context_window")
            if catalog_row is not None
            else None
        )
        if not isinstance(context_window, int) or context_window <= 0:
            context_window = None
        try:
            runtime = loop.set_session_model(
                ctx.key,
                selected_model,
                provider=provider,
                context_window_tokens=context_window,
            )
        except (KeyError, ValueError) as exc:
            return OutboundMessage(
                channel=ctx.msg.channel,
                chat_id=ctx.msg.chat_id,
                content=(
                    f"Could not switch model: {_command_error_message(exc)}\n\n"
                    f"Available presets: {_format_preset_names(preset_names)}"
                ),
                metadata=metadata,
            )
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=(
                f"Switched this session to model `{runtime.model}`.\n"
                "- Scope: current session\n"
                f"- Provider: `{provider or 'current runtime'}`\n"
                f"- Context window: {runtime.context_window_tokens:,}\n"
                "- This is a session pin; the global configuration was not changed."
            ),
            metadata=metadata,
        )

    try:
        runtime = loop.set_session_model_preset(ctx.key, name)
    except (KeyError, ValueError) as exc:
        names = _model_preset_names(loop)
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=(
                f"Could not switch model preset: {_command_error_message(exc)}\n\n"
                f"Available presets: {_format_preset_names(names)}"
            ),
            metadata=metadata,
        )

    max_tokens = runtime.generation.max_tokens
    lines = [
        f"Switched model preset to `{runtime.model_preset}`.",
        "- Scope: current session",
        f"- Model: `{runtime.model}`",
        f"- Context window: {runtime.context_window_tokens}",
    ]
    lines.append(f"- Max output tokens: {max_tokens}")
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content="\n".join(lines),
        metadata=metadata,
    )


async def cmd_dream(ctx: CommandContext) -> OutboundMessage:
    """Manually trigger a Dream consolidation run."""
    import time

    loop = ctx.loop
    msg = ctx.msg

    async def _run_dream():
        from pawbot.agent.memory import MemoryStore

        async def _silent(*_args: Any, **_kwargs: Any) -> None:
            pass

        dream_session_key = MemoryStore.dream_session_key
        build_dream_commit_message = MemoryStore.build_dream_commit_message
        prune_dream_sessions = MemoryStore.prune_dream_sessions

        store = loop.context.memory
        explicit_store = getattr(loop.context, "explicit_memory", None)
        content = ""
        resp = None
        diff_body = ""
        candidate_count = 0
        t0 = time.monotonic()
        try:
            result = store.build_dream_prompt()
            if result is None:
                await loop.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content=_format_dream_no_input_message(),
                    metadata={"render_as": "text"},
                ))
                return
            prompt, last_cursor = result
            key = dream_session_key()
            dream_runtime = loop.dream_runtime()
            personalization = getattr(loop.context, "personalization", None)
            generation_enabled = (
                personalization.memory_generation_enabled()
                if personalization is not None
                else True
            )
            if dream_runtime is not None and explicit_store is not None and generation_enabled:
                from pawbot.agent.memory_extractor import extract_and_store_dream_candidates

                try:
                    candidate_count = await extract_and_store_dream_candidates(
                        dream_runtime,
                        store.read_unprocessed_history(
                            since_cursor=store.get_last_dream_cursor(),
                        )[:20],
                        explicit_store,
                    )
                except Exception:
                    logger.exception("Dream candidate extraction failed")
            resp = await loop.process_direct(
                prompt,
                session_key=key,
                ephemeral=True,
                tools=store.build_dream_tools(),
                on_progress=_silent,
                runtime=dream_runtime,
            )
            elapsed = time.monotonic() - t0
            # The real file delta grounds the audit record; normal completion
            # decides whether this history batch has finished processing.
            diff_body = store.dream_content_diff()
            completed = MemoryStore.dream_run_completed(resp)
            if completed:
                store.set_last_dream_cursor(last_cursor)
                if diff_body:
                    content = f"Dream completed in {elapsed:.1f}s."
                else:
                    content = f"Dream completed in {elapsed:.1f}s; no memory changes."
                if candidate_count:
                    content += f" {candidate_count} memory candidate(s) await confirmation."
            else:
                reason = MemoryStore.dream_incompletion_reason(resp)
                content = (
                    f"Dream did not complete after {elapsed:.1f}s ({reason}); "
                    "memory cursor was not advanced."
                )
        except Exception as e:
            elapsed = time.monotonic() - t0
            content = f"Dream failed after {elapsed:.1f}s: {e}"
        finally:
            if store.git.is_initialized():
                commit_msg = build_dream_commit_message("dream: manual run", diff_body)
                sha = store.git.auto_commit(commit_msg)
                if sha:
                    content += f" (commit {sha})"
            store.compact_history()
            prune_dream_sessions(loop.sessions)
        await loop.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
        ))

    asyncio.create_task(_run_dream())
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content="Dreaming...",
    )


async def cmd_dream_prompt(ctx: CommandContext) -> OutboundMessage:
    """Show or set up the workspace Dream memory instructions."""
    store = ctx.loop.context.memory
    path = store.dream_prompt_file
    display_path = path.relative_to(store.workspace).as_posix()
    args = ctx.args.strip().lower()

    if args == "init":
        if not initialize_workspace_prompt(path, store.default_dream_prompt()):
            content = (
                f"Dream memory instructions already exist at `{display_path}`.\n\n"
                "Edit that file, or delete/empty it to return to pawbot's default."
            )
        else:
            content = (
                f"Created Dream memory instructions at `{display_path}`.\n\n"
                "Edit that file to teach Dream how to organize memory. "
                "This fully replaces pawbot's default Dream guide for this workspace. "
                "Delete or empty it to return to pawbot's default."
            )
    elif args:
        content = "Usage: /dream-prompt [init]"
    elif store.has_dream_prompt_override():
        content = (
            "Dream memory instructions: custom for this workspace\n\n"
            f"- Path: `{display_path}`\n"
            "- Delete or empty this file to return to pawbot's default."
        )
    else:
        content = (
            "Dream memory instructions: pawbot default\n\n"
            f"- Editable file: `{display_path}`\n"
            "- Run `/dream-prompt init` to create an editable copy."
        )

    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


async def cmd_evaluator_prompt(ctx: CommandContext) -> OutboundMessage:
    """Show or set up the workspace heartbeat evaluator prompt."""
    from pawbot.utils.evaluator import (
        default_evaluator_prompt,
        evaluator_prompt_file,
        has_evaluator_prompt_override,
    )

    workspace = ctx.loop.context.memory.workspace
    path = evaluator_prompt_file(workspace)
    display_path = path.relative_to(workspace).as_posix()
    args = ctx.args.strip().lower()

    if args == "init":
        if not initialize_workspace_prompt(path, default_evaluator_prompt()):
            content = (
                f"Heartbeat evaluator prompt already exists at `{display_path}`.\n\n"
                "Edit that file, or delete/empty it to return to pawbot's default."
            )
        else:
            content = (
                f"Created heartbeat evaluator prompt at `{display_path}`.\n\n"
                "Edit that file to control when the heartbeat notification gate speaks. "
                "It must still instruct the model to call the `evaluate_notification` tool, "
                "otherwise the gate fails closed and stays silent. "
                "Delete or empty it to return to pawbot's default."
            )
    elif args:
        content = "Usage: /evaluator-prompt [init]"
    elif has_evaluator_prompt_override(workspace):
        content = (
            "Heartbeat evaluator prompt: custom for this workspace\n\n"
            f"- Path: `{display_path}`\n"
            "- Delete or empty this file to return to pawbot's default."
        )
    else:
        content = (
            "Heartbeat evaluator prompt: pawbot default\n\n"
            f"- Editable file: `{display_path}`\n"
            "- Run `/evaluator-prompt init` to create an editable copy."
        )

    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


def _format_dream_no_input_message() -> str:
    return "\n".join([
        "Dream has no conversation history to process yet.",
        "",
        "Dream reads new entries from `memory/history.jsonl` after the current Dream cursor.",
        (
            "Short chats only reach that file after token compaction or idle auto-compact, "
            "so a fresh or short WebUI chat may leave Dream with no input."
        ),
        "",
        "Next steps:",
        "- Enable `agents.defaults.idleCompactAfterMinutes` so completed chats become Dream input automatically.",
        "- Compact the current chat into memory once that manual action is available.",
        "- If you expected history to exist, check whether `memory/history.jsonl` has new entries after the Dream cursor.",
        "- Use `/dream-prompt` to see or change how Dream organizes memory.",
    ])


def _extract_changed_files(diff: str) -> list[str]:
    """Extract changed file paths from a unified diff."""
    files: list[str] = []
    seen: set[str] = set()
    for line in diff.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        path = parts[3]
        if path.startswith("b/"):
            path = path[2:]
        if path in seen:
            continue
        seen.add(path)
        files.append(path)
    return files


def _format_changed_files(diff: str) -> str:
    files = _extract_changed_files(diff)
    if not files:
        return "No tracked memory files changed."
    return ", ".join(f"`{path}`" for path in files)


_DREAM_COMMIT_PREFIX = "dream:"


def _format_dream_log_content(
    commit: CommitInfo,
    diff: str,
    *,
    requested_sha: str | None = None,
) -> str:
    files_line = _format_changed_files(diff)
    lines = [
        "## Dream Update",
        "",
        "Here is the selected Dream memory change." if requested_sha else "Here is the latest Dream memory change.",
        "",
        f"- Commit: `{commit.sha}`",
        f"- Time: {commit.timestamp}",
        f"- Changed files: {files_line}",
    ]
    if diff:
        lines.extend([
            "",
            f"Use `/dream-restore {commit.sha}` to undo this change.",
            "",
            "```diff",
            diff.rstrip(),
            "```",
        ])
    else:
        lines.extend([
            "",
            "Dream recorded this version, but there is no file diff to display.",
        ])
    return "\n".join(lines)


def _format_dream_restore_list(commits: list[CommitInfo]) -> str:
    lines = [
        "## Dream Restore",
        "",
        "Choose a Dream memory version to restore. Latest first:",
        "",
    ]
    for c in commits:
        lines.append(f"- `{c.sha}` {c.timestamp} - {c.subject()}")
    lines.extend([
        "",
        "Preview a version with `/dream-log <sha>` before restoring it.",
        "Restore a version with `/dream-restore <sha>`.",
    ])
    return "\n".join(lines)


async def cmd_dream_log(ctx: CommandContext) -> OutboundMessage:
    """Show what the last Dream changed.

    Default: diff of the latest Dream commit versus its parent.
    With /dream-log <sha>: diff of that specific commit.
    """
    store = ctx.loop.consolidator.store
    git = store.git

    if not git.is_initialized():
        if store.get_last_dream_cursor() == 0:
            msg = (
                "Dream has not run yet. Run `/dream`, or wait for the next scheduled Dream cycle.\n\n"
                "Use `/dream-prompt` to see or change how Dream organizes memory."
            )
        else:
            msg = "Dream history is not available because memory versioning is not initialized."
        return OutboundMessage(
            channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
            content=msg, metadata={"render_as": "text"},
        )

    args = ctx.args.strip()

    if args:
        # Show diff of a specific commit
        sha = args.split()[0]
        result = git.show_commit_diff(sha)
        if not result:
            content = (
                f"Couldn't find Dream change `{sha}`.\n\n"
                "Use `/dream-restore` to list recent versions, "
                "or `/dream-log` to inspect the latest one."
            )
        else:
            commit, diff = result
            content = _format_dream_log_content(commit, diff, requested_sha=sha)
    else:
        # Default: show the latest Dream commit's diff
        commits = git.log(max_entries=1, message_prefix=_DREAM_COMMIT_PREFIX)
        result = (
            git.show_commit_diff(
                commits[0].sha,
                max_entries=1,
                message_prefix=_DREAM_COMMIT_PREFIX,
            )
            if commits else None
        )
        if result:
            commit, diff = result
            content = _format_dream_log_content(commit, diff)
        else:
            content = (
                "Dream memory has no saved versions yet.\n\n"
                "Use `/dream-prompt` to see or change how Dream organizes memory."
            )

    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content=content, metadata={"render_as": "text"},
    )


async def cmd_dream_restore(ctx: CommandContext) -> OutboundMessage:
    """Restore memory files from a previous dream commit.

    Usage:
        /dream-restore          — list recent commits
        /dream-restore <sha>    — revert a specific commit
    """
    store = ctx.loop.consolidator.store
    git = store.git
    if not git.is_initialized():
        return OutboundMessage(
            channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
            content="Dream history is not available because memory versioning is not initialized.",
        )

    args = ctx.args.strip()
    if not args:
        # Show recent Dream commits for the user to pick
        commits = git.log(max_entries=10, message_prefix=_DREAM_COMMIT_PREFIX)
        if not commits:
            content = "Dream memory has no saved versions to restore yet."
        else:
            content = _format_dream_restore_list(commits)
    else:
        sha = args.split()[0]
        result = git.show_commit_diff(sha, message_prefix=_DREAM_COMMIT_PREFIX)
        if not result:
            content = (
                f"Couldn't restore Dream change `{sha}`.\n\n"
                "Only Dream memory versions can be restored. "
                "Use `/dream-restore` to list recent versions."
            )
        else:
            changed_files = _format_changed_files(result[1])
            new_sha = git.revert(sha, message_prefix=_DREAM_COMMIT_PREFIX)
            if new_sha:
                content = (
                    f"Restored Dream memory to the state before `{sha}`.\n\n"
                    f"- New safety commit: `{new_sha}`\n"
                    f"- Restored files: {changed_files}\n\n"
                    f"Use `/dream-log {new_sha}` to inspect the restore diff."
                )
            else:
                content = (
                    f"Couldn't restore Dream change `{sha}`.\n\n"
                    "It may be the first saved version with no earlier state to restore."
                )
    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content=content, metadata={"render_as": "text"},
    )


_HISTORY_DEFAULT_COUNT = 10
_HISTORY_MAX_COUNT = 50
_HISTORY_MAX_CONTENT_CHARS = 200


def _format_history_message(msg: dict[str, Any]) -> str | None:
    """Format a single history message for display. Returns None to skip."""
    role = msg.get("role")
    if role not in ("user", "assistant"):
        return None
    content = msg.get("content") or ""
    if isinstance(content, list):
        parts = [
            text
            for block in cast(list[object], content)
            if (item := cast(dict[str, Any], block) if isinstance(block, dict) else None)
            and item.get("type") == "text"
            and isinstance(text := item.get("text"), str)
        ]
        content = " ".join(parts)
    content = str(content).strip()
    if not content:
        return None
    if len(content) > _HISTORY_MAX_CONTENT_CHARS:
        content = content[:_HISTORY_MAX_CONTENT_CHARS] + "…"
    label = "👤 You" if role == "user" else "🤖 Bot"
    return f"{label}: {content}"


async def cmd_history(ctx: CommandContext) -> OutboundMessage:
    """Show the last N messages of the current session (default 10, max 50).

    Usage: /history [count]
    """
    count = _HISTORY_DEFAULT_COUNT
    if ctx.args.strip():
        try:
            count = max(1, min(int(ctx.args.strip()), _HISTORY_MAX_COUNT))
        except ValueError:
            return OutboundMessage(
                channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
                content="Usage: /history [count] — e.g. /history 5 (default: 10, max: 50)",
                metadata=dict(ctx.msg.metadata or {}),
            )

    session = ctx.session or ctx.loop.sessions.get_or_create(ctx.key)
    history = session.get_history(max_messages=0, include_runtime_context=False)
    visible = [_format_history_message(m) for m in history]
    visible = [m for m in visible if m is not None]
    recent = visible[-count:]

    if not recent:
        return OutboundMessage(
            channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
            content="No conversation history yet.",
            metadata=dict(ctx.msg.metadata or {}),
        )

    header = f"Last {len(recent)} message(s):\n"
    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content=header + "\n".join(recent),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


async def cmd_goal(ctx: CommandContext) -> OutboundMessage | None:
    """Mark this turn as an explicit sustained-goal request."""
    from pawbot.agent.goal_permission import goal_mutation_permission

    goal = ctx.args.strip()
    if not goal:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content="Usage: /goal <long-running task description>",
            metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
        )
    if ctx.session is None:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=(
                "A task is already running for this chat. "
                "Use `/stop` first, then send `/goal <long-running task description>` again."
            ),
            metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
        )
    if not ctx.is_user_turn:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content="Goal mode can only be started by a user `/goal <task>` command.",
            metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
        )

    ctx.turn_scopes.append(goal_mutation_permission(True))
    ctx.msg.metadata = {
        **dict(ctx.msg.metadata or {}),
        "original_command": "/goal",
        "original_content": ctx.raw,
        "goal_requested": True,
        "goal_started_at": time.time(),
    }
    ctx.msg.content = ctx.raw
    return None


async def cmd_pairing(ctx: CommandContext) -> OutboundMessage:
    """List, approve, deny or revoke pairing requests."""
    from pawbot.pairing import PAIRING_COMMAND_META_KEY, handle_pairing_command

    reply = handle_pairing_command(ctx.msg.channel, ctx.args)
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=reply,
        metadata={PAIRING_COMMAND_META_KEY: True},
    )


async def cmd_skill(ctx: CommandContext) -> OutboundMessage:
    """List all enabled skills (name and description only)."""
    loop = ctx.loop
    skills = loop.context.skills.list_skills(filter_unavailable=False)
    if not skills:
        content = "No skills available."
    else:
        lines = [f"Available skills ({len(skills)}):", ""]
        for entry in skills:
            desc = loop.context.skills.get_skill_description(entry["name"])
            lines.append(f"- **{entry['name']}** — {desc}")
        content = "\n".join(lines)
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata=dict(ctx.msg.metadata or {}),
    )


async def cmd_trigger(ctx: CommandContext) -> OutboundMessage:
    """Create a local trigger bound to the current session."""
    name = ctx.args.strip()
    if not name:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=(
                "Usage: /trigger <name>\n\n"
                "Create a named local trigger bound to this chat session."
            ),
            metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
        )

    from pawbot.triggers.local_store import LocalTriggerStore

    loop = ctx.loop
    store = loop.local_trigger_store
    if store is None:
        store = LocalTriggerStore(loop.workspace)

    from pawbot.session.keys import UNIFIED_SESSION_KEY

    session_key = (
        ctx.msg.session_key
        if ctx.key == UNIFIED_SESSION_KEY
        else ctx.key
    )
    trigger = store.create(
        name=name,
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        session_key=session_key,
        sender_id="trigger",
        origin_metadata=dict(ctx.msg.metadata or {}),
    )
    command = f'pawbot trigger {trigger.id} "message"'
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=(
            f"Trigger created: {trigger.name}\n"
            f"ID: {trigger.id}\n\n"
            f"Command:\n{command}"
        ),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )

async def cmd_help(ctx: CommandContext) -> OutboundMessage:
    """Return available slash commands."""
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=build_help_text(),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


async def cmd_user_shell(ctx: CommandContext) -> OutboundMessage:
    """Run a trusted local ``!command`` through pawbot's exec policy."""
    metadata = dict(ctx.msg.metadata or {})
    if (
        ctx.msg.channel != "websocket"
        or metadata.get("webui") is not True
        or metadata.get(INBOUND_META_USER_SHELL) is not True
    ):
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content="Shell commands are only available from a trusted local client.",
            metadata={**metadata, "render_as": "text"},
        )
    if not ctx.args.strip():
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content="Type a command after `!`, for example `!pwd`.",
            metadata={**metadata, "render_as": "text"},
        )
    return await ctx.loop.execute_user_shell_command(ctx)


def build_help_text() -> str:
    """Build canonical help text shared across channels."""
    lines = ["🐕 pawbot commands:"]
    for spec in BUILTIN_COMMAND_SPECS:
        command = spec.command
        if spec.arg_hint:
            command = f"{command} {spec.arg_hint}"
        lines.append(f"{command} — {spec.description}")
    return "\n".join(lines)


def register_builtin_commands(router: CommandRouter) -> None:
    """Register the default set of slash commands."""
    router.priority("/stop", cmd_stop)
    router.priority("/restart", cmd_restart)
    router.priority("/status", cmd_status)
    router.exact("/new", cmd_new)
    router.exact("/status", cmd_status)
    router.exact("/model", cmd_model)
    router.prefix("/model ", cmd_model)
    router.exact("/models", cmd_model)
    router.exact("/effort", cmd_effort)
    router.prefix("/effort ", cmd_effort)
    router.exact("/history", cmd_history)
    router.prefix("/history ", cmd_history)
    router.exact("/trace", cmd_trace)
    router.prefix("/trace ", cmd_trace)
    router.exact("/record", cmd_record)
    router.prefix("/record ", cmd_record)
    router.exact("/eval", cmd_eval)
    router.prefix("/eval ", cmd_eval)
    router.exact("/goal", cmd_goal)
    router.prefix("/goal ", cmd_goal)
    router.exact("/trigger", cmd_trigger)
    router.prefix("/trigger ", cmd_trigger)
    router.exact("/dream", cmd_dream)
    router.exact("/dream-log", cmd_dream_log)
    router.prefix("/dream-log ", cmd_dream_log)
    router.exact("/dream-restore", cmd_dream_restore)
    router.prefix("/dream-restore ", cmd_dream_restore)
    router.exact("/dream-prompt", cmd_dream_prompt)
    router.prefix("/dream-prompt ", cmd_dream_prompt)
    router.exact("/remember", cmd_remember)
    router.prefix("/remember ", cmd_remember)
    router.exact("/memory", cmd_memory)
    router.prefix("/memory ", cmd_memory)
    router.exact("/memories", cmd_memories)
    router.prefix("/memories ", cmd_memories)
    router.exact("/forget", cmd_forget)
    router.prefix("/forget ", cmd_forget)
    router.exact("/evaluator-prompt", cmd_evaluator_prompt)
    router.prefix("/evaluator-prompt ", cmd_evaluator_prompt)
    router.exact("/skill", cmd_skill)
    router.exact("/help", cmd_help)
    router.exact("/pairing", cmd_pairing)
    router.prefix("/pairing ", cmd_pairing)
    router.exact(USER_SHELL_COMMAND, cmd_user_shell)
    router.prefix(f"{USER_SHELL_COMMAND} ", cmd_user_shell)
