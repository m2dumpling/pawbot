"""Turn-level resource budgets.

An agent turn is an execution unit, not an unbounded ``while`` loop.  This
module keeps the accounting independent from the runner so SDK callers,
replay, and future transports can inspect the same limits and counters.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

from pawbot.providers.base import LLMUsage

TurnBudgetReason = Literal[
    "max_tool_calls",
    "max_wall_time",
    "max_input_tokens",
    "max_output_tokens",
    "max_cost_usd",
]


@dataclass(slots=True)
class TurnBudget:
    """Mutable accounting for one agent turn.

    ``max_iterations`` remains owned by :class:`AgentRunSpec` for backwards
    compatibility.  The additional limits are soft admission limits: the
    runner checks them before starting the next model/tool operation and
    records the reason when it stops.  A provider response that already
    reached a normal terminal state is still returned to the user.
    """

    max_iterations: int
    max_tool_calls: int | None = None
    max_wall_seconds: float | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_cost_usd: float | None = None
    input_cost_per_million_usd: float | None = None
    output_cost_per_million_usd: float | None = None

    iterations: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    started_at: float = field(default_factory=time.monotonic)

    def start(self) -> None:
        """Reset counters before a run, allowing a spec to be reused safely."""
        self.iterations = 0
        self.tool_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_usd = (
            0.0
            if self.input_cost_per_million_usd is not None
            or self.output_cost_per_million_usd is not None
            else None
        )
        self.started_at = time.monotonic()

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.started_at)

    def note_iteration(self, iteration: int) -> None:
        self.iterations = max(self.iterations, iteration + 1)

    def try_consume_tool_calls(self, count: int) -> TurnBudgetReason | None:
        """Reserve a provider response's tool calls before executing them."""
        requested = max(0, count)
        if self.max_tool_calls is not None and self.tool_calls + requested > self.max_tool_calls:
            # Count attempted calls as well.  The diagnostic should explain
            # why a provider response was blocked, rather than showing zero.
            self.tool_calls += requested
            return "max_tool_calls"
        self.tool_calls += requested
        return None

    def observe_usage(self, usage: LLMUsage | None) -> None:
        """Accumulate one model response's canonical usage."""
        if usage is None:
            return
        self.input_tokens += max(0, usage.input_tokens)
        self.output_tokens += max(0, usage.output_tokens)
        if self.cost_usd is not None:
            input_rate = self.input_cost_per_million_usd or 0.0
            output_rate = self.output_cost_per_million_usd or 0.0
            self.cost_usd += usage.input_tokens * input_rate / 1_000_000
            self.cost_usd += usage.output_tokens * output_rate / 1_000_000

    def limit_reason(self) -> TurnBudgetReason | None:
        """Return the first non-iteration limit that has been reached."""
        if self.max_wall_seconds is not None and self.elapsed_seconds >= self.max_wall_seconds:
            return "max_wall_time"
        if self.max_input_tokens is not None and self.input_tokens >= self.max_input_tokens:
            return "max_input_tokens"
        if self.max_output_tokens is not None and self.output_tokens >= self.max_output_tokens:
            return "max_output_tokens"
        if (
            self.max_cost_usd is not None
            and self.cost_usd is not None
            and self.cost_usd >= self.max_cost_usd
        ):
            return "max_cost_usd"
        return None

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-safe diagnostic snapshot for hooks and Record & Replay."""
        return {
            "limits": {
                "max_iterations": self.max_iterations,
                "max_tool_calls": self.max_tool_calls,
                "max_wall_seconds": self.max_wall_seconds,
                "max_input_tokens": self.max_input_tokens,
                "max_output_tokens": self.max_output_tokens,
                "max_cost_usd": self.max_cost_usd,
            },
            "usage": {
                "iterations": self.iterations,
                "tool_calls": self.tool_calls,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cost_usd": self.cost_usd,
                "elapsed_seconds": round(self.elapsed_seconds, 3),
            },
        }


def budget_limit_message(reason: TurnBudgetReason, budget: TurnBudget) -> str:
    """Build a stable model/user-facing message for a stopped turn."""
    labels = {
        "max_tool_calls": f"tool-call budget ({budget.max_tool_calls!s})",
        "max_wall_time": f"wall-clock budget ({budget.max_wall_seconds!s}s)",
        "max_input_tokens": f"input-token budget ({budget.max_input_tokens!s})",
        "max_output_tokens": f"output-token budget ({budget.max_output_tokens!s})",
        "max_cost_usd": f"cost budget (${budget.max_cost_usd!s})",
    }
    return (
        f"This turn stopped before the next operation because its {labels[reason]} "
        "was reached. The partial work is saved; continue the task to resume."
    )
