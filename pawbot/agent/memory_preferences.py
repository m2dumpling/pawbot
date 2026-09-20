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
MemorySource = Literal[
    "explicit",
    "natural_language",
    "dream",
    "system",
    "session",
    "tool",
    "external",
]
MemoryTrust = Literal["trusted", "candidate", "untrusted"]
MemoryValue = Any

_SENSITIVE_KEY_RE = re.compile(
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|"
    r"authorization|private[_-]?key|client[_-]?secret|"
    r"密码|口令|密钥|令牌|访问令牌|刷新令牌|授权|私钥|凭证|秘密)",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|"
    r"Bearer\s+[A-Za-z0-9._~+/=-]{16,})",
    re.IGNORECASE,
)
_MAX_KEY_LENGTH = 120
_MAX_VALUE_LENGTH = 2_000
_MAX_EVIDENCE_REFS = 64
_MAX_EVIDENCE_REF_LENGTH = 240

_KEY_ALIASES = {
    "用户称呼": "user_name",
    "用户名字": "user_name",
    "昵称": "user_name",
    "称呼": "user_name",
    "名字": "user_name",
    "回复语言": "reply_language",
    "回答语言": "reply_language",
    "回复风格": "response_style",
    "回答风格": "response_style",
    "时区": "timezone",
    "密码": "password",
    "口令": "password",
    "密钥": "secret",
    "令牌": "token",
}


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
    trust: MemoryTrust = "candidate"
    created_at: str
    updated_at: str
    origin_session: str | None = None
    origin_turn: str | None = None
    confidence: float | None = None
    evidence: str | None = None
    evidence_refs: tuple[str, ...] = ()
    content_hash: str = ""
    supersedes: str | None = None
    expires_at: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def workspace_identity(workspace: Path) -> str:
    """Return a stable, non-reversible namespace for one workspace path."""

    canonical = str(workspace.expanduser().resolve(strict=False))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def _normalize_key(key: str) -> str:
    raw = " ".join(key.strip().split())
    raw = _KEY_ALIASES.get(raw.casefold(), raw)
    # Keep Unicode letters and numbers so an explicit command such as
    # ``/remember global 用户称呼=大海星`` remains a valid structured record.
    # Canonical aliases above keep common fields stable across Chinese and
    # English input, while arbitrary user-defined labels remain readable.
    normalized = re.sub(r"[^\w.-]+", "_", raw.casefold(), flags=re.UNICODE).strip("_.-")
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


def _default_trust(source: MemorySource, status: MemoryStatus) -> MemoryTrust:
    """Derive trust from the admission lane, never from model output."""

    if source in {"tool", "external", "session"}:
        return "untrusted"
    if status == "confirmed" and source in {"explicit", "system"}:
        return "trusted"
    return "candidate"


