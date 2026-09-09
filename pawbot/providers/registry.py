"""
Provider Registry — single source of truth for LLM provider metadata.

Adding a new provider:
  1. Add a ProviderSpec to PROVIDERS below.
  2. Add a field to ProvidersConfig in config/schema.py.
  Done. Env vars, config matching, status display all derive from here.

Order matters — it controls match priority and fallback. Gateways first.
Every entry writes out all fields so you can copy-paste as a template.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic.alias_generators import to_snake


@dataclass(frozen=True)
class ProviderModelSpec:
    """Curated model identity and capability metadata."""

    id: str
    label: str = ""
    description: str = ""
    context_window: int | None = None
    max_output_tokens: int | None = None
    reasoning_effort_values: tuple[str, ...] = ()
    supports_tools: bool | None = None
    supports_reasoning: bool | None = None
    supports_vision: bool | None = None


@dataclass(frozen=True)
class ProviderSpec:
    """One LLM provider's metadata. See PROVIDERS below for real examples.

    Placeholders in env_extras values:
      {api_key}  — the user's API key
      {api_base} — api_base from config, or this spec's default_api_base
    """

    # identity
    name: str  # config field name, e.g. "dashscope"
    keywords: tuple[str, ...]  # model-name keywords for matching (lowercase)
    env_key: str  # env var for API key, e.g. "DASHSCOPE_API_KEY"
    display_name: str = ""  # shown in `pawbot status`
    model_catalog: str = "auto"  # WebUI model-list source
    builtin_models: tuple[ProviderModelSpec, ...] = ()
    # Capability metadata for models returned by a provider's /models endpoint.
    # These entries enrich an API response; they do not restrict custom model IDs.
    model_capabilities: tuple[ProviderModelSpec, ...] = ()
    settings_alias_for: str = ""  # compatibility alias grouped under this provider in Settings

    # which provider implementation to use
    # "openai_compat" | "anthropic" | "azure_openai" | "openai_codex" | "xai_grok"
    # | "github_copilot" | "bedrock"
    backend: str = "openai_compat"

    # extra env vars / request headers supplied by the provider integration.
    env_extras: tuple[tuple[str, str], ...] = ()
    default_extra_headers: tuple[tuple[str, str], ...] = ()

    # gateway / local detection
    is_gateway: bool = False  # routes any model (OpenRouter, AiHubMix)
    is_local: bool = False  # local deployment (vLLM, Ollama)
    detect_by_key_prefix: str = ""  # match api_key prefix, e.g. "sk-or-"
    detect_by_base_keyword: str = ""  # match substring in api_base URL
    default_api_base: str = ""  # OpenAI-compatible base URL for this provider

    # gateway behavior
    strip_model_prefix: bool = False  # strip "provider/" before sending to gateway
    strip_model_prefixes: tuple[str, ...] = ()  # strip only when the first model segment matches
    supports_max_completion_tokens: bool = False

    # per-model param overrides, e.g. (("kimi-k2.5", {"temperature": 1.0}),)
    model_overrides: tuple[tuple[str, dict[str, Any]], ...] = ()

    # OAuth-based providers (e.g., OpenAI Codex) don't use API keys
    is_oauth: bool = False

    # Direct providers skip API-key validation (user supplies everything)
    is_direct: bool = False

    # Provider is listed for shared credentials but cannot serve chat completions.
    is_transcription_only: bool = False

    # Provider supports cache_control on content blocks (e.g. Anthropic prompt caching)
    supports_prompt_caching: bool = False

    # How to inject the thinking on/off toggle into extra_body.
    # ""              — no extra_body needed (default)
    # "thinking_type" — {"thinking": {"type": "enabled"/"disabled"}}
    #                   (DeepSeek, VolcEngine, BytePlus)
    # "enable_thinking" — {"enable_thinking": true/false}  (DashScope)
    # "reasoning_split" — {"reasoning_split": true/false}  (MiniMax)
    thinking_style: str = ""

    # Gateway-native reasoning control to pair with model-level thinking styles.
    # "reasoning_effort" — {"reasoning": {"effort": <none|minimal|...>}}
    #                      (OpenRouter)
    gateway_reasoning_style: str = ""

    # When True, treat the "reasoning" response field as formal content
    # when "content" is empty.  Only set this for providers (e.g. StepFun)
    # whose API returns the actual answer in "reasoning" instead of "content".
    reasoning_as_content: bool = False

    # Map user-supplied reasoning_effort (OpenAI vocab: minimal/low/medium/high)
    # to the value this provider accepts on the wire. Set when the provider's
    # accepted set differs from OpenAI's. An empty mapped value omits the kwarg.
    # Mistral: only "high"/"none" — low/minimal map to "none", medium maps to "high".
    reasoning_effort_remap: tuple[tuple[str, str], ...] = ()

    # Models whose API rejects the reasoning_effort kwarg because reasoning is
    # implicit (Magistral always reasons; sending the kwarg returns HTTP 400).
    # Substring match against the wire model name (lowercased).
    implicit_reasoning_models: tuple[str, ...] = ()

    # Models that expose the OpenAI Responses wire format.  This is model-level
    # because providers may add Responses support incrementally.
    responses_models: tuple[str, ...] = ()

    # Provider-hosted Responses tools sent unless extraBody.tools explicitly
    # supplies the hosted-tool selection. Values are raw Responses tool types.
    responses_default_tools: tuple[str, ...] = ()

    # When the model returns content as a list of {"type":"thinking",...} +
    # {"type":"text",...} blocks, extract the thinking text into
    # reasoning_content. Mistral's Magistral / reasoning-enabled responses use
    # this shape.
    extract_thinking_blocks: bool = False

    # Strip ``reasoning_content`` from assistant history messages before
    # sending. Mistral validates its request schema strictly and 400s on
    # any extra fields; other providers (DeepSeek) require this key on the
    # wire to keep thinking-mode history intact.
    strip_history_reasoning_content: bool = False

    @property
    def label(self) -> str:
        return self.display_name or self.name.title()


# ---------------------------------------------------------------------------
# PROVIDERS — the registry. Order = priority. Copy any entry as template.
# ---------------------------------------------------------------------------

PROVIDERS: tuple[ProviderSpec, ...] = (
    # === Custom (direct OpenAI-compatible endpoint) ========================
    ProviderSpec(
        name="custom",
        keywords=(),
        env_key="",
        display_name="Custom",
        backend="openai_compat",
        is_direct=True,
    ),

    # === Azure OpenAI (direct API calls with API version 2024-10-21) =====
    ProviderSpec(
        name="azure_openai",
        keywords=("azure", "azure-openai"),
        env_key="",
        display_name="Azure OpenAI",
        backend="azure_openai",
        is_direct=True,
    ),
    # === AWS Bedrock (native Converse API via bedrock-runtime) =============
    ProviderSpec(
        name="bedrock",
        keywords=(
            "bedrock",
            "anthropic.claude",
            "amazon.nova",
            "meta.",
            "mistral.",
            "cohere.",
            "qwen.",
            "deepseek.",
            "openai.gpt-oss",
            "ai21.",
            "moonshot.",
            "writer.",
            "zai.",
        ),
        env_key="AWS_BEARER_TOKEN_BEDROCK",
        display_name="AWS Bedrock",
        backend="bedrock",
        is_direct=True,
    ),
    # === Gateways (detected by api_key / api_base, not model name) =========
    # Gateways can route any model, so they win in fallback.
    # OpenRouter: global gateway, keys start with "sk-or-"
    ProviderSpec(
        name="openrouter",
        keywords=("openrouter",),
        env_key="OPENROUTER_API_KEY",
        display_name="OpenRouter",
        backend="openai_compat",
        is_gateway=True,
        detect_by_key_prefix="sk-or-",
        detect_by_base_keyword="openrouter",
        default_api_base="https://openrouter.ai/api/v1",
        supports_prompt_caching=True,
        gateway_reasoning_style="reasoning_effort",
    ),
    # OrcaRouter: global gateway, keys start with "sk-orca-"
    ProviderSpec(
        name="orcarouter",
        keywords=("orcarouter",),
        env_key="ORCAROUTER_API_KEY",
        display_name="OrcaRouter",
        backend="openai_compat",
        is_gateway=True,
        detect_by_key_prefix="sk-orca-",
        detect_by_base_keyword="orcarouter",
        default_api_base="https://api.orcarouter.ai/v1",
    ),
    # Eden AI: OpenAI-compatible gateway. Models use the "provider/model"
    # naming scheme (e.g. "anthropic/claude-sonnet-4-5"); the full id is sent upstream.
    ProviderSpec(
        name="edenai",
        keywords=("edenai",),
        env_key="EDENAI_API_KEY",
        display_name="Eden AI",
        backend="openai_compat",
        is_gateway=True,
        detect_by_base_keyword="edenai",
        default_api_base="https://api.edenai.run/v3",
    ),
    # OpenCode Zen: OpenAI-compatible chat-completions gateway for coding models.
    # models.dev/OpenCode use provider id "opencode" and model ids like
    # "opencode/<model>"; send the bare model upstream.
    ProviderSpec(
        name="opencode",
        keywords=("opencode/", "opencode", "opencode-zen", "opencode_zen"),
        env_key="OPENCODE_API_KEY",
        display_name="OpenCode Zen",
        backend="openai_compat",
        is_gateway=True,
        detect_by_base_keyword="opencode.ai/zen",
        default_api_base="https://opencode.ai/zen/v1",
        strip_model_prefixes=("opencode", "opencode_zen", "opencode-zen"),
    ),
    # Compatibility alias for configs that already used providers.opencodeZen.
    ProviderSpec(
        name="opencode_zen",
        keywords=("opencode/", "opencode_zen", "opencode-zen"),
        env_key="OPENCODE_API_KEY",
        display_name="OpenCode Zen",
        settings_alias_for="opencode",
        backend="openai_compat",
        is_gateway=True,
        detect_by_base_keyword="opencode.ai/zen",
        default_api_base="https://opencode.ai/zen/v1",
        strip_model_prefixes=("opencode", "opencode_zen", "opencode-zen"),
    ),
    # OpenCode Go: OpenAI-compatible chat-completions gateway for low-cost models.
    # OpenCode's own config uses "opencode-go/<model>"; send the bare model upstream.
    ProviderSpec(
        name="opencode_go",
        keywords=("opencode-go", "opencode_go"),
        env_key="OPENCODE_API_KEY",
        display_name="OpenCode Go",
        backend="openai_compat",
        is_gateway=True,
        detect_by_base_keyword="opencode.ai/zen/go",
        default_api_base="https://opencode.ai/zen/go/v1",
        strip_model_prefixes=("opencode-go", "opencode_go"),
    ),
    # Hugging Face Inference Providers: OpenAI-compatible router for chat models.
    ProviderSpec(
        name="huggingface",
        keywords=("huggingface", "hugging-face"),
        env_key="HF_TOKEN",
        display_name="Hugging Face",
        backend="openai_compat",
        is_gateway=True,
        detect_by_key_prefix="hf_",
        detect_by_base_keyword="huggingface",
        default_api_base="https://router.huggingface.co/v1",
    ),
    # Skywork API platform (APIFree): OpenAI-compatible MaaS gateway.
    ProviderSpec(
        name="skywork",
        keywords=("skywork", "skyclaw", "apifree"),
        env_key="SKYWORK_API_KEY",
        display_name="Skywork",
        model_catalog="official",
        backend="openai_compat",
        env_extras=(("APIFREE_API_KEY", "{api_key}"),),
        is_gateway=True,
        detect_by_base_keyword="apifree.ai",
        default_api_base="https://api.apifree.ai/agent/v1",
    ),
    # AiHubMix: global gateway, OpenAI-compatible interface.
    # strip_model_prefix=True: doesn't understand "anthropic/claude-3",
    # strips to bare "claude-3".
    ProviderSpec(
        name="aihubmix",
        keywords=("aihubmix",),
        env_key="OPENAI_API_KEY",
        display_name="AiHubMix",
        backend="openai_compat",
        is_gateway=True,
        detect_by_base_keyword="aihubmix",
        default_api_base="https://aihubmix.com/v1",
        strip_model_prefix=True,
    ),
    # SiliconFlow (硅基流动): OpenAI-compatible gateway, model names keep org prefix
    ProviderSpec(
        name="siliconflow",
        keywords=("siliconflow",),
        env_key="OPENAI_API_KEY",
        display_name="SiliconFlow",
        backend="openai_compat",
        is_gateway=True,
        detect_by_base_keyword="siliconflow",
        default_api_base="https://api.siliconflow.cn/v1",
    ),

    # Novita AI: OpenAI-compatible gateway for hosted model APIs.
    ProviderSpec(
        name="novita",
        keywords=("novita",),
        env_key="NOVITA_API_KEY",
        display_name="Novita AI",
        backend="openai_compat",
        is_gateway=True,
        detect_by_base_keyword="novita",
        default_api_base="https://api.novita.ai/openai",
    ),

    # VolcEngine (火山引擎): OpenAI-compatible gateway, pay-per-use models
    ProviderSpec(
        name="volcengine",
        keywords=("volcengine", "volces", "ark"),
        env_key="OPENAI_API_KEY",
        display_name="VolcEngine",
        backend="openai_compat",
        is_gateway=True,
        detect_by_base_keyword="volces",
        default_api_base="https://ark.cn-beijing.volces.com/api/v3",
        thinking_style="thinking_type",
        supports_max_completion_tokens=True,
    ),

    # VolcEngine Coding Plan (火山引擎 Coding Plan): same key as volcengine
    ProviderSpec(
        name="volcengine_coding_plan",
        keywords=("volcengine-plan",),
        env_key="OPENAI_API_KEY",
        display_name="VolcEngine Coding Plan",
        backend="openai_compat",
        is_gateway=True,
        default_api_base="https://ark.cn-beijing.volces.com/api/coding/v3",
        strip_model_prefix=True,
        thinking_style="thinking_type",
        supports_max_completion_tokens=True,
    ),

    # BytePlus: VolcEngine international, pay-per-use models
    ProviderSpec(
        name="byteplus",
        keywords=("byteplus",),
        env_key="OPENAI_API_KEY",
        display_name="BytePlus",
        backend="openai_compat",
        is_gateway=True,
        detect_by_base_keyword="bytepluses",
        default_api_base="https://ark.ap-southeast.bytepluses.com/api/v3",
        strip_model_prefix=True,
        thinking_style="thinking_type",
    ),

    # BytePlus Coding Plan: same key as byteplus
    ProviderSpec(
        name="byteplus_coding_plan",
        keywords=("byteplus-plan",),
        env_key="OPENAI_API_KEY",
        display_name="BytePlus Coding Plan",
        backend="openai_compat",
        is_gateway=True,
        default_api_base="https://ark.ap-southeast.bytepluses.com/api/coding/v3",
        strip_model_prefix=True,
        thinking_style="thinking_type",
    ),


    # === Standard providers (matched by model-name keywords) ===============
    # Anthropic: native Anthropic SDK
    ProviderSpec(
        name="anthropic",
        keywords=("anthropic", "claude"),
        env_key="ANTHROPIC_API_KEY",
        display_name="Anthropic",
        backend="anthropic",
        supports_prompt_caching=True,
        model_capabilities=(
            ProviderModelSpec(
                id="claude-opus-5",
                label="Claude Opus 5",
                description="Anthropic frontier model for complex agentic work.",
                context_window=1_000_000,
                max_output_tokens=128_000,
                reasoning_effort_values=("", "low", "medium", "high"),
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="claude-sonnet-5",
                label="Claude Sonnet 5",
                description="Balanced Claude model with long-context support.",
                context_window=1_000_000,
                max_output_tokens=128_000,
                reasoning_effort_values=("", "low", "medium", "high"),
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="claude-opus-4-6",
                label="Claude Opus 4.6",
                description="Claude Opus 4.6 with a 1M-token context window.",
                context_window=1_000_000,
                max_output_tokens=128_000,
                reasoning_effort_values=("", "low", "medium", "high"),
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="claude-sonnet-4-6",
                label="Claude Sonnet 4.6",
                description="Claude Sonnet 4.6 with a 1M-token context window.",
                context_window=1_000_000,
                max_output_tokens=128_000,
                reasoning_effort_values=("", "low", "medium", "high"),
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="claude-sonnet-4-5",
                label="Claude Sonnet 4.5",
                description="Claude Sonnet 4.5 with a 200K-token context window.",
                context_window=200_000,
                max_output_tokens=64_000,
                reasoning_effort_values=("", "low", "medium", "high"),
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="claude-opus-4-5",
                label="Claude Opus 4.5",
                description="Claude Opus 4.5 with a 200K-token context window.",
                context_window=200_000,
                max_output_tokens=64_000,
                reasoning_effort_values=("", "low", "medium", "high"),
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
        ),
    ),
    # OpenAI: SDK default base URL (no override needed)
    ProviderSpec(
        name="openai",
        keywords=("openai", "gpt"),
        env_key="OPENAI_API_KEY",
        display_name="OpenAI",
        backend="openai_compat",
        supports_max_completion_tokens=True,
        model_capabilities=(
            ProviderModelSpec(
                id="gpt-5.6-sol",
                label="GPT-5.6 Sol",
                description="Frontier model for complex professional work.",
                context_window=1_050_000,
                max_output_tokens=128_000,
                reasoning_effort_values=("", "low", "medium", "high", "xhigh", "max"),
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="gpt-5.6-terra",
                label="GPT-5.6 Terra",
                description="Balanced GPT-5.6 model for coding and everyday work.",
                context_window=1_050_000,
                max_output_tokens=128_000,
                reasoning_effort_values=("", "low", "medium", "high", "xhigh", "max"),
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="gpt-5.6-luna",
                label="GPT-5.6 Luna",
                description="Cost-sensitive GPT-5.6 model for high-volume workloads.",
                context_window=1_050_000,
                max_output_tokens=128_000,
                reasoning_effort_values=("", "low", "medium", "high", "xhigh", "max"),
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="gpt-5.6",
                label="GPT-5.6",
                description="Alias for GPT-5.6 Sol.",
                context_window=1_050_000,
                max_output_tokens=128_000,
                reasoning_effort_values=("", "low", "medium", "high", "xhigh", "max"),
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="gpt-5.5",
                label="GPT-5.5",
                description="Flagship model for complex professional work.",
                context_window=1_050_000,
                max_output_tokens=128_000,
                reasoning_effort_values=("", "low", "medium", "high", "xhigh"),
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="gpt-5.4",
                label="GPT-5.4",
                description="More affordable model for coding and professional work.",
                context_window=1_050_000,
                max_output_tokens=128_000,
                reasoning_effort_values=("", "low", "medium", "high", "xhigh"),
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="gpt-5.4-mini",
                label="GPT-5.4 Mini",
                description="Smaller GPT-5.4 model for cost-sensitive workloads.",
                context_window=1_050_000,
                max_output_tokens=128_000,
                reasoning_effort_values=("", "low", "medium", "high", "xhigh"),
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="gpt-4.1",
                label="GPT-4.1",
                description="Non-reasoning model with a 1M-token context window.",
                context_window=1_047_576,
                max_output_tokens=32_768,
                supports_tools=True,
                supports_reasoning=False,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="gpt-4.1-mini",
                label="GPT-4.1 Mini",
                description="Fast GPT-4.1 model with a 1M-token context window.",
                context_window=1_047_576,
                max_output_tokens=32_768,
                supports_tools=True,
                supports_reasoning=False,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="gpt-4.1-nano",
                label="GPT-4.1 Nano",
                description="Small GPT-4.1 model with a 1M-token context window.",
                context_window=1_047_576,
                max_output_tokens=32_768,
                supports_tools=True,
                supports_reasoning=False,
                supports_vision=True,
            ),
        ),
    ),
    # OpenAI Codex: OAuth-based, dedicated provider
    ProviderSpec(
        name="openai_codex",
        keywords=("openai-codex",),
        env_key="",
        display_name="OpenAI Codex",
        model_catalog="builtin",
        builtin_models=(
            ProviderModelSpec(
                id="openai-codex/gpt-5.6-sol",
                label="GPT-5.6-Sol",
                description="Latest frontier agentic coding model.",
                context_window=372000,
            ),
            ProviderModelSpec(
                id="openai-codex/gpt-5.6-terra",
                label="GPT-5.6-Terra",
                description="Balanced agentic coding model for everyday work.",
                context_window=372000,
            ),
            ProviderModelSpec(
                id="openai-codex/gpt-5.6-luna",
                label="GPT-5.6-Luna",
                description="Fast and affordable agentic coding model.",
                context_window=372000,
            ),
            ProviderModelSpec(
                id="openai-codex/gpt-5.5",
                label="GPT-5.5",
                description="Frontier model for complex coding, research, and real-world work.",
            ),
            ProviderModelSpec(
                id="openai-codex/gpt-5.4",
                label="GPT-5.4",
                description="Strong model for everyday coding.",
            ),
            ProviderModelSpec(
                id="openai-codex/gpt-5.4-mini",
                label="GPT-5.4-Mini",
                description="Small, fast, and cost-efficient model for simpler coding tasks.",
            ),
            ProviderModelSpec(
                id="openai-codex/gpt-5.3-codex-spark",
                label="GPT-5.3-Codex-Spark",
                description="Ultra-fast coding model.",
            ),
        ),
        backend="openai_codex",
        detect_by_base_keyword="codex",
        default_api_base="https://chatgpt.com/backend-api",
        is_oauth=True,
    ),
    # xAI subscription: OAuth-based, with capability-gated server-hosted X Search.
    ProviderSpec(
        name="xai_grok",
        keywords=("xai-grok", "xai_grok"),
        env_key="",
        display_name="xAI Grok",
        model_catalog="builtin",
        builtin_models=(
            ProviderModelSpec(
                id="xai-grok/grok-4.5",
                label="Grok 4.5",
                description="Grok via xAI subscription; X Search is enabled when supported.",
                context_window=500000,
            ),
        ),
        backend="xai_grok",
        default_api_base="https://cli-chat-proxy.grok.com/v1",
        is_oauth=True,
    ),
    # GitHub Copilot: OAuth-based
    ProviderSpec(
        name="github_copilot",
        keywords=("github_copilot", "copilot"),
        env_key="",
        display_name="Github Copilot",
        backend="github_copilot",
        default_api_base="https://api.githubcopilot.com",
        strip_model_prefix=True,
        is_oauth=True,
        supports_max_completion_tokens=True,
    ),
    # DeepSeek: OpenAI-compatible at api.deepseek.com
    # DeepSeek V4 models (deepseek-v4-*) accept explicit thinking effort via the
    # Responses API with a low / high / max vocabulary. Older deepseek-chat
    # models reason implicitly and must not receive the kwarg.
    ProviderSpec(
        name="deepseek",
        keywords=("deepseek",),
        env_key="DEEPSEEK_API_KEY",
        display_name="DeepSeek",
        backend="openai_compat",
        default_api_base="https://api.deepseek.com",
        thinking_style="thinking_type",
        reasoning_effort_remap=(
            ("low", "low"),
            ("high", "high"),
            ("max", "max"),
        ),
        responses_models=(
            "deepseek-v4-flash",
            "deepseek-v4-pro",
            "deepseek-v4-flash-vision-exp",
        ),
        model_capabilities=(
            ProviderModelSpec(
                id="deepseek-v4-flash",
                label="DeepSeek V4 Flash",
                description="DeepSeek V4 Flash with a 1M-token context window.",
                context_window=1_048_576,
                max_output_tokens=384_000,
                reasoning_effort_values=("", "low", "high", "max"),
                supports_tools=True,
                supports_reasoning=True,
            ),
            ProviderModelSpec(
                id="deepseek-v4-pro",
                label="DeepSeek V4 Pro",
                description="DeepSeek V4 Pro with a 1M-token context window.",
                context_window=1_048_576,
                max_output_tokens=384_000,
                reasoning_effort_values=("", "low", "high", "max"),
                supports_tools=True,
                supports_reasoning=True,
            ),
            ProviderModelSpec(
                id="deepseek-v4-flash-vision-exp",
                label="DeepSeek V4 Flash Vision",
                description="DeepSeek V4 Flash Vision with a 1M-token context window.",
                context_window=1_048_576,
                max_output_tokens=384_000,
                reasoning_effort_values=("", "low", "high", "max"),
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
        ),
        responses_default_tools=("web_search",),
    ),
    # Gemini: Google's OpenAI-compatible endpoint
    ProviderSpec(
        name="gemini",
        keywords=("gemini", "gemma"),
        env_key="GEMINI_API_KEY",
        display_name="Gemini",
        backend="openai_compat",
        default_api_base="https://generativelanguage.googleapis.com/v1beta/openai/",
        model_capabilities=(
            ProviderModelSpec(
                id="gemini-3.8-flash",
                label="Gemini 3.8 Flash",
                description="Gemini Flash model for long-context agentic workloads.",
                context_window=1_000_000,
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="gemini-3.7-flash",
                label="Gemini 3.7 Flash",
                description="Gemini Flash model for coding and agentic workflows.",
                context_window=1_000_000,
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="gemini-3.6-flash",
                label="Gemini 3.6 Flash",
                description="Gemini Flash model for multimodal agent workflows.",
                context_window=1_000_000,
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="gemini-3.5-flash",
                label="Gemini 3.5 Flash",
                description="Gemini Flash model for high-throughput workloads.",
                context_window=1_000_000,
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="gemini-3.1-pro-preview",
                label="Gemini 3.1 Pro",
                description="Gemini Pro model for complex reasoning and multimodal work.",
                context_window=1_000_000,
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="gemini-2.5-pro",
                label="Gemini 2.5 Pro",
                description="Gemini Pro reasoning model with long context.",
                context_window=1_000_000,
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="gemini-2.5-flash",
                label="Gemini 2.5 Flash",
                description="Gemini Flash reasoning model with long context.",
                context_window=1_000_000,
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
        ),
    ),
    # Zhipu (智谱): OpenAI-compatible at open.bigmodel.cn
    ProviderSpec(
        name="zhipu",
        keywords=("zhipu", "glm", "zai"),
        env_key="ZAI_API_KEY",
        display_name="Zhipu AI",
        backend="openai_compat",
        env_extras=(("ZHIPUAI_API_KEY", "{api_key}"),),
        default_api_base="https://open.bigmodel.cn/api/paas/v4",
        model_capabilities=(
            ProviderModelSpec(
                id="glm-5.2[1m]",
                label="GLM-5.2 1M",
                description="GLM-5.2 coding endpoint with a 1M-token context window.",
                context_window=1_000_000,
                supports_tools=True,
                supports_reasoning=True,
            ),
            ProviderModelSpec(
                id="glm-5.2",
                label="GLM-5.2",
                description="GLM-5.2 with an approximately 198K context window on standard Model Studio routing.",
                context_window=202_752,
                supports_tools=True,
                supports_reasoning=True,
            ),
            ProviderModelSpec(
                id="glm-5",
                label="GLM-5",
                description="GLM-5 with an approximately 198K context window.",
                context_window=202_752,
                supports_tools=True,
                supports_reasoning=True,
            ),
            ProviderModelSpec(
                id="glm-4.7",
                label="GLM-4.7",
                description="GLM-4.7 with an approximately 198K context window.",
                context_window=202_752,
                supports_tools=True,
                supports_reasoning=True,
            ),
            ProviderModelSpec(
                id="glm-4.6v",
                label="GLM-4.6V",
                description="GLM-4.6V vision reasoning model with a 128K context window.",
                context_window=128_000,
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
        ),
    ),
    # DashScope (通义): Qwen models, OpenAI-compatible endpoint
    ProviderSpec(
        name="dashscope",
        keywords=("qwen", "dashscope"),
        env_key="DASHSCOPE_API_KEY",
        display_name="DashScope",
        backend="openai_compat",
        default_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        thinking_style="enable_thinking",
        model_capabilities=(
            ProviderModelSpec(
                id="qwen3.7-max",
                label="Qwen3.7 Max",
                description="Qwen3.7 Max with a 1M-token context window.",
                context_window=1_000_000,
                max_output_tokens=131_072,
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="qwen3.7-plus",
                label="Qwen3.7 Plus",
                description="Qwen3.7 Plus with a 1M-token context window.",
                context_window=1_000_000,
                max_output_tokens=65_536,
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="qwen3.7-flash",
                label="Qwen3.7 Flash",
                description="Qwen3.7 Flash with a 1M-token context window.",
                context_window=1_000_000,
                max_output_tokens=65_536,
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="qwen3.6-plus",
                label="Qwen3.6 Plus",
                description="Qwen3.6 Plus with a 1M-token context window.",
                context_window=1_000_000,
                max_output_tokens=65_536,
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="qwen3.6-flash",
                label="Qwen3.6 Flash",
                description="Qwen3.6 Flash with a 1M-token context window.",
                context_window=1_000_000,
                max_output_tokens=65_536,
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="qwen3.5-plus",
                label="Qwen3.5 Plus",
                description="Qwen3.5 Plus with a 1M-token context window.",
                context_window=1_000_000,
                max_output_tokens=65_536,
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="qwen3.5-flash",
                label="Qwen3.5 Flash",
                description="Qwen3.5 Flash with a 1M-token context window.",
                context_window=1_000_000,
                max_output_tokens=65_536,
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="qwen3-coder-plus",
                label="Qwen3 Coder Plus",
                description="Qwen3 Coder Plus with a 1M-token context window.",
                context_window=1_000_000,
                supports_tools=True,
                supports_reasoning=True,
            ),
            ProviderModelSpec(
                id="qwen3-max-2026-01-23",
                label="Qwen3 Max",
                description="Qwen3 Max with a 256K-token context window.",
                context_window=262_144,
                supports_tools=True,
                supports_reasoning=True,
            ),
        ),
    ),
    # ModelScope (魔搭社区): OpenAI-compatible API
    ProviderSpec(
        name="modelscope",
        keywords=("modelscope",),
        env_key="MODELSCOPE_API_KEY",
        display_name="ModelScope",
        backend="openai_compat",
        is_gateway=True,
        detect_by_base_keyword="modelscope",
        default_api_base="https://api-inference.modelscope.cn/v1",
        strip_model_prefixes=("modelscope",),
        thinking_style="enable_thinking",
    ),
    # Moonshot (月之暗面): Kimi K2.5/K2.6 choose temperature from thinking mode;
    # the OpenAI-compatible provider omits it. K2.7 models require 1.0.
    ProviderSpec(
        name="moonshot",
        keywords=("moonshot", "kimi"),
        env_key="MOONSHOT_API_KEY",
        display_name="Moonshot",
        backend="openai_compat",
        default_api_base="https://api.moonshot.ai/v1",
        model_overrides=(
            ("kimi-k2.7", {"temperature": 1.0}),
            ("kimi-k2.7-code", {"temperature": 1.0}),
            ("kimi-k2.7-code-highspeed", {"temperature": 1.0}),
        ),
        model_capabilities=(
            ProviderModelSpec(
                id="kimi-k2.5",
                label="Kimi K2.5",
                description="Kimi K2.5 with a 256K-token context window.",
                context_window=262_144,
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="kimi-k2.6",
                label="Kimi K2.6",
                description="Kimi K2.6 with a 256K-token context window.",
                context_window=262_144,
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
        ),
    ),
    # Kimi Coding Plan — Anthropic Messages API at api.kimi.com/coding
    # sk-kimi-* keys; requires User-Agent: claude-code/0.1.0 header.
    ProviderSpec(
        name="kimi_coding",
        keywords=("kimi-coding", "kimi_coding", "kimi-for-coding"),
        env_key="KIMI_CODING_API_KEY",
        display_name="Kimi Coding",
        backend="anthropic",
        default_api_base="https://api.kimi.com/coding/v1",
        default_extra_headers=(("User-Agent", "claude-code/0.1.0"),),
    ),
    # MiniMax: OpenAI-compatible API
    ProviderSpec(
        name="minimax",
        keywords=("minimax",),
        env_key="MINIMAX_API_KEY",
        display_name="MiniMax",
        backend="openai_compat",
        default_api_base="https://api.minimax.io/v1",
        thinking_style="reasoning_split",
        model_capabilities=(
            ProviderModelSpec(
                id="minimax-m2.7",
                label="MiniMax M2.7",
                description="MiniMax M2.7 with a 204.8K-token context window.",
                context_window=204_800,
                supports_tools=True,
                supports_reasoning=True,
            ),
            ProviderModelSpec(
                id="minimax-m2.7-highspeed",
                label="MiniMax M2.7 Highspeed",
                description="MiniMax M2.7 Highspeed with a 204.8K-token context window.",
                context_window=204_800,
                supports_tools=True,
                supports_reasoning=True,
            ),
            ProviderModelSpec(
                id="minimax-m2.5",
                label="MiniMax M2.5",
                description="MiniMax M2.5 with a 204.8K-token context window.",
                context_window=204_800,
                supports_tools=True,
                supports_reasoning=True,
            ),
            ProviderModelSpec(
                id="minimax-m3",
                label="MiniMax M3",
                description="MiniMax M3 with a 192K-token context window.",
                context_window=196_608,
                supports_tools=True,
                supports_reasoning=True,
            ),
        ),
    ),
    # MiniMax Anthropic-compatible endpoint: supports thinking mode
    ProviderSpec(
        name="minimax_anthropic",
        keywords=("minimax_anthropic",),
        env_key="MINIMAX_API_KEY",
        display_name="MiniMax (Anthropic)",
        backend="anthropic",
        default_api_base="https://api.minimax.io/anthropic",
    ),
    # Mistral AI: OpenAI-compatible API.
    # Reasoning quirks:
    #   * mistral-medium-3-5 / mistral-vibe-cli-* accept reasoning_effort but
    #     only "high" or "none" — low/medium/minimal must be remapped.
    #   * Magistral-* models reason implicitly and reject the kwarg entirely.
    #   * Reasoning responses return content as a list of thinking + text
    #     blocks; thinking text gets extracted into reasoning_content.
    ProviderSpec(
        name="mistral",
        keywords=("mistral", "magistral", "ministral", "codestral", "devstral"),
        env_key="MISTRAL_API_KEY",
        display_name="Mistral",
        backend="openai_compat",
        default_api_base="https://api.mistral.ai/v1",
        reasoning_effort_remap=(
            ("minimal", "none"),
            ("low", "none"),
            ("medium", "high"),
            ("high", "high"),
            ("none", "none"),
        ),
        implicit_reasoning_models=("magistral",),
        extract_thinking_blocks=True,
        strip_history_reasoning_content=True,
        model_capabilities=(
            ProviderModelSpec(
                id="mistral-large-2512",
                label="Mistral Large 3",
                description="Mistral Large 3 with a 256K-token context window.",
                context_window=262_144,
                supports_tools=True,
                supports_reasoning=False,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="mistral-medium-3-5",
                label="Mistral Medium 3.5",
                description="Mistral Medium 3.5 with a 256K-token context window.",
                context_window=262_144,
                supports_tools=True,
                supports_reasoning=False,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="mistral-small-2603",
                label="Mistral Small 4",
                description="Mistral Small 4 with a 256K-token context window.",
                context_window=262_144,
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="devstral-2512",
                label="Devstral 2",
                description="Devstral 2 coding model with a 256K-token context window.",
                context_window=262_144,
                supports_tools=True,
                supports_reasoning=False,
            ),
            ProviderModelSpec(
                id="magistral-medium-2509",
                label="Magistral Medium 1.2",
                description="Magistral Medium 1.2 reasoning model with a 128K-token context window.",
                context_window=128_000,
                supports_tools=True,
                supports_reasoning=True,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="codestral",
                label="Codestral",
                description="Codestral with a 128K-token context window.",
                context_window=128_000,
                supports_tools=True,
                supports_reasoning=False,
            ),
            ProviderModelSpec(
                id="ministral-3b-2512",
                label="Ministral 3 3B",
                description="Ministral 3 3B with a 256K-token context window.",
                context_window=262_144,
                supports_tools=True,
                supports_reasoning=False,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="ministral-8b-2512",
                label="Ministral 3 8B",
                description="Ministral 3 8B with a 256K-token context window.",
                context_window=262_144,
                supports_tools=True,
                supports_reasoning=False,
                supports_vision=True,
            ),
            ProviderModelSpec(
                id="ministral-14b-2512",
                label="Ministral 3 14B",
                description="Ministral 3 14B with a 256K-token context window.",
                context_window=262_144,
                supports_tools=True,
                supports_reasoning=False,
                supports_vision=True,
            ),
        ),
    ),
    # Step Fun (阶跃星辰): OpenAI-compatible API
    ProviderSpec(
        name="stepfun",
        keywords=("stepfun", "step"),
        env_key="STEPFUN_API_KEY",
        display_name="Step Fun",
        backend="openai_compat",
        default_api_base="https://api.stepfun.com/v1",
        reasoning_as_content=True,
    ),
    # Xiaomi MIMO (小米): OpenAI-compatible API
    # Hosted API (api.xiaomimimo.com) accepts {"thinking": {"type": "enabled"|"disabled"}}
    # to toggle reasoning, matching the existing thinking_type style.
    ProviderSpec(
        name="xiaomi_mimo",
        keywords=("xiaomi_mimo", "mimo"),
        env_key="XIAOMIMIMO_API_KEY",
        display_name="Xiaomi MIMO",
        backend="openai_compat",
        default_api_base="https://api.xiaomimimo.com/v1",
        thinking_style="thinking_type",
    ),
    # LongCat: OpenAI-compatible API
    ProviderSpec(
        name="longcat",
        keywords=("longcat",),
        env_key="LONGCAT_API_KEY",
        display_name="LongCat",
        backend="openai_compat",
        default_api_base="https://api.longcat.chat/openai/v1",
    ),
    # Ant Ling: OpenAI-compatible API for Ling/Ring model families.
    ProviderSpec(
        name="ant_ling",
        keywords=("ant_ling", "ant-ling", "ling-", "ring-"),
        env_key="ANT_LING_API_KEY",
        display_name="Ant Ling",
        backend="openai_compat",
        detect_by_base_keyword="ant-ling.com",
        default_api_base="https://api.ant-ling.com/v1",
    ),
    # === Local deployment (matched by config key, NOT by api_base) =========
    # vLLM / any OpenAI-compatible local server
    ProviderSpec(
        name="vllm",
        keywords=("vllm",),
        env_key="HOSTED_VLLM_API_KEY",
        display_name="vLLM",
        backend="openai_compat",
        is_local=True,
    ),
    # Ollama (local, OpenAI-compatible)
    ProviderSpec(
        name="ollama",
        keywords=("ollama", "nemotron"),
        env_key="OLLAMA_API_KEY",
        display_name="Ollama",
        backend="openai_compat",
        is_local=True,
        detect_by_base_keyword="11434",
        default_api_base="http://localhost:11434/v1",
    ),
    # LM Studio (local, OpenAI-compatible)
    ProviderSpec(
        name="lm_studio",
        keywords=("lm-studio", "lmstudio", "lm_studio"),
        env_key="LM_STUDIO_API_KEY",
        display_name="LM Studio",
        backend="openai_compat",
        is_local=True,
        detect_by_base_keyword="1234",
        default_api_base="http://localhost:1234/v1",
    ),
    # Atomic Chat (local, OpenAI-compatible) — https://atomic.chat/
    ProviderSpec(
        name="atomic_chat",
        keywords=("atomic-chat", "atomic_chat", "atomicchat"),
        env_key="ATOMIC_CHAT_API_KEY",
        display_name="Atomic Chat",
        backend="openai_compat",
        is_local=True,
        detect_by_base_keyword="1337",
        default_api_base="http://localhost:1337/v1",
    ),
    # === OpenVINO Model Server (direct, local, OpenAI-compatible at /v3) ===
    ProviderSpec(
        name="ovms",
        keywords=("openvino", "ovms"),
        env_key="",
        display_name="OpenVINO Model Server",
        backend="openai_compat",
        is_direct=True,
        is_local=True,
        default_api_base="http://localhost:8000/v3",
    ),
    # === NVIDIA NIM (NVIDIA Inference Microservices) =======================
    # Keys start with "nvapi-", base URL at integrate.api.nvidia.com
    ProviderSpec(
        name="nvidia",
        keywords=("nvidia", "nemotron", "nvapi"),
        env_key="NVIDIA_NIM_API_KEY",
        display_name="NVIDIA NIM",
        backend="openai_compat",
        is_gateway=False,
        detect_by_key_prefix="nvapi-",
        detect_by_base_keyword="nvidia.com",
        default_api_base="https://integrate.api.nvidia.com/v1",
    ),
    # === Auxiliary (not a primary LLM provider) ============================
    # Groq: mainly used for Whisper voice transcription, also usable for LLM
    ProviderSpec(
        name="groq",
        keywords=("groq",),
        env_key="GROQ_API_KEY",
        display_name="Groq",
        backend="openai_compat",
        default_api_base="https://api.groq.com/openai/v1",
    ),
    # AssemblyAI: voice transcription only. It appears in provider settings so
    # users can manage credentials, but WebUI excludes it from chat model pickers.
    ProviderSpec(
        name="assemblyai",
        keywords=("assemblyai",),
        env_key="ASSEMBLYAI_API_KEY",
        display_name="AssemblyAI",
        backend="openai_compat",
        default_api_base="https://api.assemblyai.com/v2",
        is_transcription_only=True,
    ),
    # Qianfan (百度千帆): OpenAI-compatible API
    ProviderSpec(
        name="qianfan",
        keywords=("qianfan", "ernie"),
        env_key="QIANFAN_API_KEY",
        display_name="Qianfan",
        backend="openai_compat",
        default_api_base="https://qianfan.baidubce.com/v2"
    ),
)


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def find_by_name(name: str) -> ProviderSpec | None:
    """Find a provider spec by config field name, e.g. "dashscope"."""
    normalized = to_snake(name.replace("-", "_"))
    for spec in PROVIDERS:
        if spec.name == normalized:
            return spec
    return None


def create_dynamic_spec(
    name: str,
    *,
    display_name: str = "",
    thinking_style: str = "",
) -> ProviderSpec:
    """Create a dynamic ProviderSpec for custom user-defined providers."""
    normalized = to_snake(name.replace("-", "_"))
    strip_prefixes = tuple(dict.fromkeys((name, normalized)))
    return ProviderSpec(
        name=normalized,
        keywords=(),
        env_key="",
        display_name=display_name or name.replace("-", " ").replace("_", " ").title(),
        backend="openai_compat",
        is_direct=True,
        strip_model_prefixes=strip_prefixes,
        thinking_style=thinking_style,
    )


def model_capability_for(provider_name: str, model: str) -> ProviderModelSpec | None:
    """Resolve curated capability metadata, including gateway model IDs."""
    preferred = find_by_name(provider_name) if provider_name else None
    specs = ([preferred] if preferred is not None else []) + list(PROVIDERS)
    normalized_model = model.strip().lower().rstrip("/")
    seen: set[tuple[str, str]] = set()
    for spec in specs:
        for capability in spec.model_capabilities:
            key = (spec.name, capability.id.lower())
            if key in seen:
                continue
            seen.add(key)
            normalized_capability = capability.id.strip().lower().rstrip("/")
            if (
                normalized_model == normalized_capability
                or normalized_model.endswith(f"/{normalized_capability}")
                or normalized_model.rsplit("/", 1)[-1] == normalized_capability
            ):
                return capability
    return None


def builtin_model_for(provider_name: str, model: str) -> ProviderModelSpec | None:
    """Resolve a provider-owned built-in model before cross-provider matching.

    Some OAuth providers intentionally expose model IDs that share a suffix
    with another provider's model family (for example
    ``openai-codex/gpt-5.6-sol`` and ``gpt-5.6-sol``).  Built-in catalogues are
    authoritative for those provider-owned IDs and must not inherit the other
    provider's context limit.
    """
    spec = find_by_name(provider_name) if provider_name else None
    if spec is None:
        return None
    normalized_model = model.strip().lower().rstrip("/")
    for candidate in spec.builtin_models:
        normalized_candidate = candidate.id.strip().lower().rstrip("/")
        if (
            normalized_model == normalized_candidate
            or normalized_model.endswith(f"/{normalized_candidate}")
            or normalized_model.rsplit("/", 1)[-1] == normalized_candidate
        ):
            return candidate
    return None


LEGACY_CONTEXT_WINDOW_DEFAULTS: frozenset[int] = frozenset({128_000, 200_000})
SAFE_UNKNOWN_CONTEXT_WINDOW = 200_000


def context_window_tokens_for(
    provider_name: str,
    model: str,
    configured: int | None = None,
) -> int:
    """Resolve the runtime context budget from model metadata and config.

    The project historically used 128K/200K as generic fallback values. Treat
    those two values as legacy defaults for models with an official registry
    entry, so an existing DeepSeek V4 configuration can be upgraded to 1M
    without requiring the user to edit ``config.json``. Other positive values
    remain explicit user limits, while values above a known provider limit are
    clamped for safety.
    """
    configured_value = configured if isinstance(configured, int) and configured > 0 else None
    capability = builtin_model_for(provider_name, model) or model_capability_for(
        provider_name,
        model,
    )
    known_limit = capability.context_window if capability is not None else None
    if known_limit is None:
        return configured_value or SAFE_UNKNOWN_CONTEXT_WINDOW
    if configured_value is None or configured_value in LEGACY_CONTEXT_WINDOW_DEFAULTS:
        return known_limit
    return min(configured_value, known_limit)


def max_output_tokens_for(provider_name: str, model: str) -> int | None:
    """Return the curated provider output limit when the model is known."""
    capability = model_capability_for(provider_name, model)
    return capability.max_output_tokens if capability is not None else None


_DEFAULT_REASONING_EFFORT_VALUES: tuple[str, ...] = ("", "low", "medium", "high")

_DEEPSEEK_V4_EFFORT_VALUES: tuple[str, ...] = ("", "low", "high", "max")


def reasoning_effort_values_for(provider_name: str, model: str) -> list[str]:
    """Return user-facing reasoning_effort options for this provider+model.

    This is the canonical probe used by both the WebUI (per-preset payloads and
    the ``/api/settings/reasoning-effort-values`` route) and slash commands such
    as ``/effort``. Values are derived from the provider spec (remap tables,
    implicit-reasoning models) plus per-model overrides, never hard-coded in
    the frontend.
    """
    builtin = builtin_model_for(provider_name.strip(), model)
    capability = builtin or model_capability_for(provider_name.strip(), model)
    if capability is not None and capability.reasoning_effort_values:
        return list(capability.reasoning_effort_values)
    if not provider_name.strip():
        return list(_DEFAULT_REASONING_EFFORT_VALUES)
    spec = find_by_name(provider_name) if provider_name else None
    if spec is None:
        return list(_DEFAULT_REASONING_EFFORT_VALUES)

    model_lower = (model or "").lower()
    if model_lower.rsplit("/", 1)[-1] == "kimi-k3":
        return ["", "max"]

    # DeepSeek V4 models expose a low / high / max effort vocabulary through
    # the Responses API; older deepseek-chat models reason implicitly and only
    # get the generic defaults (so the wire value stays absent).
    if spec.name == "deepseek":
        v4_models = spec.responses_models
        if any(pattern in model_lower for pattern in v4_models) or "v4" in model_lower:
            return list(_DEEPSEEK_V4_EFFORT_VALUES)
        return list(_DEFAULT_REASONING_EFFORT_VALUES)

    if spec.implicit_reasoning_models and any(
        pattern in model_lower for pattern in spec.implicit_reasoning_models
    ):
        return [""]

    if spec.reasoning_effort_remap:
        wire_values: list[str] = []
        for _user_value, wire_value in spec.reasoning_effort_remap:
            if wire_value and wire_value != "none" and wire_value not in wire_values:
                wire_values.append(wire_value)
        return ["", *wire_values]

    return list(_DEFAULT_REASONING_EFFORT_VALUES)
