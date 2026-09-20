from __future__ import annotations

import json
from pathlib import Path

from pawbot.gateway.protocol import GatewayEventJournal, GatewayOperationLedger


def test_event_journal_assigns_monotonic_sequences_and_replays(tmp_path: Path) -> None:
    journal = GatewayEventJournal(tmp_path / "run" / "events.jsonl", max_events_per_stream=2)

    first = journal.append("client:chat", "message", {"event": "message", "text": "one"})
    second = journal.append("client:chat", "message", {"event": "message", "text": "two"})
    third = journal.append("client:chat", "message", {"event": "message", "text": "three"})

    assert first["seq"] == 1
    assert second["seq"] == 2
    assert third["seq"] == 3
    replay = journal.replay("client:chat", 1)
    assert [event["seq"] for event in replay.events] == [2, 3]
    assert replay.gap is False

    gap = journal.replay("client:chat", 0)
    assert gap.gap is True
    assert gap.oldest_seq == 2
    assert journal.latest_sequences()["client:chat"] == 3


def test_event_journal_redacts_sensitive_payloads_and_doctor_can_validate(tmp_path: Path) -> None:
    journal = GatewayEventJournal(tmp_path / "events.jsonl")
    journal.append(
        "client:chat",
        "settings.updated",
        {"event": "settings.updated", "api_key": "secret-value", "nested": {"token": "x"}},
    )

    raw = json.loads(journal.path.read_text(encoding="utf-8").splitlines()[0])
    assert raw["payload"]["api_key"] == "<redacted>"
    assert raw["payload"]["nested"]["token"] == "<redacted>"
    assert journal.validate() == []


def test_operation_ledger_reuses_completed_result_and_marks_unknown(tmp_path: Path) -> None:
    ledger = GatewayOperationLedger(tmp_path / "gateway.operations.json")
    started = ledger.begin("req-1", "sidebar.update", "digest-1")
    assert started.status == "running"
    assert ledger.get("req-1") is not None

    ledger.complete("req-1", {"result": {"saved": True}, "status": None, "message": None})
    completed = ledger.get("req-1")
    assert completed is not None
    assert completed.status == "completed"
    assert completed.result == {"result": {"saved": True}, "status": None, "message": None}

    ledger.begin("req-2", "memory.remember", "digest-2")
    ledger.unknown("req-2")
    unknown = ledger.get("req-2")
    assert unknown is not None
    assert unknown.status == "unknown_side_effect"
    assert ledger.validate() == []
