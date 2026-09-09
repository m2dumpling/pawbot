# Record & Replay

Record & Replay is pawbot's offline regression harness for agent behavior.
It freezes the provider-response rail and the tool-observation rail, then runs
the current orchestration code against those recorded inputs.

In WebUI, click **Start recording**, execute one or more tasks, and click
**Stop recording** when the sample is complete. The recording window belongs to
the AgentLoop rather than one chat session: switching sessions while recording
continues appending turns to the same directory.

## What it guarantees

During replay:

- no new provider request is required;
- no new model token is spent;
- recorded tool observations are returned instead of executing real tools;
- repeated identical tool calls consume observations in order;
- classified tool errors remain errors;
- the resulting user-visible message sequence can be structurally compared with
  the recording; provider-private reasoning traces are not treated as a
  regression by themselves.

It does not make a live model deterministic and does not replace live provider,
network, or integration tests.

The WebUI marks a directory as **ready** only when `turns.jsonl` contains valid
turn envelopes. A partially created or malformed directory remains visible so
that it can be diagnosed or deleted; it is not presented as a replayable sample.
Deleting a recording removes its local prompt, response, tool-result, and
optional HTTP cassette files.

After replay, expand any turn in the report to inspect the raw execution rail:

- the original `initial_messages` context and tool schemas;
- every recorded LLM response, including tool calls and finish reason;
- every tool name, JSON argument, status/detail, and returned value;
- every tool lifecycle state (`planned`, `running`, `succeeded`, `failed`,
  `blocked`, or `unknown`) and whether a side effect may have occurred;
- the final message envelope, usage, session, model, and optional HTTP cassette.

The green summary answers only whether the current orchestration reproduced the
recorded observable behavior. The expanded detail is the debugging surface for
answering *why* a turn changed.

## Commands

```bash
pawbot agent \
  --message "inspect the repository" \
  --record .pawbot/blackbox/run-1

pawbot agent --replay .pawbot/blackbox/run-1

# concise equivalent
pawbot replay .pawbot/blackbox/run-1

# include provider-free local timing metrics
pawbot replay .pawbot/blackbox/run-1 --benchmark

pawbot agent \
  --replay .pawbot/blackbox/run-1 \
  --break-at 2
```

## Artifact layout

```text
.pawbot/blackbox/run-1/
├── meta.json
├── turns.jsonl
├── tools.jsonl
└── <turn-id>.yaml       # optional HTTP audit cassette
```

The JSON rails are authoritative. The optional HTTP cassette is an audit layer
and is not required for replay. A turn envelope also carries a budget snapshot
when resource limits are configured, including iterations, tool calls, elapsed
time, input/output tokens, and estimated cost when pricing is known.

## Turn budgets and capability policy

The limits are configured under `agents.defaults` and are optional. Existing
configurations keep their previous behavior when these fields are omitted:

```json
{
  "agents": {
    "defaults": {
      "maxToolCalls": 40,
      "maxTurnSeconds": 900,
      "maxInputTokens": 400000,
      "maxOutputTokens": 80000,
      "maxTurnCostUsd": 1.0,
      "inputCostPerMillionUsd": 0.2,
      "outputCostPerMillionUsd": 0.8
    }
  },
  "tools": {
    "deniedCapabilities": ["execute"]
  }
}
```

The runner stops before starting the next operation once a configured limit is
reached. `deniedCapabilities` is a coarse defense-in-depth policy; workspace
scope, SSRF checks, sandboxing, and per-tool validation still apply.

## Privacy boundary

Replay artifacts may contain prompts, tool results, local paths, configuration
fragments, and user data. Keep them under `.pawbot/`, sanitize them before
sharing, and use the public fixture under `tests/fixtures/blackbox/` as the
starting point for repository tests.

## Implementation and evidence

- `pawbot/agent/blackbox/recorder.py` records the rails and turn envelope;
- `pawbot/agent/blackbox/replayer.py` provides the replay provider, tool rail,
  breakpoint probe, and structural comparison;
- `pawbot/agent/tools/execution.py` short-circuits recorded tool observations;
- `tests/test_blackbox.py` verifies determinism and side-effect isolation;
- `tests/fixtures/blackbox/basic-turn/` is a sanitized, repository-safe fixture.
