"""Independent workspace oracles and real AgentLoop integration without a model."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

EVAL_ROOT = Path(__file__).resolve().parents[2] / "evals" / "promptfoo"
sys.path.insert(0, str(EVAL_ROOT))

import workspace_provider as adapter  # noqa: E402
from workspace_tasks import ALLOWED_CHANGES, SEEDS, check, seed_workspace, snapshot  # noqa: E402

from pawbot.harness import ScriptedProvider  # noqa: E402
from pawbot.providers.base import LLMResponse  # noqa: E402

SOLUTIONS = {
    "workspace-timeout-parser": {
        "timeout_config.py": (
            "DEFAULT_TIMEOUT = 30\n"
            "def parse_timeout(value):\n"
            "    try:\n"
            "        parsed = int(value)\n"
            "        return parsed if parsed > 0 else DEFAULT_TIMEOUT\n"
            "    except (ValueError, TypeError):\n"
            "        return DEFAULT_TIMEOUT\n"
        ),
    },
    "workspace-cached-formula": {
        "usage.py": (
            "def cached_percentage(input_tokens, cache_read_tokens):\n"
            "    return round(100 * cache_read_tokens / input_tokens, 2) if input_tokens else None\n"
        ),
    },
    "workspace-config-migration": {
        "settings.json": '{"timeout_seconds":12,"retries":2,"enabled":true}\n',
        "client.py": "def request_timeout(settings):\n    return settings['timeout_seconds']\n",
    },
    "workspace-log-summary": {
        "summary.json": json.dumps({
            "total": 4,
            "tools": {
                "read": {"calls": 3, "errors": 1, "duration_ms": 25},
                "write": {"calls": 1, "errors": 0, "duration_ms": 20},
            },
        }),
    },
    "workspace-readonly-audit": {},
    "workspace-csv-edge-fix": {
        "csv_utils.py": (
            "def unique_names(names):\n"
            "    result = []\n"
            "    for item in names:\n"
            "        name = item.strip()\n"
            "        if name and name not in result:\n"
            "            result.append(name)\n"
            "    return result\n"
        ),
    },
}
READONLY_ANSWER = json.dumps({
    "recording_enabled": True, "write_allowed": False, "mcp_enabled": False,
    "sources": ["RUNTIME-DEMO", "CONFIG-DEMO"],
})


@pytest.mark.parametrize("case_id", sorted(SEEDS))
def test_every_oracle_distinguishes_incorrect_and_correct_artifacts(tmp_path, case_id):
    workspace = tmp_path / "project"
    seed_workspace(case_id, workspace)
    assert not check(case_id, workspace, final=True, answer="{}")["passed"]
    before = snapshot(workspace)
    for name, content in SOLUTIONS[case_id].items():
        (workspace / name).write_text(content, encoding="utf-8")
    changed = {name for name in before.keys() | snapshot(workspace).keys()
               if before.get(name) != snapshot(workspace).get(name)}
    assert changed <= ALLOWED_CHANGES[case_id]
    assert check(case_id, workspace, final=True, answer=READONLY_ANSWER)["passed"]
    # A second independent mutation must be rejected, not merely the seed.
    if case_id == "workspace-readonly-audit":
        bad_answer = READONLY_ANSWER.replace('"write_allowed": false', '"write_allowed": true')
        assert not check(case_id, workspace, final=True, answer=bad_answer)["passed"]
    else:
        name = next(iter(SOLUTIONS[case_id]))
        (workspace / name).write_text("bad candidate", encoding="utf-8")
        assert not check(case_id, workspace, final=True)["passed"]


def _smoke_variables():
    document = yaml.safe_load((EVAL_ROOT / "promptfooconfig.workspace.smoke.yaml").read_text(encoding="utf-8"))
    return document["tests"][0]["vars"]


def _call(variables):
    return adapter.call_api(
        variables["task"], {"config": {"mode": "smoke"}}, {"vars": variables},
    )


def test_scripted_agentloop_writes_real_file_and_external_oracle(tmp_path, monkeypatch):
    monkeypatch.setenv("PAWBOT_EVAL_TRACE_ROOT", str(tmp_path / "traces"))
    variables = _smoke_variables()
    first, second = _call(variables), _call(variables)
    for result in (first, second):
        assert "error" not in result
        metadata = result["metadata"]
        assert metadata["execution_status"] == metadata["stop_reason"] == "completed"
        assert metadata["validation"]["passed"] is True
        assert metadata["boundary_failures"] == []
        assert metadata["changed_files"] == ["summary.json"]
        assert [row["name"] for row in metadata["tool_calls"]] == ["write_file", "run_project_tests"]
        assert all(set(row["tool_names"]) == adapter.ALLOWED_TOOLS for row in metadata["contexts"])
        assert Path(metadata["trace"]).is_file()
        transcript = Path(metadata["transcript"])
        assert transcript.is_file()
        assert "summary.json" in Path(metadata["file_diff"]).read_text(encoding="utf-8")
        assert not list(transcript.parent.glob("project-*"))
        assert metadata["physical_model_requests"] is None  # scripted override has no physical observer
        usages = [row["usage"] for row in metadata["model_requests"] if row["usage"]]
        assert sum(row["input_tokens"] for row in usages) == result["tokenUsage"]["prompt"]
    assert first["metadata"]["trial_id"] != second["metadata"]["trial_id"]


def _readonly_script(*, attempt_write=False):
    script = []
    if attempt_write:
        script.append({"tool_calls": [{
            "id": "forbidden-write", "name": "write_file",
            "arguments": {"path": "config.json", "content": "{}"},
        }]})
    for name in ("config.json", "runtime.json"):
        script.append({"tool_calls": [{
            "id": name, "name": "read_file", "arguments": {"path": name},
        }]})
    script.append({"content": READONLY_ANSWER})
    return {
        "case_id": "workspace-readonly-audit", "split": "smoke", "allow_simulation": True,
        "task": "Inspect the two JSON files and report their values.", "script": script,
    }


@pytest.mark.parametrize("attempt_write", [False, True])
def test_readonly_case_requires_actual_evidence_and_blocks_writes(tmp_path, monkeypatch, attempt_write):
    monkeypatch.setenv("PAWBOT_EVAL_TRACE_ROOT", str(tmp_path / "traces"))
    result = _call(_readonly_script(attempt_write=attempt_write))
    assert "error" not in result
    metadata = result["metadata"]
    assert metadata["validation"]["passed"]
    assert metadata["changed_files"] == []
    if attempt_write:
        assert any("attempted write" in failure for failure in metadata["boundary_failures"])
        assert any(row["name"] == "write_file" and row["status"] == "error" for row in metadata["observations"])
    else:
        assert metadata["boundary_failures"] == []


def test_invented_answer_without_reading_sources_fails_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("PAWBOT_EVAL_TRACE_ROOT", str(tmp_path / "traces"))
    variables = _readonly_script()
    variables["script"] = [{"content": READONLY_ANSWER}]
    result = _call(variables)
    assert result["metadata"]["validation"]["passed"]
    assert any("source IDs" in failure for failure in result["metadata"]["boundary_failures"])


def test_provider_infrastructure_error_halts_remaining_trials(tmp_path, monkeypatch):
    monkeypatch.setenv("PAWBOT_EVAL_TRACE_ROOT", str(tmp_path / "traces"))
    monkeypatch.setenv("PAWBOT_WORKSPACE_RUN_ID", "same-run")
    calls = []

    def unavailable():
        calls.append(1)
        raise ValueError("provider unavailable")

    monkeypatch.setattr(adapter.shared, "_snapshot", unavailable)
    variables = {"case_id": "workspace-log-summary", "task": "test"}
    first = adapter.call_api("test", {"config": {}}, {"vars": variables})
    second = adapter.call_api("test", {"config": {}}, {"vars": variables})
    assert "provider unavailable" in first["error"]
    assert "halted" in second["error"]
    assert len(calls) == 1


def test_provider_error_response_is_not_scored_as_agent_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("PAWBOT_EVAL_TRACE_ROOT", str(tmp_path / "traces"))
    monkeypatch.setenv("PAWBOT_WORKSPACE_RUN_ID", "error-response")
    provider = ScriptedProvider([LLMResponse(content="provider unavailable", finish_reason="error")])
    monkeypatch.setattr(adapter.shared, "_snapshot", lambda: (
        SimpleNamespace(provider=provider, model="harness-model", context_window_tokens=128000),
        None, None,
    ))
    variables = {"case_id": "workspace-log-summary", "task": "test", "split": "dev"}
    first = adapter.call_api("test", {"config": {}}, {"vars": variables})
    second = adapter.call_api("test", {"config": {}}, {"vars": variables})
    assert first.get("error")
    assert first["metadata"]["execution_status"] == "error"
    assert second["metadata"]["diagnostic_class"] == "run_halted"


def test_unregistered_tool_attempt_is_a_boundary_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("PAWBOT_EVAL_TRACE_ROOT", str(tmp_path / "traces"))
    variables = _readonly_script()
    variables["script"].insert(0, {"tool_calls": [{
        "id": "forbidden-exec", "name": "exec", "arguments": {"command": "ignored"},
    }]})
    result = _call(variables)
    assert "error" not in result
    assert any("forbidden tool attempted" in row for row in result["metadata"]["boundary_failures"])


def test_child_environment_excludes_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("PAWBOT_TEST_FAKE_SECRET", "never-pass-this-to-candidate")
    env = adapter._child_env(tmp_path)
    assert "PAWBOT_TEST_FAKE_SECRET" not in env
    assert env["TMP"] == str(tmp_path)
    assert env["PATH"] == os.environ["PATH"]


@pytest.mark.asyncio
async def test_request_instrumentation_preserves_provider_output_and_captures_model_input():
    original = ScriptedProvider([LLMResponse(content="original response", finish_reason="stop")])
    contexts, physical = adapter._instrument_provider(original, "harness-model")
    messages = [{"role": "user", "content": "hello"}]
    tools = [{"type": "function", "function": {
        "name": "read_file", "description": "read", "parameters": {"type": "object"},
    }}]
    result = await original.chat_with_retry(messages=messages, tools=tools)
    assert result.content == "original response"
    assert original.request_messages == [messages]
    assert contexts[0]["tool_names"] == ["read_file"]
    assert contexts[0]["estimated_prompt_tokens"] > 0
    assert physical == []
