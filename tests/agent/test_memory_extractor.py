from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from pawbot.agent.loop import AgentLoop
from pawbot.agent.memory_extractor import (
    extract_and_store_dream_candidates,
    extract_memory_candidate,
)
from pawbot.agent.memory_preferences import ExplicitMemoryStore
from pawbot.agent.turn.context import TurnContext, TurnKind
from pawbot.bus.events import InboundMessage
from pawbot.providers.base import LLMResponse
from pawbot.session.manager import Session


class _Provider:
    def __init__(self, content: str):
        self.content = content

    async def chat_with_retry(self, **_kwargs):
        return LLMResponse(content=self.content)


def _runtime(content: str):
    return SimpleNamespace(
        provider=_Provider(content),
        model="fake-model",
        generation=SimpleNamespace(reasoning_effort=None),
    )


@pytest.mark.asyncio
async def test_explicit_extractor_accepts_json_only_response() -> None:
    candidate = await extract_memory_candidate(
        _runtime(
            '{"action":"remember","scope":"global","kind":"preference",'
            '"key":"reply_language","value":"zh-CN","confidence":0.98,"sensitive":false}'
        ),
        "以后请记住用简体中文回复",
    )

    assert candidate is not None
    assert candidate.key == "reply_language"
    assert candidate.scope == "global"
    assert candidate.confidence == 0.98


@pytest.mark.asyncio
async def test_dream_candidates_are_reviewable_and_do_not_replace_confirmed(tmp_path: Path) -> None:
    store = ExplicitMemoryStore(tmp_path / "workspace", data_root=tmp_path / "runtime")
    confirmed = store.remember(
        scope="global",
        kind="preference",
        key="reply_language",
        value="zh-CN",
    )
    saved = await extract_and_store_dream_candidates(
        _runtime(
            '{"items":[{"action":"remember","scope":"global","kind":"preference",'
            '"key":"reply_language","value":"en","confidence":0.72,'
            '"sensitive":false,"evidence":"old message"},'
            '{"action":"remember","scope":"workspace","kind":"fact",'
            '"key":"primary_provider","value":"deepseek","confidence":0.81,'
            '"sensitive":false,"evidence":"project setup"}]}'
        ),
        [{"timestamp": "now", "content": "old conversation"}],
        store,
    )

    assert saved == 2
    current = store.list_records()
    assert next(item for item in current if item.memory_id == confirmed.memory_id).value == "zh-CN"
    candidate = next(item for item in current if item.key == "primary_provider")
    assert candidate.status == "candidate"
    assert candidate.confidence == 0.81

    promoted = store.promote(candidate.memory_id)
    assert promoted is not None
    assert promoted.status == "confirmed"
    assert store.reject(confirmed.memory_id) is not None


@pytest.mark.asyncio
async def test_natural_language_memory_requires_confirmation_and_survives_session_state(
    tmp_path: Path,
) -> None:
    store = ExplicitMemoryStore(tmp_path / "workspace", data_root=tmp_path / "runtime")
    session = Session(key="cli:memory")
    saves = 0

    class _Sessions:
        def save(self, _session: Session) -> None:
            nonlocal saves
            saves += 1

    fake_loop = SimpleNamespace(
        context=SimpleNamespace(explicit_memory=store),
        sessions=_Sessions(),
    )
    runtime = _runtime(
        '{"action":"remember","scope":"global","kind":"preference",'
        '"key":"response_style","value":"concise","confidence":0.97,"sensitive":false}'
    )
    first = TurnContext(
        msg=InboundMessage(channel="cli", sender_id="u", chat_id="memory", content="以后请记住我喜欢简洁回答"),
        session_key=session.key,
        turn_id="turn-1",
        runtime=runtime,
        kind=TurnKind.USER,
        delivery=None,  # type: ignore[arg-type]
        session=session,
    )

    candidate_response = await AgentLoop._maybe_handle_memory_intent(fake_loop, first)  # type: ignore[arg-type]

    assert candidate_response is not None
    candidate = store.list_records(status="candidate")[0]
    assert candidate.source == "natural_language"
    assert candidate.trust == "candidate"
    assert "session:cli:memory" in candidate.evidence_refs
    assert "turn:turn-1" in candidate.evidence_refs
    assert "回复“确认”" in candidate_response.content
    assert session.metadata["_pending_memory_id"] == candidate.memory_id

    second = TurnContext(
        msg=InboundMessage(channel="cli", sender_id="u", chat_id="memory", content="确认"),
        session_key=session.key,
        turn_id="turn-2",
        runtime=runtime,
        kind=TurnKind.USER,
        delivery=None,  # type: ignore[arg-type]
        session=session,
    )
    confirmed_response = await AgentLoop._maybe_handle_memory_intent(fake_loop, second)  # type: ignore[arg-type]

    assert confirmed_response is not None
    assert store.list_records(status="confirmed")[0].key == "response_style"
    assert store.list_records(status="candidate") == []
    assert "_pending_memory_id" not in session.metadata
    assert saves >= 2
