"""Direct and interactive agent CLI command."""

import asyncio
import importlib
import signal
import sys
from collections.abc import Awaitable, Callable
from types import FrameType
from typing import Any

import typer
from rich.console import Console
from typer.models import OptionInfo

from pawbot import __logo__
from pawbot.cli.log_control import _set_pawbot_logs
from pawbot.cli.runtime_config import (
    _load_runtime_config,
    _migrate_cron_store,
    _model_display,
    _print_agent_start_error,
)

console = Console()


def _option_value(value: Any, default: Any) -> Any:
    """Return a real default when the command function is called directly.

    Typer replaces ``OptionInfo`` values when invoking through the CLI, but a
    direct Python caller (including tests and embedders) receives the metadata
    object used as the function default.  Normalize that boundary so the
    command remains a usable Python function as well as a Typer command.
    """
    return default if isinstance(value, OptionInfo) else value

_CLASSIC_DEPENDENCIES = {
    "AgentLoop": ("pawbot.agent.loop", "AgentLoop"),
    "StreamRenderer": ("pawbot.cli.stream", "StreamRenderer"),
    "consume_restart_notice_from_env": (
        "pawbot.utils.restart",
        "consume_restart_notice_from_env",
    ),
    "is_default_workspace": ("pawbot.config.paths", "is_default_workspace"),
    "sync_workspace_templates": ("pawbot.utils.helpers", "sync_workspace_templates"),
}


def __getattr__(name: str) -> Any:
    """Preserve patchable classic-agent symbols without loading them for the TUI."""
    dependency = _CLASSIC_DEPENDENCIES.get(name)
    if dependency is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = dependency
    value = getattr(importlib.import_module(module_name), attribute)
    globals()[name] = value
    return value


def _classic_dependency(name: str) -> Any:
    if name in globals():
        return globals()[name]
    return __getattr__(name)


