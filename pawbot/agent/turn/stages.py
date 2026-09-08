"""Seven-stage turn pipeline (ADR-005: extracted from the agent loop).

``AgentLoop`` mixes this in so each stage lives in a dedicated, testable
method while retaining ``self`` access to the orchestrator's collaborators.
"""

# The mixin intentionally calls protected AgentLoop helpers through its
# structural host contract; those accesses are part of the composition design.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import dataclasses
import time
from typing import Any, Protocol, cast

from loguru import logger

from pawbot.agent.tools.message import capture_message_deliveries
from pawbot.agent.tools.registry import ToolRegistry
from pawbot.agent.turn.context import TurnContext, TurnKind
from pawbot.command import CommandContext
from pawbot.session import turn_continuation
from pawbot.session.automation_turns import automation_history_overrides
from pawbot.session.recovery import (
    RECOVERY_INBOUND_METADATA_KEY,
    restore_pending_interruption,
    restore_runtime_checkpoint,
)
from pawbot.utils.document import reference_non_image_attachments
from pawbot.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE

_SUBAGENT_PROVIDER_TASK_META = "subagent_provider_task_id"


class _TurnStagesHost(Protocol):
    """Structural view of the AgentLoop collaborators used by this mixin.

    The stage methods intentionally live in a mixin to keep ``AgentLoop``
    readable.  Giving the mixin an explicit host contract keeps strict
    type-checking useful without importing AgentLoop back into this module.
    Collaborators whose concrete types are owned by AgentLoop remain ``Any``
    here; their public contracts are checked at their own definitions.
    """

    sessions: Any
    tools: ToolRegistry
    auto_compact: Any
    commands: Any
    context: Any
    workspace_scopes: Any
    consolidator: Any
    runtime_event_publisher: Any
    runtime_for_session: Any
    schedule_background: Any
    _remember_unified_session_route: Any
    _persist_subagent_followup: Any
    _persist_user_message_early: Any
    _request_context_for_turn: Any
    _resolve_runtime_context_for_turn: Any
    _replay_token_budget: Any
    _build_initial_messages: Any
    _run_agent_loop: Any
    _save_turn: Any
    _clear_pending_user_turn: Any
    _clear_runtime_checkpoint: Any
    _assemble_outbound: Any


