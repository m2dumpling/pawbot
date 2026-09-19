from __future__ import annotations

import pytest

from pawbot.agent.trace_schema import TraceSchemaError, normalize_trace_event, validate_trace_event


def _event() -> dict[str, object]:
    return {
        "schema_version": 1,
        "sequence": 1,
        "event": "turn.completed",
        "trace_id": "trace:1",
        "turn_id": "turn-1",
        "session_key": "cli:direct",
        "channel": "cli",
        "chat_id": "direct",
        "timestamp_ms": 1,
    }


def test_normalize_stamps_current_schema_without_dropping_extension_fields() -> None:
    row = normalize_trace_event({**_event(), "future_field": {"kept": True}})

    assert row["schema_version"] == 2
    assert row["future_field"] == {"kept": True}
    assert validate_trace_event(row) == []


def test_strict_validation_rejects_missing_public_envelope() -> None:
    with pytest.raises(TraceSchemaError, match="missing turn_id"):
        normalize_trace_event({"sequence": 1, "event": "bad"}, strict=True)


def test_validation_accepts_old_schema_rows_for_reader_compatibility() -> None:
    assert validate_trace_event(_event()) == []