def agent(
    message: str | None = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str | None = typer.Option(None, "--session", "-s", help="Session ID"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    markdown: bool = typer.Option(
        True,
        "--markdown/--no-markdown",
        help="Render assistant output as Markdown",
    ),
    logs: bool = typer.Option(
        False,
        "--logs/--no-logs",
        help="Show pawbot runtime logs during chat",
    ),
    classic: bool = typer.Option(
        False,
        "--classic",
        "--no-tui",
        help="Use the classic Python prompt instead of the native terminal UI",
    ),
    theme: str = typer.Option(
        "auto",
        "--theme",
        help="Terminal UI appearance: auto, dark, or light",
    ),
    record: str | None = typer.Option(
        None, "--record", help="Record this session into a blackbox directory",
    ),
    replay: str | None = typer.Option(
        None, "--replay", help="Replay a recorded blackbox directory offline (zero-token)",
    ),
    break_at: int | None = typer.Option(
        None, "--break-at", help="In replay mode: pause at iteration N and dump messages",
    ),
    benchmark: bool = typer.Option(
        False,
        "--benchmark/--no-benchmark",
        help="In replay mode: print local replay timing and resource metrics",
    ),
):
    """Chat in the terminal or send one message non-interactively."""
    message = _option_value(message, None)
    session_id = _option_value(session_id, None)
    workspace = _option_value(workspace, None)
    config = _option_value(config, None)
    markdown = _option_value(markdown, True)
    logs = _option_value(logs, False)
    classic = _option_value(classic, False)
    theme = _option_value(theme, "auto")
    record = _option_value(record, None)
    replay = _option_value(replay, None)
    break_at = _option_value(break_at, None)
    benchmark = _option_value(benchmark, False)
    if record is not None and replay is not None:
        raise typer.BadParameter("--record and --replay are mutually exclusive", param_hint="--record")
    if benchmark and replay is None:
        raise typer.BadParameter("--benchmark requires --replay", param_hint="--benchmark")
    runtime_config = _load_runtime_config(config, workspace)
    theme = theme.strip().lower()
    if theme not in {"auto", "dark", "light"}:
        raise typer.BadParameter("must be auto, dark, or light", param_hint="--theme")
    native_tui = message is None and not classic and replay is None
    if native_tui:
        from pawbot.cli.tui_launcher import TuiSessionError, TuiUnavailableError, launch_tui
        from pawbot.config.loader import get_config_path

        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise typer.BadParameter(
                "the native TUI requires an interactive terminal; use --message for "
                "one-shot input or --classic for the legacy prompt",
                param_hint="terminal",
            )
        if not markdown:
            raise typer.BadParameter("--no-markdown requires --classic", param_hint="--no-markdown")
        if logs:
            raise typer.BadParameter("--logs requires --classic", param_hint="--logs")
        try:
            exit_code = launch_tui(
                runtime_config,
                config_path=get_config_path().resolve(strict=False),
                workspace_override=workspace,
                session_id=session_id,
                theme=theme,
            )
        except TuiSessionError as exc:
            raise typer.BadParameter(str(exc), param_hint="--session") from exc
        except TuiUnavailableError as exc:
            console.print(f"[red]Native TUI unavailable: {exc}[/red]")
            console.print("[dim]Use `pawbot agent --classic` only if you want the old prompt.[/dim]")
            raise typer.Exit(1) from exc
        else:
            if exit_code:
                raise typer.Exit(exit_code)
            return

    from pawbot.agent.hooks import create_file_edit_activity_hook
    from pawbot.agent.tools.mcp import MCPProvider
    from pawbot.agent.tools.registry import ToolRegistry
    from pawbot.bus.outbound_events import (
        StreamDeltaEvent,
        StreamedResponseEvent,
        StreamEndEvent,
        outbound_event_from_message,
    )
    from pawbot.bus.queue import MessageBus
    from pawbot.cli import terminal as cli_terminal
    from pawbot.cli.stream import ThinkingSpinner
    from pawbot.cron.service import CronService
    from pawbot.providers.factory import make_provider
    from pawbot.providers.image_generation import image_gen_provider_configs
    from pawbot.utils.helpers import sanitize_surrogates as _sanitize_surrogates
    from pawbot.utils.restart import (
        format_restart_completed_message,
        should_show_cli_restart_notice,
    )

    agent_loop_class = _classic_dependency("AgentLoop")
    stream_renderer_class = _classic_dependency("StreamRenderer")
    consume_restart_notice_from_env = _classic_dependency("consume_restart_notice_from_env")
    is_default_workspace = _classic_dependency("is_default_workspace")
    sync_workspace_templates = _classic_dependency("sync_workspace_templates")

    session_id = session_id or "cli:direct"

    try:
        provider = make_provider(runtime_config)
    except ValueError as exc:
        _print_agent_start_error(exc)
        raise typer.Exit(1) from exc

    sync_workspace_templates(runtime_config.workspace_path)

    bus = MessageBus()

    # Preserve existing single-workspace installs, but keep custom workspaces clean.
    if is_default_workspace(runtime_config.workspace_path):
        _migrate_cron_store(runtime_config)

    # Create cron service with workspace-scoped store
    cron_store_path = runtime_config.workspace_path / "cron" / "jobs.json"
    cron = CronService(cron_store_path)
    tools = ToolRegistry()
    mcp_provider = MCPProvider.from_config(runtime_config, tools)
    blackbox = _build_blackbox(record, replay, break_at)

    _set_pawbot_logs(logs)

    try:
        agent_loop = agent_loop_class.from_config(
            runtime_config,
            bus,
            provider=provider,
            cron_service=cron,
            image_generation_provider_configs=image_gen_provider_configs(runtime_config),
            hook_factories=[create_file_edit_activity_hook],
            tool_registry=tools,
            blackbox=blackbox,
        )
    except ValueError as exc:
        _print_agent_start_error(exc)
        raise typer.Exit(1) from exc
    restart_notice = consume_restart_notice_from_env()
    if restart_notice and should_show_cli_restart_notice(restart_notice, session_id):
        cli_terminal._print_agent_response(
            format_restart_completed_message(restart_notice.started_at_raw),
            render_markdown=False,
        )

    async def _close_runtime() -> None:
        try:
            await agent_loop.aclose()
        finally:
            await mcp_provider.aclose()

    # Shared reference for progress callbacks
    _thinking: ThinkingSpinner | None = None

    def _make_progress(
        renderer: Any | None = None,
    ) -> Callable[..., Awaitable[None]]:
        reasoning_buffer = cli_terminal._ReasoningBuffer()

        async def _cli_progress(
            content: str,
            *,
            tool_hint: bool = False,
            reasoning: bool = False,
            **_kwargs: Any,
        ) -> None:
            ch = agent_loop.channels_config

            if _kwargs.get("reasoning_end"):
                if ch and not ch.show_reasoning:
                    reasoning_buffer.clear()
                else:
                    cli_terminal._flush_cli_reasoning(reasoning_buffer, _thinking, renderer)
                return

            if reasoning:
                if ch and not ch.show_reasoning:
                    reasoning_buffer.clear()
                    return
                text = reasoning_buffer.add(content)
                if text:
                    cli_terminal._print_cli_reasoning(text, _thinking, renderer)
                return
            if ch and tool_hint and not ch.send_tool_hints:
                return
            if ch and not tool_hint and not ch.send_progress:
                return
            cli_terminal._print_cli_progress_line(content, _thinking, renderer)

        return _cli_progress

    if replay is not None:
        # ADR-004: offline replay of recorded turns (zero-token, no network).
        async def run_replay() -> None:
            try:
                results = await agent_loop.replay_all(blackbox)
            finally:
                await _close_runtime()
            failed = 0
            for turn_id, ok, diffs in results:
                if ok:
                    print(f"[replay] {turn_id}: OK")
                else:
                    failed += 1
                    print(f"[replay] {turn_id}: MISMATCH")
                    for diff in diffs:
                        print("  " + diff)
            print(
                f"\nReplay complete: {len(results) - failed}/{len(results)} turns deterministic"
            )
            if benchmark:
                print("\nReplay benchmark (local, provider-free):")
                for row in getattr(blackbox, "last_benchmark", []):
                    print(
                        "  {turn_id}: {elapsed_ms} ms, {messages} messages, "
                        "{diffs} diffs".format(**row)
                    )
            if failed:
                raise SystemExit(1)

        asyncio.run(run_replay())
        return

    if message is not None:
        # Single message mode — direct call, no bus needed
        async def run_once() -> None:
            try:
                await mcp_provider.connect()
                renderer = stream_renderer_class(
                    render_markdown=markdown,
                    bot_name=runtime_config.agents.defaults.bot_name,
                    bot_icon=runtime_config.agents.defaults.bot_icon,
                )
                response = await agent_loop.process_direct(
                    message,
                    session_id,
                    on_progress=_make_progress(renderer),
                    on_stream=renderer.on_delta,
                    on_stream_end=renderer.on_end,
                )
                if not renderer.streamed:
                    await renderer.close()
                    print_kwargs: dict[str, Any] = {}
                    if renderer.header_printed:
                        print_kwargs["show_header"] = False
                    cli_terminal._print_agent_response(
                        response.content if response else "",
                        render_markdown=markdown,
                        metadata=response.metadata if response else None,
                        **print_kwargs,
                    )
            finally:
                await _close_runtime()

        asyncio.run(run_once())
    else:
        # Interactive mode — route through bus like other channels
        from pawbot.bus.events import InboundMessage

        cli_terminal._init_prompt_session()
        _model, _preset_tag = _model_display(runtime_config)
        _icon = runtime_config.agents.defaults.bot_icon or __logo__
        console.print(
            f"{_icon} Interactive mode [bold blue]({_model})[/bold blue]{_preset_tag} "
            "— type [bold]exit[/bold] or [bold]Ctrl+C[/bold] to quit\n"
        )

        if ":" in session_id:
            cli_channel, cli_chat_id = session_id.split(":", 1)
        else:
            cli_channel, cli_chat_id = "cli", session_id

        def _handle_signal(signum: int, _frame: FrameType | None) -> None:
            sig_name = signal.Signals(signum).name
            cli_terminal._restore_terminal()
            console.print(f"\nReceived {sig_name}, goodbye!")
            sys.exit(0)

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
        # SIGHUP is not available on Windows
        if hasattr(signal, "SIGHUP"):
            signal.signal(signal.SIGHUP, _handle_signal)
        # Ignore SIGPIPE to prevent silent process termination when writing to closed pipes
        # SIGPIPE is not available on Windows
        if hasattr(signal, "SIGPIPE"):
            signal.signal(signal.SIGPIPE, signal.SIG_IGN)

        async def run_interactive() -> None:
            await mcp_provider.connect()
            bus_task = asyncio.create_task(agent_loop.run())
            turn_done = asyncio.Event()
            turn_done.set()
            turn_response: list[Any] = []
            renderer: Any | None = None
            reasoning_buffer = cli_terminal._ReasoningBuffer()

            async def _consume_outbound() -> None:
                while True:
                    try:
                        msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
                        event = outbound_event_from_message(msg)

                        if isinstance(event, StreamDeltaEvent):
                            if renderer:
                                await renderer.on_delta(msg.content)
                            continue
                        if isinstance(event, StreamEndEvent):
                            if renderer:
                                await renderer.on_end(
                                    resuming=event.resuming,
                                )
                            continue
                        if isinstance(event, StreamedResponseEvent):
                            if msg.content and renderer and not renderer.streamed:
                                await renderer.close()
                                print_kwargs: dict[str, Any] = {}
                                if renderer.header_printed:
                                    print_kwargs["show_header"] = False
                                cli_terminal._print_agent_response(
                                    msg.content,
                                    render_markdown=markdown,
                                    metadata=msg.metadata,
                                    **print_kwargs,
                                )
                            turn_done.set()
                            continue

                        if await cli_terminal._maybe_print_interactive_progress(
                            msg,
                            None,
                            agent_loop.channels_config,
                            renderer,
                            reasoning_buffer,
                        ):
                            continue

                        if not turn_done.is_set():
                            if msg.content:
                                turn_response.append(msg)
                            turn_done.set()
                        elif msg.content:
                            await cli_terminal._print_interactive_response(
                                msg.content,
                                render_markdown=markdown,
                                metadata=msg.metadata,
                            )

                    except asyncio.TimeoutError:
                        continue
                    except asyncio.CancelledError:
                        break

            outbound_task = asyncio.create_task(_consume_outbound())

            try:
                while True:
                    try:
                        cli_terminal._flush_pending_tty_input()
                        # Stop spinner before user input to avoid prompt_toolkit conflicts
                        if renderer:
                            renderer.stop_for_input()
                        user_input = _sanitize_surrogates(
                            await cli_terminal._read_interactive_input_async()
                        )
                        command = user_input.strip()
                        if not command:
                            continue

                        if cli_terminal._is_exit_command(command):
                            cli_terminal._restore_terminal()
                            console.print("\nGoodbye!")
                            break

                        turn_done.clear()
                        turn_response.clear()
                        reasoning_buffer.clear()
                        renderer = stream_renderer_class(
                            render_markdown=markdown,
                            bot_name=runtime_config.agents.defaults.bot_name,
                            bot_icon=runtime_config.agents.defaults.bot_icon,
                        )

                        await bus.publish_inbound(
                            InboundMessage(
                                channel=cli_channel,
                                sender_id="user",
                                chat_id=cli_chat_id,
                                content=user_input,
                                metadata={"_wants_stream": True},
                            )
                        )

                        await turn_done.wait()

                        if turn_response:
                            response_msg = turn_response[0]
                            content = response_msg.content
                            meta = response_msg.metadata
                            if content and not isinstance(
                                response_msg.event,
                                StreamedResponseEvent,
                            ):
                                if renderer:
                                    await renderer.close()
                                print_kwargs: dict[str, Any] = {}
                                if renderer and renderer.header_printed:
                                    print_kwargs["show_header"] = False
                                cli_terminal._print_agent_response(
                                    content,
                                    render_markdown=markdown,
                                    metadata=meta,
                                    **print_kwargs,
                                )
                        elif renderer and not renderer.streamed:
                            await renderer.close()
                    except KeyboardInterrupt:
                        cli_terminal._restore_terminal()
                        console.print("\nGoodbye!")
                        break
                    except EOFError:
                        cli_terminal._restore_terminal()
                        console.print("\nGoodbye!")
                        break
            finally:
                agent_loop.stop()
                outbound_task.cancel()
                await asyncio.gather(bus_task, outbound_task, return_exceptions=True)
                await _close_runtime()

        asyncio.run(run_interactive())


def _build_blackbox(record: str | None, replay: str | None, break_at: int | None) -> Any | None:
    """Construct the Record & Replay controller from CLI flags (ADR-004)."""
    if record is None and replay is None:
        return None
    from pawbot.agent.blackbox import BlackboxController, ReplayController

    if record is not None:
        return BlackboxController(record)
    assert replay is not None
    return ReplayController(replay, break_at=break_at)
