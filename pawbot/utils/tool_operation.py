"""Stable identifiers for tool operations used by recovery and tracing."""

from __future__ import annotations

import hashlib
import json
from typing import Any, cast

TOOL_EXECUTION_METADATA_KEY = "_tool_execution"


def operation_id_for_tool_call(
    session_key: str | None,
    tool_name: str,
    arguments: Any,
) -> str:
    """Build a stable, non-secret identifier for one logical tool call."""

    def canonical(value: Any, *, parse_json_string: bool = False) -> Any:
        if isinstance(value, str) and parse_json_string:
            try:
                return canonical(json.loads(value))
            except (TypeError, ValueError):
                return value
        if isinstance(value, dict):
            mapping = cast(dict[Any, Any], value)
            items = list(mapping.items())
            return {
                str(key): canonical(item)
                for key, item in sorted(items, key=lambda item: str(item[0]))
            }
        if isinstance(value, (list, tuple)):
            sequence = cast(list[Any] | tuple[Any, ...], value)
            return [canonical(item) for item in sequence]
        if isinstance(value, (set, frozenset)):
            values = cast(set[Any] | frozenset[Any], value)
            normalized = [canonical(item) for item in values]
            return sorted(normalized, key=repr)
        if isinstance(value, bytes):
            return value[:256].hex()
        return value

    material = json.dumps(
        {
            "session_key": session_key or "",
            "tool": tool_name,
            "arguments": canonical(arguments, parse_json_string=True),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=repr,
    ).encode("utf-8")
    return f"op_{hashlib.sha256(material).hexdigest()[:32]}"


__all__ = ["TOOL_EXECUTION_METADATA_KEY", "operation_id_for_tool_call"]
