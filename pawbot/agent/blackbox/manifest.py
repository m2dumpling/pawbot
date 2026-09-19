"""Health manifests for opt-in Record & Replay samples."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Literal, cast

from pawbot.agent.blackbox.writer import tighten_permissions, write_json_atomic

MANIFEST_FILENAME = "manifest.json"
MANIFEST_SCHEMA_VERSION = 1
RecordingHealth = Literal["recording", "ready", "incomplete", "corrupted", "legacy_unverified"]


def manifest_path(directory: Path) -> Path:
    return directory / MANIFEST_FILENAME


def _safe_jsonl(path: Path) -> tuple[list[dict[str, Any]] | None, bool]:
    if not path.exists():
        return [], False
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    return None, True
                rows.append(cast(dict[str, Any], value))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, True
    return rows, False


def _file_digest(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def initialize_recording_manifest(directory: Path) -> dict[str, Any]:
    """Create an in-progress manifest without touching existing samples."""
    directory.mkdir(parents=True, exist_ok=True)
    tighten_permissions(directory, directory=True)
    path = manifest_path(directory)
    existing = read_recording_manifest(directory)
    if existing is not None:
        active = dict(existing)
        active["sample_status"] = "recording"
        active["finished_at_ms"] = None
        write_json_atomic(path, active)
        return active
    payload: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "sample_status": "recording",
        "started_at_ms": int(time.time() * 1000),
        "finished_at_ms": None,
        "contains_sensitive_data": True,
        "turns": [],
        "files": {},
    }
    write_json_atomic(path, payload)
    return payload


def read_recording_manifest(directory: Path) -> dict[str, Any] | None:
    path = manifest_path(directory)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return cast(dict[str, Any], raw) if isinstance(raw, dict) else None


def recording_health(directory: Path) -> dict[str, Any]:
    """Return health without treating a legacy sample as corrupted."""
    manifest = read_recording_manifest(directory)
    if manifest is None:
        return {"sample_health": "legacy_unverified", "manifest": None}
    raw_status = manifest.get("sample_status")
    status: RecordingHealth = cast(
        RecordingHealth,
        raw_status
        if raw_status in {"recording", "ready", "incomplete", "corrupted"}
        else "corrupted",
    )
    return {"sample_health": status, "manifest": manifest}


def finalize_recording_manifest(directory: Path) -> dict[str, Any]:
    """Validate rails and atomically mark a stopped sample ready or unhealthy."""
    directory.mkdir(parents=True, exist_ok=True)
    tighten_permissions(directory, directory=True)
    turns, turns_invalid = _safe_jsonl(directory / "turns.jsonl")
    tools, tools_invalid = _safe_jsonl(directory / "tools.jsonl")
    events, events_invalid = _safe_jsonl(directory / "events.jsonl")
    malformed = turns_invalid or tools_invalid or events_invalid
    turn_rows = turns or []
    complete_turns = [
        row for row in turn_rows
        if row.get("kind") == "turn" and row.get("complete") is not False
    ]
    incomplete_turns = [
        row for row in turn_rows
        if row.get("kind") == "turn" and row.get("complete") is False
    ]
    turn_summaries = [
        {
            "turn_id": row.get("turn_id"),
            "complete": row.get("complete") is not False,
            "llm_response_count": sum(
                1
                for record in tools or []
                if record.get("kind") == "llm" and record.get("turn_id") == row.get("turn_id")
            ),
            "tool_record_count": sum(
                1
                for record in tools or []
                if record.get("kind") == "tool" and record.get("turn_id") == row.get("turn_id")
            ),
            "trace_event_count": sum(
                1 for record in events or [] if record.get("turn_id") == row.get("turn_id")
            ),
        }
        for row in complete_turns
    ]
    missing_llm_rail = any(row["llm_response_count"] == 0 for row in turn_summaries)
    if malformed:
        status: RecordingHealth = "corrupted"
    elif not complete_turns or incomplete_turns or missing_llm_rail:
        status = "incomplete"
    else:
        status = "ready"

    files: dict[str, Any] = {}
    for filename in ("turns.jsonl", "tools.jsonl", "events.jsonl"):
        path = directory / filename
        if path.exists() and path.is_file():
            try:
                files[filename] = _file_digest(path)
            except OSError:
                status = "corrupted"
    payload: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "sample_status": status,
        "started_at_ms": (
            read_recording_manifest(directory) or {}
        ).get("started_at_ms"),
        "finished_at_ms": int(time.time() * 1000),
        "contains_sensitive_data": True,
        "turns": turn_summaries,
        "files": files,
    }
    write_json_atomic(manifest_path(directory), payload)
    return payload


def validate_recording_manifest(directory: Path) -> RecordingHealth:
    """Check the final manifest's file hashes before offline replay."""
    health = recording_health(directory)
    status = cast(RecordingHealth, health["sample_health"])
    if status != "ready":
        return status
    manifest = cast(dict[str, Any], health["manifest"])
    files = manifest.get("files")
    if not isinstance(files, dict):
        return "corrupted"
    for filename, expected in cast(dict[str, Any], files).items():
        if not isinstance(expected, dict):
            return "corrupted"
        path = directory / filename
        try:
            actual = _file_digest(path)
        except OSError:
            return "corrupted"
        if actual != expected:
            return "corrupted"
    return "ready"


__all__ = [
    "MANIFEST_FILENAME",
    "MANIFEST_SCHEMA_VERSION",
    "RecordingHealth",
    "finalize_recording_manifest",
    "initialize_recording_manifest",
    "manifest_path",
    "read_recording_manifest",
    "recording_health",
    "validate_recording_manifest",
]
