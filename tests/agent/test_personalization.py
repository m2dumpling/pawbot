from __future__ import annotations

from pathlib import Path

import pytest

from pawbot.agent.context import ContextBuilder
from pawbot.agent.memory_preferences import ExplicitMemoryStore
from pawbot.agent.personalization import (
    PersonalizationPolicyError,
    PersonalizationStore,
)


def test_personalization_round_trip_and_clear(tmp_path: Path) -> None:
    store = PersonalizationStore(data_root=tmp_path / "runtime")
    saved = store.update(
        instructions="始终使用简体中文回答。",
        use_memories=False,
        generate_memories=False,
    )

    assert saved.instructions == "始终使用简体中文回答。"
    assert store.read().use_memories is False
    assert store.read().generate_memories is False
    assert store.clear_instructions().instructions == ""


def test_personalization_rejects_obvious_credentials(tmp_path: Path) -> None:
    store = PersonalizationStore(data_root=tmp_path / "runtime")

    with pytest.raises(PersonalizationPolicyError):
        store.update(instructions="api_key=sk-12345678901234567890")


def test_context_injects_personalization_and_respects_memory_switch(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    runtime = tmp_path / "runtime"
    builder = ContextBuilder(workspace, memory_data_root=runtime)
    builder.personalization.update(instructions="始终使用简体中文回答。")
    ExplicitMemoryStore(workspace, data_root=runtime).remember(
        scope="global",
        kind="preference",
        key="reply_language",
        value="zh-CN",
    )

    prompt = builder.build_system_prompt(include_memory_recent_history=False)
    assert "始终使用简体中文回答。" in prompt
    assert "reply_language: zh-CN" in prompt

    builder.personalization.update(use_memories=False)
    disabled_prompt = builder.build_system_prompt(include_memory_recent_history=False)
    assert "始终使用简体中文回答。" in disabled_prompt
    assert "reply_language: zh-CN" not in disabled_prompt


def test_memory_clear_keeps_auditable_tombstones(tmp_path: Path) -> None:
    store = ExplicitMemoryStore(tmp_path / "workspace", data_root=tmp_path / "runtime")
    store.remember(scope="global", kind="preference", key="reply_language", value="zh-CN")
    store.remember(scope="workspace", kind="preference", key="response_style", value="concise")

    assert store.clear_all() == 2
    assert store.list_records() == []
    assert "operation\":\"delete" in store.global_path.read_text(encoding="utf-8")
    assert "operation\":\"delete" in store.workspace_path.read_text(encoding="utf-8")
