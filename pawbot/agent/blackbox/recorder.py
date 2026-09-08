"""Record track: deterministic capture of one agent execution.

Two recording rails, both observable offline:

- **LLM rail** — vcrpy cassette around the provider HTTP calls (httpx is patched
  by ``vcr``; the SSE stream is read fully, so the recorded body is complete).
- **Tool rail** — an ``AgentHook`` writes every observed tool result as a JSONL
  record keyed by ``tool_key(name, args)`` (see ``keys.py``). Results are
  recorded after classification, so validation failures and security-boundary
  errors are replayable too.

Turn envelopes (initial messages, final messages, stop reason) are written to
``turns.jsonl``; ``meta.json`` holds the session/model provenance.
"""

from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from loguru import logger

from pawbot.agent.blackbox.keys import tool_key
from pawbot.agent.hook import AgentHook, AgentHookContext, AgentRunHookContext

if TYPE_CHECKING:
    from pawbot.providers.base import ToolCallRequest

_TOOL_JSONL = "tools.jsonl"
_TURNS_JSONL = "turns.jsonl"
_META_JSON = "meta.json"
_BLACKBOX_SCHEMA_VERSION = 2


def _safe_vcr_import() -> Any | None:
    """Import vcr after neutralizing a broken optional httpx2 backport.

    openai v3 installs an ``httpx2`` shim that lacks transport attributes;
    vcrpy's optional-import ``else`` branch then crashes at import time. We
    never patch httpx2 transports, so aliasing the real httpx transports is
    safe and lets vcrpy's httpx patcher keep working.
    """
    try:
        import httpx2  # type: ignore[import-not-found]
    except ImportError:
        pass
    else:
        import httpx

        for name in (
            "HTTPTransport",
            "AsyncHTTPTransport",
            "WSGITransport",
            "ASGITransport",
            "MockTransport",
        ):
            if not hasattr(httpx2, name) and hasattr(httpx, name):
                setattr(httpx2, name, getattr(httpx, name))
        if not hasattr(httpx2, "AsyncMockTransport"):
            setattr(httpx2, "AsyncMockTransport", getattr(httpx2, "MockTransport", httpx.MockTransport))
    try:
        import vcr  # type: ignore[import-not-found]
    except ImportError:
        return None
    vcr_factory = getattr(vcr, "VCR")
    return vcr_factory(
        record_mode="all",
        match_on=["method", "uri", "query", "body"],
        decode_compressed_response=True,
        filter_headers=["authorization", "api-key", "x-api-key"],
    )


def _sanitize_turn_name(turn_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in turn_id)


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)


def _tool_call_payload(tool_call: ToolCallRequest) -> dict[str, Any]:
    return {
        "id": tool_call.id,
        "name": tool_call.name,
        "arguments": _json_safe(tool_call.arguments),
        "extra_content": _json_safe(tool_call.extra_content),
        "provider_specific_fields": _json_safe(tool_call.provider_specific_fields),
        "function_provider_specific_fields": _json_safe(
            tool_call.function_provider_specific_fields,
        ),
    }


def _provider_state_payload(response: Any) -> dict[str, Any] | None:
    state = getattr(response, "provider_state", None)
    serializer = getattr(state, "to_private_record", None)
    if not callable(serializer):
        return None
    try:
        value = serializer()
    except Exception:
        return None
    return _json_safe(value) if isinstance(value, dict) else None