class TurnStagesMixin:
    """The restore → compact → command → build → run → save → respond pipeline."""

    async def _restore_turn(self: _TurnStagesHost, ctx: TurnContext) -> None:
        """Restore checkpoint / pending user turn; reference non-image attachments."""
        msg = ctx.msg

        if ctx.kind is TurnKind.USER and msg.media:
            new_content, image_paths = reference_non_image_attachments(
                msg.content,
                msg.media,
            )
            ctx.msg = dataclasses.replace(msg, content=new_content, media=image_paths)
            msg = ctx.msg

        if ctx.session is None:
            if msg.require_existing_session:
                ctx.session = self.sessions.get_cached(ctx.session_key)
                if ctx.session is None:
                    raise RuntimeError("required session is not active")
            else:
                ctx.session = self.sessions.get_or_create(ctx.session_key)
        session = ctx.require_session()
        ctx.ephemeral = ctx.ephemeral or not session.policy.persist
        tools = ctx.tools or self.tools
        if session.policy.disabled_tools:
            restricted = ToolRegistry()
            for name in tools.tool_names:
                tool = tools.get(name)
                if name not in session.policy.disabled_tools and tool:
                    restricted.register(tool)
            tools = restricted
        ctx.tools = tools

        if ctx.kind is TurnKind.SYSTEM:
            logger.info("Processing system message from {}", msg.sender_id)
        elif session.policy.log_content:
            preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
            logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)
        else:
            logger.info("Processing message from {}:{}: [content hidden]", msg.channel, msg.sender_id)

        self._remember_unified_session_route(
            session,
            msg,
            is_user_turn=ctx.original_user_text is not None,
        )
        await ctx.delivery.started()
        if ctx.kind is TurnKind.USER:
            self.workspace_scopes.persist_message_scope(session, msg)

        if restore_runtime_checkpoint(session):
            self.sessions.save(session)
        if (
            RECOVERY_INBOUND_METADATA_KEY not in msg.metadata
            and restore_pending_interruption(session)
        ):
            self.sessions.save(session)

    async def _compact_session(self: _TurnStagesHost, ctx: TurnContext) -> None:
        session = ctx.require_session()
        ctx.session, pending = self.auto_compact.prepare_session(
            session,
            ctx.session_key,
        )
        ctx.pending_summary = pending

    async def _dispatch_command(self: _TurnStagesHost, ctx: TurnContext) -> bool:
        if ctx.kind is TurnKind.SYSTEM or ctx.msg.channel == "system":
            return False
        session = ctx.require_session()
        raw = ctx.msg.content.strip()
        _, automation_metadata = automation_history_overrides(ctx.msg.metadata)
        is_user_turn = (
            ctx.original_user_text is not None
            and not automation_metadata
            and ctx.msg.channel != "system"
            and ctx.msg.sender_id != "subagent"
        )
        cmd_ctx = CommandContext(
            msg=ctx.msg,
            session=session,
            key=ctx.session_key,
            raw=raw,
            loop=cast(Any, self),
            runtime=ctx.runtime,
            is_user_turn=is_user_turn,
            turn_scopes=ctx.turn_scopes,
        )
        result = await self.commands.dispatch(cmd_ctx)
        if result is not None:
            ctx.outbound = result
            # Shortcut commands skip BUILD and SAVE, so we must persist the
            # turn here so WebUI history hydration after _turn_end sees the
            # message.  Mark messages with _command so get_history can filter
            # them out of LLM context.  /new is excluded because it
            # intentionally clears the session.
            if cmd_ctx.raw.lower() != "/new":
                ctx.input_persisted_early = self._persist_user_message_early(
                    ctx.msg, session, _command=True
                )
                session.add_message(
                    "assistant", result.content, _command=True
                )
                self._clear_pending_user_turn(session)
                self.sessions.save(session)
                if not ctx.ephemeral:
                    await self.runtime_event_publisher.session_turn_persisted(
                        ctx.msg,
                        ctx.session_key,
                        turn_id=ctx.turn_id,
                        attributes=ctx.attributes,
                    )
            return True
        return False

    async def _build_turn(self: _TurnStagesHost, ctx: TurnContext) -> None:
        session = ctx.require_session()
        runtime = ctx.runtime
        if runtime is None:
            runtime = self.runtime_for_session(session)
            ctx.runtime = runtime
        if ctx.session_key.startswith("dream:"):
            logger.info(
                "Dream run using model={} (preset={})",
                runtime.model,
                runtime.model_preset or "default",
            )
        if ctx.on_runtime_admitted is not None:
            await ctx.on_runtime_admitted(runtime)
        if not ctx.ephemeral:
            await self.consolidator.maybe_consolidate_by_tokens(
                session,
                runtime=runtime,
            )
        is_subagent = ctx.kind is TurnKind.SYSTEM and ctx.msg.sender_id == "subagent"

        # ADR-002: reserve the system prompt's tokens in the replay budget.
        reserved_system_tokens = 0
        if runtime.context_window_tokens > 0:
            from pawbot.agent.token_estimation import system_prompt_tokens

            scope = self.workspace_scopes.for_message(ctx.msg, session.metadata)
            system_prompt = self.context.build_system_prompt(
                channel=ctx.delivery.route.channel,
                session_summary=ctx.pending_summary,
                workspace=scope.project_path,
                include_memory=session.policy.persist,
                include_memory_recent_history=not ctx.ephemeral,
                session_key=session.key,
            )
            reserved_system_tokens = system_prompt_tokens(system_prompt, runtime.model)

        _hist_kwargs: dict[str, Any] = {
            "max_tokens": self._replay_token_budget(
                runtime, reserved_system_tokens=reserved_system_tokens
            ),
            "extend_to_user": is_subagent,
        }
        ctx.history = session.get_history(**_hist_kwargs)
        stored_state = session.provider_state
        subagent_followup_persisted = False
        if is_subagent:
            # Keep the durable internal delivery as an assistant record, but
            # present this completion to the model as fresh follow-up input.
            # Providers without assistant-prefill support drop trailing
            # assistant messages, so using the persisted record as the current
            # prompt would hide an independently dispatched subagent result.
            subagent_followup_persisted = self._persist_subagent_followup(
                session,
                ctx.msg,
            )
            if subagent_followup_persisted:
                logger.debug("Subagent result persisted for session {}", ctx.session_key)
                # Establish a durable, replay-safe baseline before any fallible
                # provider compatibility or prompt assembly work. A compatible
                # staged state replaces this in a second atomic save below.
                session.provider_state = None
                self.sessions.save(session)
            ctx.input_persisted_early = True
        await ctx.delivery.runtime_admitted(runtime)

        ctx.request_context = self._request_context_for_turn(ctx)
        if ctx.kind is TurnKind.USER:
            ctx.runtime_context_blocks = await self._resolve_runtime_context_for_turn(ctx)
        staged_provider_state = False
        if stored_state is not None and runtime.provider.can_resume_conversation_state(
            stored_state,
            runtime.model,
        ):
            current_provider_message = self.context.build_current_message(
                ctx.msg.content,
                media=ctx.msg.media if ctx.kind is TurnKind.USER and ctx.msg.media else None,
                runtime_context_blocks=ctx.runtime_context_blocks,
            )
            task_id = ctx.msg.metadata.get("subagent_task_id") if is_subagent else None
            already_staged = False
            if isinstance(task_id, str) and task_id:
                internal_meta = current_provider_message.get("_meta")
                current_provider_message["_meta"] = {
                    **(
                        cast(dict[str, Any], internal_meta)
                        if isinstance(internal_meta, dict)
                        else {}
                    ),
                    _SUBAGENT_PROVIDER_TASK_META: task_id,
                }
                already_staged = any(
                    isinstance(message.get("_meta"), dict)
                    and cast(dict[str, Any], message["_meta"]).get(
                        _SUBAGENT_PROVIDER_TASK_META
                    )
                    == task_id
                    for message in stored_state.pending_messages
                )
            ctx.provider_state = (
                stored_state
                if already_staged
                else stored_state.with_pending_messages([
                    *stored_state.pending_messages,
                    current_provider_message,
                ])
            )
            if (
                not ctx.ephemeral
                and (ctx.kind is TurnKind.USER or subagent_followup_persisted)
            ):
                session.provider_state = ctx.provider_state
                staged_provider_state = True
        elif stored_state is not None:
            session.provider_state = None
        if ctx.kind is TurnKind.USER:
            ctx.input_persisted_early = self._persist_user_message_early(
                ctx.msg,
                session,
                runtime_context_blocks=ctx.runtime_context_blocks,
            )
            if staged_provider_state and not ctx.input_persisted_early:
                session.provider_state = stored_state
        elif subagent_followup_persisted and staged_provider_state:
            # Upgrade the replay-safe baseline to the resumable state before
            # prompt assembly and the first model checkpoint.
            self.sessions.save(session)
        ctx.initial_messages = self._build_initial_messages(ctx)

        if ctx.on_progress is None:
            ctx.on_progress = ctx.delivery.progress_callback()
        if ctx.on_retry_wait is None:
            ctx.on_retry_wait = ctx.delivery.retry_wait_callback()

    async def _run_turn(self: _TurnStagesHost, ctx: TurnContext) -> None:
        runtime = ctx.require_runtime()
        if ctx.visible_run_started_at is None:
            ctx.visible_run_started_at = time.time()
        await ctx.delivery.running(started_at=ctx.visible_run_started_at)
        with capture_message_deliveries() as message_sends:
            result = await self._run_agent_loop(
                ctx.initial_messages,
                runtime=runtime,
                on_progress=ctx.on_progress,
                on_stream=ctx.on_stream,
                on_stream_end=ctx.on_stream_end,
                on_retry_wait=ctx.on_retry_wait,
                session=ctx.session,
                pending_queue=ctx.pending_queue,
                ephemeral=ctx.ephemeral,
                run_extra_hooks_for_ephemeral=ctx.run_extra_hooks_for_ephemeral,
                hooks=ctx.hooks,
                hook_factories=ctx.hook_factories,
                turn_scopes=ctx.turn_scopes,
                tools=ctx.tools,
                request_context=ctx.request_context,
                provider_state=ctx.provider_state,
            )
        ctx.final_content = result.final_content
        ctx.all_messages = result.messages
        ctx.stop_reason = result.stop_reason
        if (
            ctx.kind is TurnKind.USER
            and (ctx.delivery.route.channel, ctx.delivery.route.chat_id) in message_sends
            and (not result.had_injections or result.stop_reason == "empty_final_response")
        ):
            ctx.suppress_response = True
        ctx.usage = result.usage
        ctx.delivery.record_usage(ctx.usage)
        if ctx.kind is TurnKind.USER:
            await turn_continuation.maybe_continue_turn(ctx)

    async def _persist_turn(self: _TurnStagesHost, ctx: TurnContext) -> None:
        runtime = ctx.require_runtime()
        session = ctx.require_session()
        turn_continuation.prepare_save_boundary(ctx)

        if (
            ctx.kind is TurnKind.USER
            and (ctx.final_content is None or not ctx.final_content.strip())
            and not ctx.suppress_response
        ):
            ctx.final_content = EMPTY_FINAL_RESPONSE_MESSAGE

        latency_started_at = (
            ctx.visible_run_started_at
            if (
                ctx.kind is TurnKind.SYSTEM
                or turn_continuation.internal_continuation_inbound(ctx.msg.metadata)
            )
            and ctx.visible_run_started_at is not None
            else ctx.turn_wall_started_at
        )
        ctx.turn_latency_ms = max(0, int((time.time() - latency_started_at) * 1000))
        if ctx.usage is not None and not ctx.ephemeral:
            session.metadata["_last_usage"] = ctx.usage.to_dict()
        self._save_turn(
            session, ctx.all_messages, ctx.save_skip,
            turn_latency_ms=ctx.turn_latency_ms,
        )
        ctx.delivery.record_latency(ctx.turn_latency_ms)
        if not ctx.ephemeral:
            self.schedule_background(
                self.consolidator.maybe_consolidate_by_tokens(
                    session,
                    runtime=runtime,
                )
            )
        self._clear_pending_user_turn(session)
        self._clear_runtime_checkpoint(session)
        self.sessions.save(session)
        if not ctx.ephemeral:
            await self.runtime_event_publisher.session_turn_persisted(
                ctx.msg,
                ctx.session_key,
                turn_id=ctx.turn_id,
                attributes=ctx.attributes,
            )

    async def _prepare_outbound(self: _TurnStagesHost, ctx: TurnContext) -> None:
        if ctx.suppress_response:
            ctx.outbound = None
            return
        if ctx.kind is TurnKind.SYSTEM:
            ctx.outbound = ctx.delivery.background_response(
                ctx.final_content,
                stop_reason=ctx.stop_reason,
                streamed=ctx.streamed_content,
                latency_ms=ctx.turn_latency_ms,
            )
            return
        ctx.outbound = self._assemble_outbound(
            ctx.delivery.delivery_message,
            cast(str, ctx.final_content),
            ctx.stop_reason,
            ctx.streamed_content,
            log_content=ctx.require_session().policy.log_content,
            turn_latency_ms=ctx.turn_latency_ms,
        )
        if ctx.ephemeral and ctx.outbound is not None:
            ctx.outbound.metadata["_stop_reason"] = ctx.stop_reason


