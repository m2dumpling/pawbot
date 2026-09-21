"""CLI configuration and diagnostics for the optional Langfuse exporter."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape

from pawbot.agent.langfuse import (
    create_langfuse_client,
    langfuse_sdk_installed,
    resolve_langfuse_settings,
)
from pawbot.config.loader import get_config_path, load_config, save_config, set_config_path

langfuse_app = typer.Typer(help="Configure and diagnose the optional Langfuse exporter")
console = Console()


def _config_path(value: str | None) -> Path:
    path = Path(value).expanduser().resolve(strict=False) if value else get_config_path()
    set_config_path(path)
    return path


def _hint(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    return f"{value[:3]}...{value[-4:]}" if len(value) > 8 else "configured"


@langfuse_app.command("status")
def langfuse_status(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON"),
) -> None:
    """Show Langfuse configuration without printing secret values."""
    loaded = load_config(_config_path(config))
    settings = resolve_langfuse_settings(loaded)
    payload = {
        "enabled": settings.enabled,
        "configured": settings.configured,
        "sdk_installed": langfuse_sdk_installed(),
        "base_url": settings.base_url,
        "environment": settings.environment,
        "sample_rate": settings.sample_rate,
        "capture_prompts": settings.capture_prompts,
        "capture_tool_results": settings.capture_tool_results,
        "public_key_hint": _hint(settings.public_key),
        "secret_key_hint": _hint(settings.secret_key),
    }
    if json_output:
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return
    console.print(f"Enabled: {payload['enabled']}")
    console.print(f"Configured: {payload['configured']}")
    console.print(f"SDK installed: {payload['sdk_installed']}")
    console.print(f"Base URL: {escape(settings.base_url)}")
    console.print(f"Environment: {escape(settings.environment)}")
    console.print(f"Sample rate: {settings.sample_rate}")
    console.print(f"Capture prompts: {settings.capture_prompts}")
    console.print(f"Capture Tool results: {settings.capture_tool_results}")
    console.print(f"Public key: {escape(str(payload['public_key_hint'] or 'not configured'))}")
    console.print(f"Secret key: {escape(str(payload['secret_key_hint'] or 'not configured'))}")


@langfuse_app.command("configure")
def langfuse_configure(
    public_key: str | None = typer.Option(None, "--public-key", help="Langfuse public key"),
    secret_key: str | None = typer.Option(
        None,
        "--secret-key",
        help="Langfuse secret key; prefer an environment variable for shell history safety",
    ),
    base_url: str | None = typer.Option(None, "--base-url", help="Langfuse Cloud or self-hosted URL"),
    environment: str | None = typer.Option(None, "--environment", help="Trace environment label"),
    sample_rate: float | None = typer.Option(None, "--sample-rate", min=0.0, max=1.0),
    capture_prompts: bool | None = typer.Option(
        None,
        "--capture-prompts/--no-capture-prompts",
        help="Export bounded redacted prompt/response previews",
    ),
    capture_tool_results: bool | None = typer.Option(
        None,
        "--capture-tool-results/--no-capture-tool-results",
        help="Export bounded redacted Tool arguments/results",
    ),
    enabled: bool = typer.Option(True, "--enable/--disable", help="Enable the exporter after saving"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Save Langfuse settings to config.json; restart pawbot to apply them."""
    path = _config_path(config)
    loaded = load_config(path)
    obs = loaded.observability
    if public_key is not None and public_key.strip():
        obs.langfuse_public_key = public_key.strip()
    if secret_key is not None and secret_key.strip():
        obs.langfuse_secret_key = secret_key.strip()
    if base_url is not None:
        obs.langfuse_base_url = base_url.strip().rstrip("/")
    if environment is not None and environment.strip():
        obs.langfuse_environment = environment.strip()
    if sample_rate is not None:
        obs.langfuse_sample_rate = sample_rate
    if capture_prompts is not None:
        obs.langfuse_capture_prompts = capture_prompts
    if capture_tool_results is not None:
        obs.langfuse_capture_tool_results = capture_tool_results
    obs.langfuse_enabled = enabled
    settings = resolve_langfuse_settings(loaded)
    if enabled and not settings.configured:
        raise typer.BadParameter(
            "Both --public-key and --secret-key are required to enable Langfuse "
            "(or set LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY)."
        )
    save_config(loaded, path)
    console.print(f"[green]Saved Langfuse settings to {escape(str(path))}[/green]")
    console.print("Restart pawbot to apply the exporter to the running Agent.")


@langfuse_app.command("test")
def langfuse_test(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Validate Langfuse credentials with a read-only connection check."""
    loaded = load_config(_config_path(config))
    settings = resolve_langfuse_settings(loaded)
    if not settings.configured:
        console.print("[red]Langfuse is not configured.[/red]")
        raise typer.Exit(1)
    if not langfuse_sdk_installed():
        console.print("[red]Langfuse SDK is not installed. Run 'pawbot plugins enable langfuse'.[/red]")
        raise typer.Exit(1)
    client = None
    try:
        client = create_langfuse_client(loaded)
        if client is None or not client.auth_check():
            console.print("[red]Langfuse credentials were rejected.[/red]")
            raise typer.Exit(1)
    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"[red]Langfuse connection failed: {escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    finally:
        if client is not None:
            try:
                client.shutdown()
            except Exception:
                pass
    console.print("[green]Langfuse connection succeeded.[/green]")


__all__ = ["langfuse_app"]
