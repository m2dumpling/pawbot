"""Turn lifecycle context (ADR-005: extracted from the agent loop).

Holds the per-turn state passed through the seven-stage pipeline and the
turn kind discriminant. Moved out of ``agent/loop.py`` so the stage pipeline
and the orchestrator no longer share one monolithic module.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Awaitable, Callable

from pawbot.agent.hook import AgentHook, AgentTurnHookFactory
from pawbot.agent.tools.context import RequestContext
from pawbot.agent.tools.registry import ToolRegistry
from pawbot.agent.turn_delivery import TurnDelivery
from pawbot.bus.events import InboundMessage, OutboundMessage
from pawbot.providers.base import LLMUsage, ProviderConversationState
from pawbot.runtime_context import RuntimeContextBlock
from pawbot.session.manager import Session
from pawbot.session.summary import SessionSummary
from pawbot.utils.llm_runtime import LLMRuntime


class TurnKind(Enum):
    USER = auto()
    SYSTEM = auto()


@dataclass
class TurnContext:
    msg: InboundMessage
    session_key: str
    turn_id: str
    runtime: LLMRuntime | None
    kind: TurnKind
    delivery: TurnDelivery
    original_user_text: str | None = None
    session: Session | None = None

    history: list[dict[str, Any]] = field(default_factory=list)
    initial_messages: list[dict[str, Any]] = field(default_factory=list)
    provider_state: ProviderConversationState | None = field(default=None, repr=False)
    request_context: RequestContext | None = None
    runtime_context_blocks: list[RuntimeContextBlock] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)

    final_content: str | None = None
    all_messages: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""
    streamed_content: bool = False

    input_persisted_early: bool = False
    save_skip: int = 0

    outbound: OutboundMessage | None = None
    suppress_response: bool = False

    on_progress: Callable[..., Awaitable[None]] | None = None
    on_stream: Callable[[str], Awaitable[None]] | None = None
    on_stream_end: Callable[..., Awaitable[None]] | None = None
    on_runtime_admitted: Callable[[LLMRuntime], Awaitable[None]] | None = None
    on_retry_wait: Callable[[str], Awaitable[None]] | None = None

    pending_queue: asyncio.Queue[InboundMessage] | None = None
    pending_summary: SessionSummary | None = None

    ephemeral: bool = False
    run_extra_hooks_for_ephemeral: bool = False
    hooks: list[AgentHook] = field(default_factory=list)
    hook_factories: list[AgentTurnHookFactory] = field(default_factory=list)
    turn_scopes: list[AbstractContextManager[Any]] = field(default_factory=list)
    tools: ToolRegistry | None = None

    turn_wall_started_at: float = field(default_factory=time.time)
    visible_run_started_at: float | None = None
    turn_latency_ms: int | None = None
    usage: LLMUsage | None = None

    def require_runtime(self) -> LLMRuntime:
        """Return the runtime established by the BUILD stage."""
        if self.runtime is None:
            raise RuntimeError("turn runtime is not initialized; BUILD must run before this stage")
        return self.runtime

    def require_session(self) -> Session:
        """Return the session established by the RESTORE stage."""
        if self.session is None:
            raise RuntimeError("turn session is not initialized; RESTORE must run before this stage")
        return self.session

