from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast

import pytest
from websockets.datastructures import Headers
from websockets.http11 import Request as WsRequest

from pawbot.webui.ws_http import GatewayHTTPHandler


def _handler() -> GatewayHTTPHandler:
    handler = object.__new__(GatewayHTTPHandler)
    handler.settings_routes = SimpleNamespace(is_mutation_path=lambda _path: False)
    return handler


def test_eval_remove_mutation_resolves_to_authenticated_route() -> None:
    handler = _handler()

    path = handler._webui_mutation_path(
        "blackbox.eval.remove",
        {"case_id": "custom-case"},
    )

    assert path == "/api/blackbox/eval/remove"
    assert handler._is_webui_mutation_path(path)


@pytest.mark.asyncio
async def test_eval_remove_mutation_dispatches_to_blackbox_action() -> None:
    handler = _handler()
    dispatched: list[tuple[str, dict[str, object]]] = []

    async def blackbox_action(action: str, payload: dict[str, object]) -> dict[str, object]:
        dispatched.append((action, payload))
        return {"deleted": True, "case_id": payload["case_id"]}

    handler.blackbox_action = blackbox_action
    request = cast(WsRequest, SimpleNamespace(headers=Headers()))
    setattr(request, "_pawbot_webui_mutation_request", True)
    setattr(request, "_pawbot_webui_mutation_payload", {"case_id": "custom-case"})

    response = await handler._dispatch_blackbox_routes(request, "/api/blackbox/eval/remove")

    assert response is not None
    assert response.status_code == 200
    assert json.loads(response.body) == {"deleted": True, "case_id": "custom-case"}
    assert dispatched == [("eval.remove", {"case_id": "custom-case"})]
