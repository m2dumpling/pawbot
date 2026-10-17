"""Regression test for the MCP reconnect lifecycle.

This test starts a real MCP streamable-http server in a child process,
lets its idle timeout kill the session, and then exercises pawbot's MCP
reconnect path.  The bug being reproduced is a gateway crash caused by
improper cleanup of the old ``streamable_http_client`` async generator during
reconnect / shutdown.

Run:

    pytest tests/agent/test_mcp_reconnect_crash.py -v
"""

import asyncio
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from pawbot.agent.tools import mcp as mcp_module
from pawbot.agent.tools.mcp import MCPProvider, MCPToolWrapper
from pawbot.agent.tools.registry import ToolRegistry
from pawbot.config.schema import MCPServerConfig
from pawbot.security import network as security_network

# Leave enough headroom for reconnect handshakes on slower CI hosts; each test
# still waits beyond this deadline explicitly before exercising recovery.
_IDLE_TIMEOUT_SECONDS = 1.0
_IDLE_EXPIRY_GRACE_SECONDS = 0.5
_TOOL_TIMEOUT_SECONDS = 10


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _run_mcp_server(port: int) -> None:
    """Run the fixture server in an interpreter independent of pytest's import mode.

    The server exposes a single ``greet`` tool and terminates idle sessions
    after ``_IDLE_TIMEOUT_SECONDS``.
    """
    import uvicorn
    from mcp.server.mcpserver import MCPServer
    from mcp.shared.inbound import MCP_METHOD_HEADER
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse, Response

    mcp = MCPServer("IdleTimeoutDemo")

    @mcp.tool()
    def greet(name: str = "World") -> str:  # noqa: N802
        """Greet someone."""
        return f"Hello, {name}!"

    app = mcp.streamable_http_app(
        json_response=True, session_idle_timeout=_IDLE_TIMEOUT_SECONDS,
    )

    async def legacy_session_only(request, call_next):
        # This regression concerns expiring legacy sessions. Modern discovery is
        # stateless, and an open GET stream intentionally prevents idle expiry.
        if request.method == "GET":
            return Response(status_code=405)
        if request.headers.get(MCP_METHOD_HEADER) == "server/discover":
            body = await request.json()
            return JSONResponse({
                "jsonrpc": "2.0", "id": body["id"],
                "error": {"code": -32601, "message": "Legacy fixture: no discovery"},
            })
        return await call_next(request)

    app.add_middleware(BaseHTTPMiddleware, dispatch=legacy_session_only)
    uvicorn.run(app, host="127.0.0.1", port=port)


async def _wait_for_server(url: str, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            async with httpx.AsyncClient(timeout=2.0, trust_env=False) as client:
                response = await client.get(
                    url,
                    headers={"Accept": "text/event-stream"},
                )
                if response.status_code < 500:
                    return True
        except Exception:
            await asyncio.sleep(0.1)
    return False


@pytest.fixture(scope="module")
def mcp_server_url(tmp_path_factory):
    """Start the idle-timeout MCP server and yield its URL."""
    port = _free_port()
    log_path = tmp_path_factory.mktemp("mcp-reconnect") / "server.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-c",
             "import runpy,sys; runpy.run_path(sys.argv[1])['_run_mcp_server'](int(sys.argv[2]))",
             str(Path(__file__).resolve()), str(port)],
            stdout=log, stderr=subprocess.STDOUT,
        )
    try:
        url = f"http://127.0.0.1:{port}/mcp"
        if not asyncio.run(_wait_for_server(url, timeout=10.0)):
            pytest.fail(
                f"MCP repro server did not start (exit={process.poll()}):\n"
                + log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
            )
        yield url
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def _make_provider(*, mcp_servers: dict) -> tuple[MCPProvider, ToolRegistry]:
    registry = ToolRegistry()
    return MCPProvider(mcp_servers, registry), registry


