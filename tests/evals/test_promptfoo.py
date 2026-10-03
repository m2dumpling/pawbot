"""Integration boundaries for the optional Promptfoo provider (no network)."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from pawbot.harness import ScriptedProvider
from pawbot.providers.base import LLMResponse

EVAL_ROOT = Path(__file__).resolve().parents[2] / "evals/promptfoo"
spec = importlib.util.spec_from_file_location("pawbot_promptfoo_provider", EVAL_ROOT / "provider.py")
assert spec is not None and spec.loader is not None
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


@pytest.fixture
def smoke(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict:
    monkeypatch.setenv("PAWBOT_EVAL_TRACE_ROOT", str(tmp_path / "traces"))
    monkeypatch.delenv("PAWBOT_EVAL_MAX_COST_USD", raising=False)
    return yaml.safe_load((EVAL_ROOT / "promptfooconfig.smoke.yaml").read_text())["tests"][0]["vars"]


def run(variables: dict, **config: object) -> dict:
    return adapter.call_api(variables["task"], {"config": {"mode": "smoke", **config}}, {"vars": variables})


def test_trials_are_isolated_and_oracle_is_not_in_messages(smoke: dict) -> None:
    first, second = run(smoke), run(smoke)
    assert "error" not in first
    assert first["metadata"]["trial_id"] != second["metadata"]["trial_id"]
    assert first["metadata"]["execution_status"] == "completed"
    assert len(first["metadata"]["observations"]) == 1
    assert first["metadata"]["observations"] == second["metadata"]["observations"]
    transcript = json.loads(Path(first["metadata"]["transcript"]).read_text(encoding="utf-8"))
    assert transcript["messages"][1]["content"] == smoke["task"]
    assert "allow_simulation" not in transcript["messages"][1]["content"]
    assert transcript["messages"][-1]["content"] == first["output"]
    assert first["metadata"]["cost_estimated_usd"] is None
    assert first["metadata"]["cost_source"] == "not_configured"
    assert "cost" not in first


def test_unmatched_fixture_is_an_observed_error_not_success(smoke: dict) -> None:
    smoke["script"][0]["tool_calls"][0]["arguments"]["receipt_id"] = "nonexistent"
    response = run(smoke)
    observed = response["metadata"]["observations"][0]
    assert observed["status"] == "error"
    assert observed["evidence_ids"] == []
    assert response["metadata"]["tool_events"][0]["status"] == "error"


def test_writes_are_blocked_before_fixture_action(smoke: dict) -> None:
    smoke["fixture"][0]["read_only"] = False
    response = run(smoke)
    assert response["metadata"]["tool_calls"][0]["name"] == "read_receipt"
    assert response["metadata"]["observations"] == []
    assert response["metadata"]["tool_events"][0]["status"] == "error"


def test_actual_budget_stop_does_not_become_completed(smoke: dict) -> None:
    smoke["script"][0]["tool_calls"] *= 2
    response = run(smoke, max_tool_calls=1)
    assert response["metadata"]["stop_reason"] == "max_tool_calls"
    assert response["metadata"]["execution_status"] == "limited"
    assert response["metadata"]["observations"] == []


def test_stalled_agent_retry_remains_a_quality_failure(smoke: dict) -> None:
    call = smoke["script"][0]
    call["tool_calls"][0]["arguments"] = {}
    smoke["script"] = [deepcopy(call), deepcopy(call)]
    smoke["script"][1]["tool_calls"][0]["id"] = "second-invalid-call"
    response = run(smoke)
    assert response["metadata"]["stop_reason"] == "tool_retry_stalled"
    assert response["metadata"]["execution_status"] == "limited"
    assert response["metadata"]["error_code"] == "NO_PROGRESS_RETRY_LIMIT"
    assert "error" not in response
    assert len(response["metadata"]["tool_calls"]) == 2


def test_case_identity_ignores_trial_identifiers_but_not_changed_oracles(smoke: dict) -> None:
    original = adapter._case_fingerprint(smoke)
    runtime_vars = {**smoke, "__evalId": "another-run", "__evalStepId": "test-0", "__repeatIndex": 2}
    assert adapter._case_fingerprint(runtime_vars) == original
    modified = deepcopy(runtime_vars)
    modified["expected"]["answer"]["facts"]["committed"] = False
    assert adapter._case_fingerprint(modified) != original


def test_missing_smoke_script_is_an_error(smoke: dict) -> None:
    del smoke["script"]
    assert "explicit response script" in run(smoke)["error"]


def test_unusable_config_fails_clearly(monkeypatch: pytest.MonkeyPatch, smoke: dict) -> None:
    monkeypatch.setenv("PAWBOT_EVAL_CONFIG", "/nonexistent/pawbot-eval-config.json")
    response = run(smoke, mode="live")
    assert "existing file" in response["error"]


def test_provider_failure_is_execution_error_not_assertion_failure(
    monkeypatch: pytest.MonkeyPatch, smoke: dict,
) -> None:
    provider = ScriptedProvider([LLMResponse(content="HTTP 401 provider unavailable", finish_reason="error")])
    snapshot = SimpleNamespace(provider=provider, model="fixture", context_window_tokens=128000)
    monkeypatch.setattr(adapter, "_snapshot", lambda: (snapshot, None, None))
    response = run(smoke, mode="live")
    assert "error" in response
    assert response["metadata"]["diagnostic_class"] == "provider_or_execution_error"
    assert Path(response["metadata"]["transcript"]).is_file()


def test_usd_budget_requires_known_prices(monkeypatch: pytest.MonkeyPatch, smoke: dict) -> None:
    snapshot = SimpleNamespace(provider=ScriptedProvider([]), model="fixture", context_window_tokens=128000)
    monkeypatch.setattr(adapter, "_snapshot", lambda: (snapshot, None, None))
    monkeypatch.setenv("PAWBOT_EVAL_MAX_COST_USD", "1")
    response = run(smoke, mode="live")
    assert "both token prices" in response["error"]


def test_case_oracles_are_supported_by_exact_fixture_observations() -> None:
    cases = yaml.safe_load((EVAL_ROOT / "cases.yaml").read_text(encoding="utf-8"))
    assert len(cases) == 10
    assert len({row["vars"]["case_id"] for row in cases}) == len(cases)
    for case in cases:
        variables = case["vars"]
        observations: list[dict] = []
        tools = adapter.build_tools(deepcopy(variables["fixture"]), observations)
        for call in variables["expected"]["required_calls"]:
            tool, _, error = tools.prepare_call(call["name"], call["arguments"])
            assert error is None
            assert tool is not None
            asyncio.run(tool.execute(**call["arguments"]))
            assert observations[-1]["status"] == call.get("status", "ok")
        available = {item for observed in observations for item in observed["evidence_ids"]}
        assert set(variables["expected"]["evidence"]) <= available


def test_sdk_client_is_closed_before_the_worker_event_loop_ends(
    monkeypatch: pytest.MonkeyPatch, smoke: dict,
) -> None:
    closed = []

    class Client:
        async def close(self):
            closed.append(asyncio.get_running_loop().is_running())

    provider = ScriptedProvider([LLMResponse(content="final answer")])
    provider._client = Client()
    snapshot = SimpleNamespace(provider=provider, model="fixture", context_window_tokens=128000)
    monkeypatch.setattr(adapter, "_snapshot", lambda: (snapshot, None, None))
    response = run(smoke, mode="live")
    assert response["metadata"]["execution_status"] == "completed"
    assert closed == [True]


def test_judge_configuration_retains_all_three_hard_assertions() -> None:
    config = yaml.safe_load((EVAL_ROOT / "promptfooconfig.judge.yaml").read_text())
    assertions = config["defaultTest"]["assert"]
    assert [row["metric"] for row in assertions[:3]] == ["runtime", "trajectory", "outcome"]
    assert assertions[3]["type"] == "llm-rubric"
    assert assertions[3]["weight"] == 0
