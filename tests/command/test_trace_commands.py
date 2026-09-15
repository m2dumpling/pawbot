"""Tests for trace and detailed-recording control commands."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from pawbot.agent.blackbox import BlackboxController
from pawbot.agent.observability import TraceStore
from pawbot.bus.events import InboundMessage
from pawbot.command.builtin import cmd_record, cmd_trace
from pawbot.command.router import CommandContext


def _context(loop: SimpleNamespace, raw: str, args: str = "") -> CommandContext:
    msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content=raw)
    return CommandContext(
        msg=msg,
        session=None,
        key=msg.session_key,
        raw=raw,
        args=args,
        loop=loop,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_trace_command_lists_current_chat_and_errors(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "traces", retention_days=0)
    trace = store.start_turn(
        session_key="cli:direct",
        turn_id="turn-1",
        channel="cli",
        chat_id="direct",
    )
    assert trace is not None
    trace.emit("tool.finished", status="failed", tool_name="exec", duration_ms=2_500)
    trace.finish(status="error", stop_reason="error")
    loop = SimpleNamespace(trace_store=store)

    listed = await cmd_trace(_context(loop, "/trace"))
    assert "turn-1" in listed.content
    assert "issue(s)" in listed.content

    errors = await cmd_trace(_context(loop, "/trace errors", args="errors"))
    assert "with issues" in errors.content
    assert "turn-1" in errors.content

    detail = await cmd_trace(
        _context(loop, "/trace cli_direct/turn-1.jsonl", args="cli_direct/turn-1.jsonl")
    )
    assert "tool.finished" in detail.content
    assert "2.5s" in detail.content


@pytest.mark.asyncio
async def test_record_command_controls_global_recording_window(tmp_path: Path) -> None:
    loop = SimpleNamespace(workspace=tmp_path, blackbox=None)

    status = await cmd_record(_context(loop, "/record"))
    assert "off" in status.content

    started = await cmd_record(_context(loop, "/record start demo", args="start demo"))
    assert "demo" in started.content
    assert isinstance(loop.blackbox, BlackboxController)
    assert loop.blackbox.directory == tmp_path / "blackbox" / "demo"

    duplicate = await cmd_record(_context(loop, "/record start other", args="start other"))
    assert "already active" in duplicate.content

    stopped = await cmd_record(_context(loop, "/record stop", args="stop"))
    assert "demo" in stopped.content
    assert loop.blackbox is None
