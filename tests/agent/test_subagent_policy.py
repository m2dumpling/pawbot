"""Exercise delegated execution through the real loop, runner and filesystem tools."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pawbot.agent.approval import ToolApprovalManager, ToolApprovalRequest, ToolApprovalResult
from pawbot.agent.loop import AgentLoop
from pawbot.agent.runner import AgentRunner, AgentRunSpec
from pawbot.agent.subagent import SubagentManager
from pawbot.agent.tools.base import ToolResult
from pawbot.agent.tools.context import RequestContext, current_request_context, request_context
from pawbot.agent.tools.registry import ToolRegistry
from pawbot.agent.tools.spawn import SpawnTool
from pawbot.bus.queue import MessageBus
from pawbot.config.schema import ToolsConfig
from pawbot.harness import HarnessTool, ScriptedProvider
from pawbot.providers.base import LLMResponse, ToolCallRequest
from pawbot.security.workspace_access import WORKSPACE_SCOPE_METADATA_KEY
from pawbot.session.manager import SessionManager
from pawbot.utils.llm_runtime import LLMRuntime


@pytest.fixture(autouse=True)
def isolated_runtime_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("pawbot.config.loader._current_config_path", tmp_path / "config.json")
    monkeypatch.setattr(
        "pawbot.session.manager.get_legacy_sessions_dir",
        lambda: tmp_path / "legacy-sessions",
    )


def _call(name: str, **arguments) -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[ToolCallRequest(id=f"call-{name}", name=name, arguments=arguments)],
        finish_reason="tool_calls",
    )


def _done() -> LLMResponse:
    return LLMResponse(content="finished", finish_reason="stop")


class DelegatingProvider(ScriptedProvider):
    """Separate scripts make background scheduling independent of response order."""

    def __init__(
        self,
        *,
        path: str = "canary.txt",
        wait: bool = True,
        delegate: bool = True,
        operation: LLMResponse | None = None,
    ):
        action = operation or _call("write_file", path=path, content="canary")
        super().__init__(
            [
                _call("spawn", task="child", wait=wait) if delegate else action,
                _done(),
            ]
        )
        self.child = ScriptedProvider([action, _done()])
        self.parent_done = asyncio.Event()

    async def _next(self, messages):
        first_user = next(item for item in messages if item["role"] == "user")
        if first_user["content"] == "child":
            return await self.child._next(messages)
        response = await super()._next(messages)
        if not response.tool_calls:
            self.parent_done.set()
        return response


def _loop(tmp_path: Path, provider: ScriptedProvider, **kwargs) -> AgentLoop:
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="harness-model",
        max_iterations=4,
        session_manager=SessionManager(
            tmp_path,
            sessions_root=tmp_path.with_name(tmp_path.name + "-sessions"),
        ),
        **kwargs,
    )


async def _run(
    loop: AgentLoop,
    provider: ScriptedProvider,
    *,
    mode="restricted",
    chat="one",
    project=None,
):
    runtime = LLMRuntime.capture(provider, "harness-model", context_window_tokens=128_000)
    result = await loop._run_agent_loop(
        [{"role": "user", "content": "parent"}],
        runtime=runtime,
        request_context=RequestContext(
            channel="websocket",
            chat_id=chat,
            session_key=f"websocket:{chat}",
            runtime=runtime,
            metadata={
                WORKSPACE_SCOPE_METADATA_KEY: {
                    "project_path": str((project or loop.workspace).resolve()),
                    "access_mode": mode,
                }
            },
        ),
    )
    tasks = list(loop.subagents._running_tasks.values())
    if tasks:
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
    assert result.error is None
    return result


@pytest.mark.parametrize("route", ["direct", "inline", "background"])
async def test_denied_read_cannot_be_delegated(tmp_path: Path, route: str):
    (tmp_path / "private.txt").write_text("private canary", encoding="utf-8")
    provider = DelegatingProvider(
        wait=route == "inline",
        delegate=route != "direct",
        operation=_call("read_file", path="private.txt"),
    )
    loop = _loop(tmp_path, provider, tools_config=ToolsConfig(denied_capabilities=["read"]))
    try:
        result = await _run(loop, provider)
        messages = result.messages if route == "direct" else provider.child.request_messages[-1]
        observations = [str(m["content"]) for m in messages if m["role"] == "tool"]
        assert any("denied capabilities: read" in value for value in observations)
        assert all("private canary" not in value for value in observations)
    finally:
        await loop.aclose()


@pytest.mark.parametrize("wait", [True, False], ids=["inline", "background"])
async def test_child_approval_timeout_rejects_late_response(tmp_path: Path, wait: bool):
    requests: list[ToolApprovalRequest] = []
    approvals = ToolApprovalManager(timeout_s=0, on_request=requests.append)

    async def approve(request):
        if request.name == "spawn":
            return ToolApprovalResult.approve()
        return await approvals.request(request)

    provider = DelegatingProvider(wait=wait)
    loop = _loop(tmp_path, provider, tool_approval_callback=approve)
    try:
        await _run(loop, provider)
        assert not (tmp_path / "canary.txt").exists()
        assert len(requests) == 1
        assert approvals.pending_requests() == []
        assert approvals.resolve_for_chat(requests[0].request_id, "one", "approved") is False
    finally:
        await loop.aclose()


async def test_background_child_waits_for_chat_bound_operator_approval(tmp_path: Path):
    entered = asyncio.Event()
    approvals = ToolApprovalManager(timeout_s=30, on_request=lambda _: entered.set())

    async def approve(request):
        if request.name == "spawn":
            return ToolApprovalResult.approve()
        return await approvals.request(request)

    provider = DelegatingProvider(wait=False)
    loop = _loop(tmp_path, provider, tool_approval_callback=approve)
    execution = asyncio.create_task(_run(loop, provider))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        request = approvals.pending_requests(session_key="websocket:one")[0]
        assert request.name == "write_file"
        assert request.channel == "websocket"
        assert not (tmp_path / "canary.txt").exists()
        assert approvals.resolve_for_chat(request.request_id, "other", "approved") is False
        assert not (tmp_path / "canary.txt").exists()
        assert approvals.resolve_for_chat(request.request_id, "one", "approved") is True
        await asyncio.wait_for(execution, timeout=5)
        assert (tmp_path / "canary.txt").read_text(encoding="utf-8") == "canary"
        assert approvals.pending_requests() == []
    finally:
        execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)
        await loop.aclose()


@pytest.mark.parametrize("wait", [True, False], ids=["inline", "background"])
async def test_stop_cancels_child_waiting_for_approval(tmp_path: Path, wait: bool):
    entered = {"one": asyncio.Event(), "two": asyncio.Event()}
    approvals = ToolApprovalManager(
        timeout_s=30,
        on_request=lambda request: entered[request.chat_id].set(),
    )

    async def approve(request):
        if request.name == "spawn":
            return ToolApprovalResult.approve()
        return await approvals.request(request)

    provider = DelegatingProvider(wait=wait)
    loop = _loop(tmp_path, provider, tool_approval_callback=approve)
    execution = asyncio.create_task(_run(loop, provider))
    active = {execution}
    execution.add_done_callback(active.discard)
    loop._active_tasks["websocket:one"] = active
    other = None
    try:
        await asyncio.wait_for(entered["one"].wait(), timeout=5)
        request = approvals.pending_requests()[0]
        assert request.session_key == "websocket:one"
        other_provider = DelegatingProvider(path="other.txt")
        other = asyncio.create_task(_run(loop, other_provider, chat="two"))
        await asyncio.wait_for(entered["two"].wait(), timeout=5)
        other_request = approvals.pending_requests(session_key="websocket:two")[0]
        assert not (tmp_path / "canary.txt").exists()
        assert await loop._cancel_active_tasks("websocket:one") >= 1
        with pytest.raises(asyncio.CancelledError):
            await execution
        assert approvals.pending_requests() == [other_request]
        assert approvals.resolve_for_chat(request.request_id, "one", "approved") is False
        assert not other.done()
        assert loop.subagents.get_running_count_by_session("websocket:one") == 0
        assert loop.subagents.get_running_count_by_session("websocket:two") == 1
        assert approvals.resolve_for_chat(other_request.request_id, "two", "approved") is True
        await asyncio.wait_for(other, timeout=5)
        assert (tmp_path / "other.txt").read_text(encoding="utf-8") == "canary"
        assert approvals.pending_requests() == []
        assert loop.subagents.get_running_count() == 0
        assert not (tmp_path / "canary.txt").exists()
    finally:
        execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)
        if other is not None:
            other.cancel()
            await asyncio.gather(other, return_exceptions=True)
        await loop.aclose()


async def test_queued_tasks_keep_their_own_access_mode_and_approval_route(tmp_path: Path):
    requests: list[ToolApprovalRequest] = []

    async def deny(request):
        requests.append(request)
        return (
            ToolApprovalResult.approve()
            if request.name == "spawn"
            else ToolApprovalResult.deny("restricted session")
        )

    restricted = DelegatingProvider(path="restricted.txt", wait=False)
    full = DelegatingProvider(path="full.txt", wait=False)
    loop = _loop(tmp_path, restricted, tool_approval_callback=deny)
    slots = asyncio.Semaphore(1)
    loop.subagents._run_slots = slots
    await slots.acquire()
    tasks = []
    try:
        tasks.append(asyncio.create_task(_run(loop, restricted, chat="restricted")))
        await asyncio.wait_for(restricted.parent_done.wait(), timeout=5)
        tasks.append(asyncio.create_task(_run(loop, full, mode="full", chat="full")))
        await asyncio.wait_for(full.parent_done.wait(), timeout=5)
        assert loop.subagents.get_running_count() == 2
        slots.release()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)
        assert not (tmp_path / "restricted.txt").exists()
        assert (tmp_path / "full.txt").read_text(encoding="utf-8") == "canary"
        writes = [r for r in requests if r.name == "write_file"]
        assert len(writes) == 1
        assert (writes[0].chat_id, writes[0].session_key) == ("restricted", "websocket:restricted")
    finally:
        slots.release()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await loop.aclose()


@pytest.mark.parametrize("wait", [True, False], ids=["inline", "background"])
async def test_concurrent_children_keep_distinct_projects_and_approvals(tmp_path: Path, wait: bool):
    projects = {name: tmp_path / name for name in ("alpha", "beta")}
    for name, project in projects.items():
        project.mkdir()
        (project / "identity.txt").write_text(name, encoding="utf-8")
    approvals = ToolApprovalManager(timeout_s=30)
    both_pending = asyncio.Event()
    requests = []

    async def approve(request):
        if request.name == "spawn":
            return ToolApprovalResult.approve()
        requests.append(request)
        if len(requests) == 2:
            both_pending.set()
        return await approvals.request(request)

    providers = {}
    for name in projects:
        provider = DelegatingProvider(wait=wait)
        provider.child = ScriptedProvider(
            [
                _call("read_file", path="identity.txt"),
                _call("write_file", path="result.txt", content=name),
                _done(),
            ]
        )
        providers[name] = provider
    loop = _loop(tmp_path, providers["alpha"], tool_approval_callback=approve)
    tasks = [
        asyncio.create_task(
            _run(
                loop,
                providers[name],
                chat=name,
                project=project,
            )
        )
        for name, project in projects.items()
    ]
    try:
        await asyncio.wait_for(both_pending.wait(), timeout=10)
        assert all(not (project / "result.txt").exists() for project in projects.values())
        for request in requests:
            assert request.session_key == f"websocket:{request.chat_id}"
            assert request.arguments["content"] == request.chat_id
            wrong_chat = "beta" if request.chat_id == "alpha" else "alpha"
            assert not approvals.resolve_for_chat(request.request_id, wrong_chat, "approved")
            assert approvals.resolve_for_chat(request.request_id, request.chat_id, "approved")
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)
        for name, project in projects.items():
            assert (project / "result.txt").read_text(encoding="utf-8") == name
            observations = [
                str(m["content"])
                for m in providers[name].child.request_messages[-1]
                if m["role"] == "tool"
            ]
            assert any(f"1| {name}" in value for value in observations)
        assert not (tmp_path / "result.txt").exists()
        assert approvals.pending_requests() == []
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await loop.aclose()


@pytest.mark.parametrize("delivery", ["dispatch", "pending"])
async def test_cancel_drops_already_queued_child_result_before_new_turn(
    tmp_path: Path, delivery: str
):
    from pawbot.bus.events import InboundMessage

    provider = ScriptedProvider([_done(), _done(), _done()])
    loop = _loop(tmp_path, provider)
    key = "websocket:one"
    session = loop.sessions.get_or_create(key)
    try:
        await loop.subagents._announce_result(
            "old-child",
            "old",
            "old task",
            "STALE_CHILD_MARKER",
            {"channel": "websocket", "chat_id": "one", "session_key": key},
            "ok",
        )
        stale = await loop.bus.consume_inbound()
        await loop._cancel_active_tasks(key)
        if delivery == "dispatch":
            await loop._dispatch(
                InboundMessage(
                    channel="websocket",
                    sender_id="user",
                    chat_id="one",
                    content="fresh request",
                )
            )
            requests = provider.request_count
            history = list(session.messages)
            await loop._dispatch(stale)
            assert provider.request_count == requests
            assert session.messages == history
        else:
            pending = asyncio.Queue()
            await pending.put(stale)
            runtime = LLMRuntime.capture(provider, "harness-model", context_window_tokens=128_000)
            await loop._run_agent_loop(
                [{"role": "user", "content": "fresh request"}],
                runtime=runtime,
                session=session,
                pending_queue=pending,
            )
            assert not any(
                "STALE_CHILD_MARKER" in str(m) for batch in provider.request_messages for m in batch
            )
    finally:
        await loop.aclose()


@pytest.mark.parametrize("wait", [True, False], ids=["inline", "background"])
async def test_child_keeps_workspace_boundary_after_approval(tmp_path: Path, wait: bool):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.txt"
    provider = DelegatingProvider(path=str(outside), wait=wait)

    async def approve(_request):
        return ToolApprovalResult.approve()

    loop = _loop(project, provider, tool_approval_callback=approve)
    try:
        await _run(loop, provider)
        assert not outside.exists()
        observations = [
            m["content"] for m in provider.child.request_messages[-1] if m["role"] == "tool"
        ]
        assert any("outside allowed directory" in str(value) for value in observations)
    finally:
        await loop.aclose()


async def test_completed_result_survives_other_session_stop_and_is_consumed_once(tmp_path: Path):
    provider = ScriptedProvider([_done(), _done()])
    loop = _loop(tmp_path, provider)
    key = "websocket:one"
    session = loop.sessions.get_or_create(key)
    session.add_message("user", "background request")
    loop.sessions.save(session)
    try:
        await loop.subagents._announce_result(
            "valid-child",
            "valid",
            "task",
            "VALID_CHILD_MARKER",
            {"channel": "websocket", "chat_id": "one", "session_key": key},
            "ok",
        )
        result = await loop.bus.consume_inbound()
        await loop._cancel_active_tasks("websocket:other")
        await loop._dispatch(result)
        assert provider.request_count == 1
        assert any("VALID_CHILD_MARKER" in str(m) for m in provider.request_messages[0])
        snapshot = list(session.messages)
        await loop._dispatch(result)
        assert provider.request_count == 1
        assert session.messages == snapshot
    finally:
        await loop.aclose()


async def test_result_is_rechecked_after_waiting_for_session_lock(tmp_path: Path):
    provider = ScriptedProvider([_done()])
    loop = _loop(tmp_path, provider)
    key = "websocket:one"
    dispatch = None
    try:
        await loop.subagents._announce_result(
            "queued-child",
            "queued",
            "task",
            "STALE_CHILD_MARKER",
            {"channel": "websocket", "chat_id": "one", "session_key": key},
            "ok",
        )
        result = await loop.bus.consume_inbound()
        async with loop._get_session_lock(key):
            dispatch = asyncio.create_task(loop._dispatch(result))
            await asyncio.sleep(0)
            assert not dispatch.done()
            await loop.subagents.cancel_by_session(key)
        await asyncio.wait_for(dispatch, timeout=5)
        assert provider.request_count == 0
        assert loop.bus.outbound_size == 0
    finally:
        if dispatch is not None:
            dispatch.cancel()
            await asyncio.gather(dispatch, return_exceptions=True)
        await loop.aclose()


async def test_custom_approval_capabilities_are_inherited(tmp_path: Path):
    async def unexpected(request):
        pytest.fail(f"unexpected approval for excluded capability: {request.name}")

    provider = DelegatingProvider()
    loop = _loop(
        tmp_path,
        provider,
        tool_approval_callback=unexpected,
        approval_capabilities=frozenset({"network"}),
    )
    try:
        await _run(loop, provider)
        assert (tmp_path / "canary.txt").read_text(encoding="utf-8") == "canary"
    finally:
        await loop.aclose()


@pytest.mark.parametrize("approved", [False, True], ids=["denied", "approved"])
async def test_full_access_child_still_confirms_unknown_side_effect_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    approved: bool,
):
    attempts = []
    requests: list[ToolApprovalRequest] = []

    def uncertain_write(_arguments):
        attempts.append("attempt")
        return ToolResult.error("remote system timed out") if len(attempts) == 1 else "accepted"

    async def confirm(request):
        requests.append(request)
        return ToolApprovalResult.approve() if approved else ToolApprovalResult.deny()

    provider = DelegatingProvider(operation=_call("mutate"))
    retry = _call("mutate")
    retry.tool_calls[0].id = "retry-mutate"
    provider.child = ScriptedProvider([_call("mutate"), retry, _done()])
    loop = _loop(tmp_path, provider, tool_approval_callback=confirm)
    build_tools = loop.subagents._build_tools

    def with_uncertain_tool(*args, **kwargs):
        tools = build_tools(*args, **kwargs)
        tools.register(
            HarnessTool(
                "mutate",
                "Simulate an external write with a lost acknowledgement.",
                {"type": "object", "properties": {}},
                uncertain_write,
                read_only=False,
                capabilities=("write",),
            )
        )
        return tools

    monkeypatch.setattr(loop.subagents, "_build_tools", with_uncertain_tool)
    try:
        await _run(loop, provider, mode="full")
        assert len(requests) == 1
        assert requests[0].recovery_required is True
        assert requests[0].chat_id == "one"
        assert len(attempts) == (2 if approved else 1)
    finally:
        await loop.aclose()


async def test_sdk_direct_call_keeps_configured_denials_without_ui_approval(tmp_path: Path):
    (tmp_path / "private.txt").write_text("private canary", encoding="utf-8")
    provider = DelegatingProvider(operation=_call("read_file", path="private.txt"))

    async def unexpected(request):
        pytest.fail(f"CLI/SDK unexpectedly requested UI approval: {request.name}")

    loop = _loop(
        tmp_path,
        provider,
        tool_approval_callback=unexpected,
        tools_config=ToolsConfig(denied_capabilities=["read"]),
    )
    try:
        result = await loop.process_direct("parent", session_key="cli:policy", ephemeral=True)
        assert result is not None
        observations = [
            str(m["content"]) for m in provider.child.request_messages[-1] if m["role"] == "tool"
        ]
        assert any("denied capabilities: read" in value for value in observations)
        assert all("private canary" not in value for value in observations)
    finally:
        await loop.aclose()


async def test_standalone_subagent_manager_applies_configured_denials(tmp_path: Path):
    (tmp_path / "private.txt").write_text("private canary", encoding="utf-8")
    provider = ScriptedProvider([_call("read_file", path="private.txt"), _done()])
    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=4096,
        tools_config=ToolsConfig(denied_capabilities=["read"]),
    )
    try:
        await manager.run_inline(
            "read",
            runtime=LLMRuntime.capture(provider, "harness-model", context_window_tokens=128_000),
        )
        observations = [
            str(m["content"]) for m in provider.request_messages[-1] if m["role"] == "tool"
        ]
        assert any("denied capabilities: read" in value for value in observations)
        assert all("private canary" not in value for value in observations)
    finally:
        await manager.close()


async def test_direct_runner_delegates_its_actual_policy_and_restores_context(tmp_path: Path):
    (tmp_path / "private.txt").write_text("private canary", encoding="utf-8")
    provider = DelegatingProvider(operation=_call("read_file", path="private.txt"))
    runtime = LLMRuntime.capture(provider, "harness-model", context_window_tokens=128_000)
    manager = SubagentManager(workspace=tmp_path, bus=MessageBus(), max_tool_result_chars=4096)
    tools = ToolRegistry()
    tools.register(SpawnTool(manager))
    request = RequestContext(channel="sdk", chat_id="one", session_key="sdk:one", runtime=runtime)
    try:
        with request_context(request):
            result = await AgentRunner().run(
                AgentRunSpec(
                    initial_messages=[{"role": "user", "content": "parent"}],
                    tools=tools,
                    runtime=runtime,
                    max_iterations=4,
                    max_tool_result_chars=4096,
                    denied_tool_capabilities=frozenset({"read"}),
                    channel="sdk",
                    chat_id="one",
                    session_key="sdk:one",
                )
            )
            assert current_request_context() is request
        assert result.error is None
        observations = [
            str(m["content"]) for m in provider.child.request_messages[-1] if m["role"] == "tool"
        ]
        assert any("denied capabilities: read" in value for value in observations)
        assert all("private canary" not in value for value in observations)
    finally:
        await manager.close()


@pytest.mark.parametrize("wait", [True, False], ids=["inline", "background"])
@pytest.mark.parametrize("approved", [False, True], ids=["denied", "approved"])
async def test_child_write_requires_its_own_approval(tmp_path: Path, wait: bool, approved: bool):
    requests: list[ToolApprovalRequest] = []

    async def approve(request):
        requests.append(request)
        if request.name == "spawn" or approved:
            return ToolApprovalResult.approve()
        return ToolApprovalResult.deny("canary denied")

    provider = DelegatingProvider(wait=wait)
    loop = _loop(tmp_path, provider, tool_approval_callback=approve)
    try:
        await _run(loop, provider)
        assert (tmp_path / "canary.txt").exists() is approved
        writes = [r for r in requests if r.name == "write_file"]
        assert len(writes) == 1
        assert writes[0].channel == "websocket"
        assert writes[0].chat_id == "one"
        assert writes[0].session_key == "websocket:one"
    finally:
        await loop.aclose()


@pytest.mark.parametrize("wait", [True, False], ids=["inline", "background"])
async def test_full_access_keeps_delegated_writes_usable(tmp_path: Path, wait: bool):
    async def unexpected(request):
        pytest.fail(f"full access unexpectedly requested approval: {request.name}")

    provider = DelegatingProvider(wait=wait)
    loop = _loop(tmp_path, provider, tool_approval_callback=unexpected)
    try:
        await _run(loop, provider, mode="full")
        assert (tmp_path / "canary.txt").read_text(encoding="utf-8") == "canary"
    finally:
        await loop.aclose()
