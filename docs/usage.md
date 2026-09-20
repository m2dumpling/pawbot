# pawbot Usage Guide

This guide explains how to use the released Pawbot features from the CLI and WebUI: Trace, the rolling replay buffer, Record & Replay, TaskContract, the Task Eval Set, and the Agent Harness.

If you only want to start chatting, remember:

```bash
pawbot             # open the WebUI
pawbot agent       # open the native TUI
```

Both entry points use the same Agent Runtime, sessions, providers, and tools.

## 1. Start Pawbot

### WebUI

```bash
pawbot
```

Then open **Settings → Models**, choose a provider, enter the API key, save the configuration, choose a model, and send a task in a new conversation.

### Native TUI

```bash
pawbot agent
```

Run one request and exit:

```bash
pawbot agent --message "Inspect this repository and summarize the Agent Loop"
```

### WebUI on a Linux server

The server binds to `127.0.0.1` by default. Use an SSH tunnel:

```bash
pawbot webui --no-open
ssh -N -L 8765:127.0.0.1:8765 <user>@<server>
```

Open `http://127.0.0.1:8765` on your own computer.

For deliberate remote binding:

```bash
pawbot webui --remote --yes --no-open
```

Protect the public port with a firewall and HTTPS/reverse proxy. Do not expose the Gateway health port publicly.

## 2. View a live execution Trace

Send a task and click the trajectory icon in the top-right of the conversation.

The live Trace shows, in order:

```text
stages
→ model requests
→ planned tool calls
→ tool arguments and results
→ retries, approvals, and recovery
→ task verification
→ final outcome
```

Use it to answer which stage was slow, which provider request failed, which tool was rejected or failed, and whether an uncertain side effect remains.

Trace is a bounded diagnostic record. It is not the complete raw Prompt and tool-output archive.

## 3. Automatic rolling replay evidence

Pawbot enables a bounded rolling replay buffer by default:

```text
latest 20 complete turns per conversation
24-hour default retention
256 MB default instance limit
```

Normal turns rotate out. These turns become candidate problem runs automatically:

- provider errors;
- tool failures;
- failed task checks;
- user cancellation;
- budget exhaustion;
- uncertain side effects;
- incomplete execution.

This means an unexpected failure can still be replayed even when you did not start manual recording first.

Rolling evidence is stored below Pawbot's runtime data directory. Legacy samples under `workspace/blackbox` remain readable.

## 4. Review candidate problem runs

Open **Settings → Execution & regression**. Candidate runs show their reason and offer three actions:

```text
Keep for regression
Add to task eval
Ignore
```

The same actions are available as slash commands:

```text
/record candidates
/record keep <candidate-id>
/record reject <candidate-id>
```

**Keep for regression** creates a permanent sample for later Replay. **Add to task eval** promotes the sample and registers it in the local custom evaluation set. If the original turn had no TaskContract, its task result may be `not_evaluable`, while its trajectory can still be checked. **Ignore** moves it to reviewed evidence.

## 5. Save an explicit user preference

Explicit preferences do not wait for Compact or Dream. They are stored in the
Pawbot runtime data directory and injected into future turns for the selected
scope.

```text
/remember global reply_language=zh-CN
/remember global response_style=concise
/remember workspace primary_provider=deepseek
/memory list
/memory candidates
/memory confirm <candidate-id>
/memory reject <candidate-id>
/forget <memory-id>
```

Use `global` for a preference that should follow you across workspaces, and
`workspace` for a project-specific rule. The WebUI exposes the same controls in
**Settings → Confirmed memory**. Pawbot rejects obvious credentials and keeps a
delete event in the local memory audit log.

Dream remains the automatic, lower-confidence memory path; it must not overwrite
an explicitly confirmed preference.

When Dream finds a possible preference or project fact, it stores a candidate with
confidence and evidence. Candidates are visible in **Settings → Confirmed memory →
Suggestions**. Save or ignore them there, or use `/memory confirm <id>` and
`/memory reject <id>`.

## 6. Save a complete regression sample manually

Use manual recording when you want to keep a known success or a complex scenario permanently:

```bash
pawbot record start --name demo
pawbot record status
pawbot record stop
pawbot record list
```

Or record one terminal run:

```bash
pawbot agent \
  --message "Inspect the repository and summarize the Agent Loop" \
  --record .pawbot/blackbox/demo
```

