# Record & Replay

Record & Replay is pawbot's detailed regression layer for Agent behavior.
Lightweight live execution records are created automatically for every turn;
this layer is the deliberate, full-fidelity sample used for offline validation.
It freezes the provider-response rail and the tool-observation rail, then runs
the current orchestration code against those recorded inputs.

In WebUI, open **Settings → Execution & regression**, click
**Save as regression sample**, execute one or more tasks, and click
**Stop saving** when the sample is complete. The capture window belongs to the
AgentLoop rather than one chat session: switching sessions while saving
continues appending turns to the same directory.

## What it guarantees

During offline validation:

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

## Task completion checks

Record & Replay can carry an optional TaskContract alongside a captured turn.
During replay, the current AgentRunner applies the same declarative checks to
the replayed result. A contract can assert final text, successful tools,
tool-result content, workspace files, file content, or a caller-owned
synchronous validator.

The result has three independent dimensions:

| Dimension | Question it answers |
| --- | --- |
| Replay consistency | Did the current orchestration reproduce the recorded messages and tool flow? |
| Original execution | Did the recorded run encounter a model, tool, cancellation, or side-effect issue? |
| Task verification | Did the declared completion conditions actually pass? |

Replay consistency must not be read as task success. A recorded task may be
replayed consistently while its original task check failed, or while the
current process cannot evaluate a custom validator.

The WebUI marks a directory as **ready for offline validation** only when `turns.jsonl` contains valid
turn envelopes. A partially created or malformed directory remains visible so
that it can be diagnosed or deleted; it is not presented as a replayable sample.
Deleting a recording removes its local prompt, response, tool-result, and
optional HTTP cassette files.

After offline validation, expand any turn in the WebUI to see a readable execution trace:

- the user request and final answer;
- model thinking when the provider actually returned a thinking field;
- each model decision in order;
- each tool call, its status, parameter preview, and result preview;
- the execution outcome and the short reason for it.

Long values can be expanded inside their event. Use **View raw record** for the
full-screen developer inspector, which shows the complete turn envelope and
the ordered `tools.jsonl` records without summarizing or rewriting their JSON.

The green summary answers only whether the current orchestration reproduced the
recorded observable behavior under fixed provider responses and tool
observations. It is not a model-quality score, and the current live provider or
tool implementation is not executed during replay.

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

# control a sample window in a running gateway
pawbot record start --name run-1
pawbot record status
pawbot record stop
pawbot record list

# inspect automatic live execution records
pawbot trace list --filter errors
pawbot trace show <trace-id>
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

`meta.json` also records the experiment ID, pawbot version, full Git revision,
whether tracked source files were dirty, Python/platform information, and the
model/session used by the sample. This makes a replay result easier to explain
when code and environment changed between recording and validation.

To enable the optional raw HTTP cassette support, install the recording extra:

```bash
uv tool install --force --upgrade "pawbot-ai[recording]"
```

For an existing virtual environment, `python -m pip install vcrpy` is enough.
The cassette may contain prompts, request bodies, local paths, tool results, and
model responses; review it before sharing or committing it. Authentication
headers are filtered by the recorder, but the cassette is not a general-purpose
secret scrubber.

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

For high-risk Tool calls, an embedding application can provide
`AgentRunSpec.tool_approval_callback`. Non-read-only Tools wait for a
`ToolApprovalResult` before execution. A denial or approval timeout returns a
model-visible error with `side_effect=not_started`; it never silently falls
through to the real Tool.

Tool implementations can also declare an execution policy with side-effect
class, idempotency, reversibility, recovery strategy, and receipt support.
When cancellation, timeout, or an uncertain error leaves an operation unresolved,
the checkpoint keeps a stable operation fingerprint. An explicitly idempotent
operation may retry; an unknown, non-idempotent, or irreversible operation
requires a human confirmation before the same operation is attempted again.
Replay remains side-effect-free and never asks for live recovery approval.

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
