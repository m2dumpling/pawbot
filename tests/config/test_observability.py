"""Configuration contract tests for local execution tracing."""

from __future__ import annotations

from pawbot.config.schema import Config


def test_observability_config_accepts_json_aliases_and_round_trips() -> None:
    config = Config.model_validate({
        "observability": {
            "enabled": False,
            "otelEnabled": True,
            "otelServiceName": "pawbot-test",
            "otelSampleRatio": 0.25,
            "retentionDays": 7,
            "maxTraces": 12,
            "maxBytes": 4096,
            "rollingEnabled": False,
            "rollingTurnsPerSession": 8,
            "rollingRetentionHours": 6,
            "rollingMaxBytes": 8192,
        }
    })

    assert config.observability.enabled is False
    assert config.observability.otel_enabled is True
    assert config.observability.otel_service_name == "pawbot-test"
    assert config.observability.otel_sample_ratio == 0.25
    assert config.observability.retention_days == 7
    assert config.observability.max_traces == 12
    assert config.observability.max_bytes == 4096
    assert config.observability.rolling_enabled is False
    assert config.observability.rolling_turns_per_session == 8
    assert config.observability.rolling_retention_hours == 6
    assert config.observability.rolling_max_bytes == 8192
    assert config.model_dump(mode="json", by_alias=True)["observability"] == {
        "enabled": False,
        "otelEnabled": True,
        "otelServiceName": "pawbot-test",
        "otelSampleRatio": 0.25,
        "retentionDays": 7,
        "maxTraces": 12,
        "maxBytes": 4096,
        "rollingEnabled": False,
        "rollingTurnsPerSession": 8,
        "rollingRetentionHours": 6,
        "rollingMaxBytes": 8192,
    }


def test_observability_config_rejects_invalid_retention_limits() -> None:
    for payload in (
        {"retentionDays": -1},
        {"maxTraces": 0},
        {"maxBytes": -1},
        {"otelSampleRatio": 1.1},
    ):
        try:
            Config.model_validate({"observability": payload})
        except ValueError:
            continue
        raise AssertionError(f"invalid observability payload was accepted: {payload}")
