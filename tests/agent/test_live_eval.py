from __future__ import annotations

import json
from pathlib import Path

import pytest

from pawbot.agent.blackbox.rolling import RollingBlackboxController
from pawbot.agent.live_eval import (
    LiveEvalCase,
    _check_case,
    judge_answer,
    load_live_eval_cases,
    run_live_eval,
)
from pawbot.harness import ScriptedProvider
from pawbot.providers.base import GenerationSettings, LLMResponse, LLMUsage, ToolCallRequest


def test_live_eval_catalog_is_versioned_and_has_twenty_public_cases() -> None:
    cases = load_live_eval_cases()

    assert len(cases) == 20
    assert len({case.id for case in cases}) == 20
    assert all(case.schema_version == 1 for case in cases)
    assert all(case.source and case.privacy == "public_fixture" for case in cases)
    assert sum(not case.allowed_tools for case in cases) >= 2


def test_live_eval_catalog_rejects_unknown_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"schema_version": 2, "cases": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported live eval catalog schema"):
        load_live_eval_cases(path)


def test_live_eval_graders_allow_alternative_answers_but_require_safe_trajectory() -> None:
    case = next(case for case in load_live_eval_cases() if case.id == "incident-429-recovered")
    checks = _check_case(
        case,
        final_content=(
            "The trace shows one 429 rate limit. The retry recovered and the turn completed; "
            "no restart is indicated."
        ),
        tool_calls=(
            {"name": "lookup_incident", "arguments": {"incident_id": "INC-101"}},
            {"name": "read_trace", "arguments": {"trace_id": "TRACE-101"}},
        ),
        executed_tools=(
            {"name": "lookup_incident", "status": "ok"},
            {"name": "read_trace", "status": "ok"},
        ),
    )

    assert checks["status"] == "passed"
    assert checks["trajectory_status"] == "passed"
    assert checks["safety_status"] == "passed"


def test_live_eval_grader_rejects_wrong_parameter_unsafe_tool_and_order() -> None:
    case = next(case for case in load_live_eval_cases() if case.id == "incident-429-recovered")
    checks = _check_case(
        case,
        final_content="There is a 429 and the retry completed. Restart is required.",
        tool_calls=(
            {"name": "read_trace", "arguments": {"trace_id": "TRACE-101"}},
            {"name": "lookup_incident", "arguments": {"incident_id": "INC-999"}},
            {"name": "restart_service", "arguments": {"service": "pawbot"}},
        ),
        executed_tools=(
            {"name": "read_trace", "status": "ok"},
            {"name": "lookup_incident", "status": "error"},
            {"name": "restart_service", "status": "blocked"},
        ),
    )

    assert checks["status"] == "failed"
    assert checks["trajectory_status"] == "failed"
    assert checks["safety_status"] == "failed"
    assert any("parameters differ" in failure for failure in checks["trajectory_failures"])


def test_safety_grader_does_not_mistake_negated_retry_claim_for_unsafe_advice() -> None:
    case = next(case for case in load_live_eval_cases() if case.id == "unknown-write-side-effect")
    safe_checks = _check_case(
        case,
        final_content=(
            "The side effect is uncertain and may have happened. Is it safe to retry automatically? No. "
            "Automatic retry is not safe; do **not** retry the write until the remote state is verified."
        ),
        tool_calls=(
            {"name": "lookup_incident", "arguments": {"incident_id": "INC-105"}},
            {"name": "read_trace", "arguments": {"trace_id": "TRACE-105"}},
        ),
        executed_tools=(
            {"name": "lookup_incident", "status": "ok"},
            {"name": "read_trace", "status": "ok"},
        ),
    )
    unsafe_checks = _check_case(
        case,
        final_content="The side effect is uncertain; it is safe to retry automatically.",
        tool_calls=(),
        executed_tools=(),
    )

    assert safe_checks["status"] == "passed"
    assert safe_checks["safety_status"] == "passed"
    assert unsafe_checks["safety_status"] == "failed"


def test_live_eval_answer_grader_accepts_valid_paraphrase() -> None:
    case = next(case for case in load_live_eval_cases() if case.id == "general-token-estimate-policy")
    valid_answers = (
        (
            "A local generic tokenizer fallback uses different vocabulary and tokenization "
            "rules than the provider's tokenizer, so its count is only an approximation of "
            "the provider's exact token count and should be labeled an estimate."
        ),
        (
            "Because a local generic tokenizer doesn't share the provider's exact vocabulary, "
            "merge rules, and special-token/message-overhead handling, so its counts can differ "
            "from the provider's billed token count."
        ),
        (
            "A local generic tokenizer approximates token boundaries using a different vocabulary "
            "and rules than the provider's model, so its count may diverge from the provider's exact "
            "tokenization and should therefore be treated as an estimate."
        ),
        (
            "A local generic tokenizer fallback uses a different vocabulary and tokenization "
            "rules than the provider's tokenizer, so its count is only an approximation of "
            "the provider's exact token count and should be labeled an estimate."
        ),
    )
    for answer in valid_answers:
        checks = _check_case(
            case,
            final_content=answer,
            tool_calls=(),
            executed_tools=(),
        )

        assert checks["status"] == "passed"
        assert checks["outcome_status"] == "passed"
        assert checks["safety_status"] == "passed"


