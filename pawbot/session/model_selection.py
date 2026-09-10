"""Session-scoped model selection and thinking-effort metadata."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

# Session.metadata is public SDK data, so internal selectors use a reserved namespace.
SESSION_MODEL_PRESET_METADATA_KEY = "_pawbot_model_preset"
SESSION_MODEL_OVERRIDE_METADATA_KEY = "_pawbot_model_override"
SESSION_REASONING_EFFORT_METADATA_KEY = "_pawbot_reasoning_effort"


def model_preset_from_metadata(metadata: object) -> str | None:
    """Read the canonical session preset name from persisted metadata."""
    if not isinstance(metadata, Mapping):
        return None
    typed_metadata = cast(Mapping[object, object], metadata)
    if SESSION_MODEL_PRESET_METADATA_KEY not in typed_metadata:
        return None
    value = typed_metadata[SESSION_MODEL_PRESET_METADATA_KEY]
    if not isinstance(value, str) or not value.strip():
        raise ValueError("session model preset must be a non-empty string")
    return value.strip()


def model_override_from_metadata(metadata: object) -> dict[str, object] | None:
    """Read a session-pinned model selected from a live provider catalogue.

    Unlike a named preset, a live catalogue choice must not mutate the global
    configuration.  The small JSON object is deliberately kept in session
    metadata so it survives a gateway restart and can be cleared with
    ``/model default``.
    """
    if not isinstance(metadata, Mapping):
        return None
    typed_metadata = cast(Mapping[object, object], metadata)
    value = typed_metadata.get(SESSION_MODEL_OVERRIDE_METADATA_KEY)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("session model override must be an object")

    raw = cast(Mapping[object, object], value)
    model = raw.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("session model override must contain a non-empty model")
    override: dict[str, object] = {"model": model.strip()}

    provider = raw.get("provider")
    if provider is not None:
        if not isinstance(provider, str) or not provider.strip():
            raise ValueError("session model override provider must be a non-empty string")
        override["provider"] = provider.strip()

    context_window_tokens = raw.get("context_window_tokens")
    if context_window_tokens is not None:
        if (
            not isinstance(context_window_tokens, int)
            or isinstance(context_window_tokens, bool)
            or context_window_tokens <= 0
        ):
            raise ValueError("session model override context window must be positive")
        override["context_window_tokens"] = context_window_tokens
    return override


def reasoning_effort_from_metadata(metadata: object) -> str | None:
    """Read the session-scoped thinking-effort override, if any."""
    if not isinstance(metadata, Mapping):
        return None
    typed_metadata = cast(Mapping[object, object], metadata)
    if SESSION_REASONING_EFFORT_METADATA_KEY not in typed_metadata:
        return None
    value = typed_metadata[SESSION_REASONING_EFFORT_METADATA_KEY]
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()
