"""Record & Replay blackbox: deterministic offline debugging.

- ``BlackboxController`` (record): hooks into ``AgentLoop._run_agent_loop``
  during a normal run and captures the LLM rail (vcr cassette) + the tool rail
  (JSONL) + per-turn envelopes.
- ``ReplayController`` (replay): re-runs each recorded turn from its recorded
  ``initial_messages`` with playback-only cassettes and keyed tool
  short-circuit, then structurally diffs the message sequence.
"""

from pawbot.agent.blackbox.recorder import BlackboxController
from pawbot.agent.blackbox.replayer import (
    ReplayBreakpoint,
    ReplayController,
    ReplayProvider,
    ReplayStore,
    compare_messages,
    lookup_replay_result,
)

__all__ = [
    "BlackboxController",
    "ReplayBreakpoint",
    "ReplayController",
    "ReplayProvider",
    "ReplayStore",
    "compare_messages",
    "lookup_replay_result",
]
