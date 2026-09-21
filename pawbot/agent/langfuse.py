"""Optional Langfuse exporter for the local Agent execution trace.

The local trace remains the source of truth.  This module is deliberately an
adapter at the trace boundary: Langfuse is optional, export failures are
isolated from the Agent turn, and the core runtime does not import the
Langfuse SDK until the feature is configured and installed.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol, cast

from loguru import logger

from pawbot import __version__
from pawbot.agent.observability import redact_text


class TraceExporter(Protocol):
    """Small synchronous boundary used by TraceStore callbacks."""

    def emit(self, event: dict[str, Any]) -> None:
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True, slots=True)
class LangfuseSettings:
    enabled: bool
    public_key: str
    secret_key: str
    base_url: str
    environment: str
    sample_rate: float
    capture_prompts: bool
    capture_tool_results: bool

    @property
    def configured(self) -> bool:
        return bool(self.public_key and self.secret_key)


def _env_or_config(config_value: Any, env_name: str, default: str = "") -> str:
    env_value = os.environ.get(env_name, "").strip()
    value = str(config_value or "").strip()
    return env_value or value or default


def resolve_langfuse_settings(config: Any) -> LangfuseSettings:
    """Resolve persisted settings with environment variables as a fallback."""
    observability = getattr(config, "observability", config)
    public_key = _env_or_config(
        getattr(observability, "langfuse_public_key", ""),
        "LANGFUSE_PUBLIC_KEY",
    )
    secret_key = _env_or_config(
        getattr(observability, "langfuse_secret_key", ""),
        "LANGFUSE_SECRET_KEY",
    )
    base_url = _env_or_config(
        getattr(observability, "langfuse_base_url", ""),
        "LANGFUSE_BASE_URL",
        "https://cloud.langfuse.com",
    ).rstrip("/")
    environment = _env_or_config(
        getattr(observability, "langfuse_environment", ""),
        "LANGFUSE_TRACING_ENVIRONMENT",
        "production",
    )
    raw_sample_rate = getattr(observability, "langfuse_sample_rate", 1.0)
    try:
        sample_rate = min(1.0, max(0.0, float(raw_sample_rate)))
    except (TypeError, ValueError):
        sample_rate = 1.0
    env_enabled = bool(
        os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
        and os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
    )
    configured_enabled = getattr(observability, "langfuse_enabled", None)
    enabled = (
        True
        if env_enabled
        else bool(configured_enabled)
    )
    return LangfuseSettings(
        enabled=enabled,
        public_key=public_key,
        secret_key=secret_key,
        base_url=base_url,
        environment=environment,
        sample_rate=sample_rate,
        capture_prompts=bool(getattr(observability, "langfuse_capture_prompts", False)),
        capture_tool_results=bool(
            getattr(observability, "langfuse_capture_tool_results", False)
        ),
    )


def langfuse_sdk_installed() -> bool:
    """Return whether the optional SDK can be imported in this environment."""
    return importlib.util.find_spec("langfuse") is not None


def langfuse_exporter_available(config: Any) -> bool:
    """Return whether the configured runtime can use the native exporter."""
    settings = resolve_langfuse_settings(config)
    return settings.enabled and settings.configured and langfuse_sdk_installed()


def _stable_trace_id(value: str) -> str:
    """Map Pawbot's readable trace id to Langfuse's 32-hex trace id format."""
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:32]


def _status_level(status: Any) -> str:
    normalized = str(status or "").lower()
    if normalized in {"error", "failed", "blocked", "cancelled", "incomplete"}:
        return "ERROR"
    if normalized in {"unknown_side_effect", "waiting", "retrying"}:
        return "WARNING"
    return "DEFAULT"


def _usage_details(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    value = cast(dict[str, Any], value)
    aliases = {
        "input": ("input", "input_tokens", "prompt_tokens"),
        "output": ("output", "output_tokens", "completion_tokens"),
        "total": ("total", "total_tokens"),
    }
    result: dict[str, int] = {}
    for target, names in aliases.items():
        for name in names:
            raw = value.get(name)
            if isinstance(raw, int) and not isinstance(raw, bool):
                result[target] = max(0, raw)
                break
    return result or None


def _event_metadata(event: dict[str, Any]) -> dict[str, Any]:
    excluded = {
        "schema_version",
        "sequence",
        "event",
        "trace_id",
        "session_key",
        "turn_id",
        "channel",
        "chat_id",
        "timestamp_ms",
        "status",
        "duration_ms",
        "error",
        "outcome",
    }
    return {
        key: value
        for key, value in event.items()
        if key not in excluded and value is not None
    }


def _trace_context(trace_id: str, parent_span_id: str | None = None) -> dict[str, str]:
    context = {"trace_id": trace_id}
    if parent_span_id:
        context["parent_span_id"] = parent_span_id
    return context


class LangfuseExporter:
    """Export normalized Trace events as Langfuse observations and scores."""

    def __init__(self, client: Any, settings: LangfuseSettings) -> None:
        self.client = client
        self.settings = settings
        self._roots: dict[str, Any] = {}
        self._generations: dict[tuple[str, int], Any] = {}
        self._tools: dict[tuple[str, str], Any] = {}
        self._failed = False
        self._failure_logged = False

    @classmethod
    def from_config(cls, config: Any) -> "LangfuseExporter | None":
        settings = resolve_langfuse_settings(config)
        if not settings.enabled or not settings.configured:
            return None
        if not langfuse_sdk_installed():
            logger.warning(
                "Langfuse is configured but the optional SDK is not installed; "
                "run `pawbot plugins enable langfuse`"
            )
            return None
        try:
            from langfuse import Langfuse

            client = Langfuse(
                public_key=settings.public_key,
                secret_key=settings.secret_key,
                base_url=settings.base_url,
                environment=settings.environment,
                release=__version__,
                sample_rate=settings.sample_rate,
            )
        except Exception:
            logger.exception("failed to initialize Langfuse exporter")
            return None
        return cls(client, settings)

    @property
    def enabled(self) -> bool:
        return not self._failed

    def _root(self, event: dict[str, Any]) -> tuple[str, Any]:
        pawbot_trace_id = str(event.get("trace_id") or event.get("turn_id") or "unknown")
        langfuse_trace_id = _stable_trace_id(pawbot_trace_id)
        root = self._roots.get(langfuse_trace_id)
        if root is not None:
            return langfuse_trace_id, root
        root = self.client.start_observation(
            trace_context=_trace_context(langfuse_trace_id),
            name=(
                "pawbot.replay.turn"
                if str(event.get("channel") or "") == "replay"
                else "pawbot.agent.turn"
            ),
            as_type="agent",
            input={
                "session_key": event.get("session_key"),
                "channel": event.get("channel"),
                "chat_id": event.get("chat_id"),
            },
            metadata={
                "pawbot_trace_id": pawbot_trace_id,
                "turn_id": event.get("turn_id"),
                "provider": event.get("provider"),
                "model": event.get("model"),
            },
            version=__version__,
        )
        self._roots[langfuse_trace_id] = root
        with suppress(Exception):
            root.update_trace(
                session_id=str(event.get("session_key") or event.get("turn_id") or ""),
                version=__version__,
                metadata={"channel": event.get("channel")},
            )
        return langfuse_trace_id, root

    def _create_event(self, trace_id: str, root: Any, event: dict[str, Any]) -> None:
        name = str(event.get("event") or "agent.event")
        self.client.create_event(
            trace_context=_trace_context(trace_id, getattr(root, "id", None)),
            name=name,
            input={
                "status": event.get("status"),
                "iteration": event.get("iteration"),
            },
            output=event.get("outcome"),
            metadata=_event_metadata(event),
            level=_status_level(event.get("status")),
            status_message=redact_text(event.get("error")) if event.get("error") else None,
        )

    def _emit_generation(self, trace_id: str, root: Any, event: dict[str, Any]) -> None:
        event_name = str(event.get("event") or "")
        try:
            iteration = int(event.get("iteration") or 0)
        except (TypeError, ValueError):
            iteration = 0
        key = (trace_id, iteration)
        if event_name == "llm.request_started":
            input_payload: dict[str, Any] = {
                "message_count": event.get("message_count"),
                "context_window_tokens": event.get("context_window_tokens"),
                "tools_available": event.get("tools_available"),
            }
            if self.settings.capture_prompts and event.get("messages_preview") is not None:
                input_payload["messages"] = event.get("messages_preview")
            generation = self.client.start_observation(
                trace_context=_trace_context(trace_id, getattr(root, "id", None)),
                name=f"llm.iteration.{iteration}",
                as_type="generation",
                input=input_payload,
                model=event.get("model"),
                metadata=_event_metadata(event),
                version=__version__,
            )
            self._generations[key] = generation
            return
        generation = self._generations.pop(key, None)
        if generation is None:
            self._create_event(trace_id, root, event)
            return
        output: Any = {
            "finish_reason": event.get("finish_reason"),
            "tool_names": event.get("tool_names"),
            "content_chars": event.get("content_chars"),
        }
        if self.settings.capture_prompts:
            output["content"] = event.get("content_preview")
        usage = _usage_details(event.get("usage"))
        with suppress(Exception):
            generation.update(
                output=output,
                usage_details=usage,
                level=_status_level(event.get("status")),
                status_message=redact_text(event.get("error")) if event.get("error") else None,
                metadata=_event_metadata(event),
            )
            generation.end()

    def _emit_tool(self, trace_id: str, root: Any, event: dict[str, Any]) -> None:
        name = str(event.get("event") or "")
        call_id = str(event.get("call_id") or f"{event.get('iteration', '?')}:{event.get('tool_name', 'unknown')}")
        key = (trace_id, call_id)
        if name in {"tool.started", "provider_tool.started"}:
            input_payload: Any = {"tool_name": event.get("tool_name")}
            if self.settings.capture_tool_results:
                input_payload["arguments"] = event.get("arguments_preview")
            tool = self.client.start_observation(
                trace_context=_trace_context(trace_id, getattr(root, "id", None)),
                name=f"tool.{event.get('tool_name') or 'unknown'}",
                as_type="tool",
                input=input_payload,
                metadata=_event_metadata(event),
                version=__version__,
            )
            self._tools[key] = tool
            return
        tool = self._tools.pop(key, None)
        if tool is None:
            self._create_event(trace_id, root, event)
            return
        output: Any = {"status": event.get("status"), "result_type": event.get("result_type")}
        if self.settings.capture_tool_results:
            output["result"] = event.get("result_preview")
        with suppress(Exception):
            tool.update(
                output=output,
                level=_status_level(event.get("status")),
                status_message=redact_text(event.get("error")) if event.get("error") else None,
                metadata=_event_metadata(event),
            )
            tool.end()

    def _emit_task_score(self, trace_id: str, event: dict[str, Any]) -> None:
        status = str(event.get("verification_status") or event.get("status") or "not_evaluable")
        try:
            self.client.create_score(
                trace_id=trace_id,
                name="task_status",
                value=status,
                data_type="CATEGORICAL",
                comment=redact_text(event.get("verification_reason"))
                if event.get("verification_reason")
                else None,
                metadata={
                    "verification_completed": event.get("verification_completed"),
                    "verification_failures": event.get("verification_failures"),
                },
            )
        except Exception:
            logger.debug("failed to export Langfuse task score", exc_info=True)

    def emit(self, event: dict[str, Any]) -> None:
        if self._failed:
            return
        try:
            trace_id, root = self._root(event)
            name = str(event.get("event") or "")
            if name in {"llm.request_started", "llm.response", "llm.request_failed"}:
                self._emit_generation(trace_id, root, event)
            elif name in {
                "tool.started",
                "tool.finished",
                "tool.cancelled",
                "provider_tool.started",
                "provider_tool.completed",
                "provider_tool.error",
            }:
                self._emit_tool(trace_id, root, event)
            else:
                self._create_event(trace_id, root, event)
            if name == "task.verification":
                self._emit_task_score(trace_id, event)
            if name in {
                "turn.completed",
                "turn.failed",
                "turn.cancelled",
                "turn.incomplete",
                "replay.compared",
            }:
                with suppress(Exception):
                    root.update(
                        output=event.get("outcome"),
                        level=_status_level(event.get("status")),
                        status_message=redact_text(event.get("error"))
                        if event.get("error")
                        else None,
                        metadata=_event_metadata(event),
                    )
                    root.end()
                self._roots.pop(trace_id, None)
        except Exception:
            self._failed = True
            if not self._failure_logged:
                self._failure_logged = True
                logger.exception("Langfuse export failed; local tracing remains active")

    def flush(self) -> None:
        if self._failed:
            return
        try:
            self.client.flush()
        except Exception:
            logger.exception("failed to flush Langfuse events")

    def close(self) -> None:
        self.flush()
        with suppress(Exception):
            self.client.shutdown()


def create_langfuse_client(config: Any) -> Any | None:
    """Create a raw client for CLI connection checks."""
    settings = resolve_langfuse_settings(config)
    if not settings.configured or not langfuse_sdk_installed():
        return None
    from langfuse import Langfuse

    return Langfuse(
        public_key=settings.public_key,
        secret_key=settings.secret_key,
        base_url=settings.base_url,
        environment=settings.environment,
        release=__version__,
        sample_rate=settings.sample_rate,
    )


__all__ = [
    "LangfuseExporter",
    "LangfuseSettings",
    "TraceExporter",
    "create_langfuse_client",
    "langfuse_exporter_available",
    "langfuse_sdk_installed",
    "resolve_langfuse_settings",
]