def _normalize_evidence_refs(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise MemoryPolicyError("memory evidence_refs must be a list")
    refs: list[str] = []
    values = cast(list[Any] | tuple[Any, ...], raw)
    for item in values[:_MAX_EVIDENCE_REFS]:
        if not isinstance(item, str):
            raise MemoryPolicyError("memory evidence_refs must contain strings")
        value = item.strip()
        if not value:
            continue
        if len(value) > _MAX_EVIDENCE_REF_LENGTH:
            raise MemoryPolicyError("memory evidence reference is too long")
        refs.append(value)
    return tuple(dict.fromkeys(refs))


def _content_hash(
    *,
    scope: MemoryScope,
    kind: MemoryKind,
    key: str,
    value: MemoryValue,
) -> str:
    payload = json.dumps(
        {"scope": scope, "kind": kind, "key": key, "value": value},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
                raw_record = dict(cast(dict[str, Any], event["record"]))
                # Upgrade records written before provenance fields existed.
                # Confirmed explicit records retain their old meaning rather
                # than silently becoming candidate memory.
                # Trust is derived on every read.  A serialized record or an
                # LLM-proposed payload cannot self-upgrade by setting
                # ``trust=trusted``.
                raw_record["trust"] = _default_trust(
                    cast(MemorySource, raw_record.get("source", "explicit")),
                    cast(MemoryStatus, raw_record.get("status", "candidate")),
                )
                raw_record["content_hash"] = _content_hash(
                    scope=cast(MemoryScope, raw_record.get("scope", "workspace")),
                    kind=cast(MemoryKind, raw_record.get("kind", "fact")),
                    key=str(raw_record.get("key", "")),
                    value=raw_record.get("value"),
                )
                record = MemoryRecord.model_validate(raw_record)
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
        evidence_refs: list[str] | tuple[str, ...] | None = None,
        supersedes: str | None = None,
        memory_id: str | None = None,
        expires_at: str | None = None,
    ) -> MemoryRecord:
        normalized_key = _normalize_key(key)
        normalized_value = _normalize_value(normalized_key, value)
        if _contains_sensitive_data(normalized_key, normalized_value):
            raise MemoryPolicyError("explicit memory cannot contain secrets or credentials")
        if source in {"tool", "external", "session"} and status == "confirmed":
            raise MemoryPolicyError("untrusted content cannot become confirmed memory")

        normalized_evidence_refs = _normalize_evidence_refs(evidence_refs)
        trust = _default_trust(source, status)
        digest = _content_hash(
            scope=scope,
            kind=kind,
            key=normalized_key,
            value=normalized_value,
        )

        now = _now()
        previous = (
            next((item for item in self.list_records() if item.memory_id == memory_id), None)
            if memory_id is not None
            else self._find_by_key(scope, kind, normalized_key)
        )
        supersedes_id = supersedes
        if previous is not None and previous.trust == "untrusted" and status == "confirmed":
            if memory_id is not None:
                raise MemoryPolicyError(
                    "untrusted memory must be re-expressed before confirmation"
                )
            # A new explicit record may replace untrusted evidence, but it
            # must receive a new identity and retain the lineage.
            supersedes_id = supersedes_id or previous.memory_id
            previous = None
        if previous is not None and status != "confirmed" and previous.status == "confirmed":
            # Auto or pending candidates must never overwrite an explicit fact.
            supersedes_id = supersedes_id or previous.memory_id
            previous = None
        record = MemoryRecord(
            memory_id=previous.memory_id if previous is not None else memory_id or f"mem_{uuid4().hex}",
            scope=scope,
            kind=kind,
            key=normalized_key,
            value=normalized_value,
            status=status,
            source=source,
            trust=trust,
            created_at=previous.created_at if previous is not None else now,
            updated_at=now,
            origin_session=origin_session,
            origin_turn=origin_turn,
            confidence=confidence if confidence is not None else (1.0 if status == "confirmed" else None),
            evidence=evidence,
            evidence_refs=normalized_evidence_refs,
            content_hash=digest,
            supersedes=supersedes_id,
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

    def clear_all(self) -> int:
        """Append user deletion events for every current memory record."""

        deleted = 0
        for scope_value in ("global", "workspace"):
            path = self.path_for_scope(scope_value)
            current = self._current_for_path(path)
            if not current:
                continue
            now = _now()
            for memory_id in current:
                self._append_event(
                    path,
                    {
                        "event_id": f"mem_evt_{uuid4().hex}",
                        "operation": "delete",
                        "memory_id": memory_id,
                        "source": "user",
                        "created_at": now,
                    },
                )
                deleted += 1
        return deleted

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
            evidence_refs=current.evidence_refs,
            supersedes=current.supersedes,
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
        if current.trust == "untrusted":
            # External/tool/session evidence is never promoted directly.  It
            # must first be re-expressed and explicitly confirmed by the user.
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
            evidence_refs=current.evidence_refs,
            supersedes=current.supersedes,
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

        records = [
            record
            for record in self.list_records(status="confirmed")
            if record.trust == "trusted"
        ]
        selected: dict[str, MemoryRecord] = {}
        for record in records:
            current = selected.get(record.key)
            if current is None or record.scope == "workspace":
                selected[record.key] = record

        lines = [
            f"- {record.key}: {record.value} "
            f"[scope={record.scope}; source={record.source}; trust={record.trust}]"
            for record in sorted(selected.values(), key=lambda item: item.key)[:max_items]
        ]
        rendered = "\n".join(lines)
        if len(rendered) > max_chars:
            rendered = rendered[:max_chars].rstrip() + "\n- ..."
        return rendered
