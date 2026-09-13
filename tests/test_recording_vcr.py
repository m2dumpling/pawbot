from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import yaml

from pawbot.agent.blackbox.recorder import _safe_vcr_import


class _CassetteHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        body = b"cassette-response"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def test_vcrpy_records_and_replays_an_httpx_request(tmp_path: Path) -> None:
    vcr = _safe_vcr_import()
    assert vcr is not None, "the recording extra must make vcrpy importable"

    server = ThreadingHTTPServer(("127.0.0.1", 0), _CassetteHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cassette = tmp_path / "http.yaml"
    url = f"http://127.0.0.1:{server.server_port}/recorded"

    try:
        with vcr.use_cassette(str(cassette)):
            response = httpx.get(url)
        assert response.status_code == 200
        assert response.text == "cassette-response"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    recorded = yaml.safe_load(cassette.read_text(encoding="utf-8"))
    assert len(recorded["interactions"]) == 1
    assert recorded["interactions"][0]["request"]["uri"] == url

    vcr.record_mode = "none"
    with vcr.use_cassette(str(cassette)):
        replayed = httpx.get(url)
    assert replayed.status_code == 200
    assert replayed.text == "cassette-response"