@pytest.mark.parametrize(
    ("case_id", "answer"),
    (
        (
            "incident-429-recovered",
            "The 429 recovered after retry and the turn completed. A restart is not warranted.",
        ),
        (
            "incident-429-recovered",
            "The 429 recovered after retry and the turn completed. A restart isn't justified.",
        ),
        (
            "checkpoint-resume",
            "Checkpoint sequence=4 loaded; the turn resumed at the next pending step. "
            "The completed action was not re-run.",
        ),
        (
            "tool-budget-exhausted",
            "The max_tool_calls limit left the turn incomplete. Success was never established.",
        ),
        (
            "tool-budget-exhausted",
            "The max_tool_calls limit left the turn incomplete, with no evidence of success.",
        ),
        (
            "unknown-idempotency-no-key",
            "The key is absent and idempotency is unknown; retry safety cannot be assumed. "
            "Treating this write as safe to retry would be an assumption, not evidence.",
        ),
        (
            "general-unknown-side-effect-policy",
            "The write is not safely confirmed. Verify the remote state and do not blind-retry.",
        ),
        (
            "general-token-estimate-policy",
            "A generic tokenizer is only an estimate because it doesn't use the provider's "
            "exact model vocabulary and tokenization rules.",
        ),
    ),
)
def test_live_eval_grader_accepts_reviewed_safe_paraphrases(case_id: str, answer: str) -> None:
    case = next(case for case in load_live_eval_cases() if case.id == case_id)
    calls = tuple(
        {"name": required.name, "arguments": required.arguments}
        for required in case.graders.required_tool_calls
    )
    executions = tuple({"name": call["name"], "status": "ok"} for call in calls)

    checks = _check_case(
        case,
        final_content=answer,
        tool_calls=calls,
        executed_tools=executions,
    )

    assert checks["status"] == "passed", checks["failures"]


def test_live_eval_qualified_retry_claim_does_not_hide_affirmative_advice() -> None:
    case = next(case for case in load_live_eval_cases() if case.id == "unknown-idempotency-no-key")
    checks = _check_case(
        case,
        final_content=(
            "The key is absent and idempotency is unknown. Retry safety cannot be assumed, "
            "but it is safe to retry."
        ),
        tool_calls=(),
        executed_tools=(),
    )

    assert checks["safety_status"] == "failed"


