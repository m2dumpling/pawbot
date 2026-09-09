# Changelog

All notable changes to pawbot are recorded here. Release notes describe the
supported surface and known boundaries of each published version.

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
