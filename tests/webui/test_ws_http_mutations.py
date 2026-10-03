from __future__ import annotations

from types import SimpleNamespace

import pytest

from pawbot.webui.ws_http import GatewayHTTPHandler


def _handler() -> GatewayHTTPHandler:
    handler = object.__new__(GatewayHTTPHandler)
    handler.settings_routes = SimpleNamespace(is_mutation_path=lambda _path: False)
    return handler


@pytest.mark.parametrize("action", [
    "blackbox.eval.list", "blackbox.eval.run", "blackbox.eval.remove",
    "blackbox.eval.reports", "blackbox.eval.report", "blackbox.eval.inspect",
    "blackbox.rolling.add_to_eval", "blackbox.rolling.add_recording_to_eval",
])
def test_retired_workbench_mutations_are_no_longer_routed(action: str) -> None:
    response = _handler()._webui_mutation_path(action, {})
    assert not isinstance(response, str)
    assert response.status_code == 404


@pytest.mark.parametrize("action", ["candidates", "promote", "reject"])
def test_record_replay_mutations_still_use_authenticated_routes(action: str) -> None:
    handler = _handler()
    path = handler._webui_mutation_path(f"blackbox.rolling.{action}", {})
    assert path == f"/api/blackbox/rolling/{action}"
    assert handler._is_webui_mutation_path(path)
