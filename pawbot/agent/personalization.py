"""User-authored personalization and memory policy.

Personalization is intentionally separate from ``ExplicitMemoryStore``:

* personalization is a small, user-authored instruction block;
* memories are structured records with provenance and lifecycle state;
* neither layer can replace the runtime safety contract or project rules.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from filelock import FileLock

from pawbot.config.paths import get_data_dir
from pawbot.utils.helpers import ensure_dir

_MAX_INSTRUCTIONS_CHARS = 16_000
_SECRET_RE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|"
    r"Bearer\s+[A-Za-z0-9._~+/=-]{16,}|(?:api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|password|passwd|secret|private[_-]?key)\s*[:=])",
    re.IGNORECASE,
)
_UNSET = object()
MEMORY_USE_OVERRIDE_METADATA_KEY = "_memory_use"
MEMORY_GENERATE_OVERRIDE_METADATA_KEY = "_memory_generate"


class PersonalizationPolicyError(ValueError):
    """Raised when a personalization update violates the local policy."""


@dataclass(frozen=True)
class PersonalizationState:
    enabled: bool = True
    instructions: str = ""
    use_memories: bool = True
    generate_memories: bool = True
    updated_at: str | None = None
    content_hash: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "enabled": self.enabled,
            "instructions": self.instructions,
            "use_memories": self.use_memories,
            "generate_memories": self.generate_memories,
            "updated_at": self.updated_at,
            "content_hash": self.content_hash,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_hash(instructions: str) -> str:
    return hashlib.sha256(instructions.encode("utf-8")).hexdigest()[:16]


class PersonalizationStore:
    """Crash-safe storage for global user instructions and memory switches."""

    def __init__(self, *, data_root: Path | None = None) -> None:
        root = (data_root or get_data_dir()).expanduser().resolve(strict=False)
        self.data_root = ensure_dir(root)
        self.profile_dir = ensure_dir(root / "profile")
        self.path = self.profile_dir / "personalization.json"
        self._lock = FileLock(str(self.path.with_suffix(".json.lock")))

    @staticmethod
    def _default_payload() -> dict[str, Any]:
        return PersonalizationState().as_dict()

    @staticmethod
    def _normalize(raw: object) -> PersonalizationState:
        mapping: dict[str, Any] = (
            cast(dict[str, Any], raw) if isinstance(raw, dict) else {}
        )
        instructions = mapping.get("instructions")
        instructions = instructions if isinstance(instructions, str) else ""
        instructions = instructions.strip()
        if len(instructions) > _MAX_INSTRUCTIONS_CHARS:
            instructions = instructions[:_MAX_INSTRUCTIONS_CHARS].rstrip()
        enabled = mapping.get("enabled")
        use_memories = mapping.get("use_memories")
        generate_memories = mapping.get("generate_memories")
        updated_at = mapping.get("updated_at")
        content_hash = mapping.get("content_hash")
        return PersonalizationState(
            enabled=enabled if isinstance(enabled, bool) else True,
            instructions=instructions,
            use_memories=use_memories if isinstance(use_memories, bool) else True,
            generate_memories=(
                generate_memories if isinstance(generate_memories, bool) else True
            ),
            updated_at=updated_at if isinstance(updated_at, str) else None,
            content_hash=(
                content_hash
                if isinstance(content_hash, str) and content_hash
                else _content_hash(instructions)
            ),
        )

    def read(self) -> PersonalizationState:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return PersonalizationState()
        return self._normalize(raw)

    def _write(self, state: PersonalizationState) -> PersonalizationState:
        payload = json.dumps(
            state.as_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        with open(tmp, "wb") as handle:
            handle.write(payload)
            handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.path)
        return state

    def update(
        self,
        *,
        instructions: object = _UNSET,
        enabled: object = _UNSET,
        use_memories: object = _UNSET,
        generate_memories: object = _UNSET,
    ) -> PersonalizationState:
        with self._lock:
            current = self.read()
            next_instructions = current.instructions
            if instructions is not _UNSET:
                if not isinstance(instructions, str):
                    raise PersonalizationPolicyError("instructions must be a string")
                # Preserve paragraph boundaries while normalizing only trailing
                # whitespace; the text is user-authored and should remain readable.
                next_instructions = instructions.strip()
                if len(next_instructions) > _MAX_INSTRUCTIONS_CHARS:
                    raise PersonalizationPolicyError(
                        f"instructions exceed {_MAX_INSTRUCTIONS_CHARS} characters"
                    )
                if _SECRET_RE.search(next_instructions):
                    raise PersonalizationPolicyError(
                        "personalization instructions must not contain credentials or API keys"
                    )

            def resolve_bool(name: str, current_value: bool, requested: object) -> bool:
                if requested is _UNSET:
                    return current_value
                if not isinstance(requested, bool):
                    raise PersonalizationPolicyError(f"{name} must be a boolean")
                return requested

            next_enabled = resolve_bool("enabled", current.enabled, enabled)
            next_use_memories = resolve_bool(
                "use_memories", current.use_memories, use_memories
            )
            next_generate = resolve_bool(
                "generate_memories", current.generate_memories, generate_memories
            )

            state = PersonalizationState(
                enabled=next_enabled,
                instructions=next_instructions,
                use_memories=next_use_memories,
                generate_memories=next_generate,
                updated_at=_now(),
                content_hash=_content_hash(next_instructions),
            )
            return self._write(state)

    def clear_instructions(self) -> PersonalizationState:
        return self.update(instructions="")

    def memory_enabled(self, override: bool | None = None) -> bool:
        state = self.read()
        return state.enabled and (state.use_memories if override is None else override)

    def memory_generation_enabled(self) -> bool:
        state = self.read()
        return state.enabled and state.generate_memories

    def payload(self) -> dict[str, Any]:
        state = self.read()
        payload = state.as_dict()
        payload["path"] = str(self.path)
        payload["max_instructions_chars"] = _MAX_INSTRUCTIONS_CHARS
        return payload


def _metadata_bool_override(metadata: object, key: str) -> bool | None:
    if not isinstance(metadata, dict):
        return None
    value = cast(dict[str, Any], metadata).get(key)
    return value if isinstance(value, bool) else None


def memory_use_override_from_metadata(metadata: object) -> bool | None:
    return _metadata_bool_override(metadata, MEMORY_USE_OVERRIDE_METADATA_KEY)


def memory_generate_override_from_metadata(metadata: object) -> bool | None:
    return _metadata_bool_override(metadata, MEMORY_GENERATE_OVERRIDE_METADATA_KEY)


__all__ = [
    "MEMORY_GENERATE_OVERRIDE_METADATA_KEY",
    "MEMORY_USE_OVERRIDE_METADATA_KEY",
    "memory_generate_override_from_metadata",
    "memory_use_override_from_metadata",
    "PersonalizationPolicyError",
    "PersonalizationState",
    "PersonalizationStore",
]
