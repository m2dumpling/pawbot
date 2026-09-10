from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pawbot.agent.goal_permission import goal_mutation_allowed
from pawbot.agent.loop import AgentLoop
from pawbot.bus.events import InboundMessage
from pawbot.bus.queue import MessageBus
from pawbot.command.builtin import (
    build_help_text,
    builtin_command_palette,
    cmd_effort,
    cmd_goal,
    cmd_model,
    register_builtin_commands,
)
from pawbot.command.router import CommandContext, CommandRouter
from pawbot.config.schema import ModelPresetConfig
from pawbot.session.model_selection import (
    SESSION_MODEL_PRESET_METADATA_KEY,
    model_preset_from_metadata,
    reasoning_effort_from_metadata,
)


def _provider(default_model: str, max_tokens: int = 123) -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = default_model
    provider.provider_name = "deepseek"
    provider.generation = SimpleNamespace(
        max_tokens=max_tokens,
        temperature=0.1,
        reasoning_effort=None,
    )
    return provider


def _make_loop(
    tmp_path,
    *,
    preset_snapshot_loader=None,
    model_catalog_loader=None,
    model_presets=None,
    model="base-model",
) -> AgentLoop:
    return AgentLoop(
        bus=MessageBus(),
        provider=_provider(model, max_tokens=123),
        workspace=tmp_path,
        model=model,
        context_window_tokens=1000,
        model_presets=model_presets or {
            "default": ModelPresetConfig(
                model=model,
                max_tokens=123,
                context_window_tokens=1000,
            ),
            "fast": ModelPresetConfig(
                model="openai/gpt-4.1",
                max_tokens=4096,
                context_window_tokens=32_768,
            ),
        },
        preset_snapshot_loader=preset_snapshot_loader,
        model_catalog_loader=model_catalog_loader,
    )


def _ctx(loop: AgentLoop, raw: str, args: str = "") -> CommandContext:
    msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content=raw)
    return CommandContext(msg=msg, session=None, key=msg.session_key, raw=raw, args=args, loop=loop)


def _ctx_session(loop: AgentLoop, raw: str, args: str = "") -> CommandContext:
    msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content=raw)
    return CommandContext(
        msg=msg, session=MagicMock(), key=msg.session_key, raw=raw, args=args, loop=loop,
        is_user_turn=True,
    )


def _saved_model_preset(loop: AgentLoop, session_key: str = "cli:direct") -> str | None:
    session = loop.sessions.get_or_create(session_key)
    return model_preset_from_metadata(session.metadata)


@pytest.mark.asyncio
async def test_model_command_lists_current_and_available_presets(tmp_path) -> None:
    loop = _make_loop(tmp_path)

    out = await cmd_model(_ctx(loop, "/model"))

    assert "Current model: `base-model`" in out.content
    assert "Current preset: `default`" in out.content
    assert "Available presets: `default`, `fast`" in out.content
    assert "`fast`" in out.content
    assert out.metadata == {"render_as": "text"}


@pytest.mark.asyncio
async def test_model_command_lists_and_pins_live_provider_models(tmp_path) -> None:
    async def discover(provider: str) -> dict[str, object]:
        assert provider == "deepseek"
        return {
            "provider": provider,
            "status": "available",
            "model_count": 2,
            "models": [
                {
                    "id": "deepseek-v4-flash",
                    "context_window": 1_048_576,
                    "reasoning_effort_values": ["", "low", "high", "max"],
                },
                {"id": "deepseek-chat", "context_window": 128_000},
            ],
        }

    loop = _make_loop(tmp_path, model_catalog_loader=discover)

    listed = await cmd_model(_ctx(loop, "/model"))
    assert "deepseek-v4-flash" in listed.content
    assert "1,048,576 ctx" in listed.content
    assert "thinking: default/low/high/max" in listed.content

    switched = await cmd_model(
        _ctx(loop, "/model deepseek-v4-flash", args="deepseek-v4-flash"),
    )
    assert "Switched this session to model `deepseek-v4-flash`." in switched.content
    assert "Context window: 1,048,576" in switched.content
    session = loop.sessions.get_or_create("cli:direct")
    assert session.metadata["_pawbot_model_override"] == {
        "model": "deepseek-v4-flash",
        "provider": "deepseek",
        "context_window_tokens": 1_048_576,
    }
    runtime = loop.runtime_for_session(session)
    assert runtime.model == "deepseek-v4-flash"
    assert runtime.context_window_tokens == 1_048_576


