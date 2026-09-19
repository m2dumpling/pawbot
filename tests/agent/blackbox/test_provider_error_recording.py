from __future__ import annotations

import json
from pathlib import Path

import pytest

from pawbot.agent.blackbox.manifest import finalize_recording_manifest
from pawbot.agent.blackbox.recorder import BlackboxController
from pawbot.agent.hook import AgentHookContext, AgentRunHookContext


@pytest.mark.asyncio
async def test_provider_error_is_saved_as_a_replayable_llm_rail(tmp_path: Path) -> None:
    directory = tmp_path / "provider-error"
    controller = BlackboxController(str(directory))
    recorder = controller.turn_hook(
        "turn-error",
        [{"role": "user", "content": "hello"}],
        model="fake-model",
    )

    await recorder.on_model_error(
        AgentHookContext(iteration=0, messages=[]),
        RuntimeError("provider unavailable"),
    )
    await recorder.on_finally(
        AgentRunHookContext(
            messages=[],
            stop_reason="error",
            error="provider unavailable",
        )
    )

    rail = [json.loads(line) for line in (directory / "tools.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rail[0]["kind"] == "llm"
    assert rail[0]["response"]["finish_reason"] == "error"
    manifest = finalize_recording_manifest(directory)
    assert manifest["sample_status"] == "ready"
