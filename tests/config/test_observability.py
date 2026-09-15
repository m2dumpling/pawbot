"""Configuration contract tests for local execution tracing."""

from __future__ import annotations

from pawbot.config.schema import Config


def test_observability_config_accepts_json_aliases_and_round_trips() -> None:
    config = Config.model_validate({
        "observability": {
            "enabled": False,
            "retentionDays": 7,
            "maxTraces": 12,
            "maxBytes": 4096,
        }
    })

    assert config.observability.enabled is False
    assert config.observability.retention_days == 7
    assert config.observability.max_traces == 12
    assert config.observability.max_bytes == 4096
    assert config.model_dump(mode="json", by_alias=True)["observability"] == {
        "enabled": False,
        "retentionDays": 7,
        "maxTraces": 12,
        "maxBytes": 4096,
    }


def test_observability_config_rejects_invalid_retention_limits() -> None:
    for payload in (
        {"retentionDays": -1},
        {"maxTraces": 0},
        {"maxBytes": -1},
    ):
        try:
            Config.model_validate({"observability": payload})
        except ValueError:
            continue
        raise AssertionError(f"invalid observability payload was accepted: {payload}")
