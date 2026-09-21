# Changelog

All notable changes to pawbot are recorded here. Release notes describe the
supported surface and known boundaries of each published version.

## 0.6.3 - 2026-09-21

### Fixed

- Fixed the WebUI model settings query that loaded reasoning-effort options
  with the gateway's path-scoped settings service.
- Added a regression test for this settings route so the pure lookup remains
  compatible with the shared path-aware read wrapper.

## 0.6.2 - 2026-09-21

### Fixed

- Prevented long Replay/Eval operations from blocking Trace, execution, and
  personalization controls on the same WebUI connection.
- Moved synchronous Trace and recording-file scans off the Gateway event loop.
- Added action-scoped WebUI request locks for fast queries and short local
  memory/personalization mutations.
- Added regression coverage for Trace and personalization requests during a
  long-running WebUI mutation.

### Verification

- Python: 6928 passed, 54 skipped; coverage 82.73%.
- WebUI: 1126 passed; lint and production build passed.
- Ruff and basedpyright passed.

## 0.6.1 - 2026-09-21

### Fixed

- Fixed Langfuse Session aggregation by propagating the Pawbot session key as
  the SDK's first-class `sessionId` on root and child observations.
- Replaced the unsupported Langfuse v3-style trace update call with the
  Langfuse 4.x observation attribute propagation API.
- Added regression coverage for WebSocket and non-ASCII/overlong session keys.

### Verification

- Confirmed live Langfuse v2 Observations contain the same `sessionId` on Agent,
  LLM, Tool, and completion observations.

## 0.6.0 - 2026-09-21

### Added

- Added a provider-independent Langfuse observation exporter for Agent turns,
  LLM generations, Tool observations, task verification, and replay comparison.
- Added `pawbot langfuse status`, `configure`, and `test` commands.
- Added WebUI configuration for Langfuse endpoint, environment, sampling, and
  bounded Prompt/Tool previews.
- Upgraded the optional Langfuse dependency to Python SDK 4.7+ for the current
  Observations API and OTEL ingestion path.

### Fixed

- Fixed WebUI mutation routing for personalization and memory clear actions;
  saving personalization, memory switches, and clearing memory no longer
  returns `unknown WebUI mutation action`.

### Verification

- Python: 6923 passed, 54 skipped.
- WebUI: 1126 passed; production build and lint passed.
- Ruff and basedpyright passed.
- Live Langfuse verification confirmed Agent, LLM, Tool, stage, checkpoint,
  and turn-completion observations under one Trace ID.

## 0.5.3 - 2026-09-20

### Added

- Added a separate WebUI **Personalization** section for global, user-authored
  instructions, independent from execution and regression tooling.
- Added global memory switches for using confirmed memories and generating
  background candidates.
- Added `/memories` controls for inspecting and overriding memory behavior in
  the current conversation.
- Added memory search and audit-preserving clear-all behavior.

### Changed

- Personalization instructions are injected as a bounded, user-authored context
  layer before project guidance and confirmed memory.
- Moved memory management out of **Execution & regression** and into
  **Personalization** in the WebUI.
- README and usage diagrams now show personalization and memory policy as part
  of the turn context path.

### Fixed

- Chinese memory keys such as `用户称呼=大海星` are normalized safely instead
  of becoming an empty key; Chinese credential labels remain blocked.

### Verification

- Python: 6919 passed, 54 skipped.
- WebUI: 1126 passed; production build passed.
- Ruff and basedpyright passed.

## 0.5.2 - 2026-09-20

### Added

- Added memory provenance fields for source, trust, origin, evidence references,
  content fingerprints, and superseding records. External, Tool, and Dream
  content cannot silently become trusted memory.
- Added deterministic `TaskContract` assertions for `must`, `must_not`, and
  partial `ordered` constraints, with per-assertion evidence and
  `passed`/`failed`/`not_evaluable` results.
- Added a local Gateway event journal with `event_id`, `stream_id`, and
  monotonic sequence numbers, plus protocol-v1 resume after an event gap.
- Added a durable idempotency ledger for protocol-v1 WebUI mutations.
- Added the read-only `pawbot doctor` command for configuration, workspace,
  Gateway, event journal, operation ledger, and WebUI bundle diagnostics.

### Changed

- WebUI clients automatically negotiate Gateway protocol v1 while older clients
  continue to use the compatible event shape.
- Task Eval and Harness reports now include assertion-level task evidence.
- WebUI and Dream prompts explicitly treat historical, Tool, web, MCP, and file
  content as reference data rather than higher-priority instructions.

### Verification

- Python: 6913 passed, 54 skipped.
- WebUI: 1126 passed.
- Agent Harness: 9/9 passed; Task Eval Set: 6/6 passed.

## Unreleased

No unreleased changes.

## 0.5.1 - 2026-09-19

### Added

