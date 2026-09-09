"""Model information helpers for the onboard wizard.

The provider registry is the canonical local model catalogue.  It contains
curated model capabilities for models whose limits are known, while provider
``/models`` discovery remains the source of truth for a user's actual
account.  Keeping these helpers registry-backed gives the terminal wizard
useful autocomplete and context-window recommendations without introducing a
second model database.
"""

from __future__ import annotations

from typing import Any

from pawbot.providers.registry import (
    PROVIDERS,
    ProviderModelSpec,
    find_by_name,
    model_capability_for,
)


def _model_matches(model_name: str, candidate: str) -> bool:
    """Match a model ID with or without a provider/gateway prefix."""
    normalized_model = model_name.strip().lower().rstrip("/")
    normalized_candidate = candidate.strip().lower().rstrip("/")
    return bool(
        normalized_model
        and normalized_candidate
        and (
            normalized_model == normalized_candidate
            or normalized_model.endswith(f"/{normalized_candidate}")
            or normalized_model.rsplit("/", 1)[-1] == normalized_candidate
        )
    )


def _provider_specs(provider: str) -> tuple[Any, ...]:
    """Return the registry providers relevant to a lookup."""
    normalized = provider.strip()
    if not normalized or normalized == "auto":
        return PROVIDERS
    spec = find_by_name(normalized)
    return (spec,) if spec is not None else ()


def _model_specs(provider: str = "auto") -> list[tuple[str, ProviderModelSpec]]:
    """Return ordered, de-duplicated registry models for a provider scope."""
    result: list[tuple[str, ProviderModelSpec]] = []
    seen: set[str] = set()
    for spec in _provider_specs(provider):
        for model in (*spec.builtin_models, *spec.model_capabilities):
            key = model.id.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            result.append((spec.name, model))
    return result


def _model_payload(provider: str, model: ProviderModelSpec) -> dict[str, Any]:
    """Convert registry metadata to the stable shape used by CLI callers."""
    payload: dict[str, Any] = {
        "id": model.id,
        "provider": provider,
        "label": model.label or model.id,
        "description": model.description,
        "context_window": model.context_window,
        "max_output_tokens": model.max_output_tokens,
        "reasoning_effort_values": list(model.reasoning_effort_values),
        "supports_tools": model.supports_tools,
        "supports_reasoning": model.supports_reasoning,
        "supports_vision": model.supports_vision,
    }
    return payload


def get_all_models() -> list[str]:
    """Return all curated model IDs in provider-registry order."""
    return [model.id for _provider, model in _model_specs()]


def find_model_info(model_name: str) -> dict[str, Any] | None:
    """Return curated metadata for a model ID, including gateway prefixes."""
    for provider, model in _model_specs():
        if _model_matches(model_name, model.id):
            return _model_payload(provider, model)

    # Gateway model IDs are often owned by a different provider, for example
    # ``deepseek/deepseek-v4-flash`` through OpenRouter.  Reuse the registry's
    # cross-provider matcher so the same 1M context metadata is available in
    # the terminal wizard and the WebUI.
    capability = model_capability_for("", model_name)
    if capability is None:
        return None
    return _model_payload("auto", capability)


def get_model_context_limit(model: str, provider: str = "auto") -> int | None:
    """Return a curated context limit, or ``None`` for an unknown model."""
    if provider.strip() and provider.strip() != "auto":
        for _provider_name, candidate in _model_specs(provider):
            if _model_matches(model, candidate.id) and candidate.context_window:
                return candidate.context_window
        capability = model_capability_for(provider, model)
        return capability.context_window if capability is not None else None

    info = find_model_info(model)
    context_window = info.get("context_window") if info else None
    return context_window if isinstance(context_window, int) and context_window > 0 else None


def get_model_suggestions(partial: str, provider: str = "auto", limit: int = 20) -> list[str]:
    """Return registry-backed autocomplete suggestions.

    A live provider catalogue is deliberately not fetched from the completer:
    prompt-toolkit may call it repeatedly while the user types.  Quick Start
    performs one explicit ``/models`` probe before this input is shown, and
    the WebUI has its own asynchronous catalogue picker.
    """
    if limit <= 0:
        return []
    query = partial.strip().lower()
    suggestions: list[str] = []
    seen: set[str] = set()
    for _provider_name, model in _model_specs(provider):
        key = model.id.lower()
        if key in seen or (query and query not in key and query not in model.label.lower()):
            continue
        seen.add(key)
        suggestions.append(model.id)
        if len(suggestions) >= limit:
            break
    return suggestions


def format_token_count(tokens: int) -> str:
    """Format token count for display (e.g., 200000 -> '200,000')."""
    return f"{tokens:,}"
