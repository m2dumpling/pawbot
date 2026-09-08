"""Tool-call execution boundary.

Executes one model response's tool calls, scheduling concurrent batches and
classifying every outcome (success / recoverable error / security violation)
into a model-facing observation.

Design (ADR-005): the public entry point :func:`execute_tool_calls` is frozen
for backward compatibility (``AgentRunner`` depends on it). Internally,
execution is a three-stage pipeline with one focused collaborator per stage:

* :class:`BatchPlanner` — partitions calls into concurrency-safe batches.
* :class:`CallExecutor` — runs one call through the replay rail, the security
  classifier, and the hook lifecycle.
* :class:`ViolationClassifier` — maps a failure to a boundary category and
  shapes the model-facing payload.

Unlike the upstream implementation this pipeline has no dead code and no
``getattr``-based duck typing: ``tools`` is a real ``ToolRegistry`` and every
stage is called through its static interface.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from pawbot.agent.hook import AgentHook, AgentHookContext
from pawbot.agent.tools.registry import ToolRegistry, is_tool_error_result
from pawbot.providers.base import ToolCallRequest
from pawbot.utils.runtime import (
    repeated_external_lookup_error,
    repeated_workspace_violation_error,
)

_RETRY_HINT = "\n\n[Analyze the error above and try a different approach.]"
# SSRF is a hard security block at the tool boundary, but the agent turn
# should recover conversationally instead of aborting the runtime.
_SSRF_MARKERS: tuple[str, ...] = (
    "internal/private url detected",
    "private/internal address",
    "private address",
)
_SSRF_BOUNDARY_NOTE = (
    "This is a non-bypassable security boundary. Stop trying to access "
    "private/internal URLs. Do not retry with curl, wget, encoded IPs, "
    "alternate DNS, redirects, proxies, or another tool. Ask the user for "
    "local files, logs, screenshots, or an explicit safe public URL instead. "
    "If the user explicitly trusts this private URL, ask them to whitelist "
    "the exact IP/CIDR via tools.ssrfWhitelist."
)
# Non-SSRF boundary markers returned to the model as recoverable tool errors.
_WORKSPACE_VIOLATION_MARKERS: tuple[str, ...] = (
    "outside the configured workspace",
    "outside allowed directory",
    "working_dir is outside",
    "working_dir could not be resolved",
    "path outside working dir",
    "path traversal detected",
)


def is_ssrf_violation(text: str) -> bool:
    """Return whether a tool error describes a blocked private-network request."""
    if not text:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in _SSRF_MARKERS)


def _is_workspace_violation(text: str) -> bool:
    """Return whether text describes any workspace or network boundary rejection."""
    if not text:
        return False
    lowered = text.lower()
    if is_ssrf_violation(lowered):
        return True
    return any(marker in lowered for marker in _WORKSPACE_VIOLATION_MARKERS)


def _ssrf_soft_payload(raw_text: str) -> str:
    text = raw_text.strip() or "Error: request blocked by SSRF guard"
    return f"{text}\n\n{_SSRF_BOUNDARY_NOTE}"


def _event_detail(prefix: str, text: str, limit: int = 160) -> str:
    return (prefix + text.replace("\n", " ").strip())[:limit]


def _ok_detail(result: Any) -> str:
    detail = "" if result is None else str(result)
    detail = detail.replace("\n", " ").strip()
    if not detail:
        return "(empty)"
    if len(detail) > 120:
        return detail[:120] + "..."
    return detail


class ViolationClassifier:
    """Map a tool failure to a boundary category and shape the payload."""

    def __init__(self, workspace_violation_counts: dict[str, int]) -> None:
        self._workspace_violation_counts = workspace_violation_counts

    def classify(
        self,
        *,
        raw_text: str,
        soft_payload: str,
        event: dict[str, str],
        tool_call: ToolCallRequest,
    ) -> tuple[Any, dict[str, str]] | None:
        """Return ``(result, event)`` for a boundary violation, else ``None``.

        ``None`` signals the caller to fall through to the plain error payload.
        """
        if is_ssrf_violation(raw_text):
            logger.warning(
                "Tool {} blocked by SSRF guard; returning non-retryable tool error: {}",
                tool_call.name,
                raw_text.replace("\n", " ").strip()[:200],
            )
            event["detail"] = _event_detail("ssrf_violation: ", raw_text)
            return _ssrf_soft_payload(raw_text), event

        if _is_workspace_violation(raw_text):
            escalation = repeated_workspace_violation_error(
                tool_call.name,
                tool_call.arguments,
                self._workspace_violation_counts,
            )
            event["detail"] = _event_detail("workspace_violation: ", raw_text)
            if escalation is not None:
                logger.warning(
                    "Tool {} hit workspace boundary repeatedly; escalating hint",
                    tool_call.name,
                )
                event["detail"] = _event_detail(
                    "workspace_violation_escalated: ",
                    raw_text,
                )
                return escalation, event
            return soft_payload, event

        return None


class BatchPlanner:
    """Partition tool calls into concurrently-executable batches."""

    @staticmethod
    def plan(
        tools: ToolRegistry,
        tool_calls: list[ToolCallRequest],
        *,
        concurrent: bool,
    ) -> list[list[ToolCallRequest]]:
        if not concurrent:
            return [[tool_call] for tool_call in tool_calls]

        batches: list[list[ToolCallRequest]] = []
        current: list[ToolCallRequest] = []
        for tool_call in tool_calls:
            tool = tools.get(tool_call.name)
            if tool is not None and tool.concurrency_safe:
                current.append(tool_call)
                continue
            if current:
                batches.append(current)
                current = []
            batches.append([tool_call])
        if current:
            batches.append(current)
        return batches


class CallExecutor:
    """Run one tool call through the replay rail, then classify the outcome.

    The execution sequence is: repeated-lookup guard → parameter preparation
    → replay short-circuit (ADR-004) → real execution → boundary/error
    classification. Hooks observe the start, completion, and error of every
    attempt.
    """

    def __init__(
        self,
        tools: ToolRegistry,
        *,
        external_lookup_counts: dict[str, int],
        workspace_violation_counts: dict[str, int],
        hook: AgentHook,
        context: AgentHookContext,
    ) -> None:
        self._tools = tools
        self._external_lookup_counts = external_lookup_counts
        self._hook = hook
        self._context = context
        self._classifier = ViolationClassifier(workspace_violation_counts)

    async def run(self, tool_call: ToolCallRequest) -> tuple[Any, dict[str, str]]:
        lookup_error = repeated_external_lookup_error(
            tool_call.name,
            tool_call.arguments,
            self._external_lookup_counts,
        )
        if lookup_error:
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": "repeated external lookup blocked",
            }
            return lookup_error + _RETRY_HINT, event

        tool, params, prep_error = self._tools.prepare_call(
            tool_call.name,
            tool_call.arguments,
        )
        if prep_error:
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": prep_error.split(": ", 1)[-1][:120],
            }
            handled = self._classifier.classify(
                raw_text=prep_error,
                soft_payload=prep_error + _RETRY_HINT,
                event=event,
                tool_call=tool_call,
            )
            if handled is not None:
                return handled
            return prep_error + _RETRY_HINT, event

        await self._hook.before_execute_tool(self._context, tool_call, tool, params)
        try:
            # ADR-004 replay rail: when a recorded turn is re-executed
            # offline, every tool call is served from the JSONL snapshot by
            # stable key instead of touching the real system (side-effect
            # isolation).
            from pawbot.agent.blackbox import lookup_replay_result

            found, replayed = lookup_replay_result(tool_call.name, params)
            if found:
                if is_tool_error_result(replayed):
                    await self._hook.on_execute_tool_error(
                        self._context,
                        tool_call,
                        tool,
                        params,
                        replayed,
                    )
                    event = {
                        "name": tool_call.name,
                        "status": "error",
                        "detail": str(replayed).replace("\n", " ").strip()[:120],
                    }
                    return replayed, event
                event = {
                    "name": tool_call.name,
                    "status": "ok",
                    "detail": "(replayed from blackbox snapshot)",
                }
                await self._hook.after_execute_tool(
                    self._context, tool_call, tool, params, replayed
                )
                return replayed, event
            if tool is not None:
                result = await tool.execute(**params)
            else:
                result = await self._tools.execute(tool_call.name, params)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._hook.on_execute_tool_error(
                self._context, tool_call, tool, params, exc
            )
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": str(exc),
            }
            payload = f"Error: {type(exc).__name__}: {exc}"
            handled = self._classifier.classify(
                raw_text=str(exc),
                # Preserve legacy exception payloads without the retry hint.
                soft_payload=payload,
                event=event,
                tool_call=tool_call,
            )
            if handled is not None:
                return handled
            return payload, event

        if is_tool_error_result(result):
            await self._hook.on_execute_tool_error(
                self._context, tool_call, tool, params, result
            )
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": result.replace("\n", " ").strip()[:120],
            }
            handled = self._classifier.classify(
                raw_text=result,
                soft_payload=result + _RETRY_HINT,
                event=event,
                tool_call=tool_call,
            )
            if handled is not None:
                return handled
            return result + _RETRY_HINT, event

        await self._hook.after_execute_tool(self._context, tool_call, tool, params, result)
        return result, {"name": tool_call.name, "status": "ok", "detail": _ok_detail(result)}


async def execute_tool_calls(
    tools: ToolRegistry,
    tool_calls: list[ToolCallRequest],
    *,
    concurrent: bool,
    external_lookup_counts: dict[str, int],
    workspace_violation_counts: dict[str, int],
    hook: AgentHook,
    context: AgentHookContext,
) -> tuple[list[Any], list[dict[str, str]]]:
    """Execute one model response's tool calls in stable result order.

    Frozen public entry point (ADR-005). The pipeline is: plan batches →
    run each call through a single :class:`CallExecutor` → collect results.
    """
    executor = CallExecutor(
        tools,
        external_lookup_counts=external_lookup_counts,
        workspace_violation_counts=workspace_violation_counts,
        hook=hook,
        context=context,
    )
    results: list[Any] = []
    events: list[dict[str, str]] = []
    for batch in BatchPlanner.plan(tools, tool_calls, concurrent=concurrent):
        if concurrent and len(batch) > 1:
            batch_results = await asyncio.gather(*(executor.run(c) for c in batch))
        else:
            batch_results = [await executor.run(c) for c in batch]
        for result, event in batch_results:
            results.append(result)
            events.append(event)
    return results, events