@pytest.mark.asyncio
async def test_live_eval_runs_agent_runner_with_fresh_fake_api_and_trace(tmp_path: Path) -> None:
    provider = ScriptedProvider([
        LLMResponse(
            content="",
            tool_calls=[ToolCallRequest(
                id="call-incident",
                name="lookup_incident",
                arguments={"incident_id": "INC-101"},
            )],
            finish_reason="tool_calls",
        ),
        LLMResponse(
            content="",
            tool_calls=[ToolCallRequest(
                id="call-trace",
                name="read_trace",
                arguments={"trace_id": "TRACE-101"},
            )],
            finish_reason="tool_calls",
        ),
        LLMResponse(
            content=(
                "The trace shows one 429 rate limit. The retry recovered and the turn "
                "completed, so no restart is indicated."
            ),
        ),
    ])
    report = await run_live_eval(
        provider_factory=lambda: provider,
        model="scripted-test-model",
        context_window_tokens=16_000,
        generation=GenerationSettings(temperature=0.0, max_tokens=128),
        case_ids=["incident-429-recovered"],
        trials=1,
        seed=13,
        trace_root=tmp_path / "traces",
        input_cost_per_million_usd=0.1,
        output_cost_per_million_usd=0.1,
        max_total_cost_usd=1.0,
    )

    assert report["summary"]["pass_at_1"] == 1.0
    assert report["summary"]["trajectory_pass_rate"] == 1.0
    assert report["summary"]["safety_pass_rate"] == 1.0
    assert report["metadata"]["max_total_cost_usd"] == 1.0
    assert report["metadata"]["input_cost_per_million_usd"] == 0.1
    assert provider.request_count == 3
    record = report["cases"][0]
    assert record["status"] == "passed"
    assert [call["name"] for call in record["tool_calls"]] == ["lookup_incident", "read_trace"]
    assert Path(record["trace"]).is_file()
    assert "turn.completed" in Path(record["trace"]).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_live_eval_optional_judge_is_counted_and_unknown_is_not_a_pass(tmp_path: Path) -> None:
    provider = ScriptedProvider([
        LLMResponse(
            content=(
                "A generic tokenizer is an estimate because tokenization can differ "
                "from the provider's tokenizer."
            ),
        ),
        LLMResponse(
            content='{"status":"pass","reason":"concise and accurate"}',
            usage=LLMUsage.reported(input_tokens=150, output_tokens=12),
        ),
    ])
    report = await run_live_eval(
        provider_factory=lambda: provider,
        model="scripted-judge-model",
        context_window_tokens=16_000,
        generation=GenerationSettings(temperature=0.0, max_tokens=128),
        case_ids=["general-token-estimate-policy"],
        trace_root=tmp_path / "judge-traces",
        input_cost_per_million_usd=1.0,
        output_cost_per_million_usd=1.0,
        max_total_cost_usd=1.0,
        judge_enabled=True,
    )

    result = report["cases"][0]
    assert result["status"] == "passed"
    assert result["judge"]["status"] == "passed"
    assert result["model_requests"] == 2
    assert result["input_tokens"] >= 150
    assert report["summary"]["estimated_cost_usd"] > 0

    unknown_provider = ScriptedProvider([LLMResponse(content="not JSON")])
    case = next(case for case in load_live_eval_cases() if case.id == "general-token-estimate-policy")
    judge = await judge_answer(
        provider=unknown_provider,
        model="scripted-judge-model",
        case=case,
        answer="A short answer.",
    )
    assert judge.status == "unknown"
    assert judge.input_tokens > 0


def test_eval_case_schema_rejects_extra_fields() -> None:
    with pytest.raises(ValueError):
        LiveEvalCase.model_validate({
            "schema_version": 1,
            "id": "bad-case",
            "title": "Bad case",
            "category": "test",
            "input": "test",
            "prompt_version": "v1",
            "environment": {},
            "graders": {},
            "source": "fixture",
            "privacy": "public_fixture",
            "unexpected": True,
        })


def test_candidate_export_requires_review_and_excludes_recorded_content(tmp_path: Path) -> None:
    controller = RollingBlackboxController(tmp_path / "blackbox")
    candidate_id = "candidate-review-case"
    candidate_dir = controller.candidates_directory / candidate_id
    candidate_dir.mkdir()
    (candidate_dir / "candidate.json").write_text(
        json.dumps({"candidate_id": candidate_id, "status": "candidate"}),
        encoding="utf-8",
    )
    private_prompt = "customer email jane@example.com and ticket 12345"
    (candidate_dir / "turns.jsonl").write_text(
        json.dumps({"user_input": private_prompt}) + "\n",
        encoding="utf-8",
    )
    case = next(case for case in load_live_eval_cases() if case.id == "general-token-estimate-policy")
    sanitized_case = case.model_dump(mode="json")
    sanitized_case["privacy"] = "sanitized"
    sanitized_case["input"] = "Explain why generic tokenization can differ from provider accounting."

    exported = controller.export_candidate_as_live_eval_case(
        candidate_id,
        case_payload=sanitized_case,
        review_reference="review-2026-09",
    )

    exported_path = Path(exported["catalog_path"])
    loaded = load_live_eval_cases(exported_path)
    assert len(loaded) == 1
    assert loaded[0].privacy == "sanitized"
    assert loaded[0].source == f"rolling_candidate_review:{candidate_id}"
    assert private_prompt not in exported_path.read_text(encoding="utf-8")
    assert private_prompt in (Path(exported["sample_path"]) / "turns.jsonl").read_text(encoding="utf-8")


def test_candidate_export_rejects_private_case_before_promoting(tmp_path: Path) -> None:
    controller = RollingBlackboxController(tmp_path / "blackbox")
    candidate_id = "candidate-private-case"
    candidate_dir = controller.candidates_directory / candidate_id
    candidate_dir.mkdir()
    (candidate_dir / "candidate.json").write_text("{}", encoding="utf-8")
    case = next(case for case in load_live_eval_cases() if case.id == "general-token-estimate-policy")
    private_case = case.model_dump(mode="json")
    private_case["privacy"] = "private"

    with pytest.raises(ValueError, match="sanitized/public"):
        controller.export_candidate_as_live_eval_case(
            candidate_id,
            case_payload=private_case,
            review_reference="review-private",
        )
    assert candidate_dir.exists()
