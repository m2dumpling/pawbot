from __future__ import annotations

import json

from pawbot.evals import EVAL_SET_NAME, eval_cases, run_eval_sync


def test_eval_set_is_stable_and_task_focused() -> None:
    cases = eval_cases()
    assert 5 <= len(cases) <= 10
    assert len({case.id for case in cases}) == len(cases)
    assert "workspace-change-and-verify" in {case.id for case in cases}


def test_eval_report_contains_task_trajectory_and_execution_axes() -> None:
    report = run_eval_sync(case_ids=["workspace-change-and-verify"])
    payload = report.to_dict()

    assert payload["benchmark"] == EVAL_SET_NAME
    assert payload["summary"]["total"] == 1
    assert payload["summary"]["task_passed"] == 1
    assert payload["summary"]["trajectory_passed"] == 1
    assert payload["cases"][0]["execution_status"] == "completed"
    json.dumps(payload, ensure_ascii=False)
