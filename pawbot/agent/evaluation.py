"""Task-level evaluation kept separate from Agent trajectory checks."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from pawbot.agent.runner import AgentRunResult

TaskEvaluationStatus = Literal["passed", "failed", "not_evaluable"]
TaskAssertionStatus = Literal["passed", "failed", "not_evaluable"]
TaskAssertionKind = str
TASK_CONTRACT_METADATA_KEY = "_task_contract"
TaskValidator = Callable[["AgentRunResult", Path | None], bool | str | None]

_MAX_CONTRACT_ID_CHARS = 120
_MAX_CONTRACT_DESCRIPTION_CHARS = 1_000
_MAX_CONTRACT_ASSERTIONS = 64
_MAX_CONTRACT_VALUE_CHARS = 1_000
_MAX_VERIFICATION_FILE_BYTES = 4 * 1024 * 1024
_MAX_ORDER_CONSTRAINTS = 64
_MAX_EVIDENCE_URLS = 64


def _bounded_string(value: Any, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


def _bounded_string_tuple(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    values: list[str] = []
    for item in cast(list[Any], raw)[:_MAX_CONTRACT_ASSERTIONS]:
        value = _bounded_string(item, limit=_MAX_CONTRACT_VALUE_CHARS)
        if value is not None:
            values.append(value)
    return tuple(values)


@dataclass(frozen=True, slots=True)
class TaskAssertion:
    """One declarative task predicate evaluated against an Agent result."""

    id: str
    kind: TaskAssertionKind
    tool: str | None = None
    value: str | None = None
    min_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "tool": self.tool,
            "value": self.value,
            "min_count": self.min_count,
        }


@dataclass(frozen=True, slots=True)
class TaskOrderConstraint:
    """A partial-order edge; unrelated tool calls may still run in parallel."""

    before: str
    after: str

    def to_dict(self) -> dict[str, str]:
        return {"before": self.before, "after": self.after}


@dataclass(frozen=True, slots=True)
class TaskAssertionResult:
    """Explain one hard task assertion without exposing private model reasoning."""

    id: str
    kind: str
    status: TaskAssertionStatus
    reason: str
    evidence_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
        }


def _parse_assertions(raw: Any) -> tuple[TaskAssertion, ...]:
    if not isinstance(raw, list):
        return ()
    assertions: list[TaskAssertion] = []
    seen: set[str] = set()
    for item in cast(list[Any], raw)[:_MAX_CONTRACT_ASSERTIONS]:
        if not isinstance(item, dict):
            continue
        mapping = cast(dict[str, Any], item)
        assertion_id = _bounded_string(mapping.get("id"), limit=120)
        kind = _bounded_string(mapping.get("kind"), limit=120)
        if assertion_id is None or kind is None or assertion_id in seen:
            continue
        tool = _bounded_string(mapping.get("tool"), limit=_MAX_CONTRACT_VALUE_CHARS)
        assertion_value = _bounded_string(
            mapping.get("value"),
            limit=_MAX_CONTRACT_VALUE_CHARS,
        )
        min_count = mapping.get("min_count")
        if isinstance(min_count, bool) or not isinstance(min_count, int):
            min_count = None
        else:
            min_count = max(0, min_count)
        assertions.append(TaskAssertion(assertion_id, kind, tool, assertion_value, min_count))
        seen.add(assertion_id)
    return tuple(assertions)


def _parse_order_constraints(raw: Any) -> tuple[TaskOrderConstraint, ...]:
    if not isinstance(raw, list):
        return ()
    constraints: list[TaskOrderConstraint] = []
    for item in cast(list[Any], raw)[:_MAX_ORDER_CONSTRAINTS]:
        before: Any = None
        after: Any = None
        if isinstance(item, dict):
            mapping = cast(dict[str, Any], item)
            before = mapping.get("before")
            after = mapping.get("after")
        elif isinstance(item, list):
            pair = cast(list[Any], item)
            if len(pair) != 2:
                continue
            before, after = pair
        before_value = _bounded_string(before, limit=120)
        after_value = _bounded_string(after, limit=120)
        if before_value and after_value and before_value != after_value:
            constraints.append(TaskOrderConstraint(before_value, after_value))
    return tuple(constraints)


@dataclass(frozen=True, slots=True)
class TaskContract:
    """Declarative assertions for the outcome of one Agent task."""

    id: str
    description: str = ""
    final_content_equals: str | None = None
    final_content_contains: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    required_files: tuple[str, ...] = ()
    file_contains: tuple[tuple[str, str], ...] = ()
    tool_result_contains: tuple[tuple[str, str], ...] = ()
    must: tuple[TaskAssertion, ...] = ()
    must_not: tuple[TaskAssertion, ...] = ()
    ordered: tuple[TaskOrderConstraint, ...] = ()
    validator: TaskValidator | None = field(default=None, repr=False, compare=False)
    validator_required: bool = field(default=False, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable contract carried by a checkpoint or SDK."""
        return {
            "id": self.id,
            "description": self.description,
            "final_content_equals": self.final_content_equals,
            "final_content_contains": list(self.final_content_contains),
            "required_tools": list(self.required_tools),
            "required_files": list(self.required_files),
            "file_contains": [list(pair) for pair in self.file_contains],
            "tool_result_contains": [list(pair) for pair in self.tool_result_contains],
            "must": [assertion.to_dict() for assertion in self.must],
            "must_not": [assertion.to_dict() for assertion in self.must_not],
            "ordered": [constraint.to_dict() for constraint in self.ordered],
            "validator_required": self.validator is not None or self.validator_required,
        }

    @classmethod
    def from_dict(cls, value: Any) -> TaskContract | None:
        """Decode an untrusted serialized contract without executing code."""
        if not isinstance(value, dict):
            return None
        contract = cast(dict[str, Any], value)
        contract_id = contract.get("id")
        if not isinstance(contract_id, str) or not contract_id.strip():
            return None

        normalized_id = _bounded_string(contract_id, limit=_MAX_CONTRACT_ID_CHARS)
        if normalized_id is None:
            return None

        def string_pairs(raw: Any) -> tuple[tuple[str, str], ...]:
            if not isinstance(raw, list):
                return ()
            pairs: list[tuple[str, str]] = []
            raw_values = cast(list[Any], raw)
            for raw_pair in raw_values[:_MAX_CONTRACT_ASSERTIONS]:
                if not isinstance(raw_pair, list):
                    continue
                pair = cast(list[Any], raw_pair)
                if len(pair) != 2:
                    continue
                left = _bounded_string(pair[0], limit=_MAX_CONTRACT_VALUE_CHARS)
                right = _bounded_string(pair[1], limit=_MAX_CONTRACT_VALUE_CHARS)
                if left is not None and right is not None:
                    pairs.append((left, right))
            return tuple(pairs)

        description = _bounded_string(
            contract.get("description", ""),
            limit=_MAX_CONTRACT_DESCRIPTION_CHARS,
        ) or ""
        return cls(
            id=normalized_id,
            description=description,
            final_content_equals=_bounded_string(
                contract.get("final_content_equals"),
                limit=_MAX_CONTRACT_VALUE_CHARS,
            ),
            final_content_contains=_bounded_string_tuple(
                contract.get("final_content_contains")
            ),
            required_tools=_bounded_string_tuple(contract.get("required_tools")),
            required_files=_bounded_string_tuple(contract.get("required_files")),
            file_contains=string_pairs(contract.get("file_contains")),
            tool_result_contains=string_pairs(contract.get("tool_result_contains")),
            must=_parse_assertions(contract.get("must")),
            must_not=_parse_assertions(contract.get("must_not")),
            ordered=_parse_order_constraints(contract.get("ordered")),
            validator_required=contract.get("validator_required") is True,
        )


