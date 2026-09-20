from __future__ import annotations

from pathlib import Path

import pytest

from pawbot.agent.context import ContextBuilder
from pawbot.agent.memory_preferences import ExplicitMemoryStore, MemoryPolicyError


def test_explicit_memory_is_durable_and_workspace_scoped(tmp_path: Path) -> None:
    data_root = tmp_path / "runtime"
    first = ExplicitMemoryStore(tmp_path / "project-a", data_root=data_root)

    saved = first.remember(
        scope="global",
        kind="preference",
        key="reply_language",
        value="简体中文",
    )
    first.remember(
        scope="workspace",
        kind="preference",
        key="response_style",
        value="简洁",
    )

    restarted = ExplicitMemoryStore(tmp_path / "project-a", data_root=data_root)
    assert [item.memory_id for item in restarted.list_records(status="confirmed")] == [
        saved.memory_id,
        next(
            item.memory_id
            for item in restarted.list_records(status="confirmed")
            if item.key == "response_style"
        ),
    ]
    assert "reply_language: zh-CN" in restarted.confirmed_for_prompt()
    assert "response_style: concise" in restarted.confirmed_for_prompt()

    other_workspace = ExplicitMemoryStore(tmp_path / "project-b", data_root=data_root)
    assert [item.key for item in other_workspace.list_records(scope="workspace")] == []
    assert [item.key for item in other_workspace.list_records(scope="global")] == [
        "reply_language"
    ]


def test_explicit_memory_upserts_by_scope_kind_and_key(tmp_path: Path) -> None:
    store = ExplicitMemoryStore(tmp_path / "project", data_root=tmp_path / "runtime")
    first = store.remember(
        scope="global",
        kind="preference",
        key="reply_language",
        value="en",
    )
    second = store.remember(
        scope="global",
        kind="preference",
        key="reply_language",
        value="zh-CN",
    )

    assert first.memory_id == second.memory_id
    current = store.list_records(status="confirmed")
    assert len(current) == 1
    assert current[0].value == "zh-CN"


def test_explicit_memory_forget_is_audited_and_stops_injection(tmp_path: Path) -> None:
    store = ExplicitMemoryStore(tmp_path / "project", data_root=tmp_path / "runtime")
    record = store.remember(
        scope="global",
        kind="preference",
        key="reply_language",
        value="zh-CN",
    )

    assert store.forget(record.memory_id) is True
    assert store.list_records(status="confirmed") == []
    assert store.forget(record.memory_id) is False
    assert "reply_language" not in store.confirmed_for_prompt()


def test_explicit_memory_rejects_secrets(tmp_path: Path) -> None:
    store = ExplicitMemoryStore(tmp_path / "project", data_root=tmp_path / "runtime")

    with pytest.raises(MemoryPolicyError):
        store.remember(
            scope="global",
            kind="preference",
            key="api_key",
            value="do-not-save",
        )
    with pytest.raises(MemoryPolicyError):
        store.remember(
            scope="global",
            kind="fact",
            key="provider",
            value="sk-12345678901234567890",
        )


def test_candidate_lifecycle_preserves_explicit_confirmation(tmp_path: Path) -> None:
    store = ExplicitMemoryStore(tmp_path / "project", data_root=tmp_path / "runtime")
    candidate = store.remember(
        scope="workspace",
        kind="fact",
        key="primary_provider",
        value="deepseek",
        source="dream",
        status="candidate",
        confidence=0.8,
        evidence="project setup",
    )
    assert store.list_records(status="candidate")[0].evidence == "project setup"
    promoted = store.promote(candidate.memory_id)
    assert promoted is not None
    assert promoted.status == "confirmed"
    assert promoted.confidence == 1.0
    assert store.list_records(status="candidate") == []


def test_context_builder_injects_confirmed_memory(tmp_path: Path) -> None:
    builder = ContextBuilder(
        tmp_path / "workspace",
        memory_data_root=tmp_path / "runtime",
    )
    builder.explicit_memory.remember(
        scope="global",
        kind="preference",
        key="reply_language",
        value="zh-CN",
    )

    prompt = builder.build_system_prompt(include_memory_recent_history=False)

    assert "# Confirmed User Preferences" in prompt
    assert "reply_language: zh-CN" in prompt
