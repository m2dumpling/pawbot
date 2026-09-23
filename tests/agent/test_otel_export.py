from __future__ import annotations

from pathlib import Path

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from pawbot.agent import otel
from pawbot.agent.observability import TraceRun, TraceWriter
from pawbot.bus.events import InboundMessage
from pawbot.bus.queue import MessageBus


def test_trace_events_export_parented_spans_without_content_or_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])
    bridge = otel.OpenTelemetryTraceBridge(
        tracer_provider.get_tracer("pawbot.test"),
        meter_provider.get_meter("pawbot.test"),
    )
    monkeypatch.setattr(otel, "_bridge_instance", bridge)

    trace = TraceRun(
        writer=TraceWriter(tmp_path / "trace.jsonl"),
        trace_id="trace-secret-id",
        session_key="user-secret-session",
        turn_id="turn-secret-id",
        channel="websocket",
        chat_id="private-chat-id",
        model="test-model",
        provider="openai",
    )
    trace.emit("turn.accepted", status="accepted")
    trace.emit(
        "llm.request_started",
        status="running",
        iteration=0,
        attempt=1,
        messages_preview="private prompt text",
    )
    trace.emit(
        "llm.response",
        status="received",
        iteration=0,
        usage={"input_tokens": 11, "output_tokens": 4},
        content_preview="private model answer",
    )
    trace.emit(
        "tool.planned",
        status="planned",
        call_id="call-1",
        tool_name="read_file",
        arguments_preview="private file path",
    )
    trace.emit("tool.started", status="running", call_id="call-1", tool_name="read_file")
    trace.emit(
        "tool.finished",
        status="succeeded",
        call_id="call-1",
        tool_name="read_file",
        duration_ms=23,
        result_preview="private file contents",
    )
    trace.finish(status="completed", stop_reason="completed")

    spans = span_exporter.get_finished_spans()
    root = next(span for span in spans if span.name == "pawbot.agent.turn")
    children = [span for span in spans if span is not root]
    assert {span.name for span in children} == {"chat test-model", "execute_tool read_file"}
    assert all(span.parent is not None and span.parent.span_id == root.context.span_id for span in children)
    assert root.attributes["gen_ai.operation.name"] == "create_agent"
    llm_span = next(span for span in children if span.name.startswith("chat "))
    assert llm_span.attributes["gen_ai.request.model"] == "test-model"
    assert llm_span.attributes["gen_ai.usage.input_tokens"] == 11
    exported = repr([
        (span.name, span.attributes, [(event.name, event.attributes) for event in span.events])
        for span in spans
    ])
    assert "private prompt text" not in exported
    assert "private model answer" not in exported
    assert "private file path" not in exported
    assert "private file contents" not in exported
    assert "user-secret-session" not in exported
    assert "private-chat-id" not in exported
    assert "trace-secret-id" not in exported

    metrics = metric_reader.get_metrics_data()
    assert metrics is not None
    instrument_names = {
        metric.name
        for resource_metrics in metrics.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
    }
    assert "pawbot.agent.turns" in instrument_names
    assert "pawbot.agent.tool.calls" in instrument_names


def test_otlp_export_stays_off_without_explicit_enable_and_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    otel.reset_for_tests()
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", raising=False)

    assert not otel.configure_otlp_export(enabled=False)
    assert not otel.configure_otlp_export(enabled=True)
    assert otel._bridge_instance is None


@pytest.mark.asyncio
async def test_message_bus_propagates_channel_parent_into_agent_root_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])
    bridge = otel.OpenTelemetryTraceBridge(
        tracer_provider.get_tracer("pawbot.test"),
        meter_provider.get_meter("pawbot.test"),
    )
    monkeypatch.setattr(otel, "_bridge_instance", bridge)
    bus = MessageBus()
    tracer = tracer_provider.get_tracer("pawbot.channel")

    with tracer.start_as_current_span("channel.request"):
        await bus.publish_inbound(InboundMessage(
            channel="websocket",
            sender_id="sender-private",
            chat_id="chat-private",
            content="hello",
        ))
        message = await bus.consume_inbound()
        assert "traceparent" in message.trace_context
        parent_token = otel.attach_trace_context(message.trace_context)
        try:
            trace = TraceRun(
                writer=TraceWriter(tmp_path / "propagated.jsonl"),
                trace_id="trace-propagated",
                session_key="private-session",
                turn_id="private-turn",
                channel=message.channel,
                chat_id=message.chat_id,
            )
            trace.emit("turn.accepted", status="accepted")
        finally:
            otel.detach_trace_context(parent_token)
        trace.finish(status="completed", stop_reason="completed")

    spans = exporter.get_finished_spans()
    root = next(span for span in spans if span.name == "pawbot.agent.turn")
    channel_span = next(span for span in spans if span.name == "channel.request")
    assert root.parent is not None
    assert root.parent.span_id == channel_span.context.span_id
    assert "sender-private" not in repr(root.attributes)
    assert "chat-private" not in repr(root.attributes)
