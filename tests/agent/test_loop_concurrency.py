from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _provider() -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(
        max_tokens=4096,
        temperature=0.1,
        reasoning_effort=None,
    )
    return provider


def test_request_concurrency_is_unlimited_by_default(
    monkeypatch: pytest.MonkeyPatch,
    loop_factory,
) -> None:
    monkeypatch.delenv("PAWBOT_MAX_CONCURRENT_REQUESTS", raising=False)

    loop = loop_factory(provider=_provider(), patch_deps=True)

    assert loop._concurrency_gate is None
    assert loop._max_concurrent_per_sender == 0


@pytest.mark.asyncio
async def test_positive_request_concurrency_keeps_explicit_cap(
    monkeypatch: pytest.MonkeyPatch,
    loop_factory,
) -> None:
    monkeypatch.setenv("PAWBOT_MAX_CONCURRENT_REQUESTS", "2")
    loop = loop_factory(provider=_provider(), patch_deps=True)
    gate = loop._concurrency_gate

    assert gate is not None
    for _ in range(2):
        await gate.acquire()
    try:
        assert gate.locked()
    finally:
        for _ in range(2):
            gate.release()


def test_positive_sender_concurrency_creates_a_separate_identity_gate(
    monkeypatch: pytest.MonkeyPatch,
    loop_factory,
) -> None:
    monkeypatch.setenv("PAWBOT_MAX_CONCURRENT_PER_SENDER", "1")
    loop = loop_factory(provider=_provider(), patch_deps=True)

    assert loop._max_concurrent_per_sender == 1
    assert loop._sender_concurrency_gates == {}
