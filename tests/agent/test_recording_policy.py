"""Tests for the cross-process detailed-recording switch."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from pawbot.agent.blackbox import (
    clear_recording_policy,
    read_recording_policy,
    write_recording_policy,
)
from pawbot.agent.blackbox.manifest import read_recording_manifest
from pawbot.agent.blackbox.recorder import BlackboxController
from pawbot.agent.loop import AgentLoop
from pawbot.bus.queue import MessageBus
from pawbot.providers.base import LLMProvider


def test_recording_policy_round_trips_without_accepting_path_escape(tmp_path: Path) -> None:
    directory = write_recording_policy(tmp_path, "demo/sample")

    assert directory == tmp_path / "blackbox" / "demo_sample"
    assert read_recording_policy(tmp_path) == directory
    payload = json.loads(
        (tmp_path / "blackbox" / ".recording.json").read_text(encoding="utf-8")
    )
    assert payload["name"] == "demo_sample"
    assert "api_key" not in json.dumps(payload)
    assert read_recording_manifest(directory)["sample_status"] == "recording"

    clear_recording_policy(tmp_path)
    assert read_recording_policy(tmp_path) is None


def test_agent_loop_syncs_and_stops_shared_recording_policy(tmp_path: Path) -> None:
    provider = MagicMock(spec=LLMProvider)
    provider.provider_name = "fake"
    provider.get_default_model.return_value = "fake-model"
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="fake-model",
    )
    write_recording_policy(tmp_path, "shared")

    loop.sync_recording_policy()
    assert isinstance(loop.blackbox, BlackboxController)
    assert loop.blackbox.directory == tmp_path / "blackbox" / "shared"

    loop.stop_detailed_recording()
    assert loop.blackbox is None
    assert read_recording_policy(tmp_path) is None