- Added DeepSeek-focused Provider contract tests for V4 capabilities, long
  context metadata, reasoning controls, Tool Call history, streaming deltas,
  usage, and provider error semantics.
- Added fault-injection checks for recovery, recording manifests, Provider
  failures, and unified TurnOutcome invariants to the quality gate.
- Added optional process-local request guardrails for per-sender concurrency and
  Provider in-flight/RPM limits; provider-specific environment variables take
  precedence over global values.

### Changed

- Documented that DeepSeek has the strongest validation coverage while other
  Provider endpoints remain compatibility paths that require endpoint-specific
  verification.
- Clarified the security boundary between local Trace/Record & Replay evidence
  and a distributed compliance audit system.

## 0.5.0 - 2026-09-16

### Added

- Added runtime task completion checks through `TaskContract`, with explicit
  `passed`, `failed`, and `not_evaluable` results.
- Added final-answer, successful-tool, tool-result, workspace-file, and custom
  validator assertions for SDK and one-shot CLI runs.
- Added task-verification feedback so a failed completion check can return the
  missing conditions to the Agent within its existing turn budget.
- Added Tool execution policies for side-effect class, idempotency,
  reversibility, recovery strategy, and bounded execution receipts.
- Added stable operation fingerprints and recovery confirmation for interrupted,
  timed-out, or uncertain side-effecting operations.

### Changed

- Record & Replay now stores task contracts and re-evaluates them during offline
  replay.
- Trace and WebUI now show task verification separately from replay consistency
  and original execution errors.
- Agent Harness scenarios use the same runtime task evaluator as normal Agent
  runs; the quality gate covers the new evaluator and recovery policy tests.
- Added `pawbot agent --task-contract <file>` for one-shot declarative checks.

### Safety

- Read-only and explicitly idempotent operations can be retried after an
  interruption; unknown, non-idempotent, and irreversible operations fail closed
  until a human confirms the retry.
- Provider-native conversation state excludes Pawbot's local recovery metadata.

## 0.4.2 - 2026-09-16

### Fixed

- Fixed `pawbot update` on Windows when Pawbot is installed as a persistent
  uv or pipx tool. The update is handed off to a detached PowerShell helper so
  the running launcher is released before it is replaced.

### Changed

- Expanded the README usage guide to explain which capabilities belong in
  WebUI, CLI, or CI.

## 0.4.1 - 2026-09-16

### Added

- Added two deterministic task Fixtures for change-and-verify and
  investigate-and-summarize workflows, both executed through the real
  `AgentRunner` with in-memory state.
- Added Harness aggregate metrics for task evaluability and pass rate, model
  requests, Tool attempts, and Tool failures.
- Added a public Agent Harness experiment baseline describing the fixtures,
  evidence model, reproducibility command, and known limitations.

### Changed

- Added a dedicated CI step that prints the machine-readable Agent Harness
  report so trajectory and task results are visible in the public workflow.

## 0.4.0 - 2026-09-16

### Added

- Added a deterministic Agent Harness Benchmark covering normal Tool use,
  Tool failure recovery, Provider errors, model timeouts, cancellation, and
  turn-budget boundaries.
- Added `pawbot harness list/run` and a local/CI quality gate that combines
  whitespace, Ruff, basedpyright, Harness, Agent/approval contract, and public
  Record & Replay fixture checks.
- Added task-level completion contracts alongside trajectory checks, so a
  deterministic execution path is no longer presented as a task-quality score.
- Added fail-closed Tool approval callbacks and `ToolApprovalManager` for
  human confirmation before write, execute, or network-capable Tools run,
  including WebUI and native TUI decision surfaces.
- Added experiment and environment provenance to Harness reports and recorded
  sample metadata without copying local paths or credentials.

## 0.3.9 - 2026-09-13

### Added

- Added `pawbot update`, which detects the active uv, pipx, or pip installation
  method and upgrades Pawbot without rerunning onboarding or rewriting user
  configuration.
- Added the `recording` extra for optional vcrpy HTTP cassette support and a
  local HTTPX cassette regression test.

### Fixed

- Made the native TUI prefer terminal-native selection in SSH and Mosh sessions,
  improving copy and paste behavior in Termius while retaining an explicit
  `PAWBOT_TUI_MOUSE=1` opt-in.
- Avoided treating remote `Ctrl+V` as a server-side image clipboard request;
  terminal paste events remain available for remote clients.

## 0.3.8 - 2026-09-13

### Fixed

- Made Record & Replay fail closed when a tool observation is missing, while
  preserving the current turn budget and denied-capability policy.
- Enforced wildcard WebSocket authentication, including trusted-proxy
  assertion checks and protected token issuance.
- Returned an OpenAI-compatible SSE error event and `[DONE]` when streaming
  execution fails.