def task_contract_from_metadata(metadata: Any) -> TaskContract | None:
    """Read the optional declarative task contract from trusted internal metadata."""
    if not isinstance(metadata, dict):
        return None
    metadata_mapping = cast(dict[str, Any], metadata)
    return TaskContract.from_dict(metadata_mapping.get(TASK_CONTRACT_METADATA_KEY))


@dataclass(frozen=True, slots=True)
class TaskEvaluation:
    """Result of evaluating the task outcome, not the internal trajectory."""

    status: TaskEvaluationStatus
    completed: bool
    reason: str | None = None
    failures: tuple[str, ...] = ()
    assertions: tuple[TaskAssertionResult, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "completed": self.completed,
            "reason": self.reason,
            "failures": list(self.failures),
            "assertions": [assertion.to_dict() for assertion in self.assertions],
        }


_SOURCE_URL_RE = re.compile(r"https?://[^\s<>\"']+")


def _tool_call_messages(result: AgentRunResult) -> list[tuple[int, str, str | None]]:
    calls: list[tuple[int, str, str | None]] = []
    for index, message in enumerate(result.messages):
        if message.get("role") != "assistant":
            continue
        raw_calls = message.get("tool_calls")
        if not isinstance(raw_calls, list):
            continue
        for raw_call in cast(list[Any], raw_calls):
            if not isinstance(raw_call, dict):
                continue
            call = cast(dict[str, Any], raw_call)
            function = call.get("function")
            function_data = cast(dict[str, Any], function) if isinstance(function, dict) else {}
            name = function_data.get("name") or call.get("name")
            if not isinstance(name, str) or not name:
                continue
            call_id = call.get("id")
            calls.append((index, name, call_id if isinstance(call_id, str) else None))
    return calls


