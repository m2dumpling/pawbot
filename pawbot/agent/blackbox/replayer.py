"""Replay track: deterministic offline re-execution of a recorded turn.

The deterministic unit is the **ReAct loop for one recorded turn**: we re-run
``AgentRunner`` from the recorded ``initial_messages`` with

- the recorded JSON provider rail (the optional vcr cassette is forced into
  ``record_mode="none"`` when available), and
- the tool rail short-circuited from ``tools.jsonl`` by stable key.

The resulting message sequence is structurally compared against the recorded
``final_messages`` (ignoring timestamps / latency and provider-private
reasoning traces). Exit code 0 means the observable agent behavior was
deterministic for that fixture.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from loguru import logger

from pawbot.agent.blackbox.keys import tool_key
from pawbot.agent.hook import AgentHook, AgentHookContext

if TYPE_CHECKING:
    from contextvars import ContextVar, Token

_TOOLS_JSONL = "tools.jsonl"
_TURNS_JSONL = "turns.jsonl"

_UNSET = object()

_replay_store: "ContextVar[Any | None] | None" = None  # set below


def _get_store_var() -> Any:
    global _replay_store
    if _replay_store is None:
        import contextvars

        _replay_store = contextvars.ContextVar("pawbot_replay_store", default=None)
    return _replay_store


def lookup_replay_result(name: str, args: dict[str, Any]) -> tuple[bool, Any]:
    """Tool-rail short-circuit consulted by ``tools/execution.py``."""
    store = _get_store_var().get()
    if store is None:
        return False, None
    return store.lookup(tool_key(name, args))


def replay_is_active() -> bool:
    """Return whether the current task is inside a replay turn scope."""
    return _get_store_var().get() is not None


class ReplayStore:
    """In-memory index of recorded tool + LLM rails by stable keys.

    Tool results are stored as ordered lists per ``turn_id`` and key. This is
    deliberate: identical calls may occur more than once in a single turn, so
    a last-write-wins dictionary would replay the wrong observation.
    """

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._tool_by_turn: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self._llm_by_turn: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            kind = record.get("kind")
            if kind == "tool":
                key = record.get("key")
                if key:
                    turn_id = str(record.get("turn_id") or "__legacy__")
                    self._tool_by_turn.setdefault(turn_id, {}).setdefault(key, []).append(record)
            elif kind == "llm":
                turn_id = record.get("turn_id")
                if turn_id:
                    self._llm_by_turn.setdefault(turn_id, []).append(record)

    def lookup(self, key: str) -> tuple[bool, Any]:
        """Legacy lookup against records without a turn binding."""
        return _ReplayToolRail(self, "__legacy__").lookup(key)

    def for_turn(self, turn_id: str) -> "_ReplayToolRail":
        return _ReplayToolRail(self, turn_id)

    def lookup_tool_record(
        self,
        turn_id: str,
        key: str,
        index: int,
    ) -> tuple[bool, Any]:
        records = self._tool_by_turn.get(turn_id, {}).get(key, [])
        if not records and turn_id != "__legacy__":
            records = self._tool_by_turn.get("__legacy__", {}).get(key, [])
        if index >= len(records):
            return False, None
        record = records[index]
        result = record.get("result")
        if record.get("status") in {"error", "unknown", "cancelled", "blocked"}:
            from pawbot.agent.tools.base import ToolResult

            return True, ToolResult.error(str(result))
        return True, result

    def llm_responses(self, turn_id: str) -> list[dict[str, Any]]:
        rows = sorted(
            self._llm_by_turn.get(turn_id, []),
            key=lambda row: (
                row.get("iteration", 0),
                row.get("response_index", 0),
            ),
        )
        return [row["response"] for row in rows]


class _ReplayToolRail:
    """One turn's ordered tool-result cursor."""

    def __init__(self, store: ReplayStore, turn_id: str) -> None:
        self._store = store
        self._turn_id = turn_id
        self._positions: dict[str, int] = {}

    def lookup(self, key: str) -> tuple[bool, Any]:
        index = self._positions.get(key, 0)
        found, result = self._store.lookup_tool_record(self._turn_id, key, index)
        if found:
            self._positions[key] = index + 1
        return found, result


