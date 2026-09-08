# Model capability registry

pawbot keeps curated model capabilities in
[`pawbot/providers/registry.py`](../pawbot/providers/registry.py). The registry
is used when a provider does not expose complete metadata, and it also upgrades
legacy 128K/200K context fallbacks when a known model has a larger official
window. A user-selected smaller window is preserved as a manual override.

When an API returns model metadata, the WebUI prefers the provider response for
that model and uses the curated registry to fill missing fields. Unknown models
remain editable and use the safe 200K fallback until the user chooses a value.

## Curated context windows

| Provider / family | Representative model IDs | Context window | Max output when documented |
| --- | --- | ---: | ---: |
| DeepSeek V4 | `deepseek-v4-flash`, `deepseek-v4-pro`, `deepseek-v4-flash-vision-exp` | 1,048,576 | 384,000 |
| OpenAI GPT-5.6 / GPT-5.5 / GPT-5.4 | `gpt-5.6-*`, `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini` | 1,050,000 | 128,000 |
| OpenAI GPT-4.1 | `gpt-4.1`, `gpt-4.1-mini`, `gpt-4.1-nano` | 1,047,576 | 32,768 |
| Claude 4.6 / 5 | `claude-opus-5`, `claude-sonnet-5`, `claude-opus-4-6`, `claude-sonnet-4-6` | 1,000,000 | 128,000 |
| Gemini long-context models | `gemini-3.*`, `gemini-2.5-pro`, `gemini-2.5-flash` | 1,000,000 | Provider-reported |
| Qwen 3.5–3.7 | `qwen3.5-plus`, `qwen3.6-plus`, `qwen3.7-plus`, `qwen3.7-max` | 1,000,000 | 65,536–131,072 |
| GLM coding endpoint | `glm-5.2[1m]` | 1,000,000 | Provider-reported |
| GLM standard routing | `glm-5.2`, `glm-5`, `glm-4.7` | 202,752 | Provider-reported |
| Mistral Large / Small / Devstral | `mistral-large-2512`, `mistral-small-2603`, `devstral-2512` | 262,144 | Provider-reported |
| MiniMax | `minimax-m2.5`, `minimax-m2.7` | 204,800 | Provider-reported |
| Kimi | `kimi-k2.5`, `kimi-k2.6` | 262,144 | Provider-reported |

## Source references

- [DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)
- [OpenAI model catalog](https://developers.openai.com/api/docs/models)
- [Anthropic model context limits](https://docs.anthropic.com/en/api/claude-on-amazon-bedrock)
- [Google Gemini models](https://ai.google.dev/gemini-api/docs/models)
- [Alibaba Model Studio model catalog](https://help.aliyun.com/en/model-studio/text-generation-model/)
- [Mistral context limits](https://docs.mistral.ai/resources/known-limitations)
- [MiniMax text generation models](https://platform.minimaxi.com/docs/guides/text-generation)

The table is deliberately conservative: a provider-specific endpoint can
override these values through its `/models` response, while an unknown model is
never assumed to support a larger window merely because its name resembles a
known family.
