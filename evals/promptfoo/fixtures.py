"""Local fake tools for Promptfoo; no shell, filesystem, or deployment side effects."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pawbot.agent.hook import AgentHookContext
from pawbot.agent.observability import TraceHook
from pawbot.agent.tools.base import ToolResult
from pawbot.agent.tools.registry import ToolRegistry
from pawbot.harness import HarnessTool


class FixtureTraceHook(TraceHook):
    def __init__(self, *args: Any, calls: list[dict[str, Any]], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.calls = calls

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        self.calls.extend(
            {"name": call.name, "arguments": deepcopy(call.arguments)}
            for call in context.tool_calls
        )
        await super().before_execute_tools(context)


def _evidence_ids(value: Any) -> list[str]:
    if isinstance(value, dict):
        direct = [v for k, v in value.items() if k.endswith("_id") and isinstance(v, str)]
        return direct + [identifier for child in value.values() for identifier in _evidence_ids(child)]
    if isinstance(value, list):
        return [identifier for child in value for identifier in _evidence_ids(child)]
    return []


def build_tools(
    definitions: list[dict[str, Any]], observations: list[dict[str, Any]]
) -> ToolRegistry:
    """Each response is a case fixture, never a real tool invocation."""
    tools = ToolRegistry()
    for definition in deepcopy(definitions):
        name = definition["name"]
        if name in tools.tool_names:
            raise ValueError(f"duplicate fixture tool: {name}")

        def respond(arguments: dict[str, Any], fixture: dict[str, Any] = definition) -> Any:
            match = next(
                (row for row in fixture.get("responses", []) if row["arguments"] == arguments),
                None,
            )
            failure = match is None or "error" in match
            value = (
                (match or {}).get("error", "No fixture matches these arguments.")
                if failure else match["result"]
            )
            observations.append({
                "name": fixture["name"], "arguments": deepcopy(arguments),
                "status": "error" if failure else "ok", "result": deepcopy(value),
                "evidence_ids": list(dict.fromkeys(
                    _evidence_ids(value) + _evidence_ids({"evidence_id": (match or {}).get("evidence_id")})
                )),
            })
            return ToolResult.error(str(value)) if failure else deepcopy(value)

        writable = definition.get("read_only", True) is False
        tools.register(HarnessTool(
            name, definition["description"], definition["parameters"], respond,
            read_only=not writable,
            capabilities=("fixture_write",) if writable else ("read",),
        ))
    return tools