def _response_from_payload(payload: dict[str, Any]) -> Any:
    """Rebuild an LLMResponse-like object from a recorded payload."""
    from pawbot.providers.base import (
        LLMResponse,
        LLMUsage,
        ProviderConversationState,
        ToolCallRequest,
    )

    raw_usage_payload = payload.get("usage")
    usage_payload: dict[str, Any] = (
        cast(dict[str, Any], raw_usage_payload)
        if isinstance(raw_usage_payload, dict)
        else {}
    )
    usage = None
    usage = LLMUsage.from_dict(usage_payload)
    if usage is None and usage_payload:
        prompt = int(usage_payload.get(
            "input_tokens",
            usage_payload.get("prompt_tokens", 0),
        ) or 0)
        completion = int(usage_payload.get(
            "output_tokens",
            usage_payload.get("completion_tokens", 0),
        ) or 0)
        total = int(
            usage_payload.get("total_tokens", prompt + completion)
            or (prompt + completion)
        )
        usage = LLMUsage.reported(
            input_tokens=max(0, prompt),
            output_tokens=max(0, completion),
            total_tokens=max(0, total),
        )
    raw_tool_calls = payload.get("tool_calls")
    tool_calls: list[ToolCallRequest] = []
    raw_tool_call_list = cast(list[Any], raw_tool_calls) if isinstance(raw_tool_calls, list) else []
    for raw_tool_call in raw_tool_call_list:
        if not isinstance(raw_tool_call, dict):
            continue
        tc = cast(dict[str, Any], raw_tool_call)
        tool_calls.append(ToolCallRequest(
            id=str(tc.get("id", "")),
            name=str(tc.get("name", "")),
            arguments=tc.get("arguments"),
            extra_content=tc.get("extra_content"),
            provider_specific_fields=tc.get("provider_specific_fields"),
            function_provider_specific_fields=tc.get("function_provider_specific_fields"),
        ))
    provider_state = ProviderConversationState.from_private_record(
        payload.get("provider_state"),
    )
    return LLMResponse(
        content=payload.get("content"),
        tool_calls=tool_calls,
        finish_reason=payload.get("finish_reason", "stop"),
        usage=usage,
        reasoning_content=payload.get("reasoning_content"),
        thinking_blocks=payload.get("thinking_blocks"),
        provider_state=provider_state,
        generation_ms=payload.get("generation_ms"),
        ttft_ms=payload.get("ttft_ms"),
        error_status_code=payload.get("error_status_code"),
        error_kind=payload.get("error_kind"),
        error_type=payload.get("error_type"),
        error_code=payload.get("error_code"),
        error_retry_after_s=payload.get("error_retry_after_s"),
        error_should_retry=payload.get("error_should_retry"),
    )


class ReplayProvider:
    """Provider wrapper that serves recorded LLM responses in iteration order.

    This is the deterministic replay engine: it bypasses the network entirely
    (and therefore the vcr rail). The vcr cassette remains an optional audit
    trail of the raw HTTP traffic recorded during ``--record``.
    """

    def __init__(self, inner: Any, store: ReplayStore, turn_id: str) -> None:
        self.__inner = inner
        self.__responses = store.llm_responses(turn_id)
        self.__index = 0
        if not self.__responses:
            raise ValueError(
                f"no recorded LLM rail for turn {turn_id!r}; cannot replay"
            )

    def _next(self) -> Any:
        if self.__index >= len(self.__responses):
            raise RuntimeError(
                f"replay exhausted LLM rail for turn at call {self.__index}"
            )
        payload = self.__responses[self.__index]
        self.__index += 1
        return _response_from_payload(payload)

    async def chat_with_retry(self, *args: Any, **kwargs: Any) -> Any:
        return self._next()

    async def chat_stream_with_retry(self, *args: Any, **kwargs: Any) -> Any:
        """Return one recorded response while emitting coarse stream callbacks."""
        response = self._next()
        on_thinking_delta = kwargs.get("on_thinking_delta")
        if response.reasoning_content and on_thinking_delta is not None:
            await on_thinking_delta(response.reasoning_content)
        on_content_delta = kwargs.get("on_content_delta")
        if response.content and on_content_delta is not None:
            await on_content_delta(response.content)
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self.__inner, name)


@dataclass(frozen=True)
class RecordedTurn:
    turn_id: str
    session_key: str | None
    model: str
    initial_messages: list[dict[str, Any]]
    final_messages: list[dict[str, Any]]
    final_content: str | None
    stop_reason: str
    cassette: str
    index: int = 0


class ReplayBreakpointError(Exception):
    """Raised by the probe hook when ``--break-at N`` is reached."""

    def __init__(self, iteration: int) -> None:
        super().__init__(f"replay breakpoint at iteration {iteration}")
        self.iteration = iteration


# Keep the historical public import stable while following the project's
# exception naming convention for the actual class.
ReplayBreakpoint = ReplayBreakpointError


