"""Model-aware token estimation for context governance.

Why this module exists (and what it fixes over the upstream helper):

1. **Model-aware encoding.** ``utils/helpers._get_token_encoding`` hard-codes
   ``cl100k_base`` regardless of the model. Newer models tokenize with
   ``o200k_base``; the difference matters for CJK-heavy prompts. Here the
   encoding follows the model family.

2. **Structural overhead.** ``estimate_message_tokens`` counts the serialized
   payload but not the per-message/role/tool-call framing that providers bill
   for. We add explicit per-message and per-tool-call overhead.

3. **Canonical entry point.** All budget decisions (replay budget, governance
   snip/compact thresholds, consolidation triggers) should ask this module,
   never hand-rolled heuristics.

Fallback: when ``tiktoken`` is unavailable, a 4 bytes/token heuristic keeps
the system functional (upstream behavior).
"""

from __future__ import annotations

import functools
import json
from typing import Any, cast

_BASE_MESSAGE_OVERHEAD = 4  # role/framing tokens per message
_TOOL_CALL_OVERHEAD = 16    # JSON framing + name/id per tool_call

try:
    import tiktoken as _tiktoken  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    _tiktoken = None


@functools.lru_cache(maxsize=16)
def _encoding(model: str | None):
    if _tiktoken is None:
        return None
    if model:
        lowered = model.lower()
        if any(k in lowered for k in ("o200k", "gpt-5", "gpt-4o", "gpt-4.1", "o1", "o3", "chatgpt")):
            return _tiktoken.get_encoding("o200k_base")
    return _tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, model: str | None = None) -> int:
    """Token count for one text blob with a byte-heuristic fallback."""
    if not text:
        return 0
    enc = _encoding(model)
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    # 4 bytes per token heuristic (upstream-compatible fallback).
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


def count_message_tokens(message: dict[str, Any], model: str | None = None) -> int:
    """Precise per-message token count including structural overhead.

    Mirrors the payload assembly of ``utils.helpers.estimate_message_tokens``
    (content blocks + tool_calls + reasoning) and adds framing overhead.
    """
    content = message.get("content")
    parts: list[str] = []
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        raw_parts = cast(list[Any], content)
        for raw_part in raw_parts:
            part: dict[str, Any] | None = (
                cast(dict[str, Any], raw_part)
                if isinstance(raw_part, dict)
                else None
            )
            if part is not None and part.get("type") == "text":
                text = part.get("text", "")
                if isinstance(text, str) and text:
                    parts.append(text)
            else:
                parts.append(json.dumps(raw_part, ensure_ascii=False, default=repr))
    elif content is not None:
        parts.append(json.dumps(content, ensure_ascii=False, default=repr))

    for key in ("name", "tool_call_id"):
        value = message.get(key)
        if isinstance(value, str) and value:
            parts.append(value)

    tool_calls = message.get("tool_calls")
    if tool_calls:
        parts.append(json.dumps(tool_calls, ensure_ascii=False, default=repr))

    reasoning = message.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning:
        parts.append(reasoning)
    thinking = message.get("thinking_blocks")
    if thinking:
        parts.append(json.dumps(thinking, ensure_ascii=False, default=repr))

    payload = "\n".join(parts)
    total = count_tokens(payload, model) + _BASE_MESSAGE_OVERHEAD
    if tool_calls:
        total += _TOOL_CALL_OVERHEAD
    return max(4, total)


def count_prompt_tokens(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    model: str | None = None,
) -> int:
    """Full prompt token estimate: messages + tool schemas."""
    total = sum(count_message_tokens(msg, model) for msg in messages)
    if tools:
        schema_tokens = sum(
            count_tokens(json.dumps(tool, ensure_ascii=False, default=repr), model)
            for tool in tools
        )
        total += schema_tokens + 32  # tools framing overhead
    return total


def system_prompt_tokens(text: str, model: str | None = None) -> int:
    """Token count for the system prompt section (used to reserve replay budget)."""
    return count_tokens(text, model) + _BASE_MESSAGE_OVERHEAD
