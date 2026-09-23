"""Optional OpenTelemetry bridge for the existing local turn trace stream.

The module has no SDK import at module import time. OTLP is enabled only when
the application opts in *and* an OTLP endpoint is configured. Exported spans
and metrics use an allowlist of metadata; prompts, answers, tool arguments,
tool results, session IDs, and user IDs are never attached.
"""

from __future__ import annotations

import atexit
import os
import threading
from dataclasses import dataclass, field
from typing import Any, cast

from loguru import logger

_LOCK = threading.RLock()
_bridge_instance: OpenTelemetryTraceBridge | None = None


@dataclass(slots=True)
class _ActiveTrace:
    root: Any
    llm_spans: dict[str, list[Any]] = field(default_factory=dict)
    tool_spans: dict[str, Any] = field(default_factory=dict)
    approval_spans: dict[str, Any] = field(default_factory=dict)


class OpenTelemetryTraceBridge:
    """Translate bounded Pawbot lifecycle events to standard OTel signals."""

    def __init__(self, tracer: Any, meter: Any) -> None:
        from opentelemetry import trace
        from opentelemetry.trace import Status, StatusCode

        self._trace_api = trace
        self._status = Status
        self._status_code = StatusCode
        self._tracer = tracer
        self._active: dict[str, _ActiveTrace] = {}
        self._turns = meter.create_counter("pawbot.agent.turns", unit="1")
        self._turn_duration = meter.create_histogram("pawbot.agent.turn.duration", unit="ms")
        self._llm_requests = meter.create_counter("pawbot.agent.llm.requests", unit="1")
        self._llm_tokens = meter.create_counter("pawbot.agent.llm.tokens", unit="{token}")
        self._tool_calls = meter.create_counter("pawbot.agent.tool.calls", unit="1")
        self._tool_duration = meter.create_histogram("pawbot.agent.tool.duration", unit="ms")
        self._approvals = meter.create_counter("pawbot.agent.approvals", unit="1")
        self._unknown_effects = meter.create_counter("pawbot.agent.unknown_side_effects", unit="1")
        self._task_verifications = meter.create_counter("pawbot.agent.task.verifications", unit="1")
        self._turn_cost = meter.create_histogram("pawbot.agent.turn.cost", unit="USD")

    def _root_context(self, state: _ActiveTrace) -> Any:
        return self._trace_api.set_span_in_context(state.root)

    @staticmethod
    def _event_key(event: dict[str, Any]) -> str:
        return str(event.get("trace_id") or "")

    @staticmethod
    def _status_name(event: dict[str, Any]) -> str:
        status = str(event.get("status") or "unknown").lower()
        if status in {"completed", "received", "accepted", "approved", "succeeded", "ok"}:
            return "success"
        if status in {"cancelled", "canceled", "unknown_side_effect"}:
            return "uncertain"
        if status in {"denied", "blocked", "failed", "error", "incomplete"}:
            return "error"
        return "other"

    @staticmethod
    def _tool_kind(name: str) -> str:
        return "mcp" if name.startswith("mcp_") else "local"

    def _start_root(self, event: dict[str, Any]) -> None:
        trace_id = self._event_key(event)
        if not trace_id:
            return
        attrs: dict[str, str] = {"gen_ai.operation.name": "create_agent"}
        channel = event.get("channel")
        if isinstance(channel, str) and channel:
            attrs["pawbot.channel"] = channel
        span = self._tracer.start_span("pawbot.agent.turn", attributes=attrs)
        with _LOCK:
            previous = self._active.pop(trace_id, None)
            if previous is not None:
                previous.root.end()
            self._active[trace_id] = _ActiveTrace(root=span)

    def _end_span(self, span: Any, *, status: str, error_type: str | None = None) -> None:
        span.set_attribute("pawbot.outcome", status)
        if status == "error":
            span.set_status(self._status(self._status_code.ERROR))
            if error_type:
                span.set_attribute("error.type", error_type[:120])
        span.end()

    def __call__(self, event: dict[str, Any]) -> None:
        """Consume one already-redacted event; never serialize payload previews."""
        try:
            self._consume(event)
        except Exception:
            logger.exception("failed to export Pawbot event to OpenTelemetry")

    def _consume(self, event: dict[str, Any]) -> None:
        name = str(event.get("event") or "")
        trace_id = self._event_key(event)
        if name == "turn.accepted":
            self._start_root(event)
            return
        with _LOCK:
            state = self._active.get(trace_id)
        if state is None:
            return

        if name == "llm.request_started":
            iteration = str(event.get("iteration", "?"))
            model = str(event.get("model") or "")
            provider = str(event.get("provider") or "unknown")
            attrs: dict[str, Any] = {
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": provider,
                "pawbot.iteration": iteration,
            }
            if model:
                attrs["gen_ai.request.model"] = model
            attempt = event.get("attempt")
            if isinstance(attempt, int):
                attrs["pawbot.request.attempt"] = attempt
            span_name = f"chat {model}" if model else "chat"
            span = self._tracer.start_span(
                span_name,
                context=self._root_context(state),
                attributes=attrs,
            )
            state.llm_spans.setdefault(iteration, []).append(span)
            self._llm_requests.add(1, {"provider": provider})
            return

        if name in {"llm.retry", "llm.request_failed", "llm.response"}:
            iteration = str(event.get("iteration", "?"))
            spans = state.llm_spans.get(iteration, [])
            if name == "llm.retry":
                if spans:
                    span = spans.pop()
                    span.add_event("pawbot.llm.retry", {"pawbot.retry_attempt": int(event.get("retry_attempt") or 1)})
                    self._end_span(span, status="error", error_type="retry")
                return
            if name == "llm.request_failed":
                while spans:
                    self._end_span(spans.pop(), status="error", error_type="provider_error")
                state.llm_spans.pop(iteration, None)
                return
            span = spans.pop() if spans else None
            if not spans:
                state.llm_spans.pop(iteration, None)
            if span is not None:
                usage = event.get("usage")
                if isinstance(usage, dict):
                    usage_values = cast(dict[str, Any], usage)
                    in_tokens = usage_values.get("input_tokens")
                    out_tokens = usage_values.get("output_tokens")
                    if isinstance(in_tokens, int):
                        span.set_attribute("gen_ai.usage.input_tokens", in_tokens)
                        self._llm_tokens.add(in_tokens, {"direction": "input"})
                    if isinstance(out_tokens, int):
                        span.set_attribute("gen_ai.usage.output_tokens", out_tokens)
                        self._llm_tokens.add(out_tokens, {"direction": "output"})
                status = "error" if self._status_name(event) == "error" else "success"
                self._end_span(span, status=status, error_type="provider_error" if status == "error" else None)
            return

        if name == "tool.planned":
            call_id = str(event.get("call_id") or f"{event.get('iteration')}:{event.get('tool_name')}")
            tool_name = str(event.get("tool_name") or "unknown")
            span = self._tracer.start_span(
                f"execute_tool {tool_name}",
                context=self._root_context(state),
                attributes={
                    "gen_ai.operation.name": "execute_tool",
                    "gen_ai.tool.name": tool_name,
                    "pawbot.tool.kind": self._tool_kind(tool_name),
                    "pawbot.tool.phase": "planned",
                },
            )
            state.tool_spans[call_id] = span
            return

        if name == "tool.started":
            call_id = str(event.get("call_id") or f"{event.get('iteration')}:{event.get('tool_name')}")
            span = state.tool_spans.get(call_id)
            if span is not None:
                span.set_attribute("pawbot.tool.phase", "started")
            return

        if name in {"tool.finished", "tool.cancelled", "tool.unknown_side_effect"}:
            call_id = str(event.get("call_id") or f"{event.get('iteration')}:{event.get('tool_name')}")
            span = state.tool_spans.pop(call_id, None)
            tool_name = str(event.get("tool_name") or "unknown")
            outcome = self._status_name(event)
            if span is not None:
                span.set_attribute("pawbot.tool.phase", "finished")
                duration = event.get("duration_ms")
                if isinstance(duration, (int, float)):
                    span.set_attribute("pawbot.tool.duration_ms", max(0, int(duration)))
                self._end_span(span, status="error" if outcome == "error" else "success")
            self._tool_calls.add(1, {"kind": self._tool_kind(tool_name), "outcome": outcome})
            if isinstance(event.get("duration_ms"), (int, float)):
                self._tool_duration.record(
                    max(0, int(cast(int | float, event["duration_ms"]))),
                    {"kind": self._tool_kind(tool_name), "outcome": outcome},
                )
            if outcome == "uncertain":
                self._unknown_effects.add(1)
            return

        if name == "tool.approval_requested":
            approval_id = str(event.get("approval_id") or event.get("call_id") or uuid_key(event))
            tool_name = str(event.get("tool_name") or "unknown")
            state.approval_spans[approval_id] = self._tracer.start_span(
                "pawbot.tool.approval",
                context=self._root_context(state),
                attributes={"pawbot.tool.name": tool_name, "pawbot.approval.status": "waiting"},
            )
            return

        if name == "tool.approval_resolved":
            approval_id = str(event.get("approval_id") or event.get("call_id") or "")
            span = state.approval_spans.pop(approval_id, None)
            decision = str(event.get("status") or "unknown")
            if span is not None:
                span.set_attribute("pawbot.approval.status", decision)
                self._end_span(span, status="error" if decision == "denied" else "success")
            self._approvals.add(1, {"decision": "approved" if decision == "approved" else "denied"})
            return

        if name == "task.verification":
            status = str(event.get("verification_status") or "unknown")
            self._task_verifications.add(1, {"status": status})
            state.root.add_event("pawbot.task.verification", {"pawbot.verification.status": status})
            return

        if name == "agent.completed":
            cost = _nested_cost(event.get("budget"))
            if cost is not None:
                self._turn_cost.record(cost, {"outcome": self._status_name(event)})
            return

        terminal_statuses = {
            "turn.completed": "success",
            "turn.failed": "error",
            "turn.cancelled": "uncertain",
            "turn.incomplete": "error",
        }
        if name in terminal_statuses:
            status = terminal_statuses[name]
            duration = event.get("duration_ms")
            state.root.set_attribute("pawbot.stop_reason", str(event.get("stop_reason") or "unknown"))
            state.root.set_attribute("pawbot.outcome", status)
            if isinstance(duration, (int, float)):
                self._turn_duration.record(max(0, int(duration)), {"outcome": status})
            self._turns.add(1, {"outcome": status})
            self._end_span(state.root, status="error" if status != "success" else "success")
            for spans in state.llm_spans.values():
                for span in spans:
                    span.end()
            for span in state.tool_spans.values():
                span.end()
            for span in state.approval_spans.values():
                span.end()
            with _LOCK:
                self._active.pop(trace_id, None)
            return

        # Checkpoint, recovery, budget, and other lifecycle metadata remain
        # visible as bounded events under the root span.
        state.root.add_event(
            name[:100] or "pawbot.event",
            {"pawbot.status": self._status_name(event)},
        )


