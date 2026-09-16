"""Local-first execution tracing for Agent turns.

The trace layer is deliberately smaller than a remote observability platform.
It gives every turn a durable, queryable execution timeline while keeping the
existing Record & Replay rails backward compatible:

* ``trace_only`` writes bounded metadata and redacted previews to one JSONL file per turn;
* an active ``BlackboxController`` can direct the same events to the recording
  directory's ``events.jsonl`` file;
* unbounded prompts, model responses, tool arguments, and tool results remain in
  the existing blackbox files; the default trace only carries bounded previews.

This module is synchronous at the write boundary on purpose. Agent lifecycle
callbacks must not create another awaitable failure path, and each event is a
small append-only JSON object that can be read while a turn is running.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, cast

from loguru import logger

from pawbot.agent.approval import ToolApprovalRequest, ToolApprovalResult
from pawbot.agent.hook import AgentHook, AgentHookContext, AgentRunHookContext
from pawbot.agent.tools.execution import execution_policy_for_tool, operation_id_for_tool_call

TRACE_SCHEMA_VERSION = 1
TRACE_EVENTS_FILENAME = "events.jsonl"
TRACE_INDEX_FILENAME = "index.jsonl"
_DEFAULT_RETENTION_DAYS = 14
_DEFAULT_MAX_TRACES = 500
_DEFAULT_MAX_BYTES = 256 * 1024 * 1024
_CLEANUP_INTERVAL_SECONDS = 300.0
_DEFAULT_SLOW_THRESHOLD_MS = 2_000
_INDEX_COMPACT_MIN_ROWS = 100

_FAILURE_STATUSES = frozenset({
    "error",
    "failed",
    "blocked",
    "denied",
    "unknown_side_effect",
    "cancelled",
    "incomplete",
})

TraceEventCallback = Callable[[dict[str, Any]], None]
TraceFinishCallback = Callable[[dict[str, Any]], None]


class TraceWriterLike(Protocol):
    def append(self, event: dict[str, Any]) -> None:
        ...

_SECRET_RE = re.compile(
    r"(?i)(bearer\s+|(?:api[-_ ]?key|access[-_ ]?token|password|secret|token)\s*[:=]\s*)([^\s,;]+)"
)
_SENSITIVE_FIELD_RE = re.compile(
    r"(?i)(api[-_ ]?key|access[-_ ]?token|refresh[-_ ]?token|authorization|cookie|password|secret)"
)
_PATH_LOCKS: dict[str, threading.Lock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def redact_text(value: Any, *, limit: int = 1_000) -> str:
    """Return a bounded diagnostic string with common credentials masked."""
    text = "" if value is None else str(value)
    text = _SECRET_RE.sub(lambda match: f"{match.group(1)}<redacted>", text)
    text = text.replace("\x00", "")
    text = text.strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def _redact_payload(value: Any, *, depth: int = 0) -> Any:
    """Redact sensitive fields and bound diagnostic payloads recursively."""
    if depth >= 6:
        return "<truncated>"
    if isinstance(value, dict):
        mapping = cast(dict[Any, Any], value)
        mapping_result: dict[str, Any] = {}
        for index, (key, item) in enumerate(mapping.items()):
            if index >= 64:
                mapping_result["<truncated>"] = f"{len(mapping) - index} more fields"
                break
            key_text = str(key)
            mapping_result[key_text] = (
                "<redacted>"
                if _SENSITIVE_FIELD_RE.search(key_text)
                else _redact_payload(item, depth=depth + 1)
            )
        return mapping_result
    if isinstance(value, (list, tuple)):
        sequence = cast(list[Any] | tuple[Any, ...], value)
        sequence_result: list[Any] = [
            _redact_payload(item, depth=depth + 1) for item in sequence[:128]
        ]
        if len(sequence) > 128:
            sequence_result.append(f"<truncated {len(sequence) - 128} items>")
        return sequence_result
    if isinstance(value, str):
        return redact_text(value)
    return value


def trace_value_preview(value: Any, *, limit: int = 720) -> str | None:
    """Return a redacted, bounded preview for the live inspection surface."""
    if value is None:
        return None
    if isinstance(value, str):
        text = redact_text(value, limit=limit)
    else:
        try:
            text = json.dumps(
                _redact_payload(value),
                ensure_ascii=False,
                separators=(",", ":"),
                default=repr,
            )
        except (TypeError, ValueError):
            text = redact_text(value, limit=limit)
    return text[:limit] + ("…" if len(text) > limit else "")


def _safe_component(value: str | None, fallback: str) -> str:
    raw = (value or fallback).strip()
    safe = "".join(character if character.isalnum() or character in "-_." else "_" for character in raw)
    return safe[:160] or fallback


def _is_tool_failure(event: dict[str, Any]) -> bool:
    name = str(event.get("event") or "")
    status = str(event.get("status") or "").lower()
    return name in {
        "tool.finished",
        "tool.failed",
        "tool.blocked",
        "tool.cancelled",
        "tool.unknown_side_effect",
    } and status in _FAILURE_STATUSES


def _is_provider_error(event: dict[str, Any]) -> bool:
    name = str(event.get("event") or "")
    status = str(event.get("status") or "").lower()
    return name in {"llm.request_failed", "provider_tool.error"} or (
        name in {"llm.response", "llm.response_received"}
        and (status == "error" or event.get("error") not in (None, ""))
    )


def _is_unknown_side_effect(event: dict[str, Any]) -> bool:
    name = str(event.get("event") or "")
    status = str(event.get("status") or "").lower()
    return status == "unknown_side_effect" or name in {
        "tool.cancelled",
        "tool.unknown_side_effect",
    }


def _trace_tool_call_key(event: dict[str, Any]) -> str | None:
    """Return a stable in-memory key for one local or hosted tool call."""
    name = str(event.get("event") or "")
    if name not in {
        "tool.planned",
        "tool.started",
        "tool.finished",
        "tool.cancelled",
        "provider_tool.started",
        "provider_tool.completed",
        "provider_tool.error",
    }:
        return None
    call_id = event.get("call_id")
    prefix = "provider" if name.startswith("provider_tool.") else "local"
    if isinstance(call_id, str) and call_id:
        return f"{prefix}:call:{call_id}"
    return (
        f"{prefix}:fallback:{event.get('iteration', '?')}:"
        f"{event.get('tool_name', '?')}"
    )


def _count_trace_tool_calls(events: list[dict[str, Any]]) -> int:
    return len({
        key
        for event in events
        if (key := _trace_tool_call_key(event)) is not None
    })


def _trace_timestamp(row: dict[str, Any]) -> int:
    value = row.get("timestamp_ms")
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _path_lock(path: Path) -> threading.Lock:
    key = str(path.resolve(strict=False))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PATH_LOCKS[key] = lock
        return lock


class TraceWriter:
    """Append structured events to a local JSONL file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = _path_lock(path)

    def append(self, event: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(
                event,
                ensure_ascii=False,
                separators=(",", ":"),
                default=repr,
            )
            with self._lock:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(payload + "\n")
                    handle.flush()
        except (OSError, TypeError, ValueError):
            # Observability must never break an Agent turn. The error is still
            # visible in the process log for a developer to investigate.
            logger.exception("failed to append agent trace event to {}", self.path)


@dataclass(slots=True)
class TraceRun:
    """The trace identity and sink shared by the outer and inner turn layers."""

    writer: TraceWriterLike
    trace_id: str
    session_key: str | None
    turn_id: str
    channel: str
    chat_id: str
    model: str | None = None
    provider: str | None = None
    owner_pid: int | None = None
    trace_file_id: str | None = None
    on_event: TraceEventCallback | None = field(default=None, repr=False)
    on_finish: TraceFinishCallback | None = field(default=None, repr=False)
    started_at_ns: int = field(default_factory=time.monotonic_ns)
    _sequence: int = 0
    _event_count: int = 0
    _tool_count: int = 0
    _tool_call_keys: set[str] = field(default_factory=set, repr=False)
    _failure_count: int = 0
    _tool_failure_count: int = 0
    _provider_error_count: int = 0
    _unknown_side_effect_count: int = 0
    _max_step_duration_ms: int = 0
    _verification_status: str | None = None
    _verification_completed: bool | None = None
    _closed: bool = False

    def set_runtime(self, *, provider: str | None, model: str | None) -> None:
        if provider:
            self.provider = provider
        if model:
            self.model = model

    @property
    def closed(self) -> bool:
        return self._closed

    def emit(
        self,
        event: str,
        *,
        status: str | None = None,
        duration_ms: int | float | None = None,
        iteration: int | None = None,
        call_id: str | None = None,
        **fields: Any,
    ) -> None:
        self._sequence += 1
        row: dict[str, Any] = {
            "schema_version": TRACE_SCHEMA_VERSION,
            "sequence": self._sequence,
            "event": event,
            "trace_id": self.trace_id,
            "session_key": self.session_key,
            "turn_id": self.turn_id,
            "channel": self.channel,
            "chat_id": self.chat_id,
            "timestamp_ms": int(time.time() * 1000),
        }
        if self.model:
            row["model"] = self.model
        if self.provider:
            row["provider"] = self.provider
        if self.owner_pid is not None:
            row["owner_pid"] = self.owner_pid
        if status is not None:
            row["status"] = status
        if duration_ms is not None:
            row["duration_ms"] = max(0, int(duration_ms))
        if iteration is not None:
            row["iteration"] = iteration
        if call_id:
            row["call_id"] = call_id
        row.update(fields)
        safe_row = cast(dict[str, Any], _redact_payload(row))
        self.writer.append(safe_row)
        self._event_count += 1
        tool_call_key = _trace_tool_call_key(row)
        if tool_call_key is not None and tool_call_key not in self._tool_call_keys:
            self._tool_call_keys.add(tool_call_key)
            self._tool_count += 1
        normalized_status = str(status or "").lower()
        if normalized_status in _FAILURE_STATUSES:
            self._failure_count += 1
        if _is_tool_failure(row):
            self._tool_failure_count += 1
        if _is_provider_error(row):
            self._provider_error_count += 1
        if _is_unknown_side_effect(row):
            self._unknown_side_effect_count += 1
        if event == "task.verification":
            verification_status = row.get("verification_status")
            self._verification_status = (
                str(verification_status)
                if verification_status is not None
                else None
            )
            completed = row.get("verification_completed")
            self._verification_completed = completed if isinstance(completed, bool) else None
        if isinstance(row.get("duration_ms"), int):
            self._max_step_duration_ms = max(self._max_step_duration_ms, row["duration_ms"])
        if self.on_event is not None:
            try:
                self.on_event(dict(safe_row))
            except Exception:
                logger.exception("failed to publish live trace event {}", event)

    def summary(
        self,
        *,
        status: str,
        stop_reason: str | None = None,
        duration_ms: int | None = None,
    ) -> dict[str, Any]:
        """Return the compact index row used by local inspection surfaces."""
        return {
            "schema_version": TRACE_SCHEMA_VERSION,
            "id": self.trace_file_id,
            "trace_id": self.trace_id,
            "session_key": self.session_key,
            "turn_id": self.turn_id,
            "channel": self.channel,
            "chat_id": self.chat_id,
            "model": self.model,
            "provider": self.provider,
            "owner_pid": self.owner_pid,
            "status": status,
            "stop_reason": stop_reason,
            "duration_ms": duration_ms,
            "event_count": self._event_count,
            "tool_count": self._tool_count,
            "failure_count": self._failure_count,
            "tool_failure_count": self._tool_failure_count,
            "provider_error_count": self._provider_error_count,
            "unknown_side_effect_count": self._unknown_side_effect_count,
            "verification_status": self._verification_status,
            "verification_completed": self._verification_completed,
            "max_step_duration_ms": self._max_step_duration_ms,
            "timestamp_ms": int(time.time() * 1000),
        }

    def finish(
        self,
        *,
        status: str,
        stop_reason: str | None = None,
        error: Any = None,
    ) -> None:
        if self._closed:
            return
        self._closed = True
        event = (
            "turn.cancelled"
            if status == "cancelled"
            else "turn.failed"
            if status == "error"
            else "turn.incomplete"
            if status == "incomplete"
            else "turn.completed"
        )
        duration_ms = (time.monotonic_ns() - self.started_at_ns) // 1_000_000
        self.emit(
            event,
            status=status,
            duration_ms=duration_ms,
            stop_reason=stop_reason,
            error=_error_payload(error) if error is not None else None,
        )
        if self.on_finish is not None:
            try:
                self.on_finish(
                    self.summary(
                        status=status,
                        stop_reason=stop_reason,
                        duration_ms=duration_ms,
                    )
                )
            except Exception:
                logger.exception("failed to index completed trace {}", self.trace_id)

    def hook(
        self,
        *,
        initial_messages: list[dict[str, Any]],
        tools_count: int,
    ) -> "TraceHook":
        return TraceHook(
            self,
            initial_message_count=len(initial_messages),
            tools_count=tools_count,
        )


class TraceHook(AgentHook):
    """Observe AgentRunner lifecycle without copying raw payloads."""

    def __init__(
        self,
        trace: TraceRun,
        *,
        initial_message_count: int,
        tools_count: int,
    ) -> None:
        super().__init__()
        self._trace = trace
        self._initial_message_count = initial_message_count
        self._tools_count = tools_count
        self._iteration_started_ns: dict[int, int] = {}
        self._model_started_ns: dict[int, int] = {}
        self._model_attempts: dict[int, int] = {}
        self._tool_metadata: dict[str, dict[str, Any]] = {}
        self._agent_finished = False

    async def before_run(self, context: AgentRunHookContext) -> None:
        self._trace.emit(
            "agent.started",
            status="running",
            message_count=self._initial_message_count,
            tools_available=self._tools_count,
        )

    async def before_iteration(self, context: AgentHookContext) -> None:
        now = time.monotonic_ns()
        self._iteration_started_ns[context.iteration] = now
        self._model_started_ns[context.iteration] = now
        self._trace.emit(
            "iteration.started",
            status="running",
            iteration=context.iteration,
            message_count=len(context.messages),
            model_message_count=context.model_message_count,
            context_window_tokens=context.context_window_tokens,
            budget=context.budget,
        )

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        """Record the model's planned tool calls before validation or execution."""
        for tool_call in context.tool_calls:
            arguments: Any = getattr(tool_call, "arguments", None)
            argument_mapping = cast(dict[Any, Any], arguments) if isinstance(arguments, dict) else {}
            argument_keys = sorted(str(key) for key in argument_mapping)
            self._trace.emit(
                "tool.planned",
                status="planned",
                iteration=context.iteration,
                call_id=str(getattr(tool_call, "id", "") or "") or None,
                tool_name=str(getattr(tool_call, "name", "") or "unknown"),
                operation_id=operation_id_for_tool_call(
                    context.session_key,
                    str(getattr(tool_call, "name", "") or "unknown"),
                    getattr(tool_call, "arguments", None),
                ),
                argument_keys=argument_keys,
                arguments_preview=trace_value_preview(argument_mapping, limit=480),
            )

    async def on_tool_approval_requested(
        self,
        context: AgentHookContext,
        request: ToolApprovalRequest,
    ) -> None:
        self._trace.emit(
            "tool.approval_requested",
            status="waiting",
            iteration=context.iteration,
            call_id=request.call_id or None,
            tool_name=request.name,
            approval_id=request.request_id,
            argument_keys=sorted(request.arguments),
            tool_capabilities=list(request.capabilities),
            operation_id=request.operation_id,
            recovery_required=request.recovery_required,
            recovery_reason=redact_text(request.recovery_reason, limit=300)
            if request.recovery_reason
            else None,
        )

    async def on_tool_approval_resolved(
        self,
        context: AgentHookContext,
        request: ToolApprovalRequest,
        decision: ToolApprovalResult,
    ) -> None:
        self._trace.emit(
            "tool.approval_resolved",
            status="approved" if decision.approved else "denied",
            iteration=context.iteration,
            call_id=request.call_id or None,
            tool_name=request.name,
            approval_id=request.request_id,
            reason=redact_text(decision.reason) if decision.reason else None,
            operation_id=request.operation_id,
            recovery_required=request.recovery_required,
            recovery_resolution=(
                "approved" if decision.approved else "denied"
            )
            if request.recovery_required
            else None,
        )

    async def on_model_response(self, context: AgentHookContext) -> None:
        response = context.response
        if response is None:
            return
        started = self._model_started_ns.pop(context.iteration, None)
        tool_names = [call.name for call in context.tool_calls if call.name]
        usage = context.usage.to_dict() if context.usage is not None else None
        finish_reason = response.finish_reason
        status = "error" if finish_reason == "error" else "received"
        self._trace.emit(
            "llm.response",
            status=status,
            iteration=context.iteration,
            duration_ms=(time.monotonic_ns() - started) // 1_000_000 if started else None,
            call_id=f"llm-{context.iteration}",
            finish_reason=finish_reason,
            tool_names=tool_names,
            content_chars=len(response.content or ""),
            reasoning_chars=len(response.reasoning_content or ""),
            content_preview=trace_value_preview(response.content, limit=720),
            reasoning_preview=trace_value_preview(response.reasoning_content, limit=720),
            usage=usage,
            generation_ms=getattr(response, "generation_ms", None),
            ttft_ms=getattr(response, "ttft_ms", None),
            error=_response_error(response),
        )

    async def on_model_request_started(self, context: AgentHookContext) -> None:
        attempt = self._model_attempts.get(context.iteration, 0) + 1
        self._model_attempts[context.iteration] = attempt
        self._model_started_ns[context.iteration] = time.monotonic_ns()
        self._trace.emit(
            "llm.request_started",
            status="running",
            iteration=context.iteration,
            call_id=f"llm-{context.iteration}-attempt-{attempt}",
            attempt=attempt,
            message_count=context.model_message_count,
            context_window_tokens=context.context_window_tokens,
            tools_available=self._tools_count,
        )

    async def on_model_retry(self, context: AgentHookContext, reason: str) -> None:
        self._trace.emit(
            "llm.retry",
            status="retrying",
            iteration=context.iteration,
            retry_attempt=self._model_attempts.get(context.iteration, 1),
            reason=redact_text(reason, limit=240),
        )

    async def on_model_error(
        self,
        context: AgentHookContext,
        error: BaseException,
    ) -> None:
        started = self._model_started_ns.pop(context.iteration, None)
        self._trace.emit(
            "llm.request_failed",
            status="error",
            iteration=context.iteration,
            duration_ms=(time.monotonic_ns() - started) // 1_000_000 if started else None,
            call_id=f"llm-{context.iteration}",
            attempt=self._model_attempts.get(context.iteration, 1),
            error=_provider_error_payload(error),
        )

    async def on_budget_exhausted(
        self,
        context: AgentHookContext,
        reason: str,
    ) -> None:
        self._trace.emit(
            "budget.exhausted",
            status="blocked",
            iteration=context.iteration,
            reason=reason,
            budget=context.budget,
        )

    async def on_task_verification(
        self,
        context: AgentRunHookContext,
        evaluation: dict[str, Any],
        *,
        attempt: int,
    ) -> None:
        del context
        status = str(evaluation.get("status") or "not_evaluable")
        trace_status = (
            "completed"
            if status == "passed"
            else "failed"
            if status == "failed"
            else "blocked"
        )
        raw_failures = evaluation.get("failures")
        failures = (
            [
                redact_text(item, limit=300)
                for item in cast(list[Any], raw_failures)
                if isinstance(item, str)
            ]
            if isinstance(raw_failures, list)
            else []
        )
        self._trace.emit(
            "task.verification",
            status=trace_status,
            verification_status=status,
            verification_completed=evaluation.get("completed") is True,
            verification_attempt=attempt,
            verification_reason=redact_text(evaluation.get("reason"), limit=300)
            if evaluation.get("reason")
            else None,
            verification_failures=failures,
        )

    async def before_execute_tool(
        self,
        context: AgentHookContext,
        tool_call: Any,
        tool: Any,
        params: Any,
    ) -> None:
        argument_mapping = cast(dict[Any, Any], params) if isinstance(params, dict) else {}
        argument_keys = (
            sorted(str(key) for key in argument_mapping)
        )
        raw_capabilities: Any = getattr(tool, "capabilities", ())
        capabilities = sorted(str(value) for value in raw_capabilities) if raw_capabilities else []
        call_id = str(getattr(tool_call, "id", "") or "")
        policy = execution_policy_for_tool(tool)
        operation_id = operation_id_for_tool_call(
            context.session_key,
            str(getattr(tool_call, "name", "") or "unknown"),
            getattr(tool_call, "arguments", None),
        )
        tool_metadata = {
            "tool_capabilities": capabilities,
            "read_only": bool(getattr(tool, "read_only", False)),
            "concurrency_safe": bool(getattr(tool, "concurrency_safe", False)),
            "exclusive": bool(getattr(tool, "exclusive", False)),
            "operation_id": operation_id,
            **policy.to_dict(),
        }
        if call_id:
            self._tool_metadata[call_id] = tool_metadata
        self._trace.emit(
            "tool.started",
            status="running",
            iteration=context.iteration,
            call_id=call_id or None,
            tool_name=str(getattr(tool_call, "name", "") or "unknown"),
            argument_keys=argument_keys,
            arguments_preview=trace_value_preview(argument_mapping, limit=480),
            tool_capabilities=capabilities,
            read_only=tool_metadata["read_only"],
            concurrency_safe=tool_metadata["concurrency_safe"],
            exclusive=tool_metadata["exclusive"],
            operation_id=operation_id,
            **policy.to_dict(),
        )

    async def on_execute_tool_cancelled(
        self,
        context: AgentHookContext,
        tool_call: Any,
        tool: Any,
        params: Any,
    ) -> None:
        del params
        policy = execution_policy_for_tool(tool)
        call_id = str(getattr(tool_call, "id", "") or "")
        operation_id = operation_id_for_tool_call(
            context.session_key,
            str(getattr(tool_call, "name", "") or "unknown"),
            getattr(tool_call, "arguments", None),
        )
        self._trace.emit(
            "tool.cancelled",
            status="unknown_side_effect",
            iteration=context.iteration,
            call_id=call_id or None,
            tool_name=str(getattr(tool_call, "name", "") or "unknown"),
            operation_id=operation_id,
            side_effect="may_have_occurred",
            **policy.to_dict(),
            error={
                "type": "tool_cancelled",
                "code": "UNKNOWN_SIDE_EFFECT",
                "message": "Tool execution was cancelled; external side effects are uncertain.",
                "retryable": False,
            },
        )

    async def on_provider_tool_event(
        self,
        context: AgentHookContext,
        event: dict[str, Any],
    ) -> None:
        if event.get("kind") != "hosted_tool":
            return
        phase = str(event.get("phase") or "event")
        self._trace.emit(
            f"provider_tool.{phase}",
            status="error" if phase == "error" else phase,
            iteration=context.iteration,
            call_id=str(event.get("call_id") or "") or None,
            tool_name=str(event.get("name") or "unknown"),
            arguments_preview=trace_value_preview(event.get("arguments"), limit=480),
            result_preview=trace_value_preview(event.get("result"), limit=720),
            result_type=(
                type(event.get("result")).__name__
                if event.get("result") is not None
                else None
            ),
            error=redact_text(event.get("error")) if event.get("error") else None,
        )

    async def after_iteration(self, context: AgentHookContext) -> None:
        states_by_call_id = {
            str(state.get("call_id")): state
            for state in context.tool_states
            if state.get("call_id")
        }
        for index, tool_call in enumerate(context.tool_calls):
            event = context.tool_events[index] if index < len(context.tool_events) else {}
            call_id = str(getattr(tool_call, "id", "") or "")
            state = states_by_call_id.get(call_id, {})
            tool_metadata = self._tool_metadata.pop(call_id, {})
            result = context.tool_results[index] if index < len(context.tool_results) else None
            lifecycle_state = str(state.get("state") or "")
            status = str(event.get("status") or lifecycle_state or "unknown")
            if lifecycle_state == "blocked" or status in {"blocked", "denied"}:
                normalized_status = "blocked"
            elif lifecycle_state == "unknown" or status in {"cancelled", "canceled", "unknown"}:
                normalized_status = "unknown_side_effect"
            elif status == "ok":
                normalized_status = "succeeded"
            else:
                normalized_status = "failed"
            self._trace.emit(
                "tool.finished",
                status=normalized_status,
                iteration=context.iteration,
                call_id=call_id or None,
                tool_name=str(getattr(tool_call, "name", "") or event.get("name") or "unknown"),
                duration_ms=state.get("duration_ms"),
                lifecycle_state=state.get("state"),
                side_effect=state.get("side_effect"),
                operation_id=state.get("operation_id") or tool_metadata.get("operation_id"),
                recovery_required=state.get("recovery_required", False),
                recovery_resolution=state.get("recovery_resolution"),
                receipt=state.get("receipt"),
                side_effect_class=state.get("side_effect_class")
                or tool_metadata.get("side_effect_class"),
                idempotency=state.get("idempotency") or tool_metadata.get("idempotency"),
                recovery_strategy=state.get("recovery_strategy")
                or tool_metadata.get("recovery_strategy"),
                reversible=state.get("reversible", tool_metadata.get("reversible", False)),
                receipt_supported=state.get(
                    "receipt_supported", tool_metadata.get("receipt_supported", False)
                ),
                detail=redact_text(event.get("detail")) if event.get("detail") else None,
                error=_tool_error_payload(event, state),
                arguments_preview=trace_value_preview(
                    getattr(tool_call, "arguments", None),
                    limit=480,
                ),
                result_preview=trace_value_preview(result, limit=720),
                result_type=type(result).__name__ if result is not None else None,
                result_chars=len(str(result)) if result is not None else 0,
                tool_capabilities=tool_metadata.get("tool_capabilities", []),
                read_only=tool_metadata.get("read_only", False),
                concurrency_safe=tool_metadata.get("concurrency_safe", False),
                exclusive=tool_metadata.get("exclusive", False),
            )

        started = self._iteration_started_ns.pop(context.iteration, None)
        self._trace.emit(
            "iteration.completed",
            status="error" if context.error else ("completed" if context.stop_reason else "waiting"),
            iteration=context.iteration,
            duration_ms=(time.monotonic_ns() - started) // 1_000_000 if started else None,
            tool_count=len(context.tool_calls),
            stop_reason=context.stop_reason,
            budget=context.budget,
        )

    async def on_error(self, context: AgentRunHookContext) -> None:
        self._trace.emit(
            "agent.error",
            status="error",
            error=_error_payload(context.error or context.exception),
            stop_reason=context.stop_reason,
            budget=context.budget,
        )

    async def after_run(self, context: AgentRunHookContext) -> None:
        self._agent_finished = True
        task_evaluation = context.task_evaluation or {}
        self._trace.emit(
            "agent.completed",
            status="error" if context.error else "completed",
            stop_reason=context.stop_reason,
            error=redact_text(context.error) if context.error else None,
            message_count=len(context.messages),
            final_content_chars=len(context.final_content or ""),
            final_content_preview=trace_value_preview(context.final_content, limit=720),
            verification_status=task_evaluation.get("status"),
            verification_completed=task_evaluation.get("completed"),
            tools_used=list(context.tools_used),
            usage=context.usage.to_dict() if context.usage is not None else None,
            budget=context.budget,
        )

    async def on_finally(self, context: AgentRunHookContext) -> None:
        if self._agent_finished:
            return
        self._trace.emit(
            "agent.finalized",
            status="cancelled" if context.stop_reason == "cancelled" else "incomplete",
            stop_reason=context.stop_reason or "cancelled",
            error=redact_text(context.error) if context.error else None,
            message_count=len(context.messages),
            budget=context.budget,
        )


def _error_payload(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, BaseException):
        return {
            "type": type(value).__name__,
            "code": type(value).__name__.upper(),
            "message": redact_text(value),
            "retryable": False,
        }
    return {
        "type": "agent_error",
        "code": "AGENT_ERROR",
        "message": redact_text(value),
        "retryable": False,
    }


def _provider_error_payload(value: BaseException) -> dict[str, Any]:
    payload = _error_payload(value) or {
        "message": redact_text(value),
        "retryable": False,
    }
    payload["type"] = "provider_error"
    return payload


def _response_error(response: Any) -> dict[str, Any] | None:
    fields = {
        "status_code": getattr(response, "error_status_code", None),
        "kind": getattr(response, "error_kind", None),
        "type": getattr(response, "error_type", None),
        "code": getattr(response, "error_code", None),
    }
    message = getattr(response, "content", None)
    if not any(value not in (None, "") for value in fields.values()) and response.finish_reason != "error":
        return None
    fields["message"] = redact_text(message)
    fields["retryable"] = getattr(response, "error_should_retry", None)
    return fields


def _tool_error_payload(
    event: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any] | None:
    status = str(event.get("status") or state.get("state") or "").lower()
    if status in {"ok", "succeeded", "success"}:
        return None
    detail = redact_text(event.get("detail"))
    lowered = detail.lower()
    if "ssrf" in lowered or "private" in lowered:
        code = "SSRF_BLOCKED"
        error_type = "tool_permission_error"
    elif "workspace" in lowered or "outside" in lowered:
        code = "WORKSPACE_BOUNDARY"
        error_type = "tool_permission_error"
    elif "capability" in lowered or "permission" in lowered:
        code = "CAPABILITY_DENIED"
        error_type = "tool_permission_error"
    elif "recovery" in lowered or "interrupted" in lowered:
        code = "RECOVERY_CONFIRMATION_REQUIRED"
        error_type = "recovery_error"
    elif status in {"cancelled", "canceled", "unknown"}:
        code = "UNKNOWN_SIDE_EFFECT"
        error_type = "unknown_side_effect"
    elif "timed out" in lowered or "timeout" in lowered:
        code = "TOOL_TIMEOUT"
        error_type = "tool_timeout"
    else:
        code = "TOOL_EXECUTION_ERROR"
        error_type = "tool_execution_error"
    return {
        "type": error_type,
        "code": code,
        "message": detail or "Tool execution did not complete successfully.",
        "retryable": None,
    }


class TraceStore:
    """Create turn traces under the instance data directory."""

    def __init__(
        self,
        root: Path,
        *,
        enabled: bool = True,
        retention_days: int = _DEFAULT_RETENTION_DAYS,
        max_traces: int = _DEFAULT_MAX_TRACES,
        max_bytes: int = _DEFAULT_MAX_BYTES,
        event_callback: TraceEventCallback | None = None,
    ) -> None:
        self.root = root.expanduser().resolve(strict=False)
        self.enabled = enabled
        self.retention_days = max(0, retention_days)
        self.max_traces = max(1, max_traces)
        self.max_bytes = max(0, max_bytes)
        self.event_callback = event_callback
        self._last_cleanup_at = 0.0
        self._active_trace_ids: set[str] = set()

    @property
    def index_path(self) -> Path:
        return self.root / TRACE_INDEX_FILENAME

    def _append_index(self, summary: dict[str, Any]) -> None:
        if not summary.get("id"):
            return
        TraceWriter(self.index_path).append(summary)

    def _compact_index(self, *, force: bool = False) -> None:
        """Collapse admission/final rows and remove entries for deleted traces."""
        if not self.index_path.exists():
            return
        try:
            with _path_lock(self.index_path):
                rows = self._read_rows(self.index_path)
                latest_by_id: dict[str, dict[str, Any]] = {}
                for row in rows:
                    identifier = row.get("id")
                    if isinstance(identifier, str) and identifier:
                        latest_by_id[identifier] = row
                if not force and len(rows) < max(self.max_traces * 2, _INDEX_COMPACT_MIN_ROWS):
                    return
                current: list[dict[str, Any]] = []
                for identifier, row in latest_by_id.items():
                    try:
                        if self.resolve(identifier).exists():
                            current.append(row)
                    except ValueError:
                        continue
                if not current:
                    with suppress(OSError):
                        self.index_path.unlink(missing_ok=True)
                    return
                current.sort(key=_trace_timestamp)
                temporary = self.index_path.with_suffix(".tmp")
                with temporary.open("w", encoding="utf-8") as handle:
                    for row in current:
                        handle.write(
                            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                        )
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.index_path)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            logger.exception("failed to compact agent trace index {}", self.index_path)

    def _reconcile_incomplete(self) -> None:
        """Close traces left open by a crashed process on the next inspection."""
        if not self.root.exists():
            return
        terminal_events = {"turn.completed", "turn.failed", "turn.cancelled", "turn.incomplete"}
        for path in self.root.rglob("*.jsonl"):
            if path.name == TRACE_INDEX_FILENAME:
                continue
            try:
                identifier = path.relative_to(self.root).as_posix()
                if identifier in self._active_trace_ids:
                    continue
                rows = self._read_rows(path)
                if not rows or rows[-1].get("event") in terminal_events:
                    continue
                last = rows[-1]
                owner_pid = last.get("owner_pid")
                if isinstance(owner_pid, int) and not isinstance(owner_pid, bool) and owner_pid != os.getpid():
                    try:
                        from pawbot.process_runtime import process_is_running

                        if process_is_running(owner_pid):
                            continue
                    except (ImportError, OSError, ValueError):
                        pass
                sequence = max(
                    (
                        int(row["sequence"])
                        for row in rows
                        if isinstance(row.get("sequence"), int)
                    ),
                    default=0,
                ) + 1
                TraceWriter(path).append({
                    "schema_version": TRACE_SCHEMA_VERSION,
                    "sequence": sequence,
                    "event": "turn.incomplete",
                    "trace_id": last.get("trace_id"),
                    "session_key": last.get("session_key"),
                    "turn_id": last.get("turn_id"),
                    "channel": last.get("channel"),
                    "chat_id": last.get("chat_id"),
                    "model": last.get("model"),
                    "provider": last.get("provider"),
                    "timestamp_ms": int(time.time() * 1000),
                    "status": "incomplete",
                    "stop_reason": "process_interrupted",
                    "error": {
                        "type": "process_interrupted",
                        "code": "INCOMPLETE_TRACE",
                        "message": "The process ended before this turn recorded a terminal event.",
                        "retryable": False,
                    },
                })
            except (OSError, ValueError, json.JSONDecodeError):
                continue

    def _trace_path(self, session_key: str | None, turn_id: str) -> Path:
        session_name = _safe_component(session_key, "unbound")
        return self.root / session_name / f"{_safe_component(turn_id, 'turn')}.jsonl"

    @staticmethod
    def _summary_from_events(
        *,
        root: Path,
        path: Path,
        events: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not events:
            return None
        first = events[0]
        last = events[-1]
        failures = sum(
            1
            for event in events
            if str(event.get("status") or "").lower() in _FAILURE_STATUSES
        )
        tools = _count_trace_tool_calls(events)
        terminal = last.get("event") in {
            "turn.completed",
            "turn.failed",
            "turn.cancelled",
            "turn.incomplete",
        }
        duration_value = last.get("duration_ms") if terminal else None
        if not isinstance(duration_value, int):
            first_timestamp = first.get("timestamp_ms")
            last_timestamp = last.get("timestamp_ms")
            if isinstance(first_timestamp, int) and isinstance(last_timestamp, int):
                duration_value = max(0, last_timestamp - first_timestamp)
            elif isinstance(last.get("duration_ms"), int):
                duration_value = last["duration_ms"]
        try:
            identifier = path.relative_to(root).as_posix()
        except ValueError:
            return None
        return {
            "schema_version": TRACE_SCHEMA_VERSION,
            "id": identifier,
            "trace_id": first.get("trace_id"),
            "session_key": first.get("session_key"),
            "turn_id": first.get("turn_id"),
            "channel": first.get("channel"),
            "chat_id": first.get("chat_id"),
            "model": last.get("model") or first.get("model"),
            "provider": last.get("provider") or first.get("provider"),
            "owner_pid": last.get("owner_pid") or first.get("owner_pid"),
            "status": (
                last.get("status") or "unknown"
                if terminal or last.get("event") == "turn.accepted"
                else "running"
            ),
            "stop_reason": last.get("stop_reason") if terminal else None,
            "duration_ms": duration_value,
            "event_count": len(events),
            "tool_count": tools,
            "failure_count": failures,
            "tool_failure_count": sum(1 for event in events if _is_tool_failure(event)),
            "provider_error_count": sum(1 for event in events if _is_provider_error(event)),
            "unknown_side_effect_count": sum(
                1 for event in events if _is_unknown_side_effect(event)
            ),
            "verification_status": next(
                (
                    event.get("verification_status")
                    for event in reversed(events)
                    if event.get("event") == "task.verification"
                ),
                None,
            ),
            "verification_completed": next(
                (
                    event.get("verification_completed")
                    for event in reversed(events)
                    if event.get("event") == "task.verification"
                ),
                None,
            ),
            "max_step_duration_ms": max(
                (
                    int(event["duration_ms"])
                    for event in events
                    if isinstance(event.get("duration_ms"), int)
                ),
                default=0,
            ),
            "timestamp_ms": first.get("timestamp_ms"),
        }

    @staticmethod
    def _read_rows(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(cast(dict[str, Any], value))
        return rows

    def resolve(self, identifier: str) -> Path:
        candidate = (self.root / identifier).resolve(strict=False)
        root = self.root.resolve(strict=False)
        if (
            candidate == root
            or root not in candidate.parents
            or candidate.suffix != ".jsonl"
            or candidate.name == TRACE_INDEX_FILENAME
        ):
            raise ValueError("trace file must stay within the local traces directory")
        return candidate

    def read(self, identifier: str) -> list[dict[str, Any]]:
        path = self.resolve(identifier)
        if not path.exists():
            raise FileNotFoundError(identifier)
        return self._read_rows(path)

    def detail(self, identifier: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Return one trace's current summary and ordered event rows."""
        path = self.resolve(identifier)
        if not path.exists():
            raise FileNotFoundError(identifier)
        events = self._read_rows(path)
        summary = self._summary_from_events(root=self.root, path=path, events=events)
        if summary is None:
            raise ValueError("trace is empty")
        return summary, events

    def list_summaries(
        self,
        *,
        limit: int = 50,
        session_key: str | None = None,
        chat_id: str | None = None,
        issues_only: bool = False,
        slow_only: bool = False,
        slow_threshold_ms: int = _DEFAULT_SLOW_THRESHOLD_MS,
    ) -> list[dict[str, Any]]:
        """Return latest compact summaries, preferring the append-only index."""
        limit = min(max(limit, 1), 100)
        self._reconcile_incomplete()
        latest_by_id: dict[str, dict[str, Any]] = {}
        try:
            for row in reversed(self._read_rows(self.index_path)):
                identifier = row.get("id")
                if not isinstance(identifier, str) or identifier in latest_by_id:
                    continue
                path = self.resolve(identifier)
                if path.exists():
                    current = self._summary_from_events(
                        root=self.root,
                        path=path,
                        events=self._read_rows(path),
                    )
                    latest_by_id[identifier] = current or row
        except (OSError, ValueError, json.JSONDecodeError):
            logger.warning("trace index is unreadable; falling back to trace files")

        # An in-flight turn has an index row at admission. Scanning also keeps
        # manually-created or pre-index traces inspectable during upgrades.
        if self.root.exists():
            for path in self.root.rglob("*.jsonl"):
                if path.name == TRACE_INDEX_FILENAME:
                    continue
                try:
                    identifier = path.relative_to(self.root).as_posix()
                    if identifier in latest_by_id:
                        continue
                    summary = self._summary_from_events(
                        root=self.root,
                        path=path,
                        events=self._read_rows(path),
                    )
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                if summary is not None:
                    latest_by_id[identifier] = summary

        rows = sorted(latest_by_id.values(), key=_trace_timestamp, reverse=True)
        if session_key is not None:
            rows = [row for row in rows if row.get("session_key") == session_key]
        if chat_id is not None:
            rows = [row for row in rows if row.get("chat_id") == chat_id]
        if issues_only:
            rows = [row for row in rows if int(row.get("failure_count") or 0) > 0]
        if slow_only:
            rows = [
                row
                for row in rows
                if max(
                    int(row.get("duration_ms") or 0),
                    int(row.get("max_step_duration_ms") or 0),
                ) >= slow_threshold_ms
            ]
        return rows[:limit]

    def delete_session(self, session_key: str) -> None:
        """Delete the lightweight traces associated with an explicitly deleted session."""
        session_name = _safe_component(session_key, "unbound")
        directory = self.root / session_name
        prefix = f"{session_name}/"
        self._active_trace_ids = {
            identifier
            for identifier in self._active_trace_ids
            if not identifier.startswith(prefix)
        }
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)
        self._compact_index(force=True)

    def cleanup(self, *, force: bool = False) -> None:
        """Bound trace retention without touching active or detailed recordings."""
        now = time.monotonic()
        if not force and now - self._last_cleanup_at < _CLEANUP_INTERVAL_SECONDS:
            return
        self._last_cleanup_at = now
        if not self.root.exists():
            return
        entries: list[tuple[Path, os.stat_result]] = []
        for path in self.root.rglob("*.jsonl"):
            if path.name == TRACE_INDEX_FILENAME:
                continue
            try:
                entries.append((path, path.stat()))
            except OSError:
                continue
        cutoff = time.time() - self.retention_days * 86_400
        retained: list[tuple[Path, os.stat_result]] = []
        for path, stat in entries:
            if self.retention_days > 0 and stat.st_mtime < cutoff:
                with suppress(OSError):
                    path.unlink(missing_ok=True)
            else:
                retained.append((path, stat))
        entries = retained
        entries.sort(key=lambda item: item[1].st_mtime_ns)
        total_bytes = sum(stat.st_size for _, stat in entries)
        while entries and (
            len(entries) > self.max_traces
            or (self.max_bytes > 0 and total_bytes > self.max_bytes)
        ):
            path, stat = entries.pop(0)
            with suppress(OSError):
                path.unlink(missing_ok=True)
            total_bytes -= stat.st_size
        for directory in sorted(self.root.rglob("*"), reverse=True):
            if directory.is_dir():
                with suppress(OSError):
                    directory.rmdir()
        self._compact_index(force=force)

    def start_turn(
        self,
        *,
        session_key: str | None,
        turn_id: str,
        channel: str,
        chat_id: str,
        model: str | None = None,
        provider: str | None = None,
        recording_directory: Path | None = None,
    ) -> TraceRun | None:
        if not self.enabled:
            return None
        self.cleanup()
        trace_file_id: str | None = None
        on_finish: TraceFinishCallback | None = None
        if recording_directory is not None:
            path = recording_directory / TRACE_EVENTS_FILENAME
        else:
            path = self._trace_path(session_key, turn_id)
            trace_file_id = path.relative_to(self.root).as_posix()
            self._active_trace_ids.add(trace_file_id)

            def _finish_index(summary: dict[str, Any]) -> None:
                identifier = summary.get("id")
                if isinstance(identifier, str):
                    self._active_trace_ids.discard(identifier)
                self._append_index(summary)

            on_finish = _finish_index
        trace = TraceRun(
            writer=TraceWriter(path),
            trace_id=f"trace:{_safe_component(turn_id, 'turn')}",
            session_key=session_key,
            turn_id=turn_id,
            channel=channel,
            chat_id=chat_id,
            model=model,
            provider=provider,
            owner_pid=os.getpid(),
            trace_file_id=trace_file_id,
            on_event=self.event_callback,
            on_finish=on_finish,
        )
        trace.emit("turn.accepted", status="accepted")
        if trace_file_id is not None:
            self._append_index(trace.summary(status="accepted"))
        return trace
