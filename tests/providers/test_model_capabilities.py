from pawbot.providers.registry import (
    context_window_tokens_for,
    model_capability_for,
)


def test_deepseek_v4_flash_has_official_long_context_metadata() -> None:
    capability = model_capability_for("deepseek", "deepseek-v4-flash")

    assert capability is not None
    assert capability.context_window == 1_048_576
    assert capability.max_output_tokens == 384_000
    assert capability.supports_tools is True
    assert capability.supports_reasoning is True


def test_capability_lookup_matches_gateway_model_ids() -> None:
    assert context_window_tokens_for(
        "openrouter",
        "deepseek/deepseek-v4-flash",
        128_000,
    ) == 1_048_576


def test_provider_owned_builtin_model_keeps_its_own_context_limit() -> None:
    assert context_window_tokens_for(
        "openai_codex",
        "openai-codex/gpt-5.6-sol",
        200_000,
    ) == 372_000


def test_mainstream_model_families_are_in_the_registry() -> None:
    assert model_capability_for("openai", "gpt-5.5").context_window == 1_050_000
    assert model_capability_for("dashscope", "qwen3.5-plus").context_window == 1_000_000
    assert model_capability_for("zhipu", "glm-4.6v").context_window == 128_000
    assert model_capability_for("mistral", "mistral-large-2512").context_window == 262_144


def test_unknown_model_keeps_safe_fallback_and_manual_budget() -> None:
    assert context_window_tokens_for("custom", "vendor/unknown", 128_000) == 128_000
    assert context_window_tokens_for("openai", "gpt-4.1", 65_536) == 65_536
