# Changelog

All notable changes to pawbot are recorded here. Release notes describe the
supported surface and known boundaries of each published version.

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