class ReplayProbe(AgentHook):
    """Observes one replayed turn and supports the iteration breakpoint."""

    def __init__(self, turn: RecordedTurn, break_at: int | None = None) -> None:
        self._turn = turn
        self._break_at = break_at
        self.messages: list[dict[str, Any]] = []

    async def before_run(self, context: Any) -> None:
        self.messages = []

    async def after_iteration(self, context: AgentHookContext) -> None:
        self.messages = context.messages
        if self._break_at is not None and context.iteration >= self._break_at:
            for msg in context.messages:
                role = msg.get("role", "?")
                content = msg.get("content", "")
                if isinstance(content, list):
                    content = json.dumps(content, ensure_ascii=False, default=repr)
                print(f"[{context.iteration}] {role}: {content}")
            raise ReplayBreakpointError(context.iteration)


class ReplayController:
    """Active during ``--replay <dir>``; drives ``AgentLoop.replay_all``."""

    mode = "replay"

    def __init__(self, directory: str, break_at: int | None = None) -> None:
        self.directory = Path(directory)
        self.break_at = break_at
        self.turns = self._load_turns()
        if not self.turns:
            raise ValueError(f"no recorded turns found in {directory}")
        self.store = self._load_store()
        self._vcr = self._load_vcr()
        # Populated by AgentLoop.replay_all when a breakpoint is hit, so callers
        # (e.g. the WebUI) can surface the dumped messages instead of a bare
        # exception.
        self.last_breakpoint: dict[str, Any] | None = None
        self.last_benchmark: list[dict[str, Any]] = []

    def _load_turns(self) -> list[RecordedTurn]:
        turns: list[RecordedTurn] = []
        with open(self.directory / _TURNS_JSONL, encoding="utf-8") as fh:
            for index, line in enumerate(fh):
                record = json.loads(line)
                if record.get("kind") != "turn":
                    continue
                if record.get("complete") is False:
                    continue
                turns.append(RecordedTurn(
                    turn_id=record.get("turn_id", f"turn_{index}"),
                    session_key=record.get("session_key"),
                    model=record.get("model", ""),
                    initial_messages=record.get("initial_messages", []),
                    final_messages=record.get("final_messages", []),
                    final_content=record.get("final_content"),
                    stop_reason=record.get("stop_reason", ""),
                    cassette=f"{''.join(c if c.isalnum() or c in '-_.' else '_' for c in record.get('turn_id', f'turn_{index}'))}.yaml",
                    index=index,
                ))
        return turns

    def _load_store(self) -> ReplayStore:
        records: list[dict[str, Any]] = []
        path = self.directory / _TOOLS_JSONL
        if path.exists():
            with open(path, encoding="utf-8") as fh:
                records = [json.loads(line) for line in fh]
        return ReplayStore(records)

    @staticmethod
    def _load_vcr() -> Any | None:
        from pawbot.agent.blackbox.recorder import (
            _safe_vcr_import,  # pyright: ignore[reportPrivateUsage]
        )

        vcr = _safe_vcr_import()
        if vcr is None:
            logger.info("vcrpy is unavailable; replay will use the JSON rails only")
            return None
        vcr.record_mode = "none"
        return vcr

    @contextmanager
    def turn_scope(self, turn: RecordedTurn):
        """Enter the LLM rail (playback-only cassette) and bind the tool store."""
        var = _get_store_var()
        token: Token[Any] | None = None
        try:
            token = var.set(self.store.for_turn(turn.turn_id))
            cassette_path = str(self.directory / turn.cassette)
            if self._vcr is None or not Path(cassette_path).exists():
                yield
            else:
                with self._vcr.use_cassette(cassette_path):
                    yield
        finally:
            if token is not None:
                var.reset(token)

    def probe_hook(self, turn: RecordedTurn) -> ReplayProbe:
        return ReplayProbe(turn, break_at=self.break_at)


def compare_messages(actual: list[dict[str, Any]], expected: list[dict[str, Any]]) -> list[str]:
    """Structural diff ignoring volatile fields (timestamps, latency, usage)."""

    def _normalize(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for msg in messages:
            entry = {
                key: value
                for key, value in msg.items()
                if key not in {
                    "timestamp",
                    "latency_ms",
                    "_meta",
                    "usage",
                    # Provider reasoning traces are diagnostic metadata, not
                    # user-visible agent behavior. Tool calls and final
                    # content remain part of the replay contract.
                    "reasoning_content",
                    "thinking_blocks",
                }
            }
            out.append(entry)
        return out

    left = _normalize(actual)
    right = _normalize(expected)
    if left == right:
        return []
    diffs: list[str] = []
    if len(left) != len(right):
        diffs.append(f"message count differs: {len(left)} != {len(right)}")
    for i, (a, b) in enumerate(zip(left, right)):
        if a != b:
            diffs.append(f"message[{i}] differs:\n  recorded: {b}\n  replayed: {a}")
    return diffs