class TurnRecorder(AgentHook):
    """Tool + LLM rail recorder for one turn."""

    def __init__(
        self,
        directory: Path,
        turn_id: str,
        session_key: str | None,
        model: str,
        initial_messages: list[dict[str, Any]],
        tools_definitions: list[dict[str, Any]] | None = None,
    ) -> None:
        self._dir = directory
        self._turn_id = turn_id
        self._session_key = session_key
        self._model = model
        self._initial_messages = initial_messages
        self._tools_definitions = tools_definitions or []
        self._response_index = 0

    def _append_tools(self, record: dict[str, Any]) -> None:
        with open(self._dir / _TOOL_JSONL, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=repr) + "\n")

    async def before_iteration(self, context: AgentHookContext) -> None:
        # Nothing to do at iteration start; the LLM rail is written after each
        # response is observed (see after_iteration).
        return

    async def after_iteration(self, context: AgentHookContext) -> None:
        """Record the provider response and final classified tool results."""
        response = context.response
        if response is None:
            return
        iteration = context.iteration
        response_index = self._response_index
        self._response_index += 1
        usage = response.usage.to_dict() if response.usage is not None else None
        self._append_tools({
            "kind": "llm",
            "schema_version": _BLACKBOX_SCHEMA_VERSION,
            "turn_id": self._turn_id,
            "iteration": iteration,
            "response_index": response_index,
            "response": {
                "content": response.content,
                "tool_calls": [_tool_call_payload(tc) for tc in response.tool_calls],
                "finish_reason": response.finish_reason,
                "usage": usage,
                "reasoning_content": _json_safe(response.reasoning_content),
                "thinking_blocks": _json_safe(response.thinking_blocks),
                "provider_state": _provider_state_payload(response),
                "generation_ms": response.generation_ms,
                "ttft_ms": response.ttft_ms,
                "error_status_code": response.error_status_code,
                "error_kind": response.error_kind,
                "error_type": response.error_type,
                "error_code": response.error_code,
                "error_retry_after_s": response.error_retry_after_s,
                "error_should_retry": response.error_should_retry,
            },
        })

        for index, (tool_call, result) in enumerate(
            zip(context.tool_calls, context.tool_results, strict=False),
        ):
            event = (
                context.tool_events[index]
                if index < len(context.tool_events)
                else {"status": "ok"}
            )
            args: dict[str, Any] = (
                cast(dict[str, Any], tool_call.arguments)
                if isinstance(tool_call.arguments, dict)
                else {}
            )
            self._append_tools({
                "kind": "tool",
                "schema_version": _BLACKBOX_SCHEMA_VERSION,
                "turn_id": self._turn_id,
                "iteration": iteration,
                "invocation_index": index,
                "name": tool_call.name,
                "key": tool_key(tool_call.name, args),
                "args": _json_safe(args),
                "status": str(event.get("status") or "ok"),
                "detail": event.get("detail", ""),
                "result": _json_safe(result),
            })

    async def after_run(self, context: AgentRunHookContext) -> None:
        with open(self._dir / _TURNS_JSONL, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "kind": "turn",
                "schema_version": _BLACKBOX_SCHEMA_VERSION,
                "turn_id": self._turn_id,
                "session_key": self._session_key,
                "model": self._model,
                "initial_messages": _json_safe(self._initial_messages),
                "final_messages": _json_safe(context.messages),
                "final_content": context.final_content,
                "stop_reason": context.stop_reason,
                "tools": _json_safe(self._tools_definitions),
                "usage": (
                    context.usage.to_dict() if context.usage is not None else None
                ),
            }, ensure_ascii=False, default=repr) + "\n")


class BlackboxController:
    """Active during a normal run with ``--record <dir>``."""

    mode = "record"

    def __init__(self, directory: str) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._meta_written = False
        self._vcr = self._load_vcr()

    @staticmethod
    def _load_vcr() -> Any | None:
        try:
            return _safe_vcr_import()
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "vcrpy import failed ({}); LLM rail will be skipped. "
                "pip install vcrpy to record provider HTTP traffic.",
                exc,
            )
            return None

    @contextmanager
    def turn_scope(self, turn_id: str):
        """Enter the LLM rail (vcr cassette) for one turn."""
        name = _sanitize_turn_name(turn_id)
        cassette_path = self.directory / f"{name}.yaml"
        if self._vcr is None:
            yield
            if not cassette_path.exists():
                cassette_path.write_text("interactions: []\n", encoding="utf-8")
            return
        with self._vcr.use_cassette(str(cassette_path)):
            yield
        # vcrpy does not persist a cassette when no HTTP interaction occurred
        # (e.g. the model is reached through an in-process provider or the run
        # stopped before the first request). Replay needs the file to exist, so
        # write an empty cassette on that path.
        if not cassette_path.exists():
            cassette_path.write_text("interactions: []\n", encoding="utf-8")

    def turn_hook(
        self,
        turn_id: str,
        initial_messages: list[dict[str, Any]],
        *,
        session_key: str | None = None,
        model: str = "",
        tools_definitions: list[dict[str, Any]] | None = None,
    ) -> AgentHook | None:
        return TurnRecorder(
            self.directory,
            turn_id,
            session_key=session_key,
            model=model,
            initial_messages=initial_messages,
            tools_definitions=tools_definitions,
        )

    def write_meta(self, *, session_key: str | None, model: str) -> None:
        if self._meta_written:
            return
        self._meta_written = True
        try:
            import subprocess

            rev = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=3,
            ).stdout.strip()
        except Exception:
            rev = "unknown"
        (self.directory / _META_JSON).write_text(
            json.dumps({
                "mode": "record",
                "schema_version": _BLACKBOX_SCHEMA_VERSION,
                "session_key": session_key,
                "model": model,
                "git_rev": rev,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _clean_artifacts(directory: Path) -> None:
        shutil.rmtree(directory, ignore_errors=True)
