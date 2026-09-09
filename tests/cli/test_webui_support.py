from pathlib import Path
from types import SimpleNamespace

from pawbot.cli.webui_support import (
    _ensure_local_webui_channel,
    _prepare_webui_bundle_for_gateway,
    _print_webui_access_instructions,
)
from pawbot.config.schema import Config


def test_source_checkout_preserves_warn_only_gateway_startup(monkeypatch) -> None:
    modes: list[str] = []
    monkeypatch.setattr(
        "pawbot.cli.webui_support.inspect_webui_bundle",
        lambda: SimpleNamespace(source_available=True),
    )
    monkeypatch.setattr("pawbot.cli.webui_support._webui_channel_enabled", lambda _config: True)
    monkeypatch.setattr(
        "pawbot.cli.webui_support.ensure_webui_bundle",
        lambda **kwargs: modes.append(kwargs["mode"]),
    )

    _prepare_webui_bundle_for_gateway(Config(), mode="warn")

    assert modes == ["warn"]


def test_vite_mode_does_not_build_the_source_webui_bundle(monkeypatch) -> None:
    modes: list[str] = []
    monkeypatch.setattr(
        "pawbot.cli.webui_support.inspect_webui_bundle",
        lambda: SimpleNamespace(source_available=True),
    )
    monkeypatch.setattr("pawbot.cli.webui_support._webui_channel_enabled", lambda _config: True)
    monkeypatch.setattr(
        "pawbot.cli.webui_support.ensure_webui_bundle",
        lambda **kwargs: modes.append(kwargs["mode"]),
    )

    _prepare_webui_bundle_for_gateway(Config(), mode="skip")

    assert modes == ["skip"]


def test_webui_remote_host_requires_and_generates_authentication() -> None:
    config = Config()

    changed, generated = _ensure_local_webui_channel(
        config,
        port=9876,
        host="0.0.0.0",
        yes=True,
    )

    websocket = config.channels.websocket
    assert changed is True
    assert generated is True
    assert websocket["host"] == "0.0.0.0"
    assert websocket["port"] == 9876
    assert websocket["websocketRequiresToken"] is True
    assert isinstance(websocket["tokenIssueSecret"], str)
    assert len(websocket["tokenIssueSecret"]) >= 32


def test_webui_keeps_an_existing_remote_host_without_local_reset() -> None:
    config = Config(
        channels={
            "websocket": {
                "enabled": True,
                "host": "10.0.0.8",
                "tokenIssueSecret": "existing-secret",
            }
        }
    )

    changed, generated = _ensure_local_webui_channel(config, port=None, yes=True)

    assert changed is False
    assert generated is False
    assert config.channels.websocket["host"] == "10.0.0.8"
    assert config.channels.websocket["tokenIssueSecret"] == "existing-secret"


def test_webui_remote_access_instructions_do_not_print_the_secret(
    capsys,
    tmp_path: Path,
) -> None:
    secret = "do-not-print-this-secret"
    config = Config(
        channels={
            "websocket": {
                "enabled": True,
                "host": "0.0.0.0",
                "port": 8765,
                "tokenIssueSecret": secret,
            }
        }
    )

    _print_webui_access_instructions(config, tmp_path / "config.json")

    output = capsys.readouterr().out
    assert "http://<server-ip>:8765" in output
    assert "ssh -N -L 8765:127.0.0.1:8765" in output
    assert "tokenIssueSecret" in output
    assert secret not in output
