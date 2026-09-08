"""Compatibility helpers while runner tests migrate to immutable runtimes."""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import Mock

from pawbot.agent.runner import AgentRunSpec
from pawbot.config.schema import AgentDefaults
from pawbot.providers.base import GenerationSettings, LLMProvider
from pawbot.utils.llm_runtime import LLMRuntime


class _MockToolRegistryAdapter:
    """Adapt legacy runner-test mocks to the real tool execution contract.

    Production execution intentionally requires ``prepare_call`` so schema
    validation and request-context binding cannot be skipped. Older runner
    tests used a loose ``MagicMock`` with only ``execute`` configured; keep
    those tests focused on runner behavior without weakening the production
    boundary.
    """

    def __init__(self, mock: Mock) -> None:
        self._mock = mock

    def get_definitions(self) -> list[dict[str, Any]]:
        value = self._mock.get_definitions()
        return value if isinstance(value, list) else []

    def get(self, name: str) -> Any | None:
        value = self._mock.get(name)
        return None if isinstance(value, Mock) else value

    def prepare_call(self, name: str, params: Any) -> tuple[Any | None, Any, None]:
        tool = self.get(name)
        return tool, params, None

    async def execute(self, name: str, params: Any) -> Any:
        value = self._mock.execute(name, params)
        return await value if inspect.isawaitable(value) else value

    @property
    def compactable_names(self) -> set[str]:
        value = self._mock.compactable_names
        if isinstance(value, set):
            return value
        # Built-in Tool.compactable defaults to True. Legacy mocks do not
        # carry tool instances, so model that default for any called name.
        return _AllToolNames(excluded={"message"})

    def get_runtime_context_providers(self) -> list[Any]:
        value = self._mock.get_runtime_context_providers()
        return value if isinstance(value, list) else []

    @property
    def tool_names(self) -> list[str]:
        value = self._mock.tool_names
        return value if isinstance(value, list) else []


class _AllToolNames(set[str]):
    """Test-only set matching the default compactable-tool policy."""

    def __init__(self, *, excluded: set[str] | None = None) -> None:
        super().__init__()
        self._excluded = excluded or set()

    def __contains__(self, value: object) -> bool:
        return isinstance(value, str) and bool(value) and value not in self._excluded


def make_run_spec(provider: LLMProvider, **kwargs: Any) -> AgentRunSpec:
    """Build a run spec from the pre-runtime test arguments.

    Keeping this translation in test support makes production's execution
    contract strict while avoiding irrelevant setup noise in runner behavior
    tests.  New tests should pass ``runtime`` to ``AgentRunSpec`` directly when
    runtime identity is itself under test.
    """
    tools = kwargs.get("tools")
    if isinstance(tools, Mock):
        tools.compactable_names = _AllToolNames(excluded={"message"})
        kwargs["tools"] = _MockToolRegistryAdapter(tools)

    model = kwargs.pop("model")
    context_window_tokens = kwargs.pop(
        "context_window_tokens",
        AgentDefaults().context_window_tokens,
    )
    provider_generation = getattr(provider, "generation", None)
    defaults = GenerationSettings()

    temperature = kwargs.pop("temperature", None)
    if temperature is None:
        candidate = getattr(provider_generation, "temperature", None)
        temperature = candidate if isinstance(candidate, (int, float)) else defaults.temperature

    max_tokens = kwargs.pop("max_tokens", None)
    if max_tokens is None:
        candidate = getattr(provider_generation, "max_tokens", None)
        max_tokens = candidate if isinstance(candidate, int) else defaults.max_tokens

    reasoning_effort = kwargs.pop("reasoning_effort", None)
    if reasoning_effort is None:
        candidate = getattr(provider_generation, "reasoning_effort", None)
        reasoning_effort = candidate if isinstance(candidate, str) else None

    runtime = LLMRuntime(
        provider=provider,
        model=model,
        generation=GenerationSettings(
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        ),
        context_window_tokens=context_window_tokens,
    )
    return AgentRunSpec(runtime=runtime, **kwargs)
