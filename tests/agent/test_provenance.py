from __future__ import annotations

import json
from pathlib import Path

from pawbot.agent.blackbox.recorder import BlackboxController
from pawbot.utils.provenance import collect_provenance


def test_collect_provenance_is_reproducible_without_local_paths(tmp_path: Path) -> None:
    payload = collect_provenance(root=tmp_path, extra={"experiment": "test"})

    assert payload["experiment"] == "test"
    assert isinstance(payload["experiment_id"], str)
    assert payload["experiment_id"]
    assert isinstance(payload["recorded_at"], str)
    assert isinstance(payload["pawbot_version"], str)
    assert isinstance(payload["git_revision"], str)
    assert isinstance(payload["git_dirty"], bool)
    assert str(tmp_path) not in json.dumps(payload, ensure_ascii=False)


def test_blackbox_meta_contains_version_and_experiment_provenance(tmp_path: Path) -> None:
    directory = tmp_path / "sample"
    controller = BlackboxController(str(directory))

    controller.write_meta(session_key="cli:test", model="test-model")
    payload = json.loads((directory / "meta.json").read_text(encoding="utf-8"))

    assert payload["mode"] == "record"
    assert payload["schema_version"] == 2
    assert payload["session_key"] == "cli:test"
    assert payload["model"] == "test-model"
    assert isinstance(payload["experiment_id"], str)
    assert payload["pawbot_version"]
    assert payload["git_revision"]
    assert payload["git_rev"]
    assert isinstance(payload["python_version"], str)
    assert isinstance(payload["platform"], str)