@pytest.mark.asyncio
async def test_model_command_switches_preset(tmp_path) -> None:
    loop = _make_loop(tmp_path)

    out = await cmd_model(_ctx(loop, "/model fast", args="fast"))

    assert "Switched model preset to `fast`." in out.content
    assert "Scope: current session" in out.content
    assert "Model: `openai/gpt-4.1`" in out.content
    assert _saved_model_preset(loop) == "fast"
    assert loop.model_preset is None
    assert loop.model == "base-model"

    await loop.process_direct("/new", session_key="cli:direct")
    assert _saved_model_preset(loop) == "fast"
    status = await loop.process_direct("/status", session_key="cli:direct")
    assert status is not None and "openai/gpt-4.1" in status.content


@pytest.mark.asyncio
async def test_model_command_accepts_canonical_names_with_spaces(tmp_path) -> None:
    loop = _make_loop(
        tmp_path,
        model_presets={
            "default": ModelPresetConfig(model="base-model"),
            "Deep Research": ModelPresetConfig(model="deep-model"),
        },
    )

    out = await cmd_model(
        _ctx(loop, "/model deep research", args="deep research"),
    )

    assert "Switched model preset to `Deep Research`." in out.content
    assert _saved_model_preset(loop) == "Deep Research"


@pytest.mark.asyncio
async def test_model_command_switches_back_to_default(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    await cmd_model(_ctx(loop, "/model fast", args="fast"))

    out = await cmd_model(_ctx(loop, "/model default", args="default"))

    assert "Switched model preset to `default`." in out.content
    assert _saved_model_preset(loop) == "default"
    assert loop.model_preset is None
    assert loop.model == "base-model"
    assert loop.context_window_tokens == 1000


@pytest.mark.asyncio
async def test_model_command_unknown_preset_keeps_old_state(tmp_path) -> None:
    loop = _make_loop(tmp_path)

    out = await cmd_model(_ctx(loop, "/model missing", args="missing"))

    assert "Could not switch model preset" in out.content
    assert "\"model_preset" not in out.content
    assert "Available presets: `default`, `fast`" in out.content
    assert loop.model_preset is None
    assert loop.model == "base-model"


@pytest.mark.asyncio
async def test_model_command_reports_provider_configuration_errors(tmp_path) -> None:
    def fail_preset(_name: str):
        raise ValueError("No API key configured for provider 'openai'.")

    loop = _make_loop(tmp_path, preset_snapshot_loader=fail_preset)

    switched = await cmd_model(_ctx(loop, "/model fast", args="fast"))
    session = loop.sessions.get_or_create("cli:direct")
    session.metadata[SESSION_MODEL_PRESET_METADATA_KEY] = "fast"
    status = await cmd_model(_ctx(loop, "/model"))

    assert "Could not switch model preset" in switched.content
    assert "No API key configured for provider 'openai'." in switched.content
    assert "Current selection error" in status.content
    assert "No API key configured for provider 'openai'." in status.content


@pytest.mark.asyncio
async def test_model_command_does_not_depend_on_my_allow_set(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    assert loop.tools_config.my.allow_set is False

    await cmd_model(_ctx(loop, "/model fast", args="fast"))

    assert _saved_model_preset(loop) == "fast"


@pytest.mark.asyncio
async def test_model_command_registered_as_exact_and_prefix(tmp_path) -> None:
    router = CommandRouter()
    register_builtin_commands(router)
    loop = _make_loop(tmp_path)

    out = await router.dispatch(_ctx(loop, "/model fast"))

    assert out is not None
    assert out.channel == "cli"
    assert out.chat_id == "direct"
    assert out.metadata == {"render_as": "text"}
    assert out.content == "\n".join([
        "Switched model preset to `fast`.",
        "- Scope: current session",
        "- Model: `openai/gpt-4.1`",
        "- Context window: 32768",
        "- Max output tokens: 4096",
    ])
    assert _saved_model_preset(loop) == "fast"


@pytest.mark.asyncio
async def test_model_command_does_not_change_another_session(tmp_path) -> None:
    loop = _make_loop(tmp_path)

    await cmd_model(_ctx(loop, "/model fast", args="fast"))
    other = InboundMessage(channel="cli", sender_id="user", chat_id="other", content="/model")
    out = await cmd_model(
        CommandContext(msg=other, session=None, key=other.session_key, raw="/model", loop=loop)
    )

    assert "Current preset: `default`" in out.content
    assert _saved_model_preset(loop) == "fast"


@pytest.mark.asyncio
async def test_model_command_reports_and_recovers_removed_session_preset(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    session = loop.sessions.get_or_create("cli:direct")
    session.metadata[SESSION_MODEL_PRESET_METADATA_KEY] = "removed"
    loop.sessions.save(session)

    status = await loop.process_direct("/model", session_key="cli:direct")
    switched = await loop.process_direct("/model default", session_key="cli:direct")

    assert status is not None
    assert "model_preset 'removed' not found" in status.content
    assert "Available presets: `default`, `fast`" in status.content
    assert "Switch with `/model <preset>`" in status.content
    assert switched is not None
    assert "Switched model preset to `default`." in switched.content
    assert _saved_model_preset(loop) == "default"


def test_model_command_in_help_and_palette() -> None:
    palette = builtin_command_palette()

    model = next(item for item in palette if item["command"] == "/model")
    assert model["arg_hint"] == "[model or preset]"
    assert model["lifecycle"] == "side_channel"
    assert model["accepts_args"] is True
    assert "/model [model or preset]" in build_help_text()


@pytest.mark.asyncio
async def test_goal_command_shows_usage_without_args(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    out = await cmd_goal(_ctx(loop, "/goal"))
    assert out is not None
    assert out.channel == "cli"
    assert out.chat_id == "direct"
    assert out.metadata == {"render_as": "text"}
    assert out.content == "Usage: /goal <long-running task description>"


@pytest.mark.asyncio
async def test_goal_command_rejects_mid_turn_without_session(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    out = await cmd_goal(_ctx(loop, "/goal do work", args="do work"))
    assert out is not None
    assert out.channel == "cli"
    assert out.chat_id == "direct"
    assert out.metadata == {"render_as": "text"}
    assert out.content == (
        "A task is already running for this chat. "
        "Use `/stop` first, then send `/goal <long-running task description>` again."
    )


@pytest.mark.asyncio
async def test_goal_command_marks_turn_and_preserves_explicit_request(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    ctx = _ctx_session(loop, "/goal audit the repo", args="audit the repo")
    out = await cmd_goal(ctx)
    assert out is None
    assert ctx.msg.content == "/goal audit the repo"
    assert ctx.msg.metadata.get("original_command") == "/goal"
    assert ctx.msg.metadata.get("original_content") == "/goal audit the repo"
    assert ctx.msg.metadata.get("goal_requested") is True
    assert isinstance(ctx.msg.metadata.get("goal_started_at"), int | float)
    assert len(ctx.turn_scopes) == 1
    with ctx.turn_scopes[0]:
        assert goal_mutation_allowed() is True
    assert goal_mutation_allowed() is False


@pytest.mark.asyncio
async def test_goal_command_registered_on_router(tmp_path) -> None:
    router = CommandRouter()
    register_builtin_commands(router)
    loop = _make_loop(tmp_path)
    ctx = _ctx_session(loop, "/goal ship it", args="ship it")
    out = await router.dispatch(ctx)
    assert out is None
    assert "ship it" in ctx.msg.content
    assert len(ctx.turn_scopes) == 1
    with ctx.turn_scopes[0]:
        assert goal_mutation_allowed() is True
    assert goal_mutation_allowed() is False


@pytest.mark.asyncio
async def test_goal_command_does_not_allow_internal_turn(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    ctx = CommandContext(
        msg=InboundMessage(
            channel="cli",
            sender_id="system",
            chat_id="direct",
            content="/goal internal work",
        ),
        session=MagicMock(),
        key="cli:direct",
        raw="/goal internal work",
        args="internal work",
        loop=loop,
        is_user_turn=False,
    )

    out = await cmd_goal(ctx)

    assert out is not None
    assert "only be started by a user" in out.content
    assert ctx.turn_scopes == []


def test_goal_command_in_help_and_palette() -> None:
    palette = builtin_command_palette()
    goal = next(item for item in palette if item["command"] == "/goal")
    assert goal["arg_hint"] == "<goal>"
    assert goal["lifecycle"] == "agent_turn_with_args"
    assert goal["accepts_args"] is True
    assert "/goal <goal>" in build_help_text()


def _effort_saved(loop: AgentLoop, session_key: str = "cli:direct") -> str | None:
    session = loop.sessions.get_or_create(session_key)
    return reasoning_effort_from_metadata(session.metadata)


@pytest.mark.asyncio
async def test_effort_command_lists_supported_levels(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    ctx = _ctx(loop, "/effort")

    out = await cmd_effort(ctx)

    assert out is not None
    assert "## Thinking effort" in out.content
    assert "base-model" in out.content
    assert _effort_saved(loop) is None


@pytest.mark.asyncio
async def test_effort_command_sets_session_effort(tmp_path) -> None:
    loop = _make_loop(
        tmp_path,
        model="deepseek-v4-flash",
        model_presets={
            "default": ModelPresetConfig(
                model="deepseek-v4-flash",
                max_tokens=123,
                context_window_tokens=1000,
                provider="deepseek",
            ),
        },
    )
    ctx = _ctx(loop, "/effort max", args="max")

    out = await cmd_effort(ctx)

    assert out is not None
    assert "max" in out.content
    assert _effort_saved(loop) == "max"

    runtime = loop.runtime_for_session(loop.sessions.get_or_create("cli:direct"))
    assert runtime.generation.reasoning_effort == "max"


@pytest.mark.asyncio
async def test_effort_command_rejects_unsupported_level(tmp_path) -> None:
    loop = _make_loop(
        tmp_path,
        model="deepseek-v4-flash",
        model_presets={
            "default": ModelPresetConfig(
                model="deepseek-v4-flash",
                max_tokens=123,
                context_window_tokens=1000,
                provider="deepseek",
            ),
        },
    )
    ctx = _ctx(loop, "/effort medium", args="medium")

    out = await cmd_effort(ctx)

    assert out is not None
    assert "Unsupported thinking effort" in out.content
    assert _effort_saved(loop) is None


@pytest.mark.asyncio
async def test_effort_command_default_resets_session_effort(tmp_path) -> None:
    loop = _make_loop(
        tmp_path,
        model="deepseek-v4-flash",
        model_presets={
            "default": ModelPresetConfig(
                model="deepseek-v4-flash",
                max_tokens=123,
                context_window_tokens=1000,
                provider="deepseek",
            ),
        },
    )
    first = _ctx(loop, "/effort high", args="high")
    await cmd_effort(first)
    assert _effort_saved(loop) == "high"

    reset = _ctx(loop, "/effort default", args="default")
    out = await cmd_effort(reset)

    assert out is not None
    assert "provider default" in out.content
    assert _effort_saved(loop) is None


def test_effort_command_in_help_and_palette() -> None:
    palette = builtin_command_palette()
    effort = next(item for item in palette if item["command"] == "/effort")
    assert effort["accepts_args"] is True
    assert "/effort" in build_help_text()
