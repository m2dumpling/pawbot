"""Stable public envelope for local execution Trace events."""

from __future__ import annotations

from typing import Any, cast

TRACE_SCHEMA_VERSION = 2

_REQUIRED_FIELDS = (
    "schema_version",
    "sequence",
    "event",
    "trace_id",
    "turn_id",
    "channel",
    "chat_id",
    "timestamp_ms",
)


class TraceSchemaError(ValueError):
    """Raised only by strict callers when an event lacks its public envelope."""


def validate_trace_event(value: Any) -> list[str]:
    """Return validation issues without rejecting forward-compatible fields."""
    if not isinstance(value, dict):
        return ["event must be an object"]
    event = cast(dict[str, Any], value)
    issues: list[str] = []
    for field in _REQUIRED_FIELDS:
        if event.get(field) is None:
            issues.append(f"missing {field}")
    if not isinstance(event.get("schema_version"), int):
        issues.append("schema_version must be an integer")
    if not isinstance(event.get("sequence"), int) or event.get("sequence", 0) < 1:
        issues.append("sequence must be a positive integer")
    if not isinstance(event.get("event"), str) or not event["event"].strip():
        issues.append("event must be a non-empty string")
    if event.get("duration_ms") is not None and not isinstance(event.get("duration_ms"), int):
        issues.append("duration_ms must be an integer when present")
    if event.get("error_code") is not None and not isinstance(event.get("error_code"), str):
        issues.append("error_code must be a string when present")
    if event.get("outcome") is not None and not isinstance(event.get("outcome"), dict):
        issues.append("outcome must be an object when present")
    return issues


def normalize_trace_event(value: dict[str, Any], *, strict: bool = False) -> dict[str, Any]:
    """Stamp the current schema version and optionally reject invalid events.

    Trace emits remain intentionally extensible: only the common envelope is
    validated here, while feature-specific fields continue to evolve without a
    giant hierarchy of event classes.
    """
    normalized = dict(value)
    normalized["schema_version"] = TRACE_SCHEMA_VERSION
    issues = validate_trace_event(normalized)
    if strict and issues:
        raise TraceSchemaError("; ".join(issues))
    return normalized


__all__ = ["TRACE_SCHEMA_VERSION", "TraceSchemaError", "normalize_trace_event", "validate_trace_event"]
