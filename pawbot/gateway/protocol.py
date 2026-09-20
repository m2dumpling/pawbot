"""Durable, local Gateway protocol state.

The Gateway is intentionally still a single-process local service.  This module
adds the two reliability primitives needed by reconnecting clients without
introducing a database or a distributed coordinator:

* a bounded event journal with per-stream monotonic sequence numbers;
* a small idempotency ledger for typed Gateway operations.

Both stores are append-safe through ``filelock`` and use atomic metadata
replacement.  Payloads are bounded and common credentials are redacted before
they leave the process.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from filelock import FileLock

_MAX_EVENT_PAYLOAD_CHARS = 2_000
_MAX_EVENT_RECORDS = 4_096
_MAX_EVENTS_PER_STREAM = 512
_OPERATION_TTL_S = 24 * 60 * 60
_SENSITIVE_KEY_RE = re.compile(
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|authorization|cookie|"
    r"password|passwd|secret|private[_-]?key)",
    re.IGNORECASE,
)


def _safe_payload(value: Any, *, depth: int = 0) -> Any:
    """Bound and redact a JSON-like event payload."""

    if depth > 4:
        return "<truncated>"
    if isinstance(value, dict):
        mapping = cast(dict[Any, Any], value)
        return {
            str(key): (
                "<redacted>"
                if _SENSITIVE_KEY_RE.search(str(key))
                else _safe_payload(item, depth=depth + 1)
            )
            for key, item in list(mapping.items())[:64]
        }
    if isinstance(value, (list, tuple)):
        values = cast(list[Any] | tuple[Any, ...], value)
        return [_safe_payload(item, depth=depth + 1) for item in values[:64]]
    if isinstance(value, str):
        return value[:_MAX_EVENT_PAYLOAD_CHARS] + (
            "..." if len(value) > _MAX_EVENT_PAYLOAD_CHARS else ""
        )
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:_MAX_EVENT_PAYLOAD_CHARS]


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class EventReplay:
    events: tuple[dict[str, Any], ...]
    gap: bool
    oldest_seq: int | None
    latest_seq: int


class GatewayEventJournal:
    """Bounded durable event journal keyed by a logical client stream."""

    def __init__(self, path: Path, *, max_events_per_stream: int = _MAX_EVENTS_PER_STREAM):
        self.path = path.expanduser().resolve(strict=False)
        self.state_path = self.path.with_suffix(self.path.suffix + ".state.json")
        self.lock = FileLock(str(self.path) + ".lock")
        self.max_events_per_stream = max(1, max_events_per_stream)

    def _read_events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        try:
            with open(self.path, encoding="utf-8") as handle:
                for line in handle:
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(value, dict):
                        events.append(cast(dict[str, Any], value))
        except OSError:
            return []
        return events

    def _read_sequences(self) -> dict[str, int]:
        if not self.state_path.exists():
            return {}
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(value, dict):
            return {}
        mapping = cast(dict[str, Any], value)
        return {
            str(key): int(raw)
            for key, raw in mapping.items()
            if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0
        }

    def append(self, stream_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        stream = stream_id.strip()[:256] or "gateway"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            sequences = self._read_sequences()
            sequence = sequences.get(stream, 0) + 1
            event = {
                "event_id": f"evt_{uuid.uuid4().hex}",
                "stream_id": stream,
                "seq": sequence,
                "type": event_type[:128],
                "occurred_at": datetime.now(UTC).isoformat(),
                "payload": _safe_payload(payload),
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            sequences[stream] = sequence
            _atomic_write_json(self.state_path, sequences)
            self._compact_locked()
            return event

    def _compact_locked(self) -> None:
        events = self._read_events()
        by_stream: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            stream = event.get("stream_id")
            if isinstance(stream, str):
                by_stream.setdefault(stream, []).append(event)
        if len(events) <= _MAX_EVENT_RECORDS and all(
            len(stream_events) <= self.max_events_per_stream
            for stream_events in by_stream.values()
        ):
            return
        kept: list[dict[str, Any]] = []
        for stream_events in by_stream.values():
            kept.extend(stream_events[-self.max_events_per_stream :])
        kept.sort(key=lambda item: str(item.get("occurred_at", "")))
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(temporary, "w", encoding="utf-8") as handle:
            for event in kept:
                handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    def replay(self, stream_id: str, after_seq: int, *, limit: int = 512) -> EventReplay:
        stream = stream_id.strip()[:256]
        events = [
            event
            for event in self._read_events()
            if event.get("stream_id") == stream and isinstance(event.get("seq"), int)
        ]
        events.sort(key=lambda item: int(item["seq"]))
        latest = self._read_sequences().get(stream, 0)
        oldest = int(events[0]["seq"]) if events else None
        gap = oldest is not None and after_seq + 1 < oldest
        pending = [event for event in events if int(event["seq"]) > after_seq]
        return EventReplay(tuple(pending[:limit]), gap, oldest, latest)

    def validate(self) -> list[str]:
        issues: list[str] = []
        seen: dict[str, int] = {}
        for event in self._read_events():
            stream = event.get("stream_id")
            sequence = event.get("seq")
            if not isinstance(stream, str) or not isinstance(sequence, int):
                issues.append("event has invalid stream_id or seq")
                continue
            previous = seen.get(stream, 0)
            if sequence <= previous:
                issues.append(f"stream {stream} has duplicate or descending seq")
            seen[stream] = sequence
        return issues

    def latest_sequences(self) -> dict[str, int]:
        return self._read_sequences()


@dataclass(frozen=True, slots=True)
class GatewayOperationRecord:
    request_id: str
    action: str
    payload_digest: str
    status: str
    result: dict[str, Any] | None
    started_at: float
    completed_at: float | None
    owner_pid: int


class GatewayOperationLedger:
    """Durable idempotency ledger for typed Gateway mutations."""

    def __init__(self, path: Path):
        self.path = path.expanduser().resolve(strict=False)
        self.lock = FileLock(str(self.path) + ".lock")

    def _read(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return cast(dict[str, dict[str, Any]], value) if isinstance(value, dict) else {}

    def get(self, request_id: str) -> GatewayOperationRecord | None:
        raw = self._read().get(request_id)
        if not isinstance(raw, dict):
            return None
        try:
            return GatewayOperationRecord(
                request_id=request_id,
                action=str(raw["action"]),
                payload_digest=str(raw["payload_digest"]),
                status=str(raw["status"]),
                result=cast(dict[str, Any] | None, raw.get("result")),
                started_at=float(raw["started_at"]),
                completed_at=(
                    float(raw["completed_at"])
                    if raw.get("completed_at") is not None
                    else None
                ),
                owner_pid=int(raw.get("owner_pid", 0)),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def begin(self, request_id: str, action: str, payload_digest: str) -> GatewayOperationRecord:
        now = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            records = self._read()
            record = {
                "action": action,
                "payload_digest": payload_digest,
                "status": "running",
                "result": None,
                "started_at": now,
                "completed_at": None,
                "owner_pid": os.getpid(),
            }
            records[request_id] = record
            _atomic_write_json(self.path, records)
        return GatewayOperationRecord(
            request_id,
            action,
            payload_digest,
            "running",
            None,
            now,
            None,
            os.getpid(),
        )

    def complete(self, request_id: str, result: dict[str, Any]) -> None:
        with self.lock:
            records = self._read()
            current = records.get(request_id)
            if not isinstance(current, dict):
                return
            current["status"] = "completed"
            current["result"] = _safe_payload(result)
            current["completed_at"] = time.time()
            records[request_id] = current
            self._prune(records)
            _atomic_write_json(self.path, records)

    def unknown(self, request_id: str) -> None:
        with self.lock:
            records = self._read()
            current = records.get(request_id)
            if not isinstance(current, dict):
                return
            current["status"] = "unknown_side_effect"
            current["completed_at"] = time.time()
            records[request_id] = current
            _atomic_write_json(self.path, records)

    def _prune(self, records: dict[str, dict[str, Any]]) -> None:
        cutoff = time.time() - _OPERATION_TTL_S
        for request_id, record in tuple(records.items()):
            completed = record.get("completed_at")
            if isinstance(completed, (int, float)) and completed < cutoff:
                records.pop(request_id, None)

    def validate(self) -> list[str]:
        issues: list[str] = []
        for request_id, record in self._read().items():
            if record.get("status") not in {"running", "completed", "unknown_side_effect"}:
                issues.append(f"operation {request_id} has invalid status")
        return issues


__all__ = [
    "EventReplay",
    "GatewayEventJournal",
    "GatewayOperationLedger",
    "GatewayOperationRecord",
]