def _tool_result_messages(result: AgentRunResult) -> list[tuple[int, str, str]]:
    messages: list[tuple[int, str, str]] = []
    for index, message in enumerate(result.messages):
        if message.get("role") != "tool" or not isinstance(message.get("name"), str):
            continue
        messages.append((index, cast(str, message["name"]), _message_content(message.get("content"))))
    return messages


def _successful_tool_names(result: AgentRunResult) -> set[str]:
    return {str(name) for name in result.tools_used}


def _evaluate_predicate(
    assertion: TaskAssertion,
    result: AgentRunResult,
) -> tuple[TaskAssertionStatus, str, tuple[str, ...], tuple[int, ...]]:
    """Evaluate a deterministic predicate and return evidence positions."""

    kind = assertion.kind
    value = assertion.value
    if kind in {"final_contains", "final_not_contains"}:
        if not result.final_content or value is None:
            return "not_evaluable", "final answer or assertion value is missing", (), ()
        present = value in result.final_content
        expected = kind == "final_contains"
        passed = present == expected
        return (
            "passed" if passed else "failed",
            "final answer contains the expected text"
            if passed and expected
            else "final answer does not contain the forbidden text"
            if passed
            else "final answer content assertion failed",
            (),
            (len(result.messages),) if present else (),
        )

    tool_calls = _tool_call_messages(result)
    successful_tools = _successful_tool_names(result)
    if kind in {"tool_called", "tool_not_called"}:
        if not assertion.tool:
            return "not_evaluable", "tool assertion is missing a tool name", (), ()
        matching = [item for item in tool_calls if item[1] == assertion.tool]
        if kind == "tool_called":
            matching = [item for item in matching if assertion.tool in successful_tools]
            passed = len(matching) >= (assertion.min_count or 1)
            return (
                "passed" if passed else "failed",
                f"successful tool {assertion.tool!r} was called"
                if passed
                else f"successful tool {assertion.tool!r} was not called enough times",
                tuple(item[2] for item in matching if item[2]),
                tuple(item[0] for item in matching),
            )
        passed = not matching
        return (
            "passed" if passed else "failed",
            f"tool {assertion.tool!r} was not called"
            if passed
            else f"forbidden tool {assertion.tool!r} was called",
            (),
            tuple(item[0] for item in matching),
        )

    if kind == "tool_result_contains":
        if not assertion.tool or value is None:
            return "not_evaluable", "tool result assertion is incomplete", (), ()
        matching = [
            item
            for item in _tool_result_messages(result)
            if item[1] == assertion.tool
            and assertion.tool in successful_tools
            and value in item[2]
        ]
        passed = bool(matching)
        return (
            "passed" if passed else "failed",
            f"successful result from {assertion.tool!r} contains the expected value"
            if passed
            else f"successful result from {assertion.tool!r} is missing the expected value",
            tuple(f"tool:{assertion.tool}:{index}" for index, _, _ in matching),
            tuple(item[0] for item in matching),
        )

    if kind == "evidence_sources":
        urls: list[str] = []
        positions: list[int] = []
        for index, tool_name, content in _tool_result_messages(result):
            if tool_name not in successful_tools:
                continue
            for url in _SOURCE_URL_RE.findall(content):
                normalized = url.rstrip(".,);]")
                if normalized and normalized not in urls:
                    urls.append(normalized)
                    positions.append(index)
        required = assertion.min_count or 1
        passed = len(urls) >= required
        return (
            "passed" if passed else "failed",
            f"found {len(urls)} evidence source(s), required {required}"
            if passed
            else f"found only {len(urls)} evidence source(s), required {required}",
            tuple(urls[:_MAX_EVIDENCE_URLS]),
            tuple(positions),
        )

    if kind == "no_side_effect":
        if not result.tool_states:
            return "not_evaluable", "tool side-effect states are unavailable", (), ()
        unsafe = [
            state
            for state in result.tool_states
            if state.get("side_effect") not in {"none", "not_started"}
        ]
        passed = not unsafe
        return (
            "passed" if passed else "failed",
            "no external side effect was observed"
            if passed
            else "one or more tool calls may have caused an external side effect",
            tuple(str(state.get("call_id")) for state in unsafe if state.get("call_id")),
            (),
        )

    return "not_evaluable", f"unsupported task assertion kind: {kind}", (), ()