def uuid_key(event: dict[str, Any]) -> str:
    return str(event.get("sequence") or "approval")


def _nested_cost(value: Any) -> float | None:
    if not isinstance(value, dict):
        return None
    usage = cast(dict[str, Any], value).get("usage")
    if isinstance(usage, dict):
        cost = cast(dict[str, Any], usage).get("cost_usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            return float(cost)
    return None


def configure_otlp_export(
    *,
    enabled: bool,
    service_name: str = "pawbot",
    sample_ratio: float = 1.0,
) -> bool:
    """Install a batched OTLP pipeline, or stay completely local when absent."""
    global _bridge_instance
    if not enabled:
        return False
    shared_endpoint = bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip())
    traces_endpoint = shared_endpoint or bool(
        os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "").strip()
    )
    metrics_endpoint = shared_endpoint or bool(
        os.environ.get("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "").strip()
    )
    if not traces_endpoint and not metrics_endpoint:
        logger.info("OpenTelemetry enabled without an OTLP endpoint; export remains disabled")
        return False
    with _LOCK:
        if _bridge_instance is not None:
            return True
        try:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            from opentelemetry.sdk.resources import SERVICE_NAME, Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
        except ImportError as exc:
            logger.warning("OpenTelemetry export was requested, install pawbot-ai[otel]: {}", exc)
            return False

        resource = Resource.create({SERVICE_NAME: service_name})
        tracer_provider = TracerProvider(
            resource=resource,
            sampler=ParentBased(TraceIdRatioBased(min(1.0, max(0.0, sample_ratio)))),
        )
        if traces_endpoint:
            tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        metric_readers = (
            [PeriodicExportingMetricReader(OTLPMetricExporter())]
            if metrics_endpoint
            else []
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=metric_readers)
        bridge = OpenTelemetryTraceBridge(
            tracer_provider.get_tracer("pawbot.agent"),
            meter_provider.get_meter("pawbot.agent"),
        )
        _bridge_instance = bridge
        atexit.register(tracer_provider.shutdown)
        atexit.register(meter_provider.shutdown)
        logger.info("OpenTelemetry OTLP export enabled for service {}", service_name)
        return True


def emit_trace_event(event: dict[str, Any]) -> None:
    """Forward a local trace event if opt-in OTLP export was configured."""
    bridge = _bridge_instance
    if bridge is not None:
        bridge(event)


def capture_trace_context() -> dict[str, str]:
    """Capture only W3C traceparent/tracestate while a channel span is active."""
    if _bridge_instance is None:
        return {}
    try:
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

        carrier: dict[str, str] = {}
        TraceContextTextMapPropagator().inject(carrier)
        return carrier
    except Exception:
        logger.debug("Could not capture inbound W3C trace context")
        return {}


def attach_trace_context(carrier: dict[str, str]) -> Any | None:
    """Temporarily attach a W3C parent around the Pawbot turn admission boundary."""
    if _bridge_instance is None or not carrier:
        return None
    try:
        from opentelemetry import context as otel_context
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

        return otel_context.attach(TraceContextTextMapPropagator().extract(carrier))
    except Exception:
        logger.debug("Could not attach inbound W3C trace context")
        return None


def detach_trace_context(token: Any | None) -> None:
    """Restore the previous async-task context after the trace root is created."""
    if token is None:
        return
    try:
        from opentelemetry import context as otel_context

        otel_context.detach(token)
    except Exception:
        logger.debug("Could not detach inbound W3C trace context")


def reset_for_tests() -> None:
    """Reset the process bridge; intended for isolated unit tests only."""
    global _bridge_instance
    with _LOCK:
        _bridge_instance = None


__all__ = [
    "OpenTelemetryTraceBridge",
    "attach_trace_context",
    "capture_trace_context",
    "configure_otlp_export",
    "detach_trace_context",
    "emit_trace_event",
]
