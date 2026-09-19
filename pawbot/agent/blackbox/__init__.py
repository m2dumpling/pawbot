"""Record & Replay blackbox: deterministic offline debugging.

- ``BlackboxController`` (record): hooks into ``AgentLoop._run_agent_loop``
  during a normal run and captures the LLM rail (vcr cassette) + the tool rail
  (JSONL) + per-turn envelopes.
- ``ReplayController`` (replay): re-runs each recorded turn from its recorded
  ``initial_messages`` with playback-only cassettes and keyed tool
  short-circuit, then structurally diffs the message sequence.
"""

from pawbot.agent.blackbox.manifest import (
    finalize_recording_manifest,
    recording_health,
    validate_recording_manifest,
)
from pawbot.agent.blackbox.recorder import (
    BlackboxController,
    clear_recording_policy,
    read_recording_policy,
    recording_policy_path,
    write_recording_policy,
)
from pawbot.agent.blackbox.replayer import (
    ReplayBreakpoint,
    ReplayController,
    ReplayProvider,
    ReplayStore,
    compare_messages,
    compare_trace_events,
    lookup_replay_result,
    replay_is_active,
)

__all__ = [
    "BlackboxController",
    "clear_recording_policy",
    "read_recording_policy",
    "recording_policy_path",
    "write_recording_policy",
    "finalize_recording_manifest",
    "recording_health",
    "validate_recording_manifest",
    "ReplayBreakpoint",
    "ReplayController",
    "ReplayProvider",
    "ReplayStore",
    "compare_trace_events",
    "compare_messages",
    "lookup_replay_result",
    "replay_is_active",
]
