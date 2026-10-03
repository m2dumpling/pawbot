# Changelog

All notable changes to pawbot are recorded here. Release notes describe the
supported surface and known boundaries of each published version.

## Unreleased

## 0.8.0

### Added

- Added a pinned Promptfoo integration with 10 sanitized diagnostic cases,
  isolated Python trials, fixed local tool fixtures, shared deterministic
  assertions, optional advisory LLM Judge, and JSON/HTML/browser reports.
- Added an API-key-free Promptfoo CI smoke check and report summaries that
  separate execution errors, quality failures, Agent/Judge tokens, and
  comparable baseline/candidate measurements.
- Added installed-wheel Gateway checks for bundled WebUI assets, authenticated
  model configuration changes, timezone/network settings, chat, and recovery.

### Changed

- Replaced the self-built live evaluation workbench, management API, catalog,
  CLI, and dedicated export path with the repository's Promptfoo suite.
  Runtime TaskContract verification, Harness/Task Eval, Trace, and Record & Replay remain.
- Native TUI and PyPI publication now run manually for an exact version tag.
  PyPI publication requires all five matching native archives and checksum
  sidecars; TUI publication runs manually for the exact release tag.

### Fixed

- The Channels → WebSocket local WebUI link now follows the current browser
  origin, including custom ports, instead of opening a hard-coded localhost port.
- Included hashed WebUI chunks in normal Git commits and validated local
  asset references during bundle inspection and wheel/sdist builds.
- Rejected extra outcome facts and corrected advisory Judge score and token
  reporting, without treating unavailable Judge results as zero quality.
- Stabilized Markdown setup and runtime checkpoint waits in full test runs.

### Local verification

- Python: 7,011 passed, 54 skipped, 83.03% coverage; WebUI: 1,134 passed;
  TUI: 164 passed. Lint, strict type checks, builds, and quality gates passed.
- Three Gateway checks passed against a wheel installed in a clean environment.
- The current fixed diagnostic suite passed 30/30 real-model trials with no
  execution errors. This is not a production success rate or an improvement
  claim; the optional Judge has not been calibrated with independent human labels.
- The v0.8.0 release-preparation commit passed all 11 remote CI jobs on `main`
  before its version tag was created. Setup, report navigation, and case
  maintenance are documented in the
  [Promptfoo guide](docs/agent-evaluation-promptfoo.zh-CN.md).

## 0.7.3 - 2026-09-28

### Fixed

- Fixed the WebUI mutation route for removing custom task evaluation cases;
  the evaluation-set delete action now reaches the backend instead of returning
  `unknown WebUI mutation action`.
- Improved WebUI stop responsiveness for long-running tasks by terminating
  child execution before joining the active turn and ignoring late stream
  events from a turn that the user already stopped.

### Verification

- Local Python: 6,999 passed, 54 skipped, 82.89% coverage.
- Local WebUI: 1,134 passed; Ruff, basedpyright, frontend lint, and build passed.
- GitHub Actions passed all CI, smoke, replay, TUI, package, and quality-gate jobs.

## 0.7.2 - 2026-09-28

### Added

- Added safe recovery for model output truncation during tool-call arguments;
  incomplete calls are discarded and repeated no-progress failures stop with
  structured error codes instead of looping.
- Added DeepSeek Flash and DeepSeek V4 Pro capability metadata, compatibility
  aliases for legacy DeepSeek model IDs, and a DeepSeek-specific 65,536-token
  output default.
- Added WebUI management for saved regression samples in the Agent task
  evaluation set, including add and remove actions.

### Changed

- Candidate problem runs now use WebUI conversation titles or readable user
  request previews instead of internal candidate IDs.
- Documented DeepSeek as the most thoroughly validated provider path and added
  guidance for provider-specific issue and pull-request contributions.

### Verification

- Local Python: 6,996 passed, 54 skipped, 82.90% coverage.
- Local WebUI: 1,134 passed.
- GitHub Actions passed Python 3.11/Linux, Python 3.13/Windows, WebUI, native
  TUI, Record & Replay, package smoke, installer smoke, and quality gates.