def _evaluate_assertion(
    assertion: TaskAssertion,
    result: AgentRunResult,
    *,
    forbidden: bool,
) -> tuple[TaskAssertionResult, tuple[int, ...]]:
    status, reason, refs, positions = _evaluate_predicate(assertion, result)
    if forbidden:
        if status == "passed":
            status = "failed"
            reason = f"forbidden condition was observed: {reason}"
        elif status == "failed":
            status = "passed"
            reason = f"forbidden condition was not observed: {reason}"
    return TaskAssertionResult(assertion.id, assertion.kind, status, reason, refs), positions


def _evaluate_contract_assertions(
    contract: TaskContract,
    result: AgentRunResult,
) -> tuple[list[TaskAssertionResult], list[str]]:
    assertion_results: list[TaskAssertionResult] = []
    failures: list[str] = []
    positions: dict[str, tuple[int, ...]] = {}
    for assertion in contract.must:
        evaluated, event_positions = _evaluate_assertion(assertion, result, forbidden=False)
        assertion_results.append(evaluated)
        positions[assertion.id] = event_positions
    for assertion in contract.must_not:
        evaluated, event_positions = _evaluate_assertion(assertion, result, forbidden=True)
        assertion_results.append(evaluated)
        positions[assertion.id] = event_positions

    for constraint in contract.ordered:
        before = positions.get(constraint.before)
        after = positions.get(constraint.after)
        order_id = f"order:{constraint.before}->{constraint.after}"
        if before is None or after is None:
            evaluated = TaskAssertionResult(
                order_id,
                "ordered",
                "not_evaluable",
                "ordered constraint references an unknown assertion",
            )
        elif not before or not after:
            evaluated = TaskAssertionResult(
                order_id,
                "ordered",
                "not_evaluable",
                "ordered constraint does not have observable evidence",
            )
        elif min(before) <= min(after):
            evaluated = TaskAssertionResult(
                order_id,
                "ordered",
                "passed",
                "ordered constraint satisfied",
            )
        else:
            evaluated = TaskAssertionResult(
                order_id,
                "ordered",
                "failed",
                f"{constraint.before!r} happened after {constraint.after!r}",
            )
        assertion_results.append(evaluated)

    for assertion in assertion_results:
        if assertion.status == "failed":
            failures.append(f"{assertion.id}: {assertion.reason}")
    return assertion_results, failures


