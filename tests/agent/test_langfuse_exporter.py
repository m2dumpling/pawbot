from __future__ import annotations

from typing import Any

from pawbot.agent.langfuse import LangfuseExporter, LangfuseSettings


class _Observation:
    def __init__(self, identifier: str) -> None:
        self.id = identifier
        self.updates: list[dict[str, Any]] = []
        self.ended = False

    def update(self, **kwargs: Any) -> None:
        self.updates.append(kwargs)

    def update_trace(self, **kwargs: Any) -> None:
        self.updates.append({"trace": kwargs})

    def end(self, **_kwargs: Any) -> None:
        self.ended = True


class _Client:
    def __init__(self) -> None:
        self.observations: list[tuple[str, _Observation]] = []
        self.events: list[dict[str, Any]] = []
        self.scores: list[dict[str, Any]] = []
        self._next_id = 0

    def start_observation(self, **kwargs: Any) -> _Observation:
        self._next_id += 1
        observation = _Observation(f"obs-{self._next_id}")
        self.observations.append((str(kwargs["as_type"]), observation))
        return observation

    def create_event(self, **kwargs: Any) -> None:
        self.events.append(kwargs)

    def create_score(self, **kwargs: Any) -> None:
        self.scores.append(kwargs)

    def flush(self) -> None:
        pass

    def shutdown(self) -> None:
        pass


def _settings(**overrides: Any) -> LangfuseSettings:
    values: dict[str, Any] = {
        "enabled": True,
        "public_key": "pk-test",
        "secret_key": "sk-test",
        "base_url": "https://langfuse.example",
        "environment": "test",
        "sample_rate": 1.0,
        "capture_prompts": True,
        "capture_tool_results": True,
    }
    values.update(overrides)
    return LangfuseSettings(**values)


def _event(name: str, **fields: Any) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "sequence": 1,
        "event": name,
        "trace_id": "trace:turn-1",
        "session_key": "websocket:chat-1",
        "turn_id": "turn-1",
        "channel": "websocket",
        "chat_id": "chat-1",
        "timestamp_ms": 1,
        **fields,
    }


def test_exporter_maps_agent_llm_tool_and_task_events() -> None:
    client = _Client()
    exporter = LangfuseExporter(client, _settings())

    exporter.emit(_event("turn.accepted", status="accepted"))
    exporter.emit(
        _event(
            "llm.request_started",
            status="running",
            iteration=1,
            message_count=3,
            messages_preview="user: summarize the news",
        )
    )
    exporter.emit(
        _event(
            "llm.response",
            status="received",
            iteration=1,
            finish_reason="tool_calls",
            content_preview="",
            content_chars=0,
            usage={"input_tokens": 12, "output_tokens": 8, "total_tokens": 20},
        )
    )
    exporter.emit(
        _event(
            "tool.started",
            status="running",
            call_id="call-1",
            tool_name="web_search",
            arguments_preview='{"query":"Los Angeles news"}',
        )
    )
    exporter.emit(
        _event(
            "tool.finished",
            status="succeeded",
            call_id="call-1",
            tool_name="web_search",
            result_preview="three search results",
            result_type="list",
        )
    )
    exporter.emit(
        _event(
            "task.verification",
            status="completed",
            verification_status="passed",
            verification_completed=True,
        )
    )
    exporter.emit(_event("turn.completed", status="completed", outcome={"ok": True}))

    observation_types = [kind for kind, _observation in client.observations]
    assert "agent" in observation_types
    assert "generation" in observation_types
    assert "tool" in observation_types
    assert any(event["name"] == "task.verification" for event in client.events)
    assert client.scores[0]["name"] == "task_status"
    assert client.scores[0]["value"] == "passed"
    assert all(observation.ended for _kind, observation in client.observations)


def test_exporter_failure_does_not_escape_to_agent() -> None:
    class BrokenClient(_Client):
        def start_observation(self, **_kwargs: Any) -> _Observation:
            raise RuntimeError("Langfuse unavailable")

    exporter = LangfuseExporter(BrokenClient(), _settings())
    exporter.emit(_event("turn.accepted", status="accepted"))
    assert exporter.enabled is False
