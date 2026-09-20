"""Bounded, automatic Record & Replay retention.

The normal ``BlackboxController`` is an explicit, user-named sample.  This
module adds the safer default layer: keep a small rolling window of complete
turns, promote abnormal turns to candidates, and let the user decide which
candidate becomes a permanent regression sample.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from loguru import logger

from pawbot.agent.blackbox.manifest import (
    finalize_recording_manifest,
    initialize_recording_manifest,
)
from pawbot.agent.blackbox.recorder import (
    TurnRecorder,
    sanitize_turn_name,
)
from pawbot.agent.blackbox.writer import tighten_permissions, write_json_atomic
from pawbot.agent.observability import redact_text
from pawbot.utils.provenance import collect_provenance

if TYPE_CHECKING:
    from pawbot.agent.evaluation import TaskContract
    from pawbot.agent.hook import AgentRunHookContext


_DEFAULT_TURNS_PER_SESSION = 20
_DEFAULT_RETENTION_SECONDS = 24 * 60 * 60
_DEFAULT_MAX_BYTES = 256 * 1024 * 1024
_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(api[-_ ]?key|access[-_ ]?token|refresh[-_ ]?token|authorization|cookie|password|secret)"
)


def _rolling_payload(value: Any, *, depth: int = 0) -> Any:
    """Redact common credentials while retaining replayable local evidence."""
    if depth >= 8:
        return "<truncated>"
    if isinstance(value, dict):
        mapping = cast(dict[Any, Any], value)
        return {
            str(key): "<redacted>" if _SENSITIVE_KEY_RE.search(str(key)) else _rolling_payload(item, depth=depth + 1)
            for key, item in mapping.items()
        }
    if isinstance(value, list):
        sequence = cast(list[Any], value)
        return [_rolling_payload(item, depth=depth + 1) for item in sequence]
    if isinstance(value, tuple):
        sequence = cast(tuple[Any, ...], value)
        return [_rolling_payload(item, depth=depth + 1) for item in sequence]
    if isinstance(value, str):
        return redact_text(value, limit=100_000)
    return value


def _session_slug(session_key: str | None) -> str:
    raw = session_key or "unbound"
    readable = sanitize_turn_name(raw).strip("._")[:48] or "unbound"
    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:10]
    return f"{readable}-{digest}"


class RollingBlackboxController:
    """Keep bounded replayable turns without making them permanent samples."""

    mode = "rolling"

    def __init__(
        self,
        root: Path,
        *,
        max_turns_per_session: int = _DEFAULT_TURNS_PER_SESSION,
        retention_seconds: int = _DEFAULT_RETENTION_SECONDS,
        max_bytes: int = _DEFAULT_MAX_BYTES,
    ) -> None:
        self.root = root.resolve()
        self.directory = self.root / "rolling"
        self.candidates_directory = self.root / "candidates"
        self.samples_directory = self.root / "samples"
        self.rejected_directory = self.root / "rejected"
        self.eval_index = self.root / "evals.json"
        self.max_turns_per_session = max(1, max_turns_per_session)
        self.retention_seconds = max(0, retention_seconds)
        self.max_bytes = max(0, max_bytes)
        self._turn_directories: dict[str, Path] = {}
        self._session_keys: dict[str, str | None] = {}
        self._directory_session_keys: dict[Path, str | None] = {}
        self._pending_contexts: dict[Path, AgentRunHookContext] = {}
        self._latest_turn_id: str | None = None
        # Rolling capture relies on the normalized LLM rail in tools.jsonl.
        # It deliberately does not persist raw HTTP cassettes by default: this
        # avoids concurrent vcr contexts and keeps headers/request bodies out
        # of the automatic buffer. Explicit /record still captures cassettes.
        self._vcr = None
        self.directory.mkdir(parents=True, exist_ok=True)
        self.candidates_directory.mkdir(parents=True, exist_ok=True)
        self.samples_directory.mkdir(parents=True, exist_ok=True)
        self.rejected_directory.mkdir(parents=True, exist_ok=True)
        tighten_permissions(self.root, directory=True)

    def recording_directory_for_turn(
        self,
        turn_id: str,
        session_key: str | None = None,
    ) -> Path:
        existing = self._turn_directories.get(turn_id)
        if existing is not None:
            return existing
        session_dir = self.directory / _session_slug(session_key)
        target = session_dir / sanitize_turn_name(turn_id)
        target.mkdir(parents=True, exist_ok=True)
        initialize_recording_manifest(target)
        self._turn_directories[turn_id] = target
        self._session_keys[turn_id] = session_key
        self._directory_session_keys[target] = session_key
        self._latest_turn_id = turn_id
        return target

    @contextmanager
    def turn_scope(self, turn_id: str):
        """Capture the provider HTTP rail for one rolling turn."""
        directory = self.recording_directory_for_turn(turn_id, self._session_keys.get(turn_id))
        cassette_path = directory / f"{sanitize_turn_name(turn_id)}.yaml"
        yield
        if not cassette_path.exists():
            cassette_path.write_text("interactions: []\n", encoding="utf-8")
            tighten_permissions(cassette_path)

    def turn_hook(
        self,
        turn_id: str,
        initial_messages: list[dict[str, Any]],
        *,
        session_key: str | None = None,
        model: str = "",
        tools_definitions: list[dict[str, Any]] | None = None,
        task_contract: TaskContract | None = None,
    ) -> TurnRecorder:
        directory = self.recording_directory_for_turn(turn_id, session_key)
        self._write_meta(directory, session_key=session_key, model=model)
        return TurnRecorder(
            directory,
            turn_id,
            session_key=session_key,
            model=model,
            initial_messages=initial_messages,
            tools_definitions=tools_definitions,
            task_contract=task_contract,
            on_finished=self._on_turn_finished,
            payload_transform=_rolling_payload,
        )

    def write_meta(self, *, session_key: str | None, model: str) -> None:
        """Keep the shared controller interface; the hook writes turn metadata."""
        if self._latest_turn_id is None:
            return
        directory = self._turn_directories.get(self._latest_turn_id)
        if directory is not None:
            self._write_meta(directory, session_key=session_key, model=model)

    def _write_meta(self, directory: Path, *, session_key: str | None, model: str) -> None:
        path = directory / "meta.json"
        if path.exists():
            return
        metadata = collect_provenance(extra={
            "mode": "rolling",
            "session_key": session_key,
            "model": model,
            "retention": {
                "max_turns_per_session": self.max_turns_per_session,
                "retention_seconds": self.retention_seconds,
                "max_bytes": self.max_bytes,
            },
        })
        write_json_atomic(path, metadata)

    @staticmethod
    def _candidate_reasons(context: AgentRunHookContext) -> list[str]:
        reasons: list[str] = []
        outcome = context.outcome if isinstance(context.outcome, dict) else {}
        execution_status = str(outcome.get("execution_status") or "")
        stop_reason = str(context.stop_reason or "")
        if context.error or execution_status in {"failed", "cancelled", "incomplete", "limited"}:
            reasons.append("execution_error" if execution_status != "cancelled" else "cancelled")
        if stop_reason in {"error", "cancelled", "task_verification_failed", "max_tool_calls", "max_iterations"}:
            reasons.append(stop_reason)
        task_evaluation = context.task_evaluation
        if isinstance(task_evaluation, dict):
            task_status = str(task_evaluation.get("status") or "")
            if task_status == "failed":
                reasons.append("task_verification_failed")
        if str(outcome.get("side_effect_status") or "") == "unknown":
            reasons.append("unknown_side_effect")
        for event in context.tool_events:
            status = str(event.get("status") or "").lower()
            if status in {"error", "failed", "blocked", "cancelled", "unknown"}:
                reasons.append(f"tool_{status}")
        return list(dict.fromkeys(reasons))

    def _on_turn_finished(self, directory: Path, context: AgentRunHookContext) -> None:
        # The hook runs before AgentLoop closes the outer Trace.  Defer manifest
        # hashing until ``finalize_turn`` so events.jsonl contains turn.completed
        # (or turn.failed/cancelled) as well.
        self._pending_contexts[directory] = context

    def finalize_turn(self, turn_id: str) -> None:
        directory = self._turn_directories.pop(turn_id, None)
        if directory is None:
            return
        self._session_keys.pop(turn_id, None)
        context = self._pending_contexts.pop(directory, None)
        if context is None:
            # A command-only or otherwise short-circuited turn has no replay rail.
            shutil.rmtree(directory, ignore_errors=True)
            self._directory_session_keys.pop(directory, None)
            self._prune()
            return
        manifest = finalize_recording_manifest(directory)
        reasons = self._candidate_reasons(context)
        try:
            if reasons:
                self._promote_candidate(directory, context, manifest, reasons)
            else:
                self._prune()
        finally:
            self._directory_session_keys.pop(directory, None)

    def _promote_candidate(
        self,
        directory: Path,
        context: AgentRunHookContext,
        manifest: dict[str, Any],
        reasons: list[str],
    ) -> None:
        candidate_id = (
            f"candidate-{int(time.time() * 1000)}-"
            f"{sanitize_turn_name(directory.name)[-24:]}"
        )
        target = self.candidates_directory / candidate_id
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(directory), str(target))
            write_json_atomic(target / "candidate.json", {
                "candidate_id": candidate_id,
                "source_turn_id": directory.name,
                "session_key": self._directory_session_keys.get(directory),
                "reasons": reasons,
                "sample_health": manifest.get("sample_status"),
                "created_at_ms": int(time.time() * 1000),
                "status": "candidate",
            })
        except OSError:
            logger.exception("failed to promote rolling recording {}", directory)
        finally:
            self._prune()

    def _prune(self) -> None:
        now = time.time()
        rolling_turns = [
            path
            for path in self.directory.rglob("manifest.json")
            if path.parent.is_dir()
        ]
        by_session: dict[Path, list[Path]] = {}
        for manifest_path in rolling_turns:
            session_dir = manifest_path.parent.parent
            by_session.setdefault(session_dir, []).append(manifest_path.parent)
        for session_dir, turns in by_session.items():
            turns.sort(key=lambda item: item.stat().st_mtime_ns if item.exists() else 0, reverse=True)
            keep: list[Path] = []
            for turn_dir in turns:
                age = now - turn_dir.stat().st_mtime if turn_dir.exists() else 0
                if len(keep) < self.max_turns_per_session and (
                    self.retention_seconds <= 0 or age <= self.retention_seconds
                ):
                    keep.append(turn_dir)
                else:
                    shutil.rmtree(turn_dir, ignore_errors=True)
            if session_dir.exists() and not any(session_dir.iterdir()):
                session_dir.rmdir()

        if self.max_bytes <= 0:
            return
        entries: list[tuple[Path, float, int]] = []
        for manifest_path in self.directory.rglob("manifest.json"):
            turn_dir = manifest_path.parent
            try:
                files = [path for path in turn_dir.rglob("*") if path.is_file()]
                size = sum(path.stat().st_size for path in files)
                modified = turn_dir.stat().st_mtime
            except OSError:
                continue
            entries.append((turn_dir, modified, size))
        total = sum(size for _, _, size in entries)
        for turn_dir, _, size in sorted(entries, key=lambda row: row[1]):
            if total <= self.max_bytes:
                break
            shutil.rmtree(turn_dir, ignore_errors=True)
            total -= size

    def list_candidates(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not self.candidates_directory.exists():
            return rows
        for directory in sorted(self.candidates_directory.iterdir(), reverse=True):
            if not directory.is_dir():
                continue
            try:
                payload = json.loads((directory / "candidate.json").read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                rows.append(cast(dict[str, Any], payload))
        return rows

    def promote_candidate(self, candidate_id: str, name: str | None = None) -> Path:
        source = (self.candidates_directory / sanitize_turn_name(candidate_id)).resolve()
        if source.parent != self.candidates_directory.resolve() or not source.is_dir():
            raise ValueError("candidate not found")
        target_name = sanitize_turn_name(name or candidate_id).strip("._") or candidate_id
        target = (self.samples_directory / target_name).resolve()
        if target.parent != self.samples_directory.resolve():
            raise ValueError("sample name must stay within samples directory")
        if target.exists():
            raise ValueError("sample already exists")
        shutil.move(str(source), str(target))
        candidate_path = target / "candidate.json"
        try:
            payload = json.loads(candidate_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict):
            promoted = cast(dict[str, Any], payload)
            promoted["status"] = "promoted"
            promoted["promoted_at_ms"] = int(time.time() * 1000)
            write_json_atomic(candidate_path, promoted)
        return target

    def reject_candidate(self, candidate_id: str) -> Path:
        """Move a reviewed candidate out of the active queue without replay promotion."""
        source = (self.candidates_directory / sanitize_turn_name(candidate_id)).resolve()
        if source.parent != self.candidates_directory.resolve() or not source.is_dir():
            raise ValueError("candidate not found")
        target = (self.rejected_directory / source.name).resolve()
        if target.exists():
            raise ValueError("candidate already reviewed")
        shutil.move(str(source), str(target))
        candidate_path = target / "candidate.json"
        try:
            payload = json.loads(candidate_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict):
            rejected = cast(dict[str, Any], payload)
            rejected["status"] = "rejected"
            rejected["rejected_at_ms"] = int(time.time() * 1000)
            write_json_atomic(candidate_path, rejected)
        return target

    def list_eval_cases(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self.eval_index.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return []
        if not isinstance(payload, dict):
            return []
        mapping = cast(dict[str, Any], payload)
        raw_cases = mapping.get("cases")
        if not isinstance(raw_cases, list):
            return []
        return [
            cast(dict[str, Any], value)
            for value in cast(list[Any], raw_cases)
            if isinstance(value, dict)
        ]

    def add_candidate_to_eval(
        self,
        candidate_id: str,
        *,
        eval_id: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Promote a candidate and register it as a local replay eval case."""
        sample = self.promote_candidate(candidate_id, name=eval_id or candidate_id)
        candidate_path = sample / "candidate.json"
        try:
            candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            candidate = {}
        candidate_mapping = cast(dict[str, Any], candidate) if isinstance(candidate, dict) else {}
        case_id = sanitize_turn_name(eval_id or candidate_id).strip("._") or candidate_id
        turn_contract: dict[str, Any] | None = None
        turns_path = sample / "turns.jsonl"
        try:
            for line in turns_path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                if isinstance(row, dict):
                    row_mapping = cast(dict[str, Any], row)
                    if isinstance(row_mapping.get("task_contract"), dict):
                        turn_contract = cast(dict[str, Any], row_mapping["task_contract"])
                        break
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
        case = {
            "id": case_id,
            "title": title or case_id,
            "category": "candidate",
            "description": "Promoted from a reviewed rolling replay candidate.",
            "source": "rolling_candidate",
            "sample_directory": str(sample),
            "task_contract": turn_contract,
            "created_at_ms": int(time.time() * 1000),
        }
        existing = [item for item in self.list_eval_cases() if item.get("id") != case_id]
        existing.append(case)
        write_json_atomic(self.eval_index, {
            "schema_version": 1,
            "cases": existing,
            "updated_at_ms": int(time.time() * 1000),
        })
        candidate_mapping["status"] = "promoted"
        candidate_mapping["eval_case_id"] = case_id
        write_json_atomic(candidate_path, candidate_mapping)
        return case


__all__ = ["RollingBlackboxController"]