## 0.7.1 - 2026-09-27

## 0.7.1 - 2026-09-27

### Fixed

- Improved Agent reply and WebUI response speed by moving event-journal and
  turn-context disk work out of the Gateway event loop.
- Kept long-running Agent tasks from blocking WebUI settings and controls.
- Reduced CLI Apps catalog loading on Windows from repeated PATH probing to one
  directory scan with final checks for installed applications.
- Preserved WebSocket event ordering and reconnect recovery while bounding the
  retained event journal.

### Verification

- GitHub Actions passed on the `v0.7.1` release candidate: Python 3.11/Linux,
  Python 3.13/Windows, WebUI, native TUI, package smoke, Record & Replay,
  Linux/macOS/Windows installer smoke tests, and the quality gate.
- A local 100-second WebUI run completed five real Agent turns, including an
  `exec` tool turn, without a settings timeout.

## 0.7.0 - 2026-09-23

### Added

- Added a versioned 20-case live model evaluation suite for synthetic incident
  triage, with isolated local fixtures, repeated trials, outcome/trajectory/safety
  reports, cost estimates, latency, and baseline comparison.
- Added an opt-in OpenTelemetry OTLP exporter for Agent spans and low-cardinality
  metrics. Local Trace and replay remain available without an exporter.
- Added token-estimate provenance to local Trace, control-flow documentation,
  and a reviewed-candidate export path for sanitized live evaluation cases.

### Changed

- Migrated the MCP client to SDK v2, with modern discovery and a legacy
  method-not-found fallback, HTTPX2 transport protection, and OAuth issuer checks.
- Added operation context for tool adapters that explicitly support idempotency.
- Calibrated the synthetic evaluation oracle to accept reviewed safe paraphrases
  and avoid treating a qualified retry-safety assumption as affirmative advice.

### Known boundaries

- Live evaluation and OTLP setup use the CLI/configuration; this release does not
  add a new WebUI page for them.
- The bundled cases are synthetic and were calibrated against the same saved
  responses used for comparison. They do not establish production task quality.
- Checkpoint recovery does not undo remote side effects; unknown write outcomes
  still require reconciliation or explicit approval before retry.

### Verification

- GitHub Actions on main commit `ebb01fe`: Python 3.11/Linux, 7,010 passed and
  15 skipped at 82.83% coverage; Python 3.13/Windows, 6,979 passed and 46
  skipped at 82.82% coverage.
- WebUI, production build, native TUI, package smoke, Record & Replay, Linux/
  macOS/Windows installer smoke tests, and the quality gate all passed.
- Built wheel and sdist installed in clean Python 3.11 environments. WebUI chat,
  Trace, offline replay (1/1 consistent), and a Microsoft Learn MCP connection
  passed local smoke checks.
- TUI CI and release packaging workflows are pinned to Bun 1.3.13 to match the
  bundled license, relinking, and source-offer documents. The five-platform TUI
  release archive workflow remains to be run for the release tag before publish.

## 0.6.5 - 2026-09-21

### Changed

- Removed the optional Langfuse exporter, configuration, CLI, WebUI settings,
  provider wrapper, and dependency.
- Local Trace is now the only observability source; Record & Replay, rolling
  evidence, Task Eval, Harness, personalization, and memory remain available.
- Removed the external exporter from the Agent turn path so Trace callbacks
  cannot perform external network work.

## 0.6.4 - 2026-09-21

### Fixed

- Prevented Token estimation, recording detail, status, rolling candidates, and
  evaluation-set reads from blocking the Gateway event loop during long Agent
  tasks.
- Added missing WebUI routes for rolling replay candidates and task evaluation
  actions.
- Made the execution and regression page load each panel independently, so one
  slow diagnostic request no longer leaves the entire page empty.

### Verification

- Python: 6930 passed, 54 skipped.
- WebUI: 1126 passed; production build passed.
- Ruff and basedpyright passed.

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
