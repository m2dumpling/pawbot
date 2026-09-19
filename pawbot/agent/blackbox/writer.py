"""Durable append helpers for opt-in detailed regression samples."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from filelock import FileLock


def tighten_permissions(path: Path, *, directory: bool = False) -> None:
    """Best-effort private permissions; unsupported platforms keep working."""
    try:
        os.chmod(path, 0o700 if directory else 0o600)
    except OSError:
        pass


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append one durable JSONL record while serializing cross-process writers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tighten_permissions(path.parent, directory=True)
    lock = FileLock(str(path.with_suffix(path.suffix + ".lock")))
    payload = json.dumps(record, ensure_ascii=False, default=repr, separators=(",", ":")) + "\n"
    with lock:
        with path.open("a", encoding="utf-8") as handle:
            tighten_permissions(path)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Atomically replace a small recording metadata document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tighten_permissions(path.parent, directory=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            tighten_permissions(temporary)
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        tighten_permissions(path)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = ["append_jsonl", "tighten_permissions", "write_json_atomic"]
