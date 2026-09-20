from __future__ import annotations

import json
from pathlib import Path

from pawbot.cli.doctor import collect_doctor_report, render_doctor_report


def test_doctor_is_read_only_and_reports_json(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config.write_text(
        json.dumps({
            "agents": {"defaults": {"workspace": str(workspace), "model": "demo-model"}},
        }),
        encoding="utf-8",
    )

    report = collect_doctor_report(config_path=config, workspace=workspace)
    payload = json.loads(render_doctor_report(report, as_json=True))

    assert payload["status"] == "passed"
    assert any(item["id"] == "config" and item["status"] == "passed" for item in payload["checks"])
    assert not (tmp_path / "run" / "gateway.events.jsonl").exists()


def test_doctor_detects_invalid_event_journal(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    run = tmp_path / "run"
    run.mkdir()
    (run / "gateway.events.jsonl").write_text(
        '{"stream_id":"client:chat","seq":2,"type":"message"}\n'
        '{"stream_id":"client:chat","seq":2,"type":"message"}\n',
        encoding="utf-8",
    )

    report = collect_doctor_report(config_path=config, workspace=tmp_path)
    event_check = next(item for item in report.checks if item.id == "event_sequence")
    assert event_check.status == "failed"
