"""Agent loop: the core processing engine."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import os
import time
import weakref
from collections.abc import Coroutine, Iterable, Mapping
from contextlib import AbstractContextManager, ExitStack, nullcontext, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, TypeVar, cast

from loguru import logger

from pawbot.agent import context as agent_context
from pawbot.agent import model_presets as preset_helpers
from pawbot.agent.autocompact import AutoCompact
from pawbot.agent.automation_turns import publish_next_deferred_turn
from pawbot.agent.budget import TurnBudget
from pawbot.agent.context import ContextBuilder, PersistedPromptContextResolver
from pawbot.agent.cron_turns import CronTurnCoordinator
from pawbot.agent.hook import AgentHook, AgentTurnHookFactory
from pawbot.agent.memory import Consolidator
from pawbot.agent.model_runtime import ModelRuntimeResolver
from pawbot.agent.runner import (
    _MAX_INJECTIONS_PER_TURN,
    AgentRunner,
    AgentRunResult,
    AgentRunSpec,
)
from pawbot.agent.subagent import SubagentManager
from pawbot.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from pawbot.agent.tools.exec_session import ExecSessionManager
from pawbot.agent.tools.file_state import FileStateStore, bind_file_states, reset_file_states
from pawbot.agent.tools.registry import ToolRegistry
from pawbot.agent.tools.runtime_control import AgentRuntimeControl
from pawbot.agent.turn.context import TurnContext, TurnKind
from pawbot.agent.turn.stages import TurnStagesMixin
from pawbot.agent.turn_delivery import (
    TurnDelivery,
    TurnDeliveryFactory,
)
from pawbot.agent.turn_delivery import TurnRoute as TurnRoute
from pawbot.agent.turn_hooks import AgentTurnHookSpec, build_agent_turn_hook
from pawbot.bus.events import INBOUND_META_USER_SHELL, InboundMessage, OutboundMessage
from pawbot.bus.outbound_events import StreamedResponseEvent
from pawbot.bus.queue import MessageBus
from pawbot.bus.runtime_events import RuntimeEventBus
from pawbot.command import CommandContext, CommandRouter, register_builtin_commands
from pawbot.config.schema import AgentDefaults, ModelPresetConfig
from pawbot.llm_usage.context import source_from_request
from pawbot.providers.base import GenerationSettings, LLMProvider, ProviderConversationState
from pawbot.providers.factory import ProviderSnapshot
from pawbot.runtime_context import (
    RUNTIME_CONTEXT_HISTORY_META,
    RUNTIME_CONTEXT_MESSAGE_META,
    RuntimeContextBlock,
    RuntimeContextProvider,
    append_runtime_context,
    resolve_runtime_context,
    runtime_context_blocks_from_metadata,
)
from pawbot.security.workspace_access import (
    WorkspaceScopeResolver,
    bind_workspace_scope,
    reset_workspace_scope,
)
from pawbot.session import turn_continuation
from pawbot.session.automation_turns import automation_history_overrides
from pawbot.session.goal_state import (
    goal_state_runtime_lines,
    runner_wall_llm_timeout_s,
)
from pawbot.session.history_visibility import HIDDEN_HISTORY_META
from pawbot.session.keys import UNIFIED_SESSION_KEY, remember_last_channel
from pawbot.session.manager import SESSION_CACHE_MAX_SIZE, Session, SessionManager
from pawbot.session.model_selection import (
    SESSION_MODEL_PRESET_METADATA_KEY,
    SESSION_REASONING_EFFORT_METADATA_KEY,
    model_preset_from_metadata,
    reasoning_effort_from_metadata,
)
from pawbot.session.recovery import (
    PENDING_FOLLOWUP_ID_KEY,
    RECOVERY_INBOUND_METADATA_KEY,
    RecoveryAdmission,
    acknowledge_pending_followups,
    record_pending_followup,
    restore_runtime_checkpoint,
)
from pawbot.triggers.local_turns import LocalTriggerTurnCoordinator
from pawbot.utils.cancellation import task_is_cancelling
from pawbot.utils.document import reference_non_image_attachments
from pawbot.utils.helpers import image_placeholder_text
from pawbot.utils.helpers import truncate_text as truncate_text_fn
from pawbot.utils.llm_runtime import LLMRuntime

if TYPE_CHECKING:
    from pawbot.config.schema import (
        ChannelsConfig,
        Config,
        ProviderConfig,
        ToolsConfig,
    )
    from pawbot.cron.service import CronService
    from pawbot.triggers.local_store import LocalTriggerStore

_T = TypeVar("_T")
_SUBAGENT_TERMINAL_WAIT_SECONDS = 300.0




class AgentLoop(TurnStagesMixin):
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    @property
    def tool_names(self) -> list[str]:
        return self.tools.tool_names

    @property
    def provider(self) -> LLMProvider:
        """Provider selected for future turn admissions."""
        return self.runtime_resolver.runtime.provider

    @property
    def model(self) -> str:
        """Model selected for future turn admissions."""
        return self.runtime_resolver.runtime.model

    @property
    def context_window_tokens(self) -> int:
        """Context limit selected for future turn admissions."""
        return self.runtime_resolver.runtime.context_window_tokens

    @property
    def model_presets(self) -> Mapping[str, ModelPresetConfig]:
        """Configured model presets exposed for selection and display."""
        return self.runtime_resolver.model_presets

    @property
    def model_preset(self) -> str | None:
        return self.runtime_resolver.model_preset

    @model_preset.setter
    def model_preset(self, name: str | None) -> None:
        self.set_model_preset(name)

    def llm_runtime(self) -> LLMRuntime:
        """Resolve the immutable default used to admit the next turn."""
        previous = self.runtime_resolver.runtime
        runtime = self.runtime_resolver.admit()
        if (
            runtime.model != previous.model
            or runtime.model_preset != previous.model_preset
            or runtime.snapshot_signature != previous.snapshot_signature
        ):
            self._publish_runtime_selection(runtime)
        return runtime

    def dream_runtime(self) -> LLMRuntime | None:
        """Resolve the optional preset used for Dream without changing defaults."""
        if not self.dream_model_preset:
            return None
        return self.runtime_resolver.resolve_preset(self.dream_model_preset)

    _RUNTIME_CHECKPOINT_KEY = "runtime_checkpoint"
    _PENDING_USER_TURN_KEY = "pending_user_turn"
    _PROVIDER_STATE_CHECKPOINT_VERSION_KEY = "provider_state_checkpoint_version"
    _PROVIDER_STATE_CHECKPOINT_VERSION = "v1"

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int | None = None,
        max_tool_calls: int | None = None,
        max_turn_seconds: float | None = None,
        max_input_tokens: int | None = None,
        max_output_tokens: int | None = None,
        max_turn_cost_usd: float | None = None,
        input_cost_per_million_usd: float | None = None,
        output_cost_per_million_usd: float | None = None,
        max_concurrent_subagents: int | None = None,
        context_window_tokens: int | None = None,
        context_block_limit: int | None = None,
        max_tool_result_chars: int | None = None,
        provider_retry_mode: str = "standard",
        tool_hint_max_length: int | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        tool_registry: ToolRegistry | None = None,
        channels_config: ChannelsConfig | None = None,
        timezone: str | None = None,
        session_ttl_minutes: int = 0,
        hooks: list[AgentHook] | None = None,
        hook_factories: list[AgentTurnHookFactory] | None = None,
        unified_session: bool = False,
        disabled_skills: list[str] | None = None,
        tools_config: ToolsConfig | None = None,
        image_generation_provider_config: ProviderConfig | None = None,
        image_generation_provider_configs: dict[str, ProviderConfig] | None = None,
        provider_snapshot_loader: Callable[..., ProviderSnapshot] | None = None,
        provider_signature: tuple[object, ...] | None = None,
        model_presets: dict[str, ModelPresetConfig] | None = None,
        preset_catalog_loader: preset_helpers.PresetCatalogLoader | None = None,
        model_preset: str | None = None,
        dream_model_preset: str | None = None,
        preset_snapshot_loader: preset_helpers.PresetSnapshotLoader | None = None,
        runtime_events: RuntimeEventBus | None = None,
        turn_delivery_factory: TurnDeliveryFactory | None = None,
        runtime_model_publisher: Callable[[str, str | None], None] | None = None,
        restart_mode: str = "auto",
        local_trigger_store: LocalTriggerStore | None = None,
        idle_compact_check_interval_seconds: int = 0,
        recovery_admission: RecoveryAdmission | None = None,
        blackbox: Any | None = None,
    ):
        from pawbot.config.schema import ToolsConfig

        _tc = tools_config or ToolsConfig()
        defaults = AgentDefaults()
        self.bus = bus
        self._recovery_admission = recovery_admission
        if turn_delivery_factory is not None:
            if turn_delivery_factory.bus is not bus:
                raise ValueError("turn delivery factory must use the agent message bus")
            if (
                runtime_events is not None
                and turn_delivery_factory.runtime_events is not runtime_events
            ):
                raise ValueError("turn delivery factory must use the agent runtime event bus")
            self.turn_delivery_factory = turn_delivery_factory
            self.runtime_events = turn_delivery_factory.runtime_events
        else:
            self.runtime_events = runtime_events or RuntimeEventBus()
            self.turn_delivery_factory = TurnDeliveryFactory(bus, self.runtime_events)
        self.runtime_event_publisher = self.turn_delivery_factory.runtime_event_publisher
        self.channels_config = channels_config
        self.restart_mode = restart_mode
        self._runtime_model_publisher = runtime_model_publisher
        self.workspace = workspace
        initial_model = model or provider.get_default_model()
        self.max_iterations = (
            max_iterations if max_iterations is not None else defaults.max_tool_iterations
        )
        self.max_tool_calls = max_tool_calls
        self.max_turn_seconds = max_turn_seconds
        self.max_input_tokens = max_input_tokens
        self.max_output_tokens = max_output_tokens
        self.max_turn_cost_usd = max_turn_cost_usd
        self.input_cost_per_million_usd = input_cost_per_million_usd
        self.output_cost_per_million_usd = output_cost_per_million_usd
        initial_context_window = (
            context_window_tokens
            if context_window_tokens is not None
            else defaults.context_window_tokens
        )
        configured_presets = model_presets or {}
        self.runtime_resolver = ModelRuntimeResolver(
            LLMRuntime.capture(
                provider,
                initial_model,
                context_window_tokens=initial_context_window,
                snapshot_signature=provider_signature,
            ),
            model_presets=configured_presets,
            preset_catalog_loader=preset_catalog_loader,
            configured_default_preset=model_preset,
            provider_snapshot_loader=provider_snapshot_loader,
            preset_snapshot_loader=preset_snapshot_loader,
        )
        self.dream_model_preset = dream_model_preset
        self.context_block_limit = context_block_limit
        self.max_tool_result_chars = (
            max_tool_result_chars
            if max_tool_result_chars is not None
            else defaults.max_tool_result_chars
        )
        self.provider_retry_mode = provider_retry_mode
        self.tool_hint_max_length = (
            tool_hint_max_length if tool_hint_max_length is not None
            else defaults.tool_hint_max_length
        )
        self.tools_config = _tc
        self.denied_tool_capabilities = frozenset(_tc.denied_capabilities)
        self.web_config = _tc.web
        self.exec_config = _tc.exec
        self._image_generation_provider_configs = dict(image_generation_provider_configs or {})
        if (
            image_generation_provider_config is not None
            and "openrouter" not in self._image_generation_provider_configs
        ):
            self._image_generation_provider_configs["openrouter"] = image_generation_provider_config
        self.cron_service = cron_service
        self.local_trigger_store = local_trigger_store
        self.blackbox = blackbox  # Record & Replay controller (ADR-004)
        self.restrict_to_workspace = restrict_to_workspace
        self.workspace_scopes = WorkspaceScopeResolver(
            default_workspace=workspace,
            default_restrict_to_workspace=restrict_to_workspace,
        )
        self._start_time = time.time()
        self._extra_hooks: list[AgentHook] = hooks or []
        self._hook_factories: list[AgentTurnHookFactory] = hook_factories or []

        self.context = ContextBuilder(workspace, timezone=timezone, disabled_skills=disabled_skills)
        self.sessions = session_manager or SessionManager(workspace)
        # One file-read/write tracker per logical session. The tool registry is
        # shared by this loop, so tools resolve the active state via contextvars.
        self._file_state_store = FileStateStore(max_sessions=SESSION_CACHE_MAX_SIZE)
        # SessionManager owns every durable deletion entrypoint, including the
        # WebUI and fork rollback paths.  Observe that boundary once instead of
        # duplicating cleanup in each consumer.
        self.sessions.set_delete_observer(self._file_state_store.discard)
        self.tools = tool_registry if tool_registry is not None else ToolRegistry()
        self._exec_session_manager = ExecSessionManager()
        self.runner = AgentRunner()
        self.subagents = SubagentManager(
            workspace=workspace,
            bus=bus,
            tools_config=_tc,
            max_tool_result_chars=self.max_tool_result_chars,
            restrict_to_workspace=restrict_to_workspace,
            disabled_skills=disabled_skills,
            max_iterations=self.max_iterations,
            max_concurrent_subagents=max_concurrent_subagents,
            llm_wall_timeout_for_session=lambda sk: runner_wall_llm_timeout_s(self.sessions, sk),
        )
        self._unified_session = unified_session
        self._running = False
        self._runtime_context_providers: list[RuntimeContextProvider] = []
        self._active_tasks: dict[str, set[asyncio.Task[Any]]] = {}
        self._discarding_sessions: set[str] = set()
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._close_lock = asyncio.Lock()
        self._session_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )
        # Per-session pending queues for mid-turn message injection.
        # When a session has an active task, new messages for that session
        # are routed here instead of creating a new task.
        self._pending_queues: dict[str, asyncio.Queue[InboundMessage]] = {}
        self._preserve_inflight_turns_on_shutdown = False
        self._deferred_automation_turns: dict[str, list[InboundMessage]] = {}
        self._cron_turns = CronTurnCoordinator(
            publish_inbound=self.bus.publish_inbound,
            dispatch=self._dispatch,
            is_running=lambda: self._running,
            deferred_queues=self._deferred_automation_turns,
        )
        self._local_trigger_turns = LocalTriggerTurnCoordinator(
            publish_inbound=self.bus.publish_inbound,
            dispatch=self._dispatch,
            is_running=lambda: self._running,
            deferred_queues=self._deferred_automation_turns,
        )
        self._automation_turn_coordinators = (
            ("cron", self._cron_turns),
            ("local trigger", self._local_trigger_turns),
        )
        # PAWBOT_MAX_CONCURRENT_REQUESTS: unset or <=0 means unlimited.
        _max = int(os.environ.get("PAWBOT_MAX_CONCURRENT_REQUESTS", "0"))
        self._concurrency_gate: asyncio.Semaphore | None = (
            asyncio.Semaphore(_max) if _max > 0 else None
        )
        self.consolidator = Consolidator(
            store=self.context.memory,
            sessions=self.sessions,
            build_messages=self.context.build_messages,
            get_tool_definitions=self.tools.get_definitions,
            resolve_prompt_context=PersistedPromptContextResolver(
                workspace_scopes=self.workspace_scopes,
                unified_session=unified_session,
            ),
            unified_session=unified_session,
        )
        self.auto_compact = AutoCompact(
            sessions=self.sessions,
            consolidator=self.consolidator,
            session_ttl_minutes=session_ttl_minutes,
        )
        self._idle_compact_check_interval_s = idle_compact_check_interval_seconds
        self._next_idle_compact_check_at = time.monotonic()
        if model_preset:
            self.set_model_preset(model_preset, publish_update=False)
        self._register_default_tools(provider_snapshot_loader=provider_snapshot_loader)
        self.commands = CommandRouter()
        register_builtin_commands(self.commands)

    @classmethod
    def from_config(
        cls,
        config: Config,
        bus: MessageBus | None = None,
        *,
        tool_registry: ToolRegistry,
        **extra: Any,
    ) -> AgentLoop:
        """Create an AgentLoop from config with the common parameter set.

        The tool registry is caller-owned so application composition can share
        it with infrastructure such as an ``MCPProvider``.

        Extra keyword arguments are forwarded to ``AgentLoop.__init__``,
        allowing callers to override or extend the standard config-derived
        parameters (e.g. ``cron_service``, ``session_manager``).
        """
        from pawbot.providers.factory import make_provider

        if bus is None:
            bus = MessageBus()
        defaults = config.agents.defaults
        if "session_manager" not in extra:
            data_dir = config.runtime_data_dir
            extra["session_manager"] = SessionManager(
                config.workspace_path,
                sessions_root=data_dir / "sessions" if data_dir is not None else None,
            )
        provider = extra.pop("provider", None) or make_provider(config)
        resolved = config.resolve_preset()
        model = extra.pop("model", None) or resolved.model
        context_window_tokens = extra.pop("context_window_tokens", None)
        if context_window_tokens is None:
            context_window_tokens = resolved.context_window_tokens
        if not isinstance(context_window_tokens, int) or context_window_tokens <= 0:
            # ADR-002: upstream silently disabled every context-defense layer when
            # the window was missing/non-positive; fail loudly instead.
            raise ValueError(
                "context_window_tokens must be a positive integer "
                f"(got {context_window_tokens!r}); refusing to run with the "
                "context-governance defenses disabled"
            )
        provider_snapshot_loader = extra.pop("provider_snapshot_loader", None)
        preset_snapshot_loader = extra.pop("preset_snapshot_loader", None) or preset_helpers.make_preset_snapshot_loader(
            config,
            provider_snapshot_loader,
        )
        return cls(
            bus=bus,
            provider=provider,
            workspace=config.workspace_path,
            model=model,
            max_iterations=defaults.max_tool_iterations,
            max_tool_calls=defaults.max_tool_calls,
            max_turn_seconds=defaults.max_turn_seconds,
            max_input_tokens=defaults.max_input_tokens,
            max_output_tokens=defaults.max_output_tokens,
            max_turn_cost_usd=defaults.max_turn_cost_usd,
            input_cost_per_million_usd=defaults.input_cost_per_million_usd,
            output_cost_per_million_usd=defaults.output_cost_per_million_usd,
            max_concurrent_subagents=defaults.max_concurrent_subagents,
            context_window_tokens=context_window_tokens,
            context_block_limit=defaults.context_block_limit,
            max_tool_result_chars=defaults.max_tool_result_chars,
            provider_retry_mode=defaults.provider_retry_mode,
            tool_hint_max_length=defaults.tool_hint_max_length,
            restrict_to_workspace=config.tools.restrict_to_workspace,
            channels_config=config.channels,
            timezone=defaults.timezone,
            unified_session=defaults.unified_session,
            disabled_skills=defaults.disabled_skills,
            session_ttl_minutes=defaults.session_ttl_minutes,
            idle_compact_check_interval_seconds=defaults.idle_compact_check_interval_seconds,
            tools_config=config.tools,
            model_presets=preset_helpers.configured_model_presets(config),
            model_preset=defaults.model_preset,
            dream_model_preset=defaults.dream.model_override,
            restart_mode=config.gateway.restart_mode,
            provider_snapshot_loader=provider_snapshot_loader,
            preset_snapshot_loader=preset_snapshot_loader,
            tool_registry=tool_registry,
            **extra,
        )

    def _sync_subagent_runtime_limits(self) -> None:
        """Keep subagent runtime limits aligned with mutable loop settings."""
        self.subagents.max_iterations = self.max_iterations

    def invalidate_runtime_config(self) -> None:
        """Invalidate runtime config for lazy refresh at the next admission."""
        self.runtime_resolver.invalidate()

    def refresh_runtime_config(self) -> LLMRuntime:
        """Refresh runtime config now and publish the canonical selection."""
        self.runtime_resolver.invalidate()
        runtime = self.runtime_resolver.admit()
        self._publish_runtime_selection(runtime)
        return runtime

    def runtime_for_session(
        self,
        session: Session,
        *,
        recover_removed: bool = True,
    ) -> LLMRuntime:
        """Resolve the immutable runtime selected by one session."""
        name = model_preset_from_metadata(session.metadata)
        if name is None:
            runtime = self.llm_runtime()
        else:
            try:
                runtime = self.runtime_resolver.resolve_preset(name)
            except KeyError:
                if not recover_removed or name in self.runtime_resolver.model_presets:
                    raise
                logger.warning(
                    "Session '{}' references removed model preset '{}'; falling back to default",
                    session.key,
                    name,
                )
                session.metadata.pop(SESSION_MODEL_PRESET_METADATA_KEY, None)
                self.sessions.save(session)
                runtime = self.llm_runtime()
        effort = reasoning_effort_from_metadata(session.metadata)
        if effort is not None:
            runtime = runtime.with_generation_overrides(reasoning_effort=effort)
        return runtime

    def set_session_model_preset(
        self,
        session_key: str,
        name: str,
    ) -> LLMRuntime:
        """Validate and persist one session's preset selection."""
        runtime = self.runtime_resolver.resolve_preset(name)
        session = self.sessions.get_or_create(session_key)
        session.metadata[SESSION_MODEL_PRESET_METADATA_KEY] = runtime.model_preset
        self.sessions.save(session)
        return runtime

    def set_session_reasoning_effort(
        self,
        session_key: str,
        effort: str,
    ) -> LLMRuntime:
        """Validate and persist one session's thinking-effort override.

        ``effort`` is one of the provider/model's supported reasoning-effort
        values (or an empty string to restore the preset default). The returned
        runtime reflects the override so callers can report the effective
        selection immediately.
        """
        session = self.sessions.get_or_create(session_key)
        runtime = self.runtime_for_session(session)
        effective = effort.strip()
        if effective:
            from pawbot.providers.registry import reasoning_effort_values_for

            provider = getattr(runtime.provider, "provider_name", "") or ""
            supported = reasoning_effort_values_for(provider, runtime.model)
            if effective not in supported:
                raise ValueError(
                    f"unsupported reasoning effort {effective!r}; "
                    f"supported values: {', '.join(supported) or '(none)'}"
                )
            session.metadata[SESSION_REASONING_EFFORT_METADATA_KEY] = effective
        else:
            session.metadata.pop(SESSION_REASONING_EFFORT_METADATA_KEY, None)
        self.sessions.save(session)
        return dataclasses.replace(
            runtime,
            generation=GenerationSettings(
                temperature=runtime.generation.temperature,
                max_tokens=runtime.generation.max_tokens,
                reasoning_effort=effective or None,
            ),
        )

    def _publish_runtime_selection(
        self,
        runtime: LLMRuntime,
        *,
        publish_update: bool = True,
    ) -> None:
        if not publish_update:
            return
        if self._runtime_model_publisher is not None:
            self._runtime_model_publisher(runtime.model, runtime.model_preset)
        self.runtime_event_publisher.runtime_model_changed(
            runtime.model,
            runtime.model_preset,
        )

    def set_model_preset(
        self,
        name: str | None,
        *,
        publish_update: bool = True,
    ) -> LLMRuntime:
        """Select a named default runtime for future turns."""
        old_model = self.model
        runtime = self.runtime_resolver.select_preset(name)
        self._publish_runtime_selection(runtime, publish_update=publish_update)
        logger.info(
            "Runtime model switched for next turn: {} -> {}",
            old_model,
            runtime.model,
        )
        return runtime

    def set_runtime_model(self, model: str) -> LLMRuntime:
        """Select a model on the current provider for future turns."""
        return self.runtime_resolver.select_model(model)

    def set_runtime_context_window(self, context_window_tokens: int) -> LLMRuntime:
        """Select a context limit for future turns."""
        return self.runtime_resolver.select_context_window(context_window_tokens)

    def _register_default_tools(
        self,
        *,
        provider_snapshot_loader: Callable[..., ProviderSnapshot] | None,
    ) -> None:
        """Register the default set of tools via plugin loader."""
        from pawbot.agent.tools.context import ToolContext
        from pawbot.agent.tools.loader import ToolLoader

        ctx = ToolContext(
            config=self.tools_config,
            workspace=str(self.workspace),
            bus=self.bus,
            subagent_manager=self.subagents,
            cron_service=self.cron_service,
            exec_session_manager=self._exec_session_manager,
            sessions=self.sessions,
            provider_snapshot_loader=provider_snapshot_loader,
            image_generation_provider_configs=self._image_generation_provider_configs,
            timezone=self.context.timezone or "UTC",
            workspace_sandbox=self.workspace_scopes.sandbox_status,
            runtime_events=self.runtime_events,
            runtime_control=AgentRuntimeControl(self),
        )
        loader = ToolLoader()
        registered = loader.load(ctx, self.tools)

        logger.info("Registered {} tools: {}", len(registered), registered)

    def register_runtime_context_provider(
        self,
        provider: RuntimeContextProvider,
    ) -> Callable[[], None]:
        """Register a per-turn context provider and return an unsubscribe callback."""
        if provider in self._runtime_context_providers:
            return lambda: None
        self._runtime_context_providers.append(provider)

        def _unsubscribe() -> None:
            with suppress(ValueError):
                self._runtime_context_providers.remove(provider)

        return _unsubscribe

    async def submit_cron_turn(self, msg: InboundMessage) -> OutboundMessage | None:
        return await self._cron_turns.submit(msg)

    async def submit_local_trigger_turn(self, msg: InboundMessage) -> OutboundMessage | None:
        return await self._local_trigger_turns.submit(msg)

    def pending_cron_job_ids_for_session(self, session_key: str) -> set[str]:
        return self._cron_turns.pending_job_ids_for_session(session_key)

    def pending_local_trigger_ids_for_session(self, session_key: str) -> set[str]:
        return self._local_trigger_turns.pending_trigger_ids_for_session(session_key)

    async def _publish_next_deferred_automation_turn(self, session_key: str) -> None:
        await publish_next_deferred_turn(
            deferred_queues=self._deferred_automation_turns,
            publish_inbound=self.bus.publish_inbound,
            session_key=session_key,
        )

    def _persist_user_message_early(
        self,
        msg: InboundMessage,
        session: Session,
        runtime_context_blocks: list[RuntimeContextBlock] | None = None,
        **kwargs: Any,
    ) -> bool:
        """Persist the triggering user message before the turn starts.

        Returns True if the message was persisted.
        """
        if not turn_continuation.should_persist_user_message(msg.metadata):
            return False
        media_paths = [
            path
            for path in (msg.media or [])
            if isinstance(cast(object, path), str) and path
        ]
        content_value = cast(object, msg.content)
        has_text = isinstance(content_value, str) and content_value.strip()
        if has_text or media_paths or runtime_context_blocks:
            extra: dict[str, Any] = ({"media": list(media_paths)} if media_paths else {}) | agent_context.session_extra(msg.metadata)
            extra.update(kwargs)
            text = content_value if isinstance(content_value, str) else ""
            text_override, automation_extra = automation_history_overrides(msg.metadata)
            if text_override is not None:
                text = text_override
            extra.update(automation_extra)
            text, runtime_context_meta = append_runtime_context(
                text,
                runtime_context_blocks or (),
            )
            if runtime_context_meta is not None:
                extra[RUNTIME_CONTEXT_HISTORY_META] = runtime_context_meta
            session.add_message("user", text, **extra)
            self._mark_pending_user_turn(session)
            followup_id = msg.metadata.get(PENDING_FOLLOWUP_ID_KEY)
            if isinstance(followup_id, str) and followup_id:
                acknowledge_pending_followups(session, [followup_id])
            self.sessions.save(session)
            return True
        return False

    def _build_initial_messages(self, ctx: TurnContext) -> list[dict[str, Any]]:
        """Build the initial message list for the LLM turn."""
        assert ctx.session is not None
        scope = self.workspace_scopes.for_message(ctx.msg, ctx.session.metadata)
        return self.context.build_messages(
            history=ctx.history,
            current_message=ctx.msg.content,
            media=ctx.msg.media if ctx.kind is TurnKind.USER and ctx.msg.media else None,
            channel=ctx.delivery.route.channel,
            session_summary=ctx.pending_summary,
            workspace=scope.project_path,
            runtime_context_blocks=ctx.runtime_context_blocks,
            include_memory=ctx.session.policy.persist,
            include_memory_recent_history=not ctx.ephemeral,
            session_key=ctx.session.key,
            unified_session=self._unified_session,
        )

    def _request_context_for_turn(self, ctx: TurnContext) -> RequestContext:
        assert ctx.session is not None
        scope = self.workspace_scopes.for_turn(
            channel=ctx.delivery.route.channel,
            message_metadata=ctx.msg.metadata,
            session_metadata=ctx.session.metadata,
        )
        return RequestContext(
            channel=ctx.delivery.route.channel,
            chat_id=ctx.delivery.route.chat_id,
            message_id=ctx.msg.metadata.get("message_id"),
            session_key=ctx.session_key,
            original_user_text=ctx.original_user_text,
            runtime=ctx.runtime,
            metadata=dict(ctx.msg.metadata or {}),
            attributes=dict(ctx.attributes),
            sender_id=ctx.msg.sender_id,
            turn_id=ctx.turn_id,
            workspace=scope.project_path,
        )

    async def _resolve_runtime_context_for_turn(
        self,
        ctx: TurnContext,
    ) -> list[RuntimeContextBlock]:
        assert ctx.request_context is not None
        return await self._resolve_runtime_context_for_request(
            ctx.request_context,
            ctx.tools or self.tools,
        )

    async def _resolve_runtime_context_for_request(
        self,
        request: RequestContext,
        tools: ToolRegistry,
    ) -> list[RuntimeContextBlock]:
        providers = [
            *tools.get_runtime_context_providers(),
            *self._runtime_context_providers,
        ]
        blocks = runtime_context_blocks_from_metadata(request.metadata)
        blocks.extend(await resolve_runtime_context(providers, request))
        skill_context = self.context.skills.build_explicit_skill_runtime_context(
            request.original_user_text or ""
        )
        if skill_context is not None and skill_context not in blocks:
            blocks.append(skill_context)
        return blocks

    async def _dispatch_command_inline(
        self,
        msg: InboundMessage,
        key: str,
        raw: str,
        dispatch_fn: Callable[[CommandContext], Awaitable[OutboundMessage | None]],
    ) -> None:
        """Dispatch a command directly from the run() loop and publish the result."""
        async def dispatch_and_publish() -> None:
            ctx = CommandContext(msg=msg, session=None, key=key, raw=raw, loop=self)
            result = await dispatch_fn(ctx)
            if result:
                await self.bus.publish_outbound(result)
            else:
                logger.warning("Command '{}' matched but dispatch returned None", raw)

        # A shell command may run for up to the configured exec timeout. Keep
        # the inbound consumer responsive when it runs beside an active turn.
        if (msg.metadata or {}).get(INBOUND_META_USER_SHELL) is True:
            self.schedule_background(dispatch_and_publish())
            return
        await dispatch_and_publish()

    async def execute_user_shell_command(self, ctx: CommandContext) -> OutboundMessage:
        """Execute one trusted user command with the active workspace policy."""
        metadata = dict(ctx.msg.metadata or {})
        tool = self.tools.get("exec")
        if tool is None:
            content = "Shell execution is disabled in this pawbot configuration."
        else:
            session = ctx.session or self.sessions.get_or_create(ctx.key)
            scope = self.workspace_scopes.for_turn(
                channel=ctx.msg.channel,
                message_metadata=metadata,
                session_metadata=session.metadata,
            )
            request_token = bind_request_context(RequestContext(
                channel=ctx.msg.channel,
                chat_id=ctx.msg.chat_id,
                message_id=metadata.get("message_id"),
                session_key=ctx.key,
                original_user_text=f"!{ctx.args.strip()}",
                runtime=ctx.runtime,
                metadata=metadata,
                sender_id=ctx.msg.sender_id,
                turn_id=metadata.get("webui_turn_id"),
                workspace=scope.project_path,
            ))
            workspace_token = bind_workspace_scope(scope)
            turn_scope_stack = ExitStack()
            try:
                for turn_scope in ctx.turn_scopes:
                    turn_scope_stack.enter_context(turn_scope)
                result = await tool.execute(
                    command=ctx.args.strip(),
                    working_dir=str(scope.project_path),
                )
                content = str(result)
            finally:
                turn_scope_stack.close()
                reset_workspace_scope(workspace_token)
                reset_request_context(request_token)
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=content,
            metadata={**metadata, "render_as": "text"},
        )

    async def _cancel_active_tasks(self, key: str) -> int:
        """Cancel and await all active work for *key*.

        Returns the total number of cancelled tasks, subagents, and exec sessions.
        """
        tasks = tuple(self._active_tasks.pop(key, set()))
        cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
        for t in tasks:
            with suppress(asyncio.CancelledError, Exception):
                await t
        sub_cancelled = await self.subagents.cancel_by_session(key)
        exec_cancelled = await self._exec_session_manager.terminate_by_owner(key)
        return cancelled + sub_cancelled + exec_cancelled

    async def discard_session(self, key: str) -> None:
        """Stop active work for *key* and forget its cached session."""
        self._discarding_sessions.add(key)
        try:
            self.sessions.invalidate(key)
            await self._cancel_active_tasks(key)
        finally:
            self.discard_session_file_state(key)
            self._discarding_sessions.discard(key)

    def discard_session_file_state(self, key: str) -> None:
        """Forget ephemeral file-read state for a reset or removed session."""
        self._file_state_store.discard(key)

    def _effective_session_key(self, msg: InboundMessage) -> str:
        """Return the session key used for task routing and mid-turn injections."""
        if self._unified_session and not msg.session_key_override:
            return UNIFIED_SESSION_KEY
        return msg.session_key

    def _remember_unified_session_route(
        self,
        session: Session,
        msg: InboundMessage,
        *,
        is_user_turn: bool,
    ) -> None:
        """Remember the latest user-facing route for unified-session delivery."""
        if (
            not self._unified_session
            or session.key != UNIFIED_SESSION_KEY
            or not is_user_turn
            or msg.channel in {"cli", "system"}
            or msg.sender_id == "subagent"
        ):
            return
        _, automation_metadata = automation_history_overrides(msg.metadata)
        if automation_metadata:
            return
        remember_last_channel(session.metadata, msg.channel, msg.chat_id)

    @staticmethod
    def _replay_token_budget(runtime: LLMRuntime, *, reserved_system_tokens: int = 0) -> int:
        """Derive a token budget for session history replay from the context window.

        Reserves the model max output, a 1024-token safety buffer, and the
        system prompt (whose tokens were historically unaccounted for; see
        ADR-002).
        """
        if runtime.context_window_tokens <= 0:
            return 0
        max_output = runtime.generation.max_tokens
        try:
            reserved_output = int(max_output)
        except (TypeError, ValueError):
            reserved_output = 4096
        budget = (
            runtime.context_window_tokens
            - max(1, reserved_output)
            - 1024
            - max(0, reserved_system_tokens)
        )
        return budget if budget > 0 else max(128, runtime.context_window_tokens // 2)

    async def _run_agent_loop(
        self,
        initial_messages: list[dict[str, Any]],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        on_retry_wait: Callable[[str], Awaitable[None]] | None = None,
        *,
        runtime: LLMRuntime,
        session: Session | None = None,
        pending_queue: asyncio.Queue[InboundMessage] | None = None,
        ephemeral: bool = False,
        run_extra_hooks_for_ephemeral: bool = False,
        hooks: list[AgentHook] | None = None,
        hook_factories: list[AgentTurnHookFactory] | None = None,
        turn_scopes: list[AbstractContextManager[Any]] | None = None,
        tools: ToolRegistry | None = None,
        request_context: RequestContext | None = None,
        provider_state: ProviderConversationState | None = None,
    ) -> AgentRunResult:
        """Run the agent iteration loop.

        *on_stream*: called with each content delta during streaming.
        *on_stream_end(resuming, merge_next)*: called when a streaming session finishes.
        ``resuming=True`` means the active turn continues. ``merge_next=True`` means
        the next text segment belongs to the same user-visible assistant message.

        Returns the complete result produced by ``AgentRunner``.
        """
        self._sync_subagent_runtime_limits()

        async def _checkpoint(payload: dict[str, Any]) -> None:
            if session is None:
                return
            public_payload = dict(payload)
            private_state = public_payload.pop("provider_state", None)
            public_payload.pop(self._PROVIDER_STATE_CHECKPOINT_VERSION_KEY, None)
            if "provider_state" in payload and (
                private_state is None
                or isinstance(private_state, ProviderConversationState)
            ):
                session.provider_state = private_state
                public_payload[self._PROVIDER_STATE_CHECKPOINT_VERSION_KEY] = (
                    self._PROVIDER_STATE_CHECKPOINT_VERSION
                )
            self._set_runtime_checkpoint(session, public_payload)

        async def _drain_pending(
            *,
            limit: int = _MAX_INJECTIONS_PER_TURN,
            first_msg: InboundMessage | None = None,
        ) -> list[dict[str, Any]]:
            """Drain only messages that are already available."""
            if pending_queue is None:
                return []

            async def _to_user_message(pending_msg: InboundMessage) -> dict[str, Any]:
                content = pending_msg.content
                image_paths = pending_msg.media if pending_msg.media else None
                if image_paths:
                    content, image_paths = reference_non_image_attachments(
                        content,
                        image_paths,
                    )
                    image_paths = image_paths or None
                user_content = self.context.build_user_content(
                    content,
                    image_paths=image_paths,
                )
                row: dict[str, Any] = {"role": "user", "content": user_content}
                metadata_value = cast(object, pending_msg.metadata)
                metadata = (
                    pending_msg.metadata
                    if isinstance(metadata_value, dict)
                    else {}
                )
                if pending_msg.is_user_input:
                    scope = self.workspace_scopes.for_turn(
                        channel=pending_msg.channel,
                        message_metadata=metadata,
                        session_metadata=session.metadata if session is not None else None,
                    )
                    pending_request = RequestContext(
                        channel=pending_msg.channel,
                        chat_id=pending_msg.chat_id,
                        message_id=metadata.get("message_id"),
                        session_key=active_session_key,
                        original_user_text=pending_msg.content,
                        runtime=runtime,
                        metadata=dict(metadata),
                        attributes=dict(request_ctx.attributes),
                        sender_id=pending_msg.sender_id,
                        turn_id=request_ctx.turn_id,
                        workspace=scope.project_path,
                    )
                    blocks = await self._resolve_runtime_context_for_request(
                        pending_request,
                        effective_tools,
                    )
                    row["content"], runtime_marker = append_runtime_context(
                        user_content,
                        blocks,
                    )
                    if runtime_marker is not None:
                        row["_meta"] = {
                            RUNTIME_CONTEXT_MESSAGE_META: runtime_marker,
                        }
                if (
                    pending_msg.sender_id == "subagent"
                    and metadata.get("injected_event") == "subagent_result"
                ):
                    subagent_marker: dict[str, Any] = {"kind": "subagent_result"}
                    task_id = metadata.get("subagent_task_id")
                    if isinstance(task_id, str) and task_id:
                        subagent_marker["subagent_task_id"] = task_id
                        row["subagent_task_id"] = task_id
                    row[HIDDEN_HISTORY_META] = subagent_marker
                    row["injected_event"] = "subagent_result"
                followup_id = metadata.get(PENDING_FOLLOWUP_ID_KEY)
                if isinstance(followup_id, str) and followup_id:
                    row[PENDING_FOLLOWUP_ID_KEY] = followup_id
                return row

            items: list[dict[str, Any]] = []
            if first_msg is not None:
                items.append(await _to_user_message(first_msg))
            while len(items) < limit:
                try:
                    items.append(await _to_user_message(pending_queue.get_nowait()))
                except asyncio.QueueEmpty:
                    break

            return items

        terminal_wait_deadline: float | None = None

        async def _wait_for_pending(
            *,
            limit: int = _MAX_INJECTIONS_PER_TURN,
        ) -> list[dict[str, Any]]:
            """Wait for a pending result only when the runner is ready to exit."""
            nonlocal terminal_wait_deadline

            items = await _drain_pending(limit=limit)
            if (
                items
                or pending_queue is None
                or session is None
                or self.subagents.get_running_count_by_session(session.key) == 0
            ):
                return items

            now = asyncio.get_running_loop().time()
            if terminal_wait_deadline is None:
                terminal_wait_deadline = now + _SUBAGENT_TERMINAL_WAIT_SECONDS
            remaining = terminal_wait_deadline - now
            if remaining <= 0:
                return []

            try:
                msg = await asyncio.wait_for(pending_queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                logger.warning(
                    "Timeout waiting for sub-agent completion before session {} exits",
                    session.key,
                )
                return []

            return await _drain_pending(limit=limit, first_msg=msg)

        request_ctx = request_context or RequestContext(
            channel="cli",
            chat_id="direct",
            session_key=session.key if session is not None else None,
            runtime=runtime,
        )
        active_session_key = session.key if session else request_ctx.session_key
        request_metadata = request_ctx.metadata
        effective_scope = self.workspace_scopes.for_turn(
            channel=request_ctx.channel,
            message_metadata=request_metadata,
            session_metadata=session.metadata if session is not None else None,
        )
        if request_context is None:
            request_ctx = dataclasses.replace(
                request_ctx,
                workspace=effective_scope.project_path,
            )
        effective_tools = tools or self.tools
        file_state_token = bind_file_states(self._file_state_store.for_session(active_session_key))
        request_token = bind_request_context(request_ctx)
        workspace_token = bind_workspace_scope(effective_scope)
        turn_scope_stack = ExitStack()
        # Compute lazily because create_goal may create goal metadata during this run.
        def _goal_continue() -> str | None:
            _goal_lines = goal_state_runtime_lines(session.metadata if session is not None else None)
            if not _goal_lines:
                return None
            return (
                "You have an active sustained goal:\n\n"
                + "\n".join(_goal_lines)
                + "\n\nPlease continue working toward the objective using your tools, "
                "or call update_goal with action='complete' if the work is truly finished."
            )

        session_metadata = session.metadata if session is not None else None
        # ADR-004: Record & Replay blackbox — enter the vcr/tool rails and
        # attach the recorder hook when --record is active.
        blackbox_scope = (
            self.blackbox.turn_scope(request_ctx.turn_id)
            if self.blackbox is not None
            else None
        )
        blackbox_hook = (
            self.blackbox.turn_hook(
                request_ctx.turn_id,
                initial_messages,
                session_key=active_session_key,
                model=runtime.model,
                tools_definitions=effective_tools.get_definitions(),
            )
            if self.blackbox is not None
            else None
        )
        if self.blackbox is not None:
            self.blackbox.write_meta(session_key=active_session_key, model=runtime.model)
        turn_hooks = list(hooks or [])
        if blackbox_hook is not None:
            turn_hooks.append(blackbox_hook)
        try:
            for scope in turn_scopes or ():
                turn_scope_stack.enter_context(scope)
            if blackbox_scope is not None:
                turn_scope_stack.enter_context(blackbox_scope)
            hook = build_agent_turn_hook(AgentTurnHookSpec(
                on_progress=on_progress,
                on_stream=on_stream,
                on_stream_end=on_stream_end,
                channel=request_ctx.channel,
                chat_id=request_ctx.chat_id,
                message_id=request_ctx.message_id,
                metadata=request_metadata,
                attributes=dict(request_ctx.attributes),
                session_key=active_session_key,
                workspace=effective_scope.project_path,
                tool_hint_max_length=self.tool_hint_max_length,
                registered_hook_factories=self._hook_factories,
                turn_hook_factories=list(hook_factories or []),
                registered_hooks=self._extra_hooks,
                turn_hooks=turn_hooks,
                ephemeral=ephemeral,
                run_extra_hooks_for_ephemeral=run_extra_hooks_for_ephemeral,
            ))
            result = await self.runner.run(AgentRunSpec(
                initial_messages=initial_messages,
                tools=effective_tools,
                runtime=runtime,
                max_iterations=self.max_iterations,
                budget=TurnBudget(
                    max_iterations=self.max_iterations,
                    max_tool_calls=self.max_tool_calls,
                    max_wall_seconds=self.max_turn_seconds,
                    max_input_tokens=self.max_input_tokens,
                    max_output_tokens=self.max_output_tokens,
                    max_cost_usd=self.max_turn_cost_usd,
                    input_cost_per_million_usd=self.input_cost_per_million_usd,
                    output_cost_per_million_usd=self.output_cost_per_million_usd,
                ),
                max_tool_result_chars=self.max_tool_result_chars,
                hook=hook,
                concurrent_tools=True,
                workspace=effective_scope.project_path,
                session_key=session.key if session else None,
                context_block_limit=self.context_block_limit,
                provider_retry_mode=self.provider_retry_mode,
                retry_wait_callback=on_retry_wait,
                checkpoint_callback=_checkpoint,
                injection_callback=_drain_pending,
                terminal_injection_callback=_wait_for_pending,
                # Sustained goals may legitimately exceed PAWBOT_LLM_TIMEOUT_S; idle stall
                # is still capped by PAWBOT_STREAM_IDLE_TIMEOUT_S in streaming providers.
                llm_timeout_s=runner_wall_llm_timeout_s(
                    self.sessions,
                    session.key if session is not None else request_ctx.session_key,
                    metadata=session_metadata,
                    message_metadata=request_metadata,
                ),
                continuation_callback=_goal_continue,
                finalize_on_max_iterations=turn_continuation.should_finalize_on_max_iterations(
                    pending_queue_available=pending_queue is not None and session is not None,
                    session_metadata=session_metadata,
                    message_metadata=request_metadata,
                ),
                provider_state=provider_state,
                denied_tool_capabilities=self.denied_tool_capabilities,
                llm_usage_source=source_from_request(
                    active_session_key,
                    channel=request_ctx.channel,
                    metadata=request_metadata,
                ),
            ))
        finally:
            turn_scope_stack.close()
            reset_workspace_scope(workspace_token)
            reset_request_context(request_token)
            reset_file_states(file_state_token)
        if session is not None and not ephemeral:
            session.provider_state = result.provider_state
        if result.stop_reason == "max_iterations":
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            should_stream = turn_continuation.should_stream_budget_response(
                stop_reason=result.stop_reason,
                pending_queue_available=pending_queue is not None and session is not None,
                session_metadata=session_metadata,
                message_metadata=request_metadata,
            )
            # Push final content through stream so streaming channels (e.g. Feishu)
            # update the card instead of leaving it empty.
            if on_stream and on_stream_end and should_stream:
                stream_content = (
                    result.pending_stream_content
                    if result.pending_stream_content is not None
                    else result.final_content or ""
                )
                await on_stream(stream_content)
                await on_stream_end(resuming=False)
        elif result.stop_reason == "error":
            logger.error("LLM returned error: {}", (result.final_content or "")[:200])
        return result

    def _check_expired_sessions_if_due(self) -> None:
        """Scan idle sessions no more often than the configured interval."""
        now = time.monotonic()
        if now < self._next_idle_compact_check_at:
            return
        self._next_idle_compact_check_at = now + self._idle_compact_check_interval_s
        self.auto_compact.check_expired(
            self.schedule_background,
            self.runtime_for_session,
            active_session_keys=self._pending_queues.keys(),
        )

    async def run(self) -> None:
        """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
        self._running = True
        try:
            logger.info("Agent loop started")

            while self._running:
                try:
                    msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
                except asyncio.TimeoutError:
                    self._check_expired_sessions_if_due()
                    continue
                except asyncio.CancelledError:
                    # Preserve real task cancellation so shutdown can complete cleanly.
                    # Only ignore non-task CancelledError signals that may leak from integrations.
                    if not self._running or task_is_cancelling():
                        raise
                    logger.warning(
                        "Ignoring leaked CancelledError while consuming inbound messages"
                    )
                    continue
                except Exception as e:
                    logger.warning("Error consuming inbound message: {}, continuing...", e)
                    continue

                raw = msg.content.strip()
                effective_key = self._effective_session_key(msg)
                if await agent_context.handle_runtime_control(self, msg, self.tools):
                    continue
                if (
                    msg.require_existing_session
                    and self.sessions.get_cached(effective_key) is None
                ):
                    continue
                if msg.is_user_input:
                    await self.runtime_event_publisher.user_input_accepted(msg, effective_key)
                if msg.channel != "system" and self.commands.is_priority(raw):
                    await self._dispatch_command_inline(
                        msg, effective_key, raw,
                        self.commands.dispatch_priority,
                    )
                    continue
                deferred = False
                for label, coordinator in self._automation_turn_coordinators:
                    if coordinator.defer_if_active(
                        msg,
                        session_key=effective_key,
                        active_session_keys=self._pending_queues.keys(),
                    ):
                        logger.info(
                            "Deferred {} turn for active session {}",
                            label,
                            effective_key,
                        )
                        deferred = True
                        break
                if deferred:
                    continue
                routed_msg = msg
                if effective_key != msg.session_key:
                    routed_msg = dataclasses.replace(
                        msg,
                        session_key_override=effective_key,
                    )
                # A newer WebUI message must supersede an explicit recovery
                # before it is injected into that recovery's pending queue.
                # Without this admission point, a recovered turn could finish
                # first and only then observe the user's newer request.
                if (
                    effective_key in self._pending_queues
                    and msg.channel == "websocket"
                    and self._recovery_admission is not None
                    and not await self._recovery_admission.admit(routed_msg)
                ):
                    continue
                # If this session already has an active pending queue (i.e. a task
                # is processing this session), route the message there for mid-turn
                # injection instead of creating a competing task.
                if effective_key in self._pending_queues:
                    # Non-priority commands must not be queued for injection;
                    # dispatch them directly (same pattern as priority commands).
                    if msg.channel != "system" and self.commands.is_dispatchable_command(raw):
                        await self._dispatch_command_inline(
                            msg, effective_key, raw,
                            self.commands.dispatch,
                        )
                        continue
                    pending_msg = routed_msg
                    session = self.sessions.get_or_create(effective_key)
                    followup_id = record_pending_followup(session, pending_msg)
                    if followup_id is not None:
                        pending_msg = dataclasses.replace(
                            pending_msg,
                            metadata={
                                **pending_msg.metadata,
                                PENDING_FOLLOWUP_ID_KEY: followup_id,
                            },
                        )
                        self.sessions.save(session)
                    try:
                        self._pending_queues[effective_key].put_nowait(pending_msg)
                    except asyncio.QueueFull:
                        logger.warning(
                            "Pending queue full for session {}, falling back to queued task",
                            effective_key,
                        )
                        msg = pending_msg
                    else:
                        logger.info(
                            "Routed follow-up message to pending queue for session {}",
                            effective_key,
                        )
                        continue
                # Compute the effective session key before dispatching
                # This ensures /stop command can find tasks correctly when unified session is enabled
                task = asyncio.create_task(self._dispatch(msg))
                active_tasks: set[asyncio.Task[Any]] = self._active_tasks.setdefault(
                    effective_key,
                    set(),
                )
                active_tasks.add(task)
                task.add_done_callback(active_tasks.discard)
        finally:
            await self.aclose()

    def preserve_inflight_turns_on_shutdown(self) -> None:
        """Keep durable checkpoints when the owning gateway exits.

        Normal cancellation intentionally materializes partial output so a
        user-stopped turn leaves a readable conversation.  Gateway lifecycle
        shutdown is different: RecoveryCoordinator needs the checkpoint intact
        to safely offer the unfinished turn for explicit continuation later.
        """
        self._preserve_inflight_turns_on_shutdown = True

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message: per-session serial, cross-session concurrent."""
        session_key = self._effective_session_key(msg)
        if session_key != msg.session_key:
            msg = dataclasses.replace(msg, session_key_override=session_key)
        recovery_task_registered = False
        recovery_admission = self._recovery_admission
        current_task: asyncio.Task[Any] | None = None
        if recovery_admission is not None:
            recovery_id = msg.metadata.get(RECOVERY_INBOUND_METADATA_KEY)
            if isinstance(recovery_id, str) and recovery_id:
                current_task = asyncio.current_task()
                if current_task is not None:
                    recovery_admission.register_recovery_task(session_key, current_task)
                    recovery_task_registered = True
            if not await recovery_admission.admit(msg):
                logger.info("Skipped stale recovery for session {}", session_key)
                if recovery_task_registered and current_task is not None:
                    recovery_admission.unregister_recovery_task(session_key, current_task)
                return
        lock = self._get_session_lock(session_key)
        gate = self._concurrency_gate or nullcontext()

        delivery = self.turn_delivery_factory.unrouted(msg, session_key)
        pending: asyncio.Queue[InboundMessage] | None = None
        try:
            async with lock, gate:
                # Only the task that owns the session lock may publish the
                # active mid-turn injection queue for this session.
                pending = asyncio.Queue(maxsize=20)
                self._pending_queues[session_key] = pending
                try:
                    delivery = self.turn_delivery_factory.create(
                        msg,
                        session_key,
                        enable_stream=True,
                    )
                    response = await self._process_message(
                        msg,
                        on_stream=delivery.on_stream,
                        on_stream_end=delivery.on_stream_end,
                        pending_queue=pending,
                        delivery=delivery,
                    )
                    continuing = turn_continuation.internal_continuation_pending(msg.metadata)
                    await delivery.complete(
                        response,
                        publish_completion=not continuing,
                    )
                    for _, coordinator in self._automation_turn_coordinators:
                        coordinator.complete(msg, response=response)
                except asyncio.CancelledError:
                    for _, coordinator in self._automation_turn_coordinators:
                        coordinator.complete(msg, error=asyncio.CancelledError())
                    logger.info("Task cancelled for session {}", session_key)
                    try:
                        await delivery.abort_stream()
                    except Exception:
                        logger.debug(
                            "Could not close stream for cancelled session {}",
                            session_key,
                            exc_info=True,
                        )
                    # An explicit turn stop materializes partial context so
                    # the next prompt can see completed tool results.  Gateway
                    # shutdown keeps the durable checkpoint untouched instead,
                    # allowing RecoveryCoordinator to offer Continue safely.
                    if (
                        session_key in self._discarding_sessions
                        or self._preserve_inflight_turns_on_shutdown
                    ):
                        raise
                    try:
                        key = self._effective_session_key(msg)
                        session = self.sessions.get_or_create(key)
                        if restore_runtime_checkpoint(session):
                            self._clear_pending_user_turn(session)
                            self.sessions.save(session)
                            logger.info(
                                "Restored partial context for cancelled session {}",
                                key,
                            )
                    except Exception:
                        logger.debug(
                            "Could not restore checkpoint for cancelled session {}",
                            session_key,
                            exc_info=True,
                        )
                    raise
                except Exception as exc:
                    logger.exception("Error processing message for session {}", session_key)
                    await delivery.fail(
                        publish_completion=not turn_continuation.internal_continuation_pending(
                            msg.metadata
                        )
                    )
                    for _, coordinator in self._automation_turn_coordinators:
                        coordinator.complete(msg, error=exc)
                finally:
                    # Drain any messages still in the pending queue and re-publish
                    # them to the bus so they are processed as fresh inbound messages
                    # rather than silently lost.  Only remove our own queue; a
                    # later task waiting on the lock must not be able to steal
                    # cleanup ownership.
                    queue = None
                    if self._pending_queues.get(session_key) is pending:
                        queue = self._pending_queues.pop(session_key, None)
                    else:
                        queue = pending
                    if queue is not None:
                        leftover = 0
                        while True:
                            try:
                                item = queue.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                            await self.bus.publish_inbound(item)
                            leftover += 1
                        if leftover:
                            logger.info(
                                "Re-published {} leftover message(s) to bus for session {}",
                                leftover, session_key,
                            )
                    if not turn_continuation.internal_continuation_pending(msg.metadata):
                        await delivery.idle()
                    await self._publish_next_deferred_automation_turn(session_key)
        finally:
            if (
                recovery_task_registered
                and current_task is not None
                and recovery_admission is not None
            ):
                recovery_admission.unregister_recovery_task(session_key, current_task)
            if pending is None:
                await delivery.idle()
                await self._publish_next_deferred_automation_turn(session_key)

    async def aclose(self) -> None:
        """Stop active work, then close resources owned by the agent loop.

        Resource teardown must still run if cancellation interrupts task draining.
        Gateway shutdown deliberately bounds this coroutine, so keeping the cleanup
        phase in ``finally`` prevents a timed-out background task from leaving
        subprocess transports alive after the event loop closes.
        """
        # The loop closes itself from ``run()`` while application shutdown also
        # performs a guaranteed final close. Serialize those owners so they cannot
        # tear down the same resources concurrently.
        close_lock = getattr(self, "_close_lock", None)
        if close_lock is None:
            close_lock = self._close_lock = asyncio.Lock()
        async with close_lock:
            await self._aclose_unlocked()

    async def _aclose_unlocked(self) -> None:
        errors: list[BaseException] = []
        active_task_groups = getattr(self, "_active_tasks", {})
        active_tasks = tuple({task for tasks in active_task_groups.values() for task in tasks})
        active_task_groups.clear()
        current_task = asyncio.current_task()
        active_tasks = tuple(task for task in active_tasks if task is not current_task)
        for task in active_tasks:
            if not task.done():
                task.cancel()
        try:
            if active_tasks:
                await asyncio.gather(*active_tasks, return_exceptions=True)
            if self._background_tasks:
                await asyncio.gather(*self._background_tasks, return_exceptions=True)
        except BaseException as exc:
            errors.append(exc)
        finally:
            self._background_tasks.clear()

        cleanup_steps = (
            self.subagents.close,
            self._exec_session_manager.close_all,
        )
        for cleanup in cleanup_steps:
            try:
                await cleanup()
            except BaseException as exc:
                errors.append(exc)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("failed to close agent resources", errors)

    def schedule_background(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Schedule a coroutine as a tracked background task (drained on shutdown)."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        pending_queue: asyncio.Queue[InboundMessage] | None = None,
        ephemeral: bool = False,
        run_extra_hooks_for_ephemeral: bool = False,
        hooks: list[AgentHook] | None = None,
        hook_factories: list[AgentTurnHookFactory] | None = None,
        tools: ToolRegistry | None = None,
        runtime: LLMRuntime | None = None,
        delivery: TurnDelivery | None = None,
        on_runtime_admitted: Callable[[LLMRuntime], Awaitable[None]] | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> OutboundMessage | None:
        """Process a single inbound message and return the response."""
        kind = TurnKind.USER if msg.is_user_input else TurnKind.SYSTEM
        if kind is TurnKind.SYSTEM:
            destination = (
                msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id)
            )
            key = session_key or msg.session_key_override or f"{destination[0]}:{destination[1]}"
        else:
            key = session_key or msg.session_key
        if delivery is None:
            delivery = self.turn_delivery_factory.create(msg, key)
        elif delivery.session_key != key:
            raise ValueError("turn delivery session does not match the processing session")
        if on_stream is None:
            on_stream = delivery.on_stream
        if on_stream_end is None:
            on_stream_end = delivery.on_stream_end
        t0 = time.time()
        ctx = TurnContext(
            msg=msg,
            session=None,
            session_key=key,
            turn_id=f"{key}:{time.time_ns()}",
            runtime=runtime,
            kind=kind,
            delivery=delivery,
            original_user_text=(
                None
                if kind is TurnKind.SYSTEM
                or turn_continuation.internal_continuation_inbound(msg.metadata)
                else msg.content
            ),
            turn_wall_started_at=t0,
            visible_run_started_at=turn_continuation.internal_continuation_run_started_at(
                msg.metadata,
            ),
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            on_runtime_admitted=on_runtime_admitted,
            pending_queue=pending_queue,
            ephemeral=ephemeral,
            run_extra_hooks_for_ephemeral=run_extra_hooks_for_ephemeral,
            hooks=list(hooks or []),
            hook_factories=list(hook_factories or []),
            tools=tools,
            attributes=dict(attributes or {}),
        )
        # A streaming callback may be present even when the final text comes from a
        # non-streaming recovery. Only the last completed segment can suppress the
        # regular outbound message.
        if ctx.on_stream is not None:
            stream_callback = ctx.on_stream
            stream_end_callback = ctx.on_stream_end
            stream_end_accepts_merge_next = False
            if stream_end_callback is not None:
                try:
                    stream_end_signature = inspect.signature(stream_end_callback)
                    stream_end_accepts_merge_next = (
                        "merge_next" in stream_end_signature.parameters
                        or any(
                            parameter.kind is inspect.Parameter.VAR_KEYWORD
                            for parameter in stream_end_signature.parameters.values()
                        )
                    )
                except (TypeError, ValueError):
                    pass
            segment_streamed_content = False

            async def _tracked_stream(delta: str) -> None:
                nonlocal segment_streamed_content
                if delta:
                    segment_streamed_content = True
                await stream_callback(delta)

            async def _tracked_stream_end(
                *,
                resuming: bool = False,
                merge_next: bool = False,
            ) -> None:
                nonlocal segment_streamed_content
                ctx.streamed_content = segment_streamed_content
                segment_streamed_content = False
                if stream_end_callback is not None:
                    if merge_next and stream_end_accepts_merge_next:
                        await stream_end_callback(resuming=resuming, merge_next=True)
                    else:
                        await stream_end_callback(resuming=resuming)

            ctx.on_stream = _tracked_stream
            ctx.on_stream_end = _tracked_stream_end

        await self._run_turn_stage(ctx, "restore", self._restore_turn)
        await self._run_turn_stage(ctx, "compact", self._compact_session)
        if await self._run_turn_stage(ctx, "command", self._dispatch_command):
            return ctx.outbound
        await self._run_turn_stage(ctx, "build", self._build_turn)
        await self._run_turn_stage(ctx, "run", self._run_turn)
        await self._run_turn_stage(ctx, "save", self._persist_turn)
        await self._run_turn_stage(ctx, "respond", self._prepare_outbound)
        return ctx.outbound

    async def _run_turn_stage(
        self,
        ctx: TurnContext,
        name: str,
        handler: Callable[[TurnContext], Awaitable[_T]],
    ) -> _T:
        started_at = time.perf_counter()
        try:
            result = await handler(ctx)
        except Exception:
            duration_ms = (time.perf_counter() - started_at) * 1000
            logger.debug(
                "[turn {}] Stage {} failed after {:.1f}ms",
                ctx.turn_id,
                name,
                duration_ms,
            )
            raise
        duration_ms = (time.perf_counter() - started_at) * 1000
        logger.debug(
            "[turn {}] Stage {} completed in {:.1f}ms",
            ctx.turn_id,
            name,
            duration_ms,
        )
        return result

    def _assemble_outbound(
        self,
        msg: InboundMessage,
        final_content: str,
        stop_reason: str,
        streamed_content: bool,
        *,
        log_content: bool = True,
        turn_latency_ms: int | None = None,
    ) -> OutboundMessage | None:
        """Assemble the final outbound message from turn results."""
        if log_content:
            preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
            logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)
        else:
            logger.info("Response to {}:{}: [content hidden]", msg.channel, msg.sender_id)

        event = None
        meta = dict(msg.metadata or {})
        if streamed_content and stop_reason not in {"error", "tool_error"}:
            event = StreamedResponseEvent()
        if turn_latency_ms is not None:
            meta["latency_ms"] = int(turn_latency_ms)

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            event=event,
            metadata=meta,
        )

    def _sanitize_persisted_blocks(
        self,
        content: list[object],
        *,
        should_truncate_text: bool = False,
    ) -> list[object]:
        """Strip volatile multimodal payloads before writing session history."""
        filtered: list[object] = []
        for block in content:
            if not isinstance(block, dict):
                filtered.append(block)
                continue

            block_data = cast(dict[str, Any], block)
            image_url = cast(dict[str, Any], block_data.get("image_url", {}))
            if block_data.get("type") == "image_url" and str(
                image_url.get("url", "")
            ).startswith("data:image/"):
                internal_meta = cast(dict[str, Any], block_data.get("_meta") or {})
                path = cast(str, internal_meta.get("path", ""))
                filtered.append(
                    {"type": "text", "text": image_placeholder_text(path)}
                )
                continue

            if block_data.get("type") == "text" and isinstance(
                block_data.get("text"),
                str,
            ):
                text = cast(str, block_data["text"])
                if should_truncate_text and len(text) > self.max_tool_result_chars:
                    text = truncate_text_fn(text, self.max_tool_result_chars)
                filtered.append({**block_data, "text": text})
                continue

            filtered.append(block_data)

        return filtered

    def _save_turn(
        self,
        session: Session,
        messages: list[dict[str, Any]],
        skip: int,
        *,
        turn_latency_ms: int | None = None,
    ) -> None:
        """Save new-turn messages into session, truncating large tool results."""
        from datetime import datetime

        declared_tool_call_ids = {
            str(tc["id"])
            for m in session.messages
            if m.get("role") == "assistant"
            for tc_value in cast(Iterable[object], m.get("tool_calls") or [])
            if isinstance(tc_value, dict)
            for tc in (cast(dict[str, Any], tc_value),)
            if tc.get("id")
        }
        fulfilled_tool_call_ids = {
            str(m["tool_call_id"])
            for m in session.messages
            if m.get("role") == "tool" and m.get("tool_call_id")
        }
        last_assistant_idx: int | None = None
        saved_followup_ids: set[str] = set()
        for m in messages[skip:]:
            entry = dict(m)
            followup_id_value = cast(object, entry.pop(PENDING_FOLLOWUP_ID_KEY, None))
            followup_ids = (
                [followup_id_value]
                if isinstance(followup_id_value, str)
                else [
                    followup_id
                    for followup_id in cast(list[object], followup_id_value)
                    if isinstance(followup_id, str)
                ]
                if isinstance(followup_id_value, list)
                else []
            )
            internal_meta = cast(object, entry.pop("_meta", None))
            runtime_context_meta = (
                cast(dict[str, Any], internal_meta).get(
                    RUNTIME_CONTEXT_MESSAGE_META
                )
                if isinstance(internal_meta, dict)
                else None
            )
            role, content = entry.get("role"), entry.get("content")
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue  # skip empty assistant messages — they poison session context
            if role == "tool":
                tool_call_id = entry.get("tool_call_id")
                tool_call_id_str = str(tool_call_id) if tool_call_id else ""
                if (
                    not tool_call_id_str
                    or tool_call_id_str not in declared_tool_call_ids
                    or tool_call_id_str in fulfilled_tool_call_ids
                ):
                    # Undeclared tool results corrupt future provider requests.
                    logger.warning(
                        "Dropping invalid tool result {} from session {} during persistence",
                        tool_call_id_str or "(missing id)",
                        session.key,
                    )
                    continue
                fulfilled_tool_call_ids.add(tool_call_id_str)
                if isinstance(content, str) and len(content) > self.max_tool_result_chars:
                    entry["content"] = truncate_text_fn(content, self.max_tool_result_chars)
                elif isinstance(content, list):
                    filtered = self._sanitize_persisted_blocks(
                        cast(list[object], content),
                        should_truncate_text=True,
                    )
                    if not filtered:
                        # Preserve the tool_call/result pair after block filtering.
                        filtered = [
                            {"type": "text", "text": "[tool result omitted during persistence]"}
                        ]
                    entry["content"] = filtered
            elif role == "user":
                if isinstance(content, list):
                    filtered = self._sanitize_persisted_blocks(
                        cast(list[object], content),
                    )
                    if not filtered:
                        continue
                    entry["content"] = filtered
                if isinstance(runtime_context_meta, dict):
                    entry[RUNTIME_CONTEXT_HISTORY_META] = runtime_context_meta
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
            if role == "user":
                saved_followup_ids.update(followup_id for followup_id in followup_ids if followup_id)
            if role == "assistant":
                last_assistant_idx = len(session.messages) - 1
                declared_tool_call_ids.update(
                    str(tc["id"])
                    for tc_value in cast(
                        Iterable[object],
                        entry.get("tool_calls") or [],
                    )
                    if isinstance(tc_value, dict)
                    for tc in (cast(dict[str, Any], tc_value),)
                    if tc.get("id")
                )
        if turn_latency_ms is not None and last_assistant_idx is not None:
            session.messages[last_assistant_idx]["latency_ms"] = int(turn_latency_ms)
        if saved_followup_ids:
            acknowledge_pending_followups(session, saved_followup_ids)
        session.updated_at = datetime.now()

    def _persist_subagent_followup(self, session: Session, msg: InboundMessage) -> bool:
        """Persist subagent follow-ups before prompt assembly so history stays durable.

        Returns True if a new entry was appended; False if the follow-up was
        deduped (same ``subagent_task_id`` already in session) or carries no
        content worth persisting.
        """
        if not msg.content:
            return False
        metadata_value = cast(object, msg.metadata)
        task_id = (
            msg.metadata.get("subagent_task_id")
            if isinstance(metadata_value, dict)
            else None
        )
        if task_id and any(
            m.get("injected_event") == "subagent_result" and m.get("subagent_task_id") == task_id
            for m in session.messages
        ):
            return False
        session.add_message(
            "assistant",
            msg.content,
            sender_id=msg.sender_id,
            injected_event="subagent_result",
            subagent_task_id=task_id,
        )
        return True

    def _set_runtime_checkpoint(self, session: Session, payload: dict[str, Any]) -> None:
        """Persist the latest in-flight turn state into session metadata."""
        session.metadata[self._RUNTIME_CHECKPOINT_KEY] = payload
        self.sessions.save_runtime_checkpoint(session)

    def _mark_pending_user_turn(self, session: Session) -> None:
        session.metadata[self._PENDING_USER_TURN_KEY] = True

    def _clear_pending_user_turn(self, session: Session) -> None:
        session.metadata.pop(self._PENDING_USER_TURN_KEY, None)

    def _clear_runtime_checkpoint(self, session: Session) -> None:
        if self._RUNTIME_CHECKPOINT_KEY in session.metadata:
            session.metadata.pop(self._RUNTIME_CHECKPOINT_KEY, None)

    async def replay_all(self, controller: Any) -> list[tuple[str, bool, list[str]]]:
        """Re-run every recorded turn offline and return (turn_id, ok, diffs).

        ADR-004: the LLM rail is served by ``ReplayProvider`` from the recorded
        provider responses (no network, no vcr dependency for correctness), and
        the tool rail short-circuits every call by stable key (no side
        effects). Exit semantics: ok=True ⇔ structural diff is empty.
        """
        from pawbot.agent.blackbox import ReplayBreakpoint, ReplayProvider, compare_messages

        results: list[tuple[str, bool, list[str]]] = []
        benchmark_rows: list[dict[str, Any]] = []
        runtime = self.llm_runtime()
        for turn in controller.turns:
            replay_started_at = time.perf_counter()
            probe = controller.probe_hook(turn)
            replay_provider = ReplayProvider(runtime.provider, controller.store, turn.turn_id)
            replay_runtime = LLMRuntime.capture(
                cast(LLMProvider, replay_provider),
                turn.model or runtime.model,
                context_window_tokens=runtime.context_window_tokens,
            )
            try:
                with controller.turn_scope(turn):
                    result = await self.runner.run(AgentRunSpec(
                        initial_messages=turn.initial_messages,
                        tools=self.tools,
                        runtime=replay_runtime,
                        max_iterations=self.max_iterations,
                        max_tool_result_chars=self.max_tool_result_chars,
                        hook=probe,
                        concurrent_tools=True,
                        workspace=self.workspace,
                        session_key=turn.session_key,
                        context_block_limit=self.context_block_limit,
                        provider_retry_mode=self.provider_retry_mode,
                        finalize_on_max_iterations=True,
                    ))
            except ReplayBreakpoint as bp:
                # Capture the dumped messages so WebUI callers can render them
                # instead of surfacing a bare exception.
                controller.last_breakpoint = {
                    "iteration": bp.iteration,
                    "turn_id": turn.turn_id,
                    "messages": list(probe.messages),
                }
                raise
            diffs = compare_messages(result.messages, turn.final_messages)
            results.append((turn.turn_id, not diffs, diffs))
            benchmark_rows.append({
                "turn_id": turn.turn_id,
                "elapsed_ms": max(0, int((time.perf_counter() - replay_started_at) * 1000)),
                "messages": len(result.messages),
                "diffs": len(diffs),
                "tool_calls": len(result.tool_states),
            })
        if hasattr(controller, "last_benchmark"):
            controller.last_benchmark = benchmark_rows
        return results

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        sender_id: str = "user",
        media: list[str] | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        ephemeral: bool = False,
        _run_extra_hooks_for_ephemeral: bool = False,
        hooks: list[AgentHook] | None = None,
        hook_factories: list[AgentTurnHookFactory] | None = None,
        tools: ToolRegistry | None = None,
        persist_user_message: bool = True,
        runtime: LLMRuntime | None = None,
        on_runtime_admitted: Callable[[LLMRuntime], Awaitable[None]] | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> OutboundMessage | None:
        """Process an external message directly and return the outbound payload."""
        if channel == "system":
            raise ValueError("channel 'system' is reserved for internal messages")
        metadata: dict[str, Any] = {}
        if not persist_user_message:
            metadata[turn_continuation.SKIP_USER_PERSIST_META] = True
        msg = InboundMessage(
            channel=channel, sender_id=sender_id, chat_id=chat_id,
            content=content, media=media or [], metadata=metadata,
        )
        # Share the dispatch lock so direct calls serialize with bus turns.
        lock = self._get_session_lock(session_key)
        try:
            async with lock:
                kwargs: dict[str, Any] = {
                    "session_key": session_key,
                    "on_progress": on_progress,
                    "on_stream": on_stream,
                    "on_stream_end": on_stream_end,
                    "ephemeral": ephemeral,
                }
                if _run_extra_hooks_for_ephemeral:
                    kwargs["run_extra_hooks_for_ephemeral"] = True
                if hooks is not None:
                    kwargs["hooks"] = hooks
                if hook_factories is not None:
                    kwargs["hook_factories"] = hook_factories
                if tools is not None:
                    kwargs["tools"] = tools
                if runtime is not None:
                    kwargs["runtime"] = runtime
                if on_runtime_admitted is not None:
                    kwargs["on_runtime_admitted"] = on_runtime_admitted
                if attributes is not None:
                    kwargs["attributes"] = dict(attributes)
                return await self._process_message(
                    msg,
                    **kwargs,
                )
        finally:
            await self.runtime_event_publisher.run_status_changed(msg, session_key, "idle")
            self.runtime_event_publisher.clear_turn(session_key)

    def _get_session_lock(self, session_key: str) -> asyncio.Lock:
        """Return the shared lock while allowing idle session entries to expire."""
        lock = self._session_locks.get(session_key)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[session_key] = lock
        return lock
