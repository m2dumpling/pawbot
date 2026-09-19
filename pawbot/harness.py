"""Deterministic Agent Harness Benchmark for pawbot.

The harness exercises the real :class:`AgentRunner` with scripted provider
responses and small in-memory tools.  It deliberately does not call a real
provider or touch the user's workspace.  That makes the benchmark suitable
for local development and CI, while still covering the failure boundaries
that matter in an agent loop.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from pawbot.agent.approval import (
    DEFAULT_APPROVAL_CAPABILITIES,
    ToolApprovalCallback,
    ToolApprovalRequest,
    ToolApprovalResult,
)
from pawbot.agent.budget import TurnBudget
from pawbot.agent.evaluation import TaskContract, TaskEvaluationStatus, evaluate_task
from pawbot.agent.runner import AgentRunner, AgentRunResult, AgentRunSpec
from pawbot.agent.tools.base import Tool, ToolResult
from pawbot.agent.tools.registry import ToolRegistry
from pawbot.agent.turn.outcome import TurnOutcome
from pawbot.providers.base import (
    GenerationSettings,
    LLMProvider,
    LLMResponse,
    LLMUsage,
    ToolCallRequest,
)
from pawbot.utils.llm_runtime import LLMRuntime
from pawbot.utils.provenance import collect_provenance

HARNESS_SCHEMA_VERSION = 1
HARNESS_NAME = "pawbot-agent-harness"
HARNESS_VERSION = "1.0"

HarnessExecutionStatus = Literal["completed", "error", "cancelled"]
HarnessResultStatus = Literal["passed", "failed"]
ToolAction = Callable[[dict[str, Any]], Any | Awaitable[Any]]


class HarnessTool(Tool):
    """Small in-memory Tool implementation used by deterministic scenarios."""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        action: ToolAction,
        *,
        read_only: bool = True,
        capabilities: Iterable[str] = ("read",),
    ) -> None:
        self._name = name
        self._description = description
        self._parameters = deepcopy(parameters)
        self._action = action
        self._read_only = read_only
        self._capabilities = frozenset(str(value) for value in capabilities)

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return deepcopy(self._parameters)

    @property
    def read_only(self) -> bool:
        return self._read_only

    @property
    def capabilities(self) -> frozenset[str]:
        return self._capabilities

    async def execute(self, **kwargs: Any) -> Any:
        value = self._action(dict(kwargs))
        if inspect.isawaitable(value):
            return await value
        return value


class ScriptedProvider(LLMProvider):
    """Provider that returns a fixed response sequence without network I/O."""

    def __init__(
        self,
        responses: Sequence[LLMResponse],
        *,
        model: str = "harness-model",
        delay_s: float = 0.0,
    ) -> None:
        super().__init__(provider_name="harness")
        self._responses = list(responses)
        self._model = model
        self.delay_s = max(0.0, delay_s)
        self.request_count = 0
        self.request_messages: list[list[dict[str, Any]]] = []
        self.generation = GenerationSettings(max_tokens=1024, temperature=0.0)

    def get_default_model(self) -> str:
        return self._model

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        del tools, model, max_tokens, temperature, reasoning_effort, tool_choice
        return await self._next(messages)

    async def chat_with_retry(self, *args: Any, **kwargs: Any) -> LLMResponse:
        del args
        messages = kwargs.get("messages")
        if not isinstance(messages, list):
            messages = []
        return await self._next(cast(list[dict[str, Any]], messages))

    async def chat_stream_with_retry(self, *args: Any, **kwargs: Any) -> LLMResponse:
        del args
        messages = kwargs.get("messages")
        if not isinstance(messages, list):
            messages = []
        response = await self._next(cast(list[dict[str, Any]], messages))
        on_thinking_delta = kwargs.get("on_thinking_delta")
        if response.reasoning_content and callable(on_thinking_delta):
            value = on_thinking_delta(response.reasoning_content)
            if inspect.isawaitable(value):
                await value
        on_content_delta = kwargs.get("on_content_delta")
        if response.content and callable(on_content_delta):
            value = on_content_delta(response.content)
            if inspect.isawaitable(value):
                await value
        return response

    async def _next(self, messages: list[dict[str, Any]]) -> LLMResponse:
        self.request_count += 1
        self.request_messages.append(deepcopy(messages))
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if not self._responses:
            raise RuntimeError("harness provider response script exhausted")
        return deepcopy(self._responses.pop(0))


@dataclass(slots=True)
class HarnessScenario:
    """One deterministic execution setup consumed by :func:`run_case`."""

    initial_messages: list[dict[str, Any]]
    provider: LLMProvider
    tools: ToolRegistry
    model: str = "harness-model"
    session_key: str = "harness:case"
    context_window_tokens: int = 128_000
    max_iterations: int = 8
    max_tool_result_chars: int = 4_096
    max_tool_calls: int | None = None
    max_turn_seconds: float | None = None
    llm_timeout_s: float | None = None
    cancel_after_s: float | None = None
    tool_approval_callback: ToolApprovalCallback | None = None
    approval_capabilities: frozenset[str] = DEFAULT_APPROVAL_CAPABILITIES
    task_contract: TaskContract | None = None

    def build_spec(self) -> AgentRunSpec:
        """Build a real runner spec with explicit resource accounting."""
        return AgentRunSpec(
            initial_messages=deepcopy(self.initial_messages),
            tools=self.tools,
            runtime=LLMRuntime.capture(
                self.provider,
                self.model,
                context_window_tokens=self.context_window_tokens,
            ),
            max_iterations=self.max_iterations,
            max_tool_result_chars=self.max_tool_result_chars,
            concurrent_tools=False,
            workspace=Path.cwd(),
            session_key=self.session_key,
            budget=TurnBudget(
                max_iterations=self.max_iterations,
                max_tool_calls=self.max_tool_calls,
                max_wall_seconds=self.max_turn_seconds,
            ),
            llm_timeout_s=self.llm_timeout_s,
            tool_approval_callback=self.tool_approval_callback,
            approval_capabilities=self.approval_capabilities,
            task_contract=self.task_contract,
            channel="harness",
            chat_id=self.session_key,
        )


@dataclass(frozen=True, slots=True)
class HarnessExpectation:
    """Observable contract a benchmark case must satisfy."""

    execution_status: HarnessExecutionStatus = "completed"
    stop_reason: str | None = None
    final_content_equals: str | None = None
    final_content_contains: tuple[str, ...] = ()
    tool_names: tuple[str, ...] = ()
    tool_statuses: tuple[str, ...] = ()
    model_requests: int | None = None
    tool_attempts: int | None = None
    require_error: bool | None = None
    task_completed: bool = True
    task_required_tools: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HarnessCase:
    """Named benchmark case with a fresh scenario factory."""

    id: str
    title: str
    category: str
    description: str
    factory: Callable[[], HarnessScenario]
    expectation: HarnessExpectation
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HarnessResult:
    """One case result, including both execution telemetry and assertions."""

    case_id: str
    title: str
    category: str
    status: HarnessResultStatus
    trajectory_status: HarnessResultStatus
    task_status: TaskEvaluationStatus
    task_completed: bool
    execution_status: HarnessExecutionStatus
    elapsed_ms: int
    model_requests: int
    tool_attempts: int
    tool_failures: int
    tool_names: tuple[str, ...]
    tool_statuses: tuple[str, ...]
    successful_tool_names: tuple[str, ...]
    stop_reason: str | None
    final_content: str | None
    error: str | None
    trajectory_failures: tuple[str, ...] = ()
    task_failures: tuple[str, ...] = ()
    task_reason: str | None = None
    failures: tuple[str, ...] = ()
    outcome: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "title": self.title,
            "category": self.category,
            "status": self.status,
            "trajectory": {
                "status": self.trajectory_status,
                "failures": list(self.trajectory_failures),
            },
            "task": {
                "status": self.task_status,
                "completed": self.task_completed,
                "reason": self.task_reason,
                "failures": list(self.task_failures),
            },
            "execution_status": self.execution_status,
            "outcome": self.outcome,
            "elapsed_ms": self.elapsed_ms,
            "model_requests": self.model_requests,
            "tool_attempts": self.tool_attempts,
            "tool_failures": self.tool_failures,
            "tool_names": list(self.tool_names),
            "tool_statuses": list(self.tool_statuses),
            "successful_tool_names": list(self.successful_tool_names),
            "stop_reason": self.stop_reason,
            "final_content": self.final_content,
            "error": self.error,
            "failures": list(self.failures),
        }


@dataclass(frozen=True, slots=True)
class HarnessReport:
    """Aggregate benchmark output suitable for CLI, CI, or later WebUI use."""

    results: tuple[HarnessResult, ...]
    elapsed_ms: int
    schema_version: int = HARNESS_SCHEMA_VERSION
    name: str = HARNESS_NAME
    version: str = HARNESS_VERSION
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def passed_count(self) -> int:
        return sum(result.status == "passed" for result in self.results)

    @property
    def failed_count(self) -> int:
        return sum(result.status == "failed" for result in self.results)

    @property
    def passed(self) -> bool:
        return bool(self.results) and self.failed_count == 0

    @property
    def task_passed_count(self) -> int:
        return sum(result.task_status == "passed" for result in self.results)

    @property
    def task_not_evaluable_count(self) -> int:
        return sum(result.task_status == "not_evaluable" for result in self.results)

    @property
    def task_failed_count(self) -> int:
        return sum(result.task_status == "failed" for result in self.results)

    @property
    def task_evaluable_count(self) -> int:
        return self.task_passed_count + self.task_failed_count

    @property
    def task_pass_rate(self) -> float | None:
        if self.task_evaluable_count == 0:
            return None
        return self.task_passed_count / self.task_evaluable_count

    @property
    def model_request_count(self) -> int:
        return sum(result.model_requests for result in self.results)

    @property
    def tool_attempt_count(self) -> int:
        return sum(result.tool_attempts for result in self.results)

    @property
    def tool_failure_count(self) -> int:
        return sum(result.tool_failures for result in self.results)

    def to_dict(self) -> dict[str, Any]:
        categories: dict[str, dict[str, int]] = {}
        for result in self.results:
            row = categories.setdefault(result.category, {"total": 0, "passed": 0, "failed": 0})
            row["total"] += 1
            row[result.status] += 1
        return {
            "schema_version": self.schema_version,
            "benchmark": self.name,
            "version": self.version,
            "metadata": deepcopy(self.metadata),
            "summary": {
                "total": len(self.results),
                "passed": self.passed_count,
                "failed": self.failed_count,
                "status": "passed" if self.passed else "failed",
                "elapsed_ms": self.elapsed_ms,
                "trajectory_passed": sum(
                    result.trajectory_status == "passed" for result in self.results
                ),
                "task_passed": self.task_passed_count,
                "task_failed": self.task_failed_count,
                "task_evaluable": self.task_evaluable_count,
                "task_not_evaluable": self.task_not_evaluable_count,
                "task_pass_rate": self.task_pass_rate,
                "model_requests": self.model_request_count,
                "tool_attempts": self.tool_attempt_count,
                "tool_failures": self.tool_failure_count,
                "categories": categories,
            },
            "results": [result.to_dict() for result in self.results],
        }


def _usage(input_tokens: int = 20, output_tokens: int = 5) -> LLMUsage:
    return LLMUsage.reported(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )


def _tool_response(call_id: str, name: str, arguments: dict[str, Any]) -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[ToolCallRequest(id=call_id, name=name, arguments=arguments)],
        finish_reason="tool_calls",
        usage=_usage(),
    )


def _final_response(content: str) -> LLMResponse:
    return LLMResponse(content=content, finish_reason="stop", usage=_usage())


def _echo_tool() -> HarnessTool:
    return HarnessTool(
        "echo",
        "Return the supplied text.",
        {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        lambda arguments: f"echo:{arguments['text']}",
    )


def _registry(*tools: HarnessTool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def _basic_tool_call() -> HarnessScenario:
    provider = ScriptedProvider([
        _tool_response("echo-1", "echo", {"text": "hello"}),
        _final_response("echo:hello"),
    ])
    return HarnessScenario(
        initial_messages=[{"role": "user", "content": "Say hello using the echo tool."}],
        provider=provider,
        tools=_registry(_echo_tool()),
        session_key="harness:basic-tool-call",
    )


def _tool_failure_recovery() -> HarnessScenario:
    def unstable_lookup(_arguments: dict[str, Any]) -> ToolResult:
        return ToolResult.error("temporary lookup failed")

    fallback = HarnessTool(
        "fallback_lookup",
        "Return a deterministic fallback observation.",
        {"type": "object", "properties": {}},
        lambda _arguments: "fallback data",
    )
    unstable = HarnessTool(
        "unstable_lookup",
        "A lookup that fails once so recovery can be tested.",
        {"type": "object", "properties": {}},
        unstable_lookup,
    )
    provider = ScriptedProvider([
        _tool_response("unstable-1", "unstable_lookup", {}),
        _tool_response("fallback-1", "fallback_lookup", {}),
        _final_response("recovered: fallback data"),
    ])
    return HarnessScenario(
        initial_messages=[{"role": "user", "content": "Look up the value and recover if needed."}],
        provider=provider,
        tools=_registry(unstable, fallback),
        session_key="harness:tool-failure-recovery",
    )


def _workspace_change_and_verify() -> HarnessScenario:
    state: dict[str, str] = {}

    def write_note(arguments: dict[str, Any]) -> str:
        path = str(arguments["path"])
        content = str(arguments["content"])
        state[path] = content
        return f"saved:{path}"

    def read_note(arguments: dict[str, Any]) -> str:
        path = str(arguments["path"])
        return state.get(path, "missing")

    write_tool = HarnessTool(
        "write_note",
        "Write a note into the in-memory task workspace.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
        write_note,
        read_only=False,
        capabilities=("write",),
    )
    read_tool = HarnessTool(
        "read_note",
        "Read a note from the in-memory task workspace.",
        {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        read_note,
    )
    return HarnessScenario(
        initial_messages=[
            {"role": "user", "content": "Write the note and verify its contents."}
        ],
        provider=ScriptedProvider([
            _tool_response(
                "write-1",
                "write_note",
                {"path": "release-check.txt", "content": "ready"},
            ),
            _tool_response("read-1", "read_note", {"path": "release-check.txt"}),
            _final_response("verified: release-check.txt contains ready"),
        ]),
        tools=_registry(write_tool, read_tool),
        session_key="harness:workspace-change-and-verify",
    )


def _investigate_and_summarize() -> HarnessScenario:
    incident = {
        "id": "INC-42",
        "status": "open",
        "summary": "provider timeout affects release smoke test",
    }

    def search_incidents(_arguments: dict[str, Any]) -> str:
        return incident["id"]

    def inspect_incident(arguments: dict[str, Any]) -> dict[str, str]:
        if str(arguments["incident_id"]) != incident["id"]:
            return {"error": "incident not found"}
        return dict(incident)

    search_tool = HarnessTool(
        "search_incidents",
        "Find the relevant incident ID.",
        {"type": "object", "properties": {}},
        search_incidents,
    )
    inspect_tool = HarnessTool(
        "inspect_incident",
        "Read the incident status and impact.",
        {
            "type": "object",
            "properties": {"incident_id": {"type": "string"}},
            "required": ["incident_id"],
        },
        inspect_incident,
    )
    return HarnessScenario(
        initial_messages=[
            {"role": "user", "content": "Find the release blocker and summarize it."}
        ],
        provider=ScriptedProvider([
            _tool_response("search-1", "search_incidents", {}),
            _tool_response("inspect-1", "inspect_incident", {"incident_id": "INC-42"}),
            _final_response("release blocker: INC-42 is open and affects the smoke test"),
        ]),
        tools=_registry(search_tool, inspect_tool),
        session_key="harness:investigate-and-summarize",
    )


def _approval_gated_tool() -> HarnessScenario:
    write_tool = HarnessTool(
        "write_value",
        "Write a value to an external system.",
        {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        lambda _arguments: "written",
        read_only=False,
        capabilities=("write",),
    )

    async def approve(_request: ToolApprovalRequest) -> ToolApprovalResult:
        return ToolApprovalResult.approve("benchmark approval")

    return HarnessScenario(
        initial_messages=[{"role": "user", "content": "Write the value after approval."}],
        provider=ScriptedProvider([
            _tool_response("write-1", "write_value", {"value": "x"}),
            _final_response("approved write"),
        ]),
        tools=_registry(write_tool),
        session_key="harness:approval-gated-tool",
        tool_approval_callback=approve,
    )


def _provider_error() -> HarnessScenario:
    provider = ScriptedProvider([
        LLMResponse(
            content="provider unavailable",
            finish_reason="error",
            error_kind="connection",
            error_type="provider_unavailable",
            error_code="503",
            error_should_retry=False,
        ),
    ])
    return HarnessScenario(
        initial_messages=[{"role": "user", "content": "Make one model request."}],
        provider=provider,
        tools=ToolRegistry(),
        session_key="harness:provider-error",
    )


def _llm_timeout() -> HarnessScenario:
    return HarnessScenario(
        initial_messages=[{"role": "user", "content": "This request should time out."}],
        provider=ScriptedProvider([_final_response("too late")], delay_s=0.05),
        tools=ToolRegistry(),
        session_key="harness:llm-timeout",
        llm_timeout_s=0.005,
    )


def _cancelled_turn() -> HarnessScenario:
    return HarnessScenario(
        initial_messages=[{"role": "user", "content": "Cancel this in-flight request."}],
        provider=ScriptedProvider([_final_response("cancelled")], delay_s=0.1),
        tools=ToolRegistry(),
        session_key="harness:cancelled-turn",
        cancel_after_s=0.005,
    )


def _turn_budget() -> HarnessScenario:
    provider = ScriptedProvider([
        LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(id="echo-1", name="echo", arguments={"text": "one"}),
                ToolCallRequest(id="echo-2", name="echo", arguments={"text": "two"}),
            ],
            finish_reason="tool_calls",
            usage=_usage(),
        ),
    ])
    return HarnessScenario(
        initial_messages=[{"role": "user", "content": "Use no more than one tool call."}],
        provider=provider,
        tools=_registry(_echo_tool()),
        session_key="harness:turn-budget",
        max_tool_calls=1,
    )


def builtin_cases() -> tuple[HarnessCase, ...]:
    """Return the stable benchmark catalog in display and execution order."""
    return (
        HarnessCase(
            id="basic-tool-call",
            title="Normal tool execution",
            category="success",
            description="A model chooses a tool and then returns a final answer.",
            factory=_basic_tool_call,
            expectation=HarnessExpectation(
                stop_reason="completed",
                final_content_equals="echo:hello",
                tool_names=("echo",),
                tool_statuses=("ok",),
                model_requests=2,
                tool_attempts=1,
                task_required_tools=("echo",),
            ),
            tags=("react", "tool", "success"),
        ),
        HarnessCase(
            id="tool-failure-recovery",
            title="Tool failure and recovery",
            category="recovery",
            description="The first tool fails; the model receives the error and uses a fallback.",
            factory=_tool_failure_recovery,
            expectation=HarnessExpectation(
                stop_reason="completed",
                final_content_equals="recovered: fallback data",
                tool_names=("unstable_lookup", "fallback_lookup"),
                tool_statuses=("error", "ok"),
                model_requests=3,
                tool_attempts=2,
                require_error=False,
                task_required_tools=("fallback_lookup",),
            ),
            tags=("react", "tool-error", "recovery"),
        ),
        HarnessCase(
            id="workspace-change-and-verify",
            title="Task fixture: change and verify",
            category="task",
            description="A write is followed by a read-back verification in an in-memory workspace.",
            factory=_workspace_change_and_verify,
            expectation=HarnessExpectation(
                stop_reason="completed",
                final_content_equals="verified: release-check.txt contains ready",
                tool_names=("write_note", "read_note"),
                tool_statuses=("ok", "ok"),
                model_requests=3,
                tool_attempts=2,
                task_required_tools=("write_note", "read_note"),
            ),
            tags=("task-fixture", "write", "verification"),
        ),
        HarnessCase(
            id="investigate-and-summarize",
            title="Task fixture: investigate and summarize",
            category="task",
            description="The model searches a deterministic incident and summarizes the inspected result.",
            factory=_investigate_and_summarize,
            expectation=HarnessExpectation(
                stop_reason="completed",
                final_content_contains=("INC-42", "open"),
                tool_names=("search_incidents", "inspect_incident"),
                tool_statuses=("ok", "ok"),
                model_requests=3,
                tool_attempts=2,
                task_required_tools=("search_incidents", "inspect_incident"),
            ),
            tags=("task-fixture", "investigation", "summary"),
        ),
        HarnessCase(
            id="approval-gated-tool",
            title="Human approval before side effect",
            category="human-in-the-loop",
            description="A write-capable Tool waits for an approval decision before it runs.",
            factory=_approval_gated_tool,
            expectation=HarnessExpectation(
                stop_reason="completed",
                final_content_equals="approved write",
                tool_names=("write_value",),
                tool_statuses=("ok",),
                model_requests=2,
                tool_attempts=1,
                task_required_tools=("write_value",),
            ),
            tags=("approval", "write", "human-in-the-loop"),
        ),
        HarnessCase(
            id="provider-error",
            title="Provider error classification",
            category="provider",
            description="A structured provider failure becomes a visible completed turn error.",
            factory=_provider_error,
            expectation=HarnessExpectation(
                stop_reason="error",
                final_content_contains=("provider unavailable",),
                model_requests=1,
                tool_attempts=0,
                require_error=True,
                task_completed=False,
            ),
            tags=("llm", "provider-error"),
        ),
        HarnessCase(
            id="llm-timeout",
            title="LLM timeout",
            category="timeout",
            description="A slow provider is stopped by the request timeout boundary.",
            factory=_llm_timeout,
            expectation=HarnessExpectation(
                stop_reason="error",
                final_content_contains=("timed out",),
                model_requests=1,
                tool_attempts=0,
                require_error=True,
                task_completed=False,
            ),
            tags=("llm", "timeout"),
        ),
        HarnessCase(
            id="cancelled-turn",
            title="User cancellation",
            category="cancellation",
            description="An in-flight request is cancelled and does not become a false success.",
            factory=_cancelled_turn,
            expectation=HarnessExpectation(
                execution_status="cancelled",
                stop_reason="cancelled",
                model_requests=1,
                tool_attempts=0,
                task_completed=False,
            ),
            tags=("cancel", "in-flight"),
        ),
        HarnessCase(
            id="turn-budget",
            title="Turn budget boundary",
            category="governance",
            description="A response that exceeds the tool budget is blocked before execution.",
            factory=_turn_budget,
            expectation=HarnessExpectation(
                stop_reason="max_tool_calls",
                final_content_contains=("tool-call budget",),
                tool_names=("echo", "echo"),
                tool_statuses=("error", "error"),
                model_requests=1,
                tool_attempts=2,
                require_error=False,
                task_completed=False,
            ),
            tags=("budget", "side-effect-boundary"),
        ),
    )


REQUIRED_CASE_IDS = frozenset(case.id for case in builtin_cases())


def _benchmark_metadata(selected_cases: Sequence[str]) -> dict[str, Any]:
    return collect_provenance(
        root=Path.cwd(),
        extra={
            "benchmark": HARNESS_NAME,
            "benchmark_version": HARNESS_VERSION,
            "provider_mode": "scripted",
            "network": "disabled",
            "workspace_io": False,
            "selected_cases": list(selected_cases),
        },
    )


def _request_count(provider: LLMProvider) -> int:
    value = getattr(provider, "request_count", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _execution_fields(
    result: AgentRunResult | None,
    execution_status: HarnessExecutionStatus,
    exception_text: str | None,
) -> tuple[str | None, str | None, tuple[str, ...], tuple[str, ...], str | None]:
    if result is None:
        return (
            "cancelled" if execution_status == "cancelled" else "error",
            None,
            (),
            (),
            exception_text,
        )
    tool_names = tuple(str(event.get("name") or "") for event in result.tool_events)
    tool_statuses = tuple(str(event.get("status") or "") for event in result.tool_events)
    return (
        result.stop_reason,
        result.final_content,
        tool_names,
        tool_statuses,
        result.error or exception_text,
    )


def _assert_case(
    expectation: HarnessExpectation,
    *,
    execution_status: HarnessExecutionStatus,
    stop_reason: str | None,
    error: str | None,
    tool_names: tuple[str, ...],
    tool_statuses: tuple[str, ...],
    model_requests: int,
) -> list[str]:
    failures: list[str] = []
    if execution_status != expectation.execution_status:
        failures.append(
            f"execution status {execution_status!r} != {expectation.execution_status!r}"
        )
    if expectation.stop_reason is not None and stop_reason != expectation.stop_reason:
        failures.append(f"stop reason {stop_reason!r} != {expectation.stop_reason!r}")
    if expectation.tool_names and tool_names != expectation.tool_names:
        failures.append(f"tool names {tool_names!r} != {expectation.tool_names!r}")
    if expectation.tool_statuses and tool_statuses != expectation.tool_statuses:
        failures.append(f"tool statuses {tool_statuses!r} != {expectation.tool_statuses!r}")
    if expectation.model_requests is not None and model_requests != expectation.model_requests:
        failures.append(
            f"model requests {model_requests} != {expectation.model_requests}"
        )
    if expectation.tool_attempts is not None and len(tool_names) != expectation.tool_attempts:
        failures.append(f"tool attempts {len(tool_names)} != {expectation.tool_attempts}")
    if expectation.require_error is True and not error:
        failures.append("expected an execution error, but none was reported")
    if expectation.require_error is False and error:
        failures.append("did not expect a turn error")
    return failures


async def run_case(case: HarnessCase) -> HarnessResult:
    """Run one case through the real AgentRunner and evaluate its contract."""
    scenario = case.factory()
    scenario.task_contract = TaskContract(
        id=case.id,
        description=case.description,
        final_content_equals=case.expectation.final_content_equals,
        final_content_contains=case.expectation.final_content_contains,
        required_tools=case.expectation.task_required_tools,
    )
    started_at = time.perf_counter()
    task = asyncio.create_task(AgentRunner().run(scenario.build_spec()))
    execution_status: HarnessExecutionStatus = "completed"
    run_result: AgentRunResult | None = None
    exception_text: str | None = None
    try:
        if scenario.cancel_after_s is None:
            run_result = await task
        else:
            await asyncio.sleep(max(0.0, scenario.cancel_after_s))
            if not task.done():
                task.cancel()
            try:
                run_result = await task
            except asyncio.CancelledError:
                execution_status = "cancelled"
    except asyncio.CancelledError:
        execution_status = "cancelled"
    except Exception as exc:
        execution_status = "error"
        exception_text = f"{type(exc).__name__}: {exc}"
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except BaseException:
                pass

    elapsed_ms = max(0, round((time.perf_counter() - started_at) * 1000))
    stop_reason, final_content, tool_names, tool_statuses, error = _execution_fields(
        run_result,
        execution_status,
        exception_text,
    )
    model_requests = _request_count(scenario.provider)
    trajectory_failures = _assert_case(
        case.expectation,
        execution_status=execution_status,
        stop_reason=stop_reason,
        error=error,
        tool_names=tool_names,
        tool_statuses=tool_statuses,
        model_requests=model_requests,
    )
    task_evaluation = (
        run_result.task_evaluation
        if run_result is not None and run_result.task_evaluation is not None
        else evaluate_task(
            scenario.task_contract,
            run_result,
            execution_status=execution_status,
        )
    )
    task_failures = list(task_evaluation.failures)
    if case.expectation.task_completed and task_evaluation.status != "passed":
        task_failures.append(
            "task did not satisfy completion contract"
            if task_evaluation.reason is None
            else f"task not completed: {task_evaluation.reason}"
        )
    elif not case.expectation.task_completed and task_evaluation.completed:
        task_failures.append("task completed when the scenario expected a boundary")
    failures = [*trajectory_failures, *task_failures]
    if run_result is not None and run_result.outcome is not None:
        outcome = run_result.outcome.to_dict()
    else:
        outcome = TurnOutcome(
            execution_status=(
                "cancelled" if execution_status == "cancelled" else "failed"
            ),
            stop_reason=stop_reason,
            task_status=(
                task_evaluation.status
                if task_evaluation.status in {"passed", "failed", "not_evaluable"}
                else "not_evaluable"
            ),
            side_effect_status="unknown" if execution_status == "cancelled" else "not_applicable",
            recovery_status="resumable",
            error_code="TURN_CANCELLED" if execution_status == "cancelled" else "AGENT_RUN_ERROR",
            error_message=error,
        ).to_dict()
    tool_failures = sum(status != "ok" for status in tool_statuses)
    trajectory_status: HarnessResultStatus = (
        "passed" if not trajectory_failures else "failed"
    )
    return HarnessResult(
        case_id=case.id,
        title=case.title,
        category=case.category,
        status="passed" if not failures else "failed",
        trajectory_status=trajectory_status,
        task_status=task_evaluation.status,
        task_completed=task_evaluation.completed,
        execution_status=execution_status,
        elapsed_ms=elapsed_ms,
        model_requests=model_requests,
        tool_attempts=len(tool_names),
        tool_failures=tool_failures,
        tool_names=tool_names,
        tool_statuses=tool_statuses,
        successful_tool_names=tuple(run_result.tools_used) if run_result else (),
        stop_reason=stop_reason,
        final_content=final_content,
        error=error,
        trajectory_failures=tuple(trajectory_failures),
        task_failures=tuple(task_failures),
        task_reason=task_evaluation.reason,
        failures=tuple(failures),
        outcome=outcome,
    )


async def run_benchmark(
    *,
    case_ids: Sequence[str] | None = None,
    fail_fast: bool = False,
) -> HarnessReport:
    """Run the complete catalog, or a selected subset, in stable order."""
    catalog = {case.id: case for case in builtin_cases()}
    selected = list(catalog)
    if case_ids is not None:
        selected = [str(case_id) for case_id in case_ids]
        unknown = [case_id for case_id in selected if case_id not in catalog]
        if unknown:
            raise ValueError(f"unknown harness case(s): {', '.join(unknown)}")

    started_at = time.perf_counter()
    results: list[HarnessResult] = []
    for case_id in selected:
        result = await run_case(catalog[case_id])
        results.append(result)
        if fail_fast and result.status == "failed":
            break
    elapsed_ms = max(0, round((time.perf_counter() - started_at) * 1000))
    return HarnessReport(
        results=tuple(results),
        elapsed_ms=elapsed_ms,
        metadata=_benchmark_metadata([result.case_id for result in results]),
    )


def run_benchmark_sync(
    *,
    case_ids: Sequence[str] | None = None,
    fail_fast: bool = False,
) -> HarnessReport:
    """Synchronous convenience wrapper for scripts and shell entrypoints."""
    return asyncio.run(run_benchmark(case_ids=case_ids, fail_fast=fail_fast))


__all__ = [
    "HARNESS_NAME",
    "HARNESS_SCHEMA_VERSION",
    "HARNESS_VERSION",
    "REQUIRED_CASE_IDS",
    "HarnessCase",
    "HarnessExpectation",
    "HarnessReport",
    "HarnessResult",
    "HarnessScenario",
    "HarnessTool",
    "ScriptedProvider",
    "TaskContract",
    "builtin_cases",
    "run_benchmark",
    "run_benchmark_sync",
    "run_case",
]