def evaluate_task(
    contract: TaskContract,
    result: AgentRunResult | None,
    *,
    execution_status: str,
    workspace: Path | None = None,
) -> TaskEvaluation:
    """Evaluate final task completion without treating trace shape as quality."""
    if result is None or execution_status != "completed":
        return TaskEvaluation(
            status="not_evaluable",
            completed=False,
            reason=(
                "execution was cancelled"
                if execution_status == "cancelled"
                else "execution did not produce an Agent result"
            ),
        )
    if result.stop_reason != "completed" or result.error:
        return TaskEvaluation(
            status="not_evaluable",
            completed=False,
            reason=(result.error or f"stop_reason={result.stop_reason}"),
        )
    if not result.final_content or not result.final_content.strip():
        return TaskEvaluation(
            status="not_evaluable",
            completed=False,
            reason="the Agent did not produce a final answer",
        )
    if contract.validator_required and contract.validator is None:
        return TaskEvaluation(
            status="not_evaluable",
            completed=False,
            reason="custom task validator is unavailable in this process",
        )

    failures: list[str] = []
    if (
        contract.final_content_equals is not None
        and result.final_content != contract.final_content_equals
    ):
        failures.append("final answer does not equal the task contract")
    for expected in contract.final_content_contains:
        if expected not in result.final_content:
            failures.append(f"final answer is missing {expected!r}")
    missing_tools = [
        name for name in contract.required_tools if name not in result.tools_used
    ]
    if missing_tools:
        failures.append("required successful tools missing: " + ", ".join(missing_tools))
    if contract.tool_result_contains:
        tool_messages = [
            message
            for message in result.messages
            if message.get("role") == "tool" and isinstance(message.get("name"), str)
        ]
        successful_tools = set(result.tools_used)
        for tool_name, expected in contract.tool_result_contains:
            matching = [
                message
                for message in tool_messages
                if message.get("name") == tool_name and tool_name in successful_tools
            ]
            if not matching:
                failures.append(f"successful result missing for tool: {tool_name}")
                continue
            if not any(
                expected in _message_content(message.get("content"))
                for message in matching
            ):
                failures.append(f"tool result for {tool_name!r} is missing {expected!r}")
    if contract.required_files or contract.file_contains:
        if workspace is None:
            return TaskEvaluation(
                status="not_evaluable",
                completed=False,
                reason="the task contract requires a workspace",
            )
        root = workspace.expanduser().resolve(strict=False)

        def resolve_contract_path(raw_path: str) -> Path | None:
            candidate = (root / raw_path).resolve(strict=False)
            if candidate == root or root not in candidate.parents:
                return None
            return candidate

        for raw_path in contract.required_files:
            path = resolve_contract_path(raw_path)
            if path is None:
                failures.append(f"required file path is outside the workspace: {raw_path}")
            elif not path.is_file():
                failures.append(f"required file is missing: {raw_path}")
        for raw_path, expected in contract.file_contains:
            path = resolve_contract_path(raw_path)
            if path is None:
                failures.append(f"file check path is outside the workspace: {raw_path}")
                continue
            try:
                if path.stat().st_size > _MAX_VERIFICATION_FILE_BYTES:
                    failures.append(f"file is too large to verify: {raw_path}")
                    continue
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                failures.append(f"file could not be read: {raw_path}")
                continue
            if expected not in content:
                failures.append(f"file is missing expected content: {raw_path}")
    if contract.validator is not None:
        try:
            verdict = contract.validator(result, workspace)
        except Exception as exc:
            return TaskEvaluation(
                status="not_evaluable",
                completed=False,
                reason=f"custom task validator failed: {type(exc).__name__}",
                failures=tuple(failures),
            )
        if verdict is False:
            failures.append("custom task validator failed")
        elif isinstance(verdict, str) and verdict.strip():
            failures.append(verdict.strip()[:_MAX_CONTRACT_VALUE_CHARS])

    assertion_results, assertion_failures = _evaluate_contract_assertions(contract, result)
    failures.extend(assertion_failures)
    has_not_evaluable_assertion = any(
        assertion.status == "not_evaluable" for assertion in assertion_results
    )
    if failures:
        status: TaskEvaluationStatus = "failed"
        reason = "task assertions failed"
    elif has_not_evaluable_assertion:
        status = "not_evaluable"
        reason = "one or more task assertions could not be evaluated"
    else:
        status = "passed"
        reason = None
    return TaskEvaluation(
        status=status,
        completed=status == "passed",
        reason=reason,
        failures=tuple(failures),
        assertions=tuple(assertion_results),
    )


def _message_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        import json

        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


__all__ = [
    "TaskContract",
    "TaskAssertion",
    "TaskAssertionResult",
    "TaskAssertionStatus",
    "TaskOrderConstraint",
    "TaskEvaluation",
    "TaskEvaluationStatus",
    "TaskValidator",
    "TASK_CONTRACT_METADATA_KEY",
    "evaluate_task",
    "task_contract_from_metadata",
]
