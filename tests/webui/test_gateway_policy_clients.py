"""Actual WebUI/TUI clients against a gateway process and a local scripted API."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from pawbot.session.manager import SessionManager
from pawbot.session.recovery import PENDING_USER_TURN_KEY, RUNTIME_CHECKPOINT_KEY
from tests.webui.test_gateway_webui_smoke import (
    _free_port,
    _start_gateway,
    _stop_gateway,
    _wait_for_bootstrap,
    _write_smoke_config,
)

ROOT = Path(__file__).resolve().parents[2]


class PolicyProvider(BaseHTTPRequestHandler):
    """Serve only deterministic chat responses; no external credentials or services."""

    def log_message(self, *_args) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(body)
        marker = re.search(r"PAWBOT_POLICY:([a-z_]+)", json.dumps(body["messages"]))
        case = marker.group(1) if marker else "recovery"
        tools = body.get("tools") or []
        parent = any(tool.get("function", {}).get("name") == "spawn" for tool in tools)
        tool_name = "spawn" if parent else "write_file"
        already_called = any(
            call.get("function", {}).get("name") == tool_name
            for message in body["messages"]
            for call in message.get("tool_calls", [])
        )
        message = {"role": "assistant", "content": f"ACCEPTANCE_DONE:{case}"}
        finish = "stop"
        if marker and tools and not already_called:
            arguments = (
                {"task": f"PAWBOT_POLICY:{case}", "wait": case.endswith("inline")}
                if parent
                else {"path": case + ".txt", "content": case}
            )
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call-{case}-{tool_name}",
                        "type": "function",
                        "function": {"name": tool_name, "arguments": json.dumps(arguments)},
                    }
                ],
            }
            finish = "tool_calls"
        usage = {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}
        if body.get("stream"):
            delta = {key: value for key, value in message.items() if value is not None}
            if "tool_calls" in delta:
                delta["tool_calls"][0]["index"] = 0
            chunks = [
                {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": usage},
            ]
            payload = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
            payload += "data: [DONE]\n\n"
            content_type = "text/event-stream"
        else:
            payload = json.dumps(
                {
                    "id": "scripted",
                    "object": "chat.completion",
                    "model": "smoke-model",
                    "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                    "usage": usage,
                }
            )
            content_type = "application/json"
        encoded = payload.encode()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def _seed_recovery(workspace: Path, sessions_root: Path, chat: str) -> None:
    sessions = SessionManager(workspace, sessions_root=sessions_root)
    session = sessions.get_or_create(f"websocket:{chat}")
    session.messages.append({"role": "user", "content": "finish the saved inspection"})
    session.metadata.update({"webui": True, PENDING_USER_TURN_KEY: True})
    call = {
        "id": "pending-write",
        "type": "function",
        "function": {
            "name": "write_file",
            "arguments": json.dumps({"path": chat + ".txt", "content": "unsafe"}),
        },
    }
    session.metadata[RUNTIME_CHECKPOINT_KEY] = {
        "phase": "awaiting_tools",
        "assistant_message": {"role": "assistant", "content": "", "tool_calls": [call]},
        "completed_tool_results": [],
        "pending_tool_calls": [call],
    }
    sessions.save(session, fsync=True)


@pytest.mark.parametrize("client", ["webui", "tui"])
def test_gateway_delegation_approval_stop_and_recovery_with_actual_client(
    tmp_path: Path, client: str
):
    bun = shutil.which("bun")
    if bun is None:
        pytest.skip(
            "Bun is required for actual WebUI/TUI client integration (required in WebUI CI)"
        )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = ThreadingHTTPServer(("127.0.0.1", 0), PolicyProvider)
    provider.requests = []
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    process = None
    try:
        config_path, log_path = tmp_path / "config.json", tmp_path / "gateway.log"
        ws_port = _free_port()
        _write_smoke_config(
            config_path, workspace=workspace, ws_port=ws_port, gateway_port=_free_port()
        )
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["agents"]["defaults"]["maxToolIterations"] = 6
        config["providers"]["custom"]["apiBase"] = f"http://127.0.0.1:{provider.server_port}/v1"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        for action in ("continue", "dismiss"):
            _seed_recovery(workspace, tmp_path / "sessions", f"recovery-{action}")
        process = _start_gateway(config_path, log_path)
        bootstrap = _wait_for_bootstrap(f"http://127.0.0.1:{ws_port}", process, log_path)
        args_path = tmp_path / "client-args.json"
        args_path.write_text(
            json.dumps(
                {
                    "client": client,
                    "bootstrap": bootstrap,
                    "base": f"http://127.0.0.1:{ws_port}",
                    "workspace": str(workspace),
                }
            ),
            encoding="utf-8",
        )
        bundle = tmp_path / "policy-clients.js"
        build = subprocess.run(
            [
                bun,
                "build",
                str(Path(__file__).with_name("policy_clients.ts")),
                "--target=bun",
                "--define",
                'import.meta.env.MODE="test"',
                "--define",
                "import.meta.env.PROD=true",
                "--outfile",
                str(bundle),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert build.returncode == 0, build.stdout + build.stderr
        result = subprocess.run(
            [bun, str(bundle), str(args_path)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=120,
        )
        assert result.returncode == 0, (
            result.stdout
            + result.stderr
            + "\n"
            + log_path.read_text(
                encoding="utf-8",
                errors="replace",
            )[-20000:]
        )
        summary = json.loads(result.stdout.strip().splitlines()[-1])
        assert summary["client"] == client
        assert len(summary["completed"]) == 8

        def completed_child(case: str) -> bool:
            return any(
                f"PAWBOT_POLICY:{case}" in json.dumps(request["messages"])
                and any(message["role"] == "tool" for message in request["messages"])
                and not any(
                    tool.get("function", {}).get("name") == "spawn"
                    for tool in request.get("tools", [])
                )
                for request in provider.requests
            )

        # Parent turn_end can precede a background child. Verify that each
        # approved/denied child actually observed its tool result before cleanup.
        expected = [f"{action}_{mode}" for action in ("approve", "deny")
                    for mode in ("inline", "background")]
        deadline = time.monotonic() + 5
        while not all(completed_child(case) for case in expected) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert all(completed_child(case) for case in expected)
        # A resumed inspection must not execute the interrupted write on its own.
        assert not (workspace / "recovery-continue.txt").exists()
        assert not (workspace / "recovery-dismiss.txt").exists()
        assert provider.requests
    finally:
        if process is not None:
            _stop_gateway(process)
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
