"""Small local projects and independent checks for the workspace eval.

The model receives the task and workspace files, never this module or the
expected answers. Both the public check and final verifier run as a separate
process with a fixed command and no extra dependencies.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

SEEDS: dict[str, dict[str, str]] = {
    "workspace-timeout-parser": {
        "timeout_config.py": (
            "DEFAULT_TIMEOUT = 30\n\n"
            "def parse_timeout(value):\n"
            "    return int(value or DEFAULT_TIMEOUT)\n"
        ),
        "README.md": "# Timeout configuration\nThe default timeout is 30 seconds.\n",
    },
    "workspace-cached-formula": {
        "usage.py": (
            "def cached_percentage(input_tokens, cache_read_tokens):\n"
            "    return round(100 * cache_read_tokens / (input_tokens + cache_read_tokens), 2)\n"
        ),
        "README.md": "# Usage\nInput tokens already include cached tokens.\n",
    },
    "workspace-config-migration": {
        "settings.json": '{\n  "request_timeout_s": 12,\n  "retries": 2,\n  "enabled": true\n}\n',
        "client.py": (
            "def request_timeout(settings):\n"
            "    return settings['request_timeout_s']\n"
        ),
        "README.md": "# Client settings\nOther configuration keys must be preserved.\n",
    },
    "workspace-log-summary": {
        "events.jsonl": (
            '{"tool":"read","status":"ok","duration_ms":12}\n'
            '{"tool":"read","status":"error","duration_ms":8}\n'
            '{"tool":"write","status":"ok","duration_ms":20}\n'
            '{"tool":"read","status":"ok","duration_ms":5}\n'
        ),
        "README.md": "# Local event log\nEach line is one tool event.\n",
    },
    "workspace-readonly-audit": {
        "config.json": '{\n  "source_id": "CONFIG-DEMO",\n  "recording_enabled": true,\n  "write_allowed": false\n}\n',
        "runtime.json": '{\n  "source_id": "RUNTIME-DEMO",\n  "mcp_enabled": false\n}\n',
        "README.md": "# Runtime settings\nInspect the two JSON files without modifying anything.\n",
    },
    "workspace-csv-edge-fix": {
        "csv_utils.py": (
            "def unique_names(names):\n"
            "    return list(set(name.strip() for name in names if name))\n"
        ),
        "README.md": "# CSV name handling\nThe input is a list of fields read from a CSV column.\n",
    },
}

ALLOWED_CHANGES: dict[str, set[str]] = {
    "workspace-timeout-parser": {"timeout_config.py"},
    "workspace-cached-formula": {"usage.py"},
    "workspace-config-migration": {"settings.json", "client.py"},
    "workspace-log-summary": {"summary.json"},
    "workspace-readonly-audit": set(),
    "workspace-csv-edge-fix": {"csv_utils.py"},
}


def seed_workspace(case_id: str, workspace: Path) -> None:
    """Create a new, deterministic project in an empty directory."""
    if case_id not in SEEDS:
        raise ValueError(f"Unknown workspace case: {case_id}")
    workspace.mkdir(parents=True, exist_ok=False)
    for name, content in SEEDS[case_id].items():
        (workspace / name).write_text(content, encoding="utf-8", newline="\n")


def snapshot(workspace: Path) -> dict[str, str]:
    """Read a file-tree snapshot; reject symlinks instead of following them."""
    import hashlib

    result: dict[str, str] = {}
    for path in sorted(workspace.rglob("*")):
        relative = path.relative_to(workspace).as_posix()
        if path.is_symlink():
            raise ValueError(f"Symlink in workspace: {relative}")
        if path.is_dir():
            result[f"{relative}/"] = "directory"
        elif path.is_file():
            result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            raise ValueError(f"Unsupported workspace entry: {relative}")
    return result


def changes(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))


def _load(workspace: Path, name: str) -> Any:
    path = workspace / name
    module = ModuleType("_workspace_eval_module")
    module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


def _equal(failures: list[str], name: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        failures.append(f"{name}: expected {expected!r}, got {actual!r}")


def _check(case_id: str, workspace: Path, *, final: bool, answer: str = "") -> dict[str, Any]:
    """Evaluate declared requirements; candidate code errors are quality failures."""
    if case_id not in SEEDS:
        raise ValueError(f"Unknown workspace case: {case_id}")
    failures: list[str] = []
    try:
        if case_id == "workspace-timeout-parser":
            function = _load(workspace, "timeout_config.py").parse_timeout
            rows = [("12", 12), ("", 30), ("bad", 30)]
            if final:
                rows += [(" 10 ", 10), (7, 7), (0, 30), (-5, 30), ("0", 30)]
            for value, expected in rows:
                _equal(failures, f"parse_timeout({value!r})", function(value), expected)
        elif case_id == "workspace-cached-formula":
            function = _load(workspace, "usage.py").cached_percentage
            rows = [(10000, 3500, 35.0), (100, 0, 0.0)]
            if final:
                rows += [(0, 0, None), (3, 1, 33.33), (1000, 125, 12.5)]
            for total, cached, expected in rows:
                _equal(failures, f"cached_percentage({total}, {cached})", function(total, cached), expected)
        elif case_id == "workspace-config-migration":
            settings = json.loads((workspace / "settings.json").read_text(encoding="utf-8"))
            function = _load(workspace, "client.py").request_timeout
            _equal(failures, "timeout_seconds", settings.get("timeout_seconds"), 12)
            _equal(failures, "old key removed", "request_timeout_s" in settings, False)
            _equal(failures, "client timeout", function(settings), 12)
            if final:
                _equal(failures, "other settings", {k: v for k, v in settings.items() if k != "timeout_seconds"}, {"retries": 2, "enabled": True})
                _equal(failures, "client uses supplied value", function({"timeout_seconds": 17}), 17)
        elif case_id == "workspace-log-summary":
            summary = json.loads((workspace / "summary.json").read_text(encoding="utf-8"))
            _equal(failures, "total events", summary.get("total"), 4)
            if final:
                _equal(failures, "tool aggregates", summary.get("tools"), {
                    "read": {"calls": 3, "errors": 1, "duration_ms": 25},
                    "write": {"calls": 1, "errors": 0, "duration_ms": 20},
                })
        elif case_id == "workspace-readonly-audit":
            # The public check only confirms the readable inputs. The final
            # check validates the model answer; observed evidence is checked
            # independently by the provider after the run.
            for name in ("config.json", "runtime.json"):
                json.loads((workspace / name).read_text(encoding="utf-8"))
            if final:
                text = answer.strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                value = json.loads(text)
                expected = {
                    "recording_enabled": True,
                    "write_allowed": False,
                    "mcp_enabled": False,
                }
                if not isinstance(value, dict):
                    failures.append("answer must be a JSON object")
                else:
                    _equal(failures, "answer keys", set(value), {*expected, "sources"})
                    for name, expected_value in expected.items():
                        if type(value.get(name)) is not bool or value[name] is not expected_value:
                            failures.append(f"{name}: expected {expected_value!r}")
                    sources = value.get("sources")
                    if not isinstance(sources, list) or len(sources) != 2 or set(sources) != {"CONFIG-DEMO", "RUNTIME-DEMO"}:
                        failures.append("sources must contain both observed source IDs")
        elif case_id == "workspace-csv-edge-fix":
            function = _load(workspace, "csv_utils.py").unique_names
            _equal(failures, "deduplication", function([" 苹果 ", "苹果", "", "梨"]), ["苹果", "梨"])
            if final:
                _equal(failures, "whitespace, case and order", function([" ", " A", "A ", "a", "梨", "梨"]), ["A", "a", "梨"])
                _equal(failures, "empty input", function([]), [])
    except Exception as exc:
        failures.append(f"candidate output could not be checked: {type(exc).__name__}: {str(exc)[:300]}")
    return {"passed": not failures, "failures": failures, "scope": "final" if final else "public"}


class _DiscardOutput(io.TextIOBase):
    def write(self, text: str) -> int:
        return len(text)


def check(case_id: str, workspace: Path, *, final: bool, answer: str = "") -> dict[str, Any]:
    # Candidate print statements must not corrupt the verifier's JSON stream.
    with contextlib.redirect_stdout(_DiscardOutput()), contextlib.redirect_stderr(_DiscardOutput()):
        return _check(case_id, workspace, final=final, answer=answer)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True, choices=sorted(SEEDS))
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--final", action="store_true")
    args = parser.parse_args()
    answer = sys.stdin.read() if args.final and args.case == "workspace-readonly-audit" else ""
    print(json.dumps(check(args.case, args.workspace, final=args.final, answer=answer), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
