"""Stable keys for deterministic tool-call matching during replay."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _json_default(value: Any) -> str:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return str(value)
    return repr(value)


def canonical_args(args: dict[str, Any]) -> str:
    """Canonical JSON for argument matching, stable across dict ordering."""
    return json.dumps(args, sort_keys=True, default=_json_default)


def tool_key(name: str, args: dict[str, Any]) -> str:
    """Deterministic per-call key: tool name + canonical argument hash."""
    payload = json.dumps(
        {"name": name, "args": canonical_args(args)},
        sort_keys=True,
        default=_json_default,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