- Fixed the Docker Compose API image's optional dependency and API-key entry
  point; bounded API session-lock lifetime and corrected relative exec paths.
- Kept configured model aliases visible in the WebUI model picker and recorded
  provider-hosted tool events for later inspection.

## 0.3.7 - 2026-09-10

### Fixed

- Removed duplicate model labels from the TUI when the selected model preset
  has the same ID as the active model.
- Kept the TUI welcome card focused on version and workspace information;
  live model and access state now has one clear home in the title controls.

## 0.3.6 - 2026-09-10

### Fixed

- Localized the complete Record & Replay settings surface, including invalid
  recording reasons, so switching the WebUI to English no longer leaves the
  feature in Chinese.
- Added headless Linux WebUI guidance and the `pawbot webui --remote` shortcut;
  server startup no longer presents `127.0.0.1` as if it were a remotely
  reachable address.
- Sent a stable `x-opencode-session` and a Pawbot user agent for OpenCode Go
  requests, preserving the conversation routing contract required by that
  gateway.
- Separated replay consistency from original execution health in the WebUI;
  a deterministic replay can now remain visibly paired with an original tool
  or model failure instead of looking like an overall success.
- Added a readable model-request failure card with the recorded HTTP status,
  provider error type, and error code.

### Added

- Connected the TUI model picker and `/model`/`/models` commands to the current
  provider's live `/models` catalogue.
- Added session-scoped model pins for live or manually entered model IDs;
  switching a session does not rewrite the global configuration.
- Made the curated model capability table authoritative for known model IDs so
  stale provider metadata cannot downgrade a known context window or reasoning
  vocabulary.
- Enabled native Anthropic model discovery with its required authentication and
  model-capability fields; Azure, Bedrock, and OAuth-only providers remain on
  their dedicated/manual paths instead of being sent an incompatible generic
  `/models` request.

## 0.3.5 - 2026-09-10

### Changed

- Reworked the WebUI replay detail into a readable execution trace showing the
  user request, provider-returned thinking, model decisions, tool calls,
  tool-result previews, and final answer in order.
- Kept long parameters and results collapsed behind their own event while
  preserving a dedicated full-screen raw JSON/JSONL inspector.
- Clarified that a green replay result is an offline orchestration regression
  check under recorded inputs and observations, not a model-quality score or a
  live tool/provider test.

## 0.3.4 - 2026-09-10

### Changed

- Reworked the WebUI Record & Replay view around a compact per-turn summary:
  user request, final answer, model, decision count, tool count, and outcome
  are visible first.
- Moved the complete turn-level JSON records into a dedicated full-screen
  viewer so raw execution data remains available without overwhelming the
  normal inspection path.
- Clarified replay terminology to distinguish an offline orchestration check
  from a log viewer or a model-quality score.

## 0.3.3 - 2026-09-09

### Fixed

- Published checksum-verified native TUI archives for the supported desktop and
  server platforms; packaged installs now download the matching client instead
  of failing with a missing-archive message.
- Made Quick Start discover models from a configured compatible provider and
  apply the shared context-window and reasoning-capability metadata to the
  generated preset, with manual entry retained for unsupported catalogues.
- Added explicit remote WebUI binding with safe authentication and operator
  guidance; localhost remains the default.
- Re-enabled registry-backed terminal model autocomplete and recommendations.

## 0.3.2 - 2026-09-09

### Fixed

- Made the macOS/Linux and Windows installers preflight `venv`/`ensurepip`, stop
  cleanly on dependency failures, verify the installed CLI, and report the
  actual launcher/PATH state without printing a false success command.
- Added cross-platform installer smoke coverage for Ubuntu, macOS, and Windows,
  including a missing-venv failure regression test.

### Release notes

- This patch release improves first-install diagnostics and launcher discovery;
  the Agent runtime behavior is unchanged.

## 0.3.1 - 2026-09-09

### Added

- Added turn budgets for iterations, tool calls, wall time, tokens, and costs.
- Added tool lifecycle evidence, cancellation diagnostics, replay benchmarks,
  and core Provider/Tool/Session contract tests.
- Added WebUI replay diagnostics and the `pawbot replay` command.

### Changed

- Reframed the project around a replayable, inspectable AI agent.
- Added a sanitized Record & Replay fixture for regression tests.
- Added CI coverage for Python quality gates, WebUI checks, package smoke tests,
  and clean installation.
- Removed local interview materials, local data, private configuration, and
  stale release planning documents from the public source surface.

- Stabilized oversized structured tool-result references and recursive glob
  matching.

### Fixed

- Removed stale repository, documentation, and deployment links from the public
  project description.
- Made local documentation paths the safe default when no hosted documentation
  URL is configured.

### Boundaries

- The default agent process remains single-process and single-node.
- Record & Replay verifies orchestration against recorded rails; it does not
  make a live provider deterministic.