@pytest.fixture(autouse=True)
def allow_loopback_mcp_urls(monkeypatch: pytest.MonkeyPatch):
    """The repro server runs on 127.0.0.1; allow pawbot to talk to it."""
    class TestPinnedDNSAsyncTransport(security_network.PinnedDNSAsyncTransport2):
        _resolver_lock = asyncio.Lock()

    monkeypatch.setattr(mcp_module, "PinnedDNSAsyncTransport2", TestPinnedDNSAsyncTransport)
    monkeypatch.setattr(
        mcp_module,
        "validate_url_target",
        lambda url, *, allow_loopback=False: (True, ""),
    )
    monkeypatch.setattr(
        mcp_module,
        "resolve_url_target",
        lambda url, *, allow_loopback=False: (True, "", ("127.0.0.1",)),
    )
    monkeypatch.setattr(
        security_network,
        "resolve_url_target",
        lambda url, *, allow_loopback=False: (True, "", ("127.0.0.1",)),
    )
    monkeypatch.setattr(
        mcp_module,
        "env_proxy_applies_to_url",
        lambda url: False,
    )
    monkeypatch.setattr(
        mcp_module,
        "httpx2_env_proxy_mounts",
        lambda: {},
    )


@pytest.mark.asyncio
async def test_mcp_reconnect_after_session_timeout(tmp_path, mcp_server_url):
    """Reconnect to a real MCP server after its idle timeout kills the session."""
    cfg = MCPServerConfig(
        type="streamableHttp",
        url=mcp_server_url,
        tool_timeout=_TOOL_TIMEOUT_SECONDS,
        enabled_tools=["*"],
    )
    provider, registry = _make_provider(mcp_servers={"repro": cfg})

    try:
        await asyncio.create_task(provider.connect())
        assert provider.connected_server_names == {"repro"}

        tool = registry.get("mcp_repro_greet")
        assert isinstance(tool, MCPToolWrapper)
        initial_session = tool._session

        output = await asyncio.create_task(tool.execute(name="first"))
        assert "Hello, first" in output

        # Wait for the server-side idle timeout to terminate the session.
        await asyncio.sleep(_IDLE_TIMEOUT_SECONDS + _IDLE_EXPIRY_GRACE_SECONDS)

        output = await asyncio.create_task(tool.execute(name="second"))
        assert "Hello, second" in output
        assert tool._session is not initial_session
    finally:
        await asyncio.create_task(provider.aclose())


@pytest.mark.asyncio
async def test_mcp_reconnect_during_shutdown_does_not_crash(
    tmp_path,
    mcp_server_url,
    monkeypatch: pytest.MonkeyPatch,
):
    """Simulate the production crash: shutdown while reconnect is in flight."""
    cfg = MCPServerConfig(
        type="streamableHttp",
        url=mcp_server_url,
        tool_timeout=_TOOL_TIMEOUT_SECONDS,
        enabled_tools=["*"],
    )
    provider, registry = _make_provider(mcp_servers={"repro": cfg})

    await asyncio.create_task(provider.connect())
    tool = registry.get("mcp_repro_greet")
    assert isinstance(tool, MCPToolWrapper)

    await asyncio.create_task(tool.execute(name="first"))
    await asyncio.sleep(_IDLE_TIMEOUT_SECONDS + _IDLE_EXPIRY_GRACE_SECONDS)

    reconnect_started = asyncio.Event()
    finish_reconnect = asyncio.Event()
    real_connect = mcp_module.connect_mcp_servers

    async def gated_connect(*args, **kwargs):
        reconnect_started.set()
        await finish_reconnect.wait()
        return await real_connect(*args, **kwargs)

    monkeypatch.setattr(mcp_module, "connect_mcp_servers", gated_connect)
    unhandled: list[BaseException] = []

    def capture_unhandled(_loop, context):
        exc = context.get("exception")
        if exc is not None:
            unhandled.append(exc)

    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(capture_unhandled)
    call_task = asyncio.create_task(tool.execute(name="second"))
    try:
        await asyncio.wait_for(reconnect_started.wait(), timeout=5)
        close_task = asyncio.create_task(provider.aclose())
        await asyncio.sleep(0)
        finish_reconnect.set()
        await asyncio.wait_for(asyncio.gather(call_task, close_task), timeout=15)
        await asyncio.sleep(0)
        assert not unhandled, f"Unhandled exception leaked during reconnect/shutdown: {unhandled[0]}"
        assert provider.connected_server_names == set()
    finally:
        finish_reconnect.set()
        if not call_task.done():
            call_task.cancel()
        await asyncio.gather(call_task, return_exceptions=True)
        await asyncio.create_task(provider.aclose())
        loop.set_exception_handler(previous_handler)
