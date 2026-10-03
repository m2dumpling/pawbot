"""Black-box smoke test for the real gateway WebUI transport."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import httpx
import pytest
import websockets

from pawbot.session.manager import SessionManager
from pawbot.session.recovery import PENDING_USER_TURN_KEY, RUNTIME_CHECKPOINT_KEY

_BOOTSTRAP_SECRET = "smoke-secret"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_smoke_config(path: Path, *, workspace: Path, ws_port: int, gateway_port: int) -> None:
    config = {
        "agents": {
            "defaults": {
                "workspace": str(workspace),
                "provider": "custom",
                "model": "custom/smoke-model",
                "maxToolIterations": 1,
                "dream": {"enabled": False},
            }
        },
        "providers": {
            "custom": {
                "apiKey": "smoke-no-external-call",
                "apiBase": "http://127.0.0.1:9/v1",
            }
        },
        "channels": {
            "websocket": {
                "enabled": True,
                "host": "127.0.0.1",
                "port": ws_port,
                "allowFrom": ["*"],
                "tokenIssueSecret": _BOOTSTRAP_SECRET,
            }
        },
        "gateway": {
            "host": "127.0.0.1",
            "port": gateway_port,
            "heartbeat": {"enabled": False},
        },
    }
    path.write_text(json.dumps(config), encoding="utf-8")


def _start_gateway(config_path: Path, log_path: Path) -> subprocess.Popen[bytes]:
    log_file = log_path.open("wb")
    try:
        process = subprocess.Popen(
            [
                os.environ.get("PAWBOT_SMOKE_PYTHON", sys.executable),
                "-m",
                "pawbot",
                "gateway",
                "--config",
                str(config_path),
            ],
            # Exercise the installed wheel when CI supplies another interpreter;
            # never let the repository shadow its bundled Python/static files.
            cwd=config_path.parent,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    finally:
        log_file.close()
    return process


def _stop_gateway(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _get_json(url: str, *, token: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = httpx.get(url, headers=headers, timeout=5.0, trust_env=False)
    response.raise_for_status()
    return response.json()


def _get_bootstrap(url: str) -> dict:
    response = httpx.get(
        url,
        headers={"X-Pawbot-Auth": _BOOTSTRAP_SECRET},
        timeout=5.0,
        trust_env=False,
    )
    response.raise_for_status()
    return response.json()


def _wait_for_bootstrap(base_url: str, process: subprocess.Popen[bytes], log_path: Path) -> dict:
    deadline = time.monotonic() + 20
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            return _get_bootstrap(f"{base_url}/webui/bootstrap")
        except (httpx.HTTPError, OSError) as exc:
            last_error = exc
            time.sleep(0.2)
    logs = log_path.read_text(encoding="utf-8", errors="replace")
    raise AssertionError(f"gateway did not start; last_error={last_error!r}\n{logs}")


async def _recv_until(ws: websockets.WebSocketClientProtocol, event: str) -> dict:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        raw = await asyncio.wait_for(ws.recv(), timeout=5)
        payload = json.loads(raw)
        if payload.get("event") == event:
            return payload
    raise AssertionError(f"websocket event {event!r} was not received")


async def _mutation(ws, action: str, payload: dict, *, status: int = 200) -> dict:
    request_id = uuid4().hex
    await ws.send(json.dumps({
        "type": "webui_request", "request_id": request_id, "action": action, "payload": payload,
    }))
    response = await _recv_until(ws, "webui_response")
    assert response["request_id"] == request_id
    if status != 200:
        assert response["ok"] is False, response
        assert response["error"]["status"] == status, response
        return response
    assert response["ok"] is True, response
    return response["result"]


@pytest.mark.asyncio
async def test_gateway_webui_assets_and_settings_mutations(tmp_path: Path) -> None:
    """Test real settings-button transport, persistence and every lazy asset."""
    ws_port, gateway_port = _free_port(), _free_port()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path, log_path = tmp_path / "config.json", tmp_path / "gateway.log"
    _write_smoke_config(config_path, workspace=workspace, ws_port=ws_port, gateway_port=gateway_port)
    process = _start_gateway(config_path, log_path)
    base_url = f"http://127.0.0.1:{ws_port}"
    try:
        bootstrap = _wait_for_bootstrap(base_url, process, log_path)
        async with httpx.AsyncClient(base_url=base_url, timeout=10, trust_env=False) as http:
            assert (await http.get("/webui/bootstrap")).status_code == 401
            assert (await http.get("/api/settings")).status_code == 401
            assert (await http.get("/")).status_code == 200
            manifest_response = await http.get("/asset-manifest.json")
            manifest_response.raise_for_status()
            assets = set()
            for entry in manifest_response.json().values():
                assets.add(entry["file"])
                assets.update(entry.get("css", []))
                assets.update(entry.get("assets", []))
            assert any("SettingsView" in path for path in assets)
            for path in sorted(assets):
                asset = await http.get(f"/{path}")
                assert asset.status_code == 200, path
                assert "text/html" not in asset.headers["content-type"], path

            http.headers["Authorization"] = f'Bearer {bootstrap["api_token"]}'
            before = (await http.get("/api/settings")).json()
            assert before["agent"]["model"] == "custom/smoke-model"
            features = (await http.get("/api/settings/pawbot-features")).json()["features"]
            websocket_feature = next(row for row in features if row["name"] == "websocket")
            assert websocket_feature["setup"]["official_url"] == "/"
            # Mutations must use the authenticated socket, not a GET link.
            assert (await http.get("/api/settings/update?timezone=UTC")).status_code == 405

            ws_url = f'{bootstrap["ws_url"]}?token={bootstrap["token"]}&client_id=settings-smoke'
            async with websockets.connect(ws_url, origin=base_url) as ws:
                await _recv_until(ws, "ready")
                updated = await _mutation(ws, "settings.agent.update", {"timezone": "UTC", "tool_hint_max_length": 120})
                assert updated["agent"]["timezone"] == "UTC"
                created = await _mutation(ws, "settings.model_configuration.create", {
                    "name": "Smoke spare", "provider": "custom", "model": "custom/spare-model",
                })
                assert created["created_model_preset"] == "Smoke spare"
                await _mutation(ws, "settings.model_configuration.create", {
                    "name": "Smoke spare", "provider": "custom", "model": "custom/spare-model",
                }, status=409)
                changed = await _mutation(ws, "settings.model_configuration.update", {
                    "name": "Smoke spare", "temperature": 0.2,
                })
                assert next(row for row in changed["model_presets"] if row["name"] == "Smoke spare")["temperature"] == 0.2
                deleted = await _mutation(ws, "settings.model_configuration.delete", {"name": "Smoke spare"})
                assert "Smoke spare" not in {row["name"] for row in deleted["model_presets"]}
                network = await _mutation(ws, "settings.network_safety.update", {"webui_allow_local_service_access": False})
                assert network["advanced"]["webui_allow_local_service_access"] is False
                await _mutation(ws, "settings.agent.update", {"timezone": "invalid/zone"}, status=400)
                await _mutation(ws, "blackbox.status", {})
                await _mutation(ws, "trace.list", {"limit": 5})
                await _mutation(ws, "blackbox.eval.list", {}, status=404)

            persisted = (await http.get("/api/settings")).json()
            assert persisted["agent"]["timezone"] == "UTC"
            assert persisted["advanced"]["webui_allow_local_service_access"] is False
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            assert saved["agents"]["defaults"]["timezone"] == "UTC"
            assert saved["tools"]["webuiAllowLocalServiceAccess"] is False
    finally:
        _stop_gateway(process)


@pytest.mark.asyncio
async def test_gateway_webui_bootstrap_message_and_thread_hydration(tmp_path: Path) -> None:
    ws_port = _free_port()
    gateway_port = _free_port()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"
    log_path = tmp_path / "gateway.log"
    _write_smoke_config(
        config_path,
        workspace=workspace,
        ws_port=ws_port,
        gateway_port=gateway_port,
    )

    process = _start_gateway(config_path, log_path)
    base_url = f"http://127.0.0.1:{ws_port}"
    try:
        bootstrap = _wait_for_bootstrap(base_url, process, log_path)
        assert bootstrap["model_name"] == "custom/smoke-model"

        ws_url = f'{bootstrap["ws_url"]}?token={bootstrap["token"]}&client_id=smoke'
        async with websockets.connect(ws_url) as ws:
            ready = await _recv_until(ws, "ready")
            assert ready["client_id"] == "smoke"

            await ws.send(json.dumps({"type": "new_chat"}))
            attached = await _recv_until(ws, "attached")
            chat_id = attached["chat_id"]
            await _recv_until(ws, "session_updated")

            await ws.send(json.dumps({
                "type": "message",
                "chat_id": chat_id,
                "content": "/model",
                "webui": True,
                "turn_id": "smoke-turn",
            }))
            answer = await _recv_until(ws, "message")
            assert "Current model: `custom/smoke-model`" in answer["text"]
            await _recv_until(ws, "turn_end")

            await ws.send(json.dumps({
                "type": "message",
                "chat_id": chat_id,
                "content": "!printf shell-ok",
                "webui": True,
                "user_shell": True,
                "turn_id": "shell-turn",
            }))
            shell = await _recv_until(ws, "message")
            assert "shell-ok" in shell["text"]
            assert shell["turn_id"] == "shell-turn"
            await _recv_until(ws, "turn_end")

        api_token = _wait_for_bootstrap(base_url, process, log_path)["api_token"]
        sessions = _get_json(f"{base_url}/api/sessions", token=api_token)
        key = f"websocket:{chat_id}"
        assert key in {row["key"] for row in sessions["sessions"]}

        encoded_key = quote(key, safe="")
        thread = _get_json(
            f"{base_url}/api/sessions/{encoded_key}/webui-thread",
            token=api_token,
        )
        contents = [str(message.get("content") or "") for message in thread["messages"]]
        assert "/model" in contents
        assert any("Current model: `custom/smoke-model`" in text for text in contents)
        assert "!printf shell-ok" in contents
        assert any("shell-ok" in text for text in contents)
    finally:
        _stop_gateway(process)


def test_gateway_restart_restores_a_completed_answer_without_replaying_model(
    tmp_path: Path,
) -> None:
    """Exercise recovery through two real gateway processes and durable files."""
    ws_port = _free_port()
    gateway_port = _free_port()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"
    first_log = tmp_path / "gateway-first.log"
    second_log = tmp_path / "gateway-second.log"
    _write_smoke_config(
        config_path,
        workspace=workspace,
        ws_port=ws_port,
        gateway_port=gateway_port,
    )
    base_url = f"http://127.0.0.1:{ws_port}"

    first = _start_gateway(config_path, first_log)
    try:
        _wait_for_bootstrap(base_url, first, first_log)
    finally:
        _stop_gateway(first)

    sessions_root = tmp_path / "sessions"
    sessions = SessionManager(workspace, sessions_root=sessions_root)
    session = sessions.get_or_create("websocket:recovery-smoke")
    session.messages.append({"role": "user", "content": "recover this answer"})
    session.metadata["webui"] = True
    session.metadata[PENDING_USER_TURN_KEY] = True
    session.metadata[RUNTIME_CHECKPOINT_KEY] = {
        "phase": "final_response",
        "assistant_message": {
            "role": "assistant",
            "content": "restored without another model request",
        },
        "completed_tool_results": [],
        "pending_tool_calls": [],
    }
    sessions.save(session, fsync=True)

    second = _start_gateway(config_path, second_log)
    try:
        bootstrap = _wait_for_bootstrap(base_url, second, second_log)
        deadline = time.monotonic() + 20
        restored = None
        while time.monotonic() < deadline:
            restored = SessionManager(
                workspace,
                sessions_root=sessions_root,
            ).get_or_create("websocket:recovery-smoke")
            if any(
                message.get("content") == "restored without another model request"
                for message in restored.messages
            ):
                break
            time.sleep(0.1)
        else:
            logs = second_log.read_text(encoding="utf-8", errors="replace")
            raise AssertionError(f"answer was not recovered after restart\n{logs}")

        assert restored is not None
        assert PENDING_USER_TURN_KEY not in restored.metadata
        assert RUNTIME_CHECKPOINT_KEY not in restored.metadata
        assert restored.metadata["webui_recovery"]["reason"] == "answer_restored"

        async def assert_attach_state() -> None:
            ws_url = f'{bootstrap["ws_url"]}?token={bootstrap["token"]}&client_id=recovery-smoke'
            async with websockets.connect(ws_url) as ws:
                await _recv_until(ws, "ready")
                await ws.send(json.dumps({"type": "attach", "chat_id": "recovery-smoke"}))
                attached = await _recv_until(ws, "attached")
                assert attached["recovery_state"]["status"] == "recovered"
                assert attached["recovery_state"]["reason"] == "answer_restored"

        asyncio.run(assert_attach_state())
    finally:
        _stop_gateway(second)
