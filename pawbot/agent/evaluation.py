"""Task-level evaluation kept separate from Agent trajectory checks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from pawbot.agent.runner import AgentRunResult

TaskEvaluationStatus = Literal["passed", "failed", "not_evaluable"]
TASK_CONTRACT_METADATA_KEY = "_task_contract"
TaskValidator = Callable[["AgentRunResult", Path | None], bool | str | None]

_MAX_CONTRACT_ID_CHARS = 120
_MAX_CONTRACT_DESCRIPTION_CHARS = 1_000
_MAX_CONTRACT_ASSERTIONS = 64
_MAX_CONTRACT_VALUE_CHARS = 1_000
_MAX_VERIFICATION_FILE_BYTES = 4 * 1024 * 1024


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "completed": self.completed,
            "reason": self.reason,
            "failures": list(self.failures),
        }


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
    return TaskEvaluation(
        status="failed" if failures else "passed",
        completed=not failures,
        reason=None if not failures else "task assertions failed",
        failures=tuple(failures),
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
    "TaskEvaluation",
    "TaskEvaluationStatus",
    "TaskValidator",
    "TASK_CONTRACT_METADATA_KEY",
    "evaluate_task",
    "task_contract_from_metadata",
]
