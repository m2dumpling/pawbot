"""Executable architecture constraints for the WebSocket transport adapter."""

from __future__ import annotations

import ast
from pathlib import Path

from pawbot.channels.websocket import runtime

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_RUNTIME_PATH = _REPOSITORY_ROOT / "pawbot" / "channels" / "websocket" / "runtime.py"
_SESSION_IDENTITY_PATH = _REPOSITORY_ROOT / "pawbot" / "webui" / "session_identity.py"
_FORBIDDEN_RUNTIME_IMPORTS = (
    "pawbot.bus.outbound_events",
    "pawbot.command",
    "pawbot.runtime_context",
    "pawbot.security.workspace_access",
    "pawbot.session.goal_state",
    "pawbot.webui.cli_apps_api",
    "pawbot.webui.forking",
    "pawbot.webui.mcp_presets_api",
    "pawbot.webui.sidebar_state",
    "pawbot.webui.transcription_ws",
)


def _channel_method(name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(_RUNTIME_PATH.read_text(encoding="utf-8"))
    channel = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "WebSocketChannel"
    )
    return next(
        node
        for node in channel.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name
    )


def _statements_without_docstring(node: ast.AsyncFunctionDef) -> list[ast.stmt]:
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body.pop(0)
    return body


def test_websocket_runtime_does_not_import_application_command_trees() -> None:
    tree = ast.parse(_RUNTIME_PATH.read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    violations = sorted(
        module
        for module in imported
        if module.startswith(_FORBIDDEN_RUNTIME_IMPORTS)
    )
    assert violations == []


def test_business_entrypoints_are_thin_transport_delegations() -> None:
    for method_name in ("_dispatch_envelope", "_hydrate_after_subscribe", "send"):
        statements = _statements_without_docstring(_channel_method(method_name))
        assert len(statements) == 1, method_name
        assert isinstance(statements[0], ast.Expr), method_name
        assert isinstance(statements[0].value, ast.Await), method_name

    statements = _statements_without_docstring(_channel_method("_dispatch_http"))
    assert len(statements) == 1
    assert isinstance(statements[0], ast.Return)
    assert isinstance(statements[0].value, ast.Await)


def test_persisted_webui_session_prefix_has_one_production_owner() -> None:
    owners = []
    for path in (_REPOSITORY_ROOT / "pawbot").rglob("*.py"):
        if "tests" in path.parts or path == _SESSION_IDENTITY_PATH:
            continue
        if "websocket:" in path.read_text(encoding="utf-8"):
            owners.append(path.relative_to(_REPOSITORY_ROOT).as_posix())
    assert owners == []
    assert 'WEBUI_SESSION_STORAGE_PREFIX = "websocket:"' in _SESSION_IDENTITY_PATH.read_text(
        encoding="utf-8"
    )


def test_runtime_exports_compatibility_protocol_helpers() -> None:
    assert runtime._is_valid_chat_id("unified:default")  # pyright: ignore[reportPrivateUsage]
    assert not runtime._is_valid_chat_id("../escape")  # pyright: ignore[reportPrivateUsage]