Manual samples can contain prompts, files, tool arguments, and tool results. Review them before sharing.

## 7. Replay offline

In WebUI, choose a saved sample and click **Validate offline**. From the CLI:

```bash
pawbot replay .pawbot/blackbox/demo
pawbot replay .pawbot/blackbox/demo --break-at 2
```

Replay does not make a new provider request, spend new tokens, execute real tools, modify the real workspace, or access external systems.

Read the three result axes separately:

```text
Replay result: consistent / divergent
Original execution: success / tool error / model error / cancelled / uncertain side effect
Task verification: passed / failed / not evaluable
```

“Replay consistent” means that the current code reproduced the recorded observable path. It does not mean that the original task succeeded.

Expand a turn for readable details. **View raw record** opens the unmodified JSON/JSONL data in a full-screen inspector.

## 8. Use TaskContract for an objective task

TaskContract is a task-specific completion contract, not a tool plan for every chat.

Example:

```json
{
  "id": "release-note",
  "final_content_contains": ["verified"],
  "required_files": ["CHANGELOG.md"],
  "file_contains": [["CHANGELOG.md", "verified"]]
}
```

```bash
pawbot agent \
  --message "Create and verify the release note" \
  --task-contract contract.json
```

You can check final text, required files, file content, tool-result content, and explicitly required successful tools. Do not require a particular tool unless the workflow really requires it; a different tool can still produce the correct outcome.

If a check fails, the failed assertions are fed back to the Agent while budget remains. If there is not enough evidence, the result is `not_evaluable`. Ordinary free-form WebUI chats do not automatically create a TaskContract.

## 9. Run the Agent Task Eval Set

The built-in Task Eval Set is a small provider-free regression suite above the Harness:

```bash
pawbot eval list
pawbot eval run
pawbot eval run --json
```

In the WebUI, open **Settings → Execution & regression → Agent task evaluation set → Run evaluation**. The report separates task results, trajectory results, `not_evaluable`, tool failures, model requests, tool calls, and elapsed time.

The built-in cases cover normal tool use, tool failure recovery, file change and verification, investigation and summary, human approval, and budget boundaries.

The distinction is:

```text
Harness: does the Runtime obey failure, cancellation, budget, and approval rules?
Task Eval Set: does a fixed task still produce the expected result and path?
```

Reviewed rolling candidates can be added to the local custom Eval Set from the WebUI.

## 10. Run the Agent Harness

Harness is a developer-facing deterministic incident bench. It uses scripted providers and in-memory tools to drive the real AgentRunner without a network request or real workspace side effect:

```bash
pawbot harness list
pawbot harness run
pawbot harness run --json
```

It covers normal tool execution, recovery, task fixtures, approval, provider errors, timeouts, cancellation, and budget boundaries. A green Harness result proves the declared engineering contracts, not general model quality.

Run the complete local quality gate with:

```bash
python scripts/quality_gate.py
```

## 11. Common slash commands

```text
/trace
/trace errors
/trace slow
/trace <trace-id>

/record status
/record start <name>
/record stop
/record candidates
/record keep <candidate-id>
/record reject <candidate-id>

/eval list
/eval run

/remember global reply_language=zh-CN
/remember workspace response_style=concise
/memory list
/forget <memory-id>

/model
/models
/effort
/stop
/help
```

## 12. Data and privacy

By default, data stays on the local machine:

```text
config: instance config directory/config.json
sessions: runtime data directory/sessions
lightweight traces: runtime data directory/traces
rolling replay: runtime data directory/blackbox/rolling
candidates: runtime data directory/blackbox/candidates
permanent samples: runtime data directory/blackbox/samples
```

Rolling capture redacts common credential fields and does not persist raw HTTP cassettes. Manual samples may contain sensitive content. Never commit configuration, credentials, sessions, traces, blackbox data, personal prompts, or private tool results.

## 13. Troubleshooting

```text
No Trace: verify observability is enabled and send a task in the current session.
No full replay: a lightweight Trace is not a complete Replay sample; inspect rolling candidates or start manual recording.
Task is not_evaluable: no TaskContract was attached or the original run lacks deterministic evidence.
Replay differs: inspect message differences, tool order, result insertion, budget, and context governance.
Linux WebUI is unreachable: use an SSH tunnel or pawbot webui --remote --yes --no-open.
```
