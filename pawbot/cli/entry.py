"""Low-overhead console entrypoint for pawbot's CLI and WebUI."""

from __future__ import annotations

import os
import sys
from contextlib import suppress

from pawbot.cli.process_identity import set_cli_process_identity

_ROOT_OPTIONS = frozenset(
    {
        "-h",
        "--help",
        "-v",
        "--version",
        "--install-completion",
        "--show-completion",
    }
)


def _agent_invocation_args(args: list[str]) -> list[str] | None:
    """Return agent arguments when the root command should act as ``agent``."""
    if not args:
        return []
    if args[0] == "agent":
        return args[1:]
    if args[0].startswith("-") and args[0].split("=", 1)[0] not in _ROOT_OPTIONS:
        return args
    return None


def _replay_invocation_args(args: list[str]) -> list[str] | None:
    """Translate the concise ``pawbot replay DIR`` form to the agent command."""
    if not args or args[0] != "replay":
        return None
    if len(args) == 1:
        return ["--replay"]
    if args[1] in {"--help", "-h"}:
        return [args[1]]
    return ["--replay", args[1], *args[2:]]


def _native_tui_candidate(args: list[str]) -> bool:
    """Return whether ``agent`` can start without the classic agent stack."""
    if not args or args[0] != "agent":
        return False
    for argument in args[1:]:
        if argument in {"--classic", "--no-tui", "-m", "--message"}:
            return False
        if argument.startswith("--message=") or (
            argument.startswith("-m") and not argument.startswith("--")
        ):
            return False
    return True


def _configure_windows_console() -> None:
    if sys.platform != "win32" or sys.stdout.encoding == "utf-8":
        return
    os.environ["PYTHONIOENCODING"] = "utf-8"
    with suppress(Exception):
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if callable(reconfigure):
                reconfigure(encoding="utf-8", errors="replace")


def _run_agent(args: list[str], *, prog_name: str) -> None:
    """Run the shared agent command without importing the complete CLI graph."""
    import typer

    from pawbot.cli.agent import agent

    agent_app = typer.Typer(add_completion=False)
    agent_app.command()(agent)
    command = typer.main.get_command(agent_app)
    command.main(args=args, prog_name=prog_name)


def _run_webui(args: list[str], *, prog_name: str) -> None:
    """Run the shared WebUI command without treating Typer defaults as values."""
    import typer

    from pawbot.cli.webui import webui

    webui_app = typer.Typer(add_completion=False)
    webui_app.command()(webui)
    command = typer.main.get_command(webui_app)
    command.main(args=args, prog_name=prog_name)


def main() -> None:
    """Dispatch the default WebUI without importing the complete CLI graph."""
    raw_args = sys.argv[1:]
    # Installed completion scripts call ``pawbot`` without positional arguments
    # and pass the request through this environment variable. Keep those requests
    # on the root command so subcommands remain discoverable.
    shell_completion = bool(os.environ.get("_PAWBOT_COMPLETE"))
    replay_args = None if shell_completion else _replay_invocation_args(raw_args)
    if replay_args is not None:
        set_cli_process_identity(["agent", *replay_args])
        _configure_windows_console()
        _run_agent(replay_args, prog_name="pawbot replay")
        return
    if not raw_args and not shell_completion:
        set_cli_process_identity(["webui"])
        _configure_windows_console()
        _run_webui([], prog_name="pawbot")
        return
    agent_args = None if shell_completion else _agent_invocation_args(raw_args)
    dispatch_args = ["agent", *agent_args] if agent_args is not None else raw_args
    set_cli_process_identity(dispatch_args)
    _configure_windows_console()
    root_agent_alias = agent_args is not None and raw_args[:1] != ["agent"]
    if agent_args is not None and (
        root_agent_alias or _native_tui_candidate(dispatch_args)
    ):
        prog_name = "pawbot" if root_agent_alias else "pawbot agent"
        _run_agent(agent_args, prog_name=prog_name)
        return

    from pawbot.cli.commands import app

    app()
