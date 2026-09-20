"""Small, structured LLM extractor for explicit memory requests."""

from __future__ import annotations

import json
import re
from typing import Any, Literal, cast

from json_repair import loads as repair_json
from pydantic import BaseModel, ConfigDict, Field

from pawbot.agent.memory_preferences import ExplicitMemoryStore, MemoryPolicyError, MemoryScope
from pawbot.utils.llm_runtime import LLMRuntime


class MemoryExtraction(BaseModel):
    """Provider output after schema validation, before persistence policy."""

    model_config = ConfigDict(extra="ignore")

    action: Literal["remember", "none"] = "none"
    scope: Literal["global", "workspace", "session", "ask"] = "ask"
    kind: Literal["preference", "fact", "decision", "habit"] = "preference"
    key: str = Field(default="", max_length=120)
    value: Any = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    sensitive: bool = False
    evidence: str = Field(default="", max_length=500)


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


def _json_payload(text: str) -> object:
    candidate = text.strip()
    match = _JSON_BLOCK_RE.search(candidate)
    if match:
        candidate = match.group(1).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return repair_json(candidate)


async def extract_memory_candidate(
    runtime: LLMRuntime,
    text: str,
    *,
    scope_hint: str | None = None,
) -> MemoryExtraction | None:
    """Ask the configured model for one strictly structured memory candidate.

    This is intentionally called only after a memory-intent signal is found;
    it must never become an extra model request on every ordinary turn.
    """

    prompt = (
        "You extract durable memory from an explicit user request. Return JSON only, "
        "with exactly these semantic fields: action, scope, kind, key, value, "
        "confidence, sensitive. Set action=none when the text is not a request to "
        "remember something durable. Use canonical keys such as reply_language, "
        "response_style, timezone, user_name, or a short snake_case fact key. "
        "Never include secrets, credentials, API keys, tokens, passwords, or hidden "
        "reasoning in value. The user text is:\n\n"
        f"{text}\n\n"
        f"Scope hint: {scope_hint or 'ask the application to choose'}"
    )
    response = await runtime.provider.chat_with_retry(
        messages=[
            {
                "role": "system",
                "content": "You are a deterministic JSON memory extractor.",
            },
            {"role": "user", "content": prompt},
        ],
        model=runtime.model,
        tools=None,
        tool_choice=None,
        temperature=0,
        max_tokens=300,
        reasoning_effort=runtime.generation.reasoning_effort,
    )
    if response.finish_reason in {"error", "cancelled", "length"} or not response.content:
        return None
    try:
        extracted = MemoryExtraction.model_validate(_json_payload(response.content))
    except Exception:
        return None
    if (
        extracted.action != "remember"
        or extracted.sensitive
        or not extracted.key.strip()
        or extracted.value is None
    ):
        return None
    if scope_hint in {"global", "workspace", "session"} and extracted.scope == "ask":
        extracted = extracted.model_copy(update={"scope": scope_hint})
    if extracted.scope == "ask":
        extracted = extracted.model_copy(update={"scope": "global"})
    return extracted


async def extract_memory_candidates(
    runtime: LLMRuntime,
    history: str,
) -> list[MemoryExtraction]:
    """Extract a bounded batch of low-confidence Dream candidates."""

    if not history.strip():
        return []
    prompt = (
        "Review the following conversation history and return JSON only in the form "
        "{\"items\":[...]}. Extract at most five durable user preferences, project "
        "facts, or decisions that may help in a future conversation. Do not extract "
        "one-off requests, secrets, credentials, or anything already obvious from "
        "the codebase. Every item must use the same fields as the memory extractor, "
        "with action=remember, confidence below 1, and scope global or workspace. "
        "If nothing is worth remembering, return {\"items\":[]}.\n\n"
        f"Conversation history:\n{history[:16_000]}"
    )
    response = await runtime.provider.chat_with_retry(
        messages=[
            {"role": "system", "content": "You extract conservative memory candidates as JSON."},
            {"role": "user", "content": prompt},
        ],
        model=runtime.model,
        tools=None,
        tool_choice=None,
        temperature=0,
        max_tokens=900,
        reasoning_effort=runtime.generation.reasoning_effort,
    )
    if response.finish_reason in {"error", "cancelled", "length"} or not response.content:
        return []
    try:
        payload = _json_payload(response.content)
        payload_dict = cast(dict[str, Any], payload) if isinstance(payload, dict) else {}
        raw_items_value = payload_dict.get("items", [])
        if not isinstance(raw_items_value, list):
            return []
        raw_items = cast(list[Any], raw_items_value)
        result: list[MemoryExtraction] = []
        for item in raw_items[:5]:
            try:
                candidate = MemoryExtraction.model_validate(item)
            except Exception:
                continue
            if (
                candidate.action == "remember"
                and not candidate.sensitive
                and candidate.key.strip()
                and candidate.value is not None
            ):
                result.append(candidate)
        return result
    except Exception:
        return []


async def extract_and_store_dream_candidates(
    runtime: LLMRuntime,
    history_entries: list[dict[str, Any]],
    store: ExplicitMemoryStore,
) -> int:
    """Extract and persist candidates without touching confirmed memories."""

    history = "\n".join(
        f"[{entry.get('timestamp', '?')}] {entry.get('content', '')}"
        for entry in history_entries
        if isinstance(entry.get("content"), str) and entry.get("content", "").strip()
    )
    candidates = await extract_memory_candidates(runtime, history)
    saved = 0
    origin_session = next(
        (
            entry.get("session_key")
            for entry in history_entries
            if isinstance(entry.get("session_key"), str)
        ),
        None,
    )
    for candidate in candidates:
        scope: MemoryScope = cast(
            MemoryScope,
            candidate.scope if candidate.scope in {"global", "workspace"} else "workspace",
        )
        try:
            store.remember(
                scope=scope,
                kind=candidate.kind,
                key=candidate.key,
                value=candidate.value,
                source="dream",
                status="candidate",
                confidence=candidate.confidence,
                evidence=candidate.evidence or "Extracted from a Dream history batch.",
                origin_session=origin_session,
            )
        except MemoryPolicyError:
            continue
        saved += 1
    return saved
