"""Explicit, structured memory for durable user and workspace preferences.

This module intentionally sits beside :mod:`pawbot.agent.memory` rather than
replacing it.  ``MemoryStore`` and Dream continue to manage human-readable,
automatically consolidated memory files.  ``ExplicitMemoryStore`` is the
high-confidence lane for facts the user explicitly asked Pawbot to remember.

The model may propose a record, but only this module validates and persists it.
That boundary prevents an LLM response from silently rewriting durable memory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from filelock import FileLock
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from pawbot.config.paths import get_data_dir
from pawbot.utils.helpers import ensure_dir

MemoryScope = Literal["global", "workspace"]
MemoryKind = Literal["preference", "fact", "decision", "habit"]
MemoryStatus = Literal["confirmed", "candidate", "rejected"]
MemorySource = Literal["explicit", "dream", "system"]
MemoryValue = Any

_SENSITIVE_KEY_RE = re.compile(
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|"
    r"authorization|private[_-]?key|client[_-]?secret)",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|"
    r"Bearer\s+[A-Za-z0-9._~+/=-]{16,})",
    re.IGNORECASE,
)
_MAX_KEY_LENGTH = 120
_MAX_VALUE_LENGTH = 2_000


class MemoryPolicyError(ValueError):
    """Raised when a proposed memory violates the persistence policy."""


class MemoryRecord(BaseModel):
    """A durable memory item after validation and normalization."""

    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(min_length=4, max_length=96)
    scope: MemoryScope
    kind: MemoryKind
    key: str = Field(min_length=1, max_length=_MAX_KEY_LENGTH)
    value: MemoryValue
    status: MemoryStatus
    source: MemorySource
    created_at: str
    updated_at: str
    origin_session: str | None = None
    origin_turn: str | None = None
    confidence: float | None = None
    evidence: str | None = None
    expires_at: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def workspace_identity(workspace: Path) -> str:
    """Return a stable, non-reversible namespace for one workspace path."""

    canonical = str(workspace.expanduser().resolve(strict=False))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def _normalize_key(key: str) -> str:
    normalized = re.sub(r"[^a-z0-9_.-]+", "_", key.strip().lower()).strip("_.-")
    if not normalized:
        raise MemoryPolicyError("memory key cannot be empty")
    if len(normalized) > _MAX_KEY_LENGTH:
        raise MemoryPolicyError("memory key is too long")
    return normalized


def _normalize_value(key: str, value: MemoryValue) -> MemoryValue:
    if isinstance(value, str):
        normalized = " ".join(value.strip().split())
        if len(normalized) > _MAX_VALUE_LENGTH:
            raise MemoryPolicyError("memory value is too long")

        aliases: dict[str, dict[str, str]] = {
            "reply_language": {
                "中文": "zh-CN",
                "简体中文": "zh-CN",
                "中文（简体）": "zh-CN",
                "chinese": "zh-CN",
                "simplified chinese": "zh-CN",
                "英语": "en",
                "英文": "en",
                "english": "en",
            },
            "response_style": {
                "简洁": "concise",
                "简短": "concise",
                "concise": "concise",
                "详细": "detailed",
                "详细一些": "detailed",
                "detailed": "detailed",
            },
        }
        return aliases.get(key, {}).get(normalized.lower(), normalized)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        values = cast(list[Any], value)
        if len(values) > 100:
            raise MemoryPolicyError("memory list value is too large")
        return values
    if isinstance(value, dict):
        mapping = cast(dict[str, Any], value)
        if len(mapping) > 100:
            raise MemoryPolicyError("memory object value is too large")
        return mapping
    raise MemoryPolicyError("memory value must be JSON-serializable")


def _contains_sensitive_data(key: str, value: MemoryValue) -> bool:
    if _SENSITIVE_KEY_RE.search(key):
        return True
    if isinstance(value, str) and _SECRET_VALUE_RE.search(value):
        return True
    return False


class ExplicitMemoryStore:
    """Persist confirmed and candidate memories outside the project checkout.

    The file is append-only at the event level.  The latest event for each
    ``memory_id`` is the current state, which makes updates and deletions
    auditable while keeping normal writes small and crash-safe.
    """

    def __init__(self, workspace: Path, *, data_root: Path | None = None) -> None:
        self.workspace = workspace.expanduser().resolve(strict=False)
        root = (data_root or get_data_dir()).expanduser().resolve(strict=False)
        self.data_root = ensure_dir(root)
        self.profile_dir = ensure_dir(root / "profile")
        self.workspaces_dir = ensure_dir(root / "workspaces")
        self.global_path = self.profile_dir / "preferences.jsonl"
        self.workspace_path = (
            ensure_dir(self.workspaces_dir / workspace_identity(self.workspace))
            / "preferences.jsonl"
        )

    def path_for_scope(self, scope: MemoryScope) -> Path:
        return self.global_path if scope == "global" else self.workspace_path

    def _lock_for(self, path: Path) -> FileLock:
        return FileLock(str(path.with_suffix(path.suffix + ".lock")))

    def _read_events(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        events: list[dict[str, Any]] = []
        try:
            with open(path, encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        raw: object = json.loads(stripped)
                        if isinstance(raw, dict):
                            events.append(cast(dict[str, Any], raw))
                    except json.JSONDecodeError:
                        logger.warning(
                            "Ignoring malformed explicit memory event at {}:{}",
                            path,
                            line_number,
                        )
        except OSError as exc:
            logger.warning("Could not read explicit memory file {}: {}", path, exc)
        return events

    def _current_for_path(self, path: Path) -> dict[str, MemoryRecord]:
        current: dict[str, MemoryRecord] = {}
        for event in self._read_events(path):
            operation = event.get("operation")
            memory_id = event.get("memory_id")
            if not isinstance(memory_id, str):
                continue
            if operation == "delete":
                current.pop(memory_id, None)
                continue
            if operation != "upsert" or not isinstance(event.get("record"), dict):
                continue
            try:
                record = MemoryRecord.model_validate(event["record"])
            except Exception as exc:
                logger.warning("Ignoring invalid explicit memory record {}: {}", memory_id, exc)
                continue
            current[memory_id] = record
        return current

    def _append_event(self, path: Path, event: dict[str, Any]) -> None:
        ensure_dir(path.parent)
        payload = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock_for(path):
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())

    def _find_by_key(self, scope: MemoryScope, kind: MemoryKind, key: str) -> MemoryRecord | None:
        for record in self.list_records(scope=scope):
            if record.kind == kind and record.key == key and record.status != "rejected":
                return record
        return None

    def remember(
        self,
        *,
        scope: MemoryScope,
        kind: MemoryKind,
        key: str,
        value: MemoryValue,
        source: MemorySource = "explicit",
        status: MemoryStatus = "confirmed",
        origin_session: str | None = None,
        origin_turn: str | None = None,
        confidence: float | None = None,
        evidence: str | None = None,
        memory_id: str | None = None,
        expires_at: str | None = None,
    ) -> MemoryRecord:
        normalized_key = _normalize_key(key)
        normalized_value = _normalize_value(normalized_key, value)
        if _contains_sensitive_data(normalized_key, normalized_value):
            raise MemoryPolicyError("explicit memory cannot contain secrets or credentials")

        now = _now()
        previous = (
            next((item for item in self.list_records() if item.memory_id == memory_id), None)
            if memory_id is not None
            else self._find_by_key(scope, kind, normalized_key)
        )
        if previous is not None and status != "confirmed" and previous.status == "confirmed":
            # Auto or pending candidates must never overwrite an explicit fact.
            previous = None
        record = MemoryRecord(
            memory_id=previous.memory_id if previous is not None else memory_id or f"mem_{uuid4().hex}",
            scope=scope,
            kind=kind,
            key=normalized_key,
            value=normalized_value,
            status=status,
            source=source,
            created_at=previous.created_at if previous is not None else now,
            updated_at=now,
            origin_session=origin_session,
            origin_turn=origin_turn,
            confidence=confidence if confidence is not None else (1.0 if status == "confirmed" else None),
            evidence=evidence,
            expires_at=expires_at,
        )
        self._append_event(
            self.path_for_scope(scope),
            {
                "event_id": f"mem_evt_{uuid4().hex}",
                "operation": "upsert",
                "memory_id": record.memory_id,
                "record": record.model_dump(mode="json"),
                "created_at": now,
            },
        )
        return record

    def forget(self, memory_id: str) -> bool:
        for scope_value in ("global", "workspace"):
            current = self._current_for_path(self.path_for_scope(scope_value))
            if memory_id not in current:
                continue
            now = _now()
            self._append_event(
                self.path_for_scope(scope_value),
                {
                    "event_id": f"mem_evt_{uuid4().hex}",
                    "operation": "delete",
                    "memory_id": memory_id,
                    "source": "user",
                    "created_at": now,
                },
            )
            return True
        return False

    def remember_note(
        self,
        *,
        scope: MemoryScope,
        text: str,
        source: MemorySource = "explicit",
        origin_session: str | None = None,
        origin_turn: str | None = None,
    ) -> MemoryRecord:
        """Save a confirmed free-form fact without overwriting another note."""

        normalized = " ".join(text.strip().split())
        if not normalized:
            raise MemoryPolicyError("memory note cannot be empty")
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
        return self.remember(
            scope=scope,
            kind="fact",
            key=f"note_{digest}",
            value=normalized,
            source=source,
            status="confirmed",
            origin_session=origin_session,
            origin_turn=origin_turn,
        )

    def update_status(
        self,
        memory_id: str,
        status: MemoryStatus,
        *,
        source: MemorySource | None = None,
    ) -> MemoryRecord | None:
        """Change a candidate's lifecycle state while retaining its history."""

        current = next(
            (record for record in self.list_records() if record.memory_id == memory_id),
            None,
        )
        if current is None:
            return None
        return self.remember(
            scope=current.scope,
            kind=current.kind,
            key=current.key,
            value=current.value,
            source=source or current.source,
            status=status,
            origin_session=current.origin_session,
            origin_turn=current.origin_turn,
            confidence=current.confidence,
            evidence=current.evidence,
            memory_id=memory_id,
            expires_at=current.expires_at,
        )

    def promote(self, memory_id: str) -> MemoryRecord | None:
        current = next(
            (record for record in self.list_records() if record.memory_id == memory_id),
            None,
        )
        if current is None:
            return None
        return self.remember(
            scope=current.scope,
            kind=current.kind,
            key=current.key,
            value=current.value,
            source="explicit",
            status="confirmed",
            origin_session=current.origin_session,
            origin_turn=current.origin_turn,
            confidence=1.0,
            evidence=current.evidence,
            memory_id=memory_id,
            expires_at=current.expires_at,
        )

    def reject(self, memory_id: str) -> MemoryRecord | None:
        return self.update_status(memory_id, "rejected")

    def list_records(
        self,
        *,
        scope: MemoryScope | None = None,
        status: MemoryStatus | None = None,
    ) -> list[MemoryRecord]:
        paths = (
            [self.path_for_scope(scope)]
            if scope is not None
            else [self.global_path, self.workspace_path]
        )
        records: dict[str, MemoryRecord] = {}
        for path in paths:
            records.update(self._current_for_path(path))
        values = list(records.values())
        if status is not None:
            values = [record for record in values if record.status == status]
        return sorted(values, key=lambda record: (record.scope, record.key, record.updated_at))

    def confirmed_for_prompt(self, *, max_items: int = 64, max_chars: int = 4_000) -> str:
        """Render confirmed memories with workspace records overriding global ones."""

        records = [record for record in self.list_records(status="confirmed")]
        selected: dict[str, MemoryRecord] = {}
        for record in records:
            current = selected.get(record.key)
            if current is None or record.scope == "workspace":
                selected[record.key] = record

        lines = [
            f"- {record.key}: {record.value}"
            for record in sorted(selected.values(), key=lambda item: item.key)[:max_items]
        ]
        rendered = "\n".join(lines)
        if len(rendered) > max_chars:
            rendered = rendered[:max_chars].rstrip() + "\n- ..."
        return rendered
