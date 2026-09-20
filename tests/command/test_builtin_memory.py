from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from pawbot.agent.memory_preferences import ExplicitMemoryStore
from pawbot.bus.events import InboundMessage
from pawbot.command.builtin import cmd_forget, cmd_memory, cmd_remember
from pawbot.command.router import CommandContext


def _context(tmp_path: Path, args: str) -> CommandContext:
    msg = InboundMessage(
        channel="cli",
        sender_id="user",
        chat_id="memory-test",
        content=f"/remember {args}",
    )
    store = ExplicitMemoryStore(tmp_path / "workspace", data_root=tmp_path / "runtime")
    loop = SimpleNamespace(context=SimpleNamespace(explicit_memory=store))
    return CommandContext(
        msg=msg,
        session=None,
        key=msg.session_key,
        raw=f"/remember {args}",
        args=args,
        loop=loop,
    )


@pytest.mark.asyncio
async def test_remember_command_persists_without_llm(tmp_path: Path) -> None:
    ctx = _context(tmp_path, "global 默认使用简体中文回复")

    result = await cmd_remember(ctx)

    assert "已保存" in result.content
    records = ctx.loop.context.explicit_memory.list_records(status="confirmed")
    assert len(records) == 1
    assert records[0].key == "reply_language"
    assert records[0].value == "zh-CN"


@pytest.mark.asyncio
async def test_memory_and_forget_commands_manage_saved_record(tmp_path: Path) -> None:
    ctx = _context(tmp_path, "global response_style=concise")
    saved = await cmd_remember(ctx)
    memory_id = ctx.loop.context.explicit_memory.list_records()[0].memory_id

    listed = await cmd_memory(
        CommandContext(
            msg=ctx.msg,
            session=None,
            key=ctx.key,
            raw="/memory list",
            args="list",
            loop=ctx.loop,
        )
    )
    assert "response_style" in listed.content
    assert "concise" in saved.content

    forgotten = await cmd_forget(
        CommandContext(
            msg=ctx.msg,
            session=None,
            key=ctx.key,
            raw=f"/forget {memory_id}",
            args=memory_id,
            loop=ctx.loop,
        )
    )
    assert "已删除" in forgotten.content
    assert ctx.loop.context.explicit_memory.list_records(status="confirmed") == []
