import json

from pawbot.agent import token_estimation
from pawbot.utils import helpers
from pawbot.utils.helpers import (
    estimate_message_tokens,
    estimate_prompt_tokens,
    estimate_prompt_tokens_chain,
    truncate_text_to_tokens,
)


class _NoCounterProvider:
    pass


class _BrokenCounterProvider:
    def estimate_prompt_tokens(self, messages, tools=None, model=None):
        raise RuntimeError("counter unavailable")


def test_estimate_prompt_tokens_chain_falls_back_without_provider_counter() -> None:
    tokens, source = estimate_prompt_tokens_chain(
        _NoCounterProvider(),
        "test-model",
        [{"role": "user", "content": "hello"}],
    )

    assert tokens > 0
    assert source == "tiktoken:cl100k_base:generic_fallback"


def test_estimate_prompt_tokens_chain_falls_back_when_provider_counter_fails() -> None:
    tokens, source = estimate_prompt_tokens_chain(
        _BrokenCounterProvider(),
        "test-model",
        [{"role": "user", "content": "hello"}],
    )

    assert tokens > 0
    assert source == "tiktoken:cl100k_base:generic_fallback"


def test_estimate_prompt_tokens_uses_conservative_fallback_when_tiktoken_fails(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        helpers,
        "_get_token_encoding",
        lambda: (_ for _ in ()).throw(RuntimeError("encoding unavailable")),
    )

    content = "你" * 1_000
    messages = [{"role": "user", "content": content}]
    tokens = estimate_prompt_tokens(messages)
    chain_tokens, source = estimate_prompt_tokens_chain(
        _NoCounterProvider(),
        "test-model",
        messages,
    )

    actual_tokens = len(helpers.tiktoken.get_encoding("cl100k_base").encode(content)) + 4
    assert tokens == len(content.encode("utf-8")) + 4
    assert tokens >= actual_tokens
    assert chain_tokens == tokens
    assert source == "heuristic:utf8_bytes"


def test_estimate_message_tokens_uses_canonical_byte_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        token_estimation,
        "_encoding",
        lambda _model: None,
    )
    content = "🙂你" * 100

    expected = (len(content.encode("utf-8")) + 3) // 4 + 4
    assert estimate_message_tokens({"role": "user", "content": content}) == (
        expected
    )


def test_truncate_text_to_tokens_uses_utf8_byte_budget_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        helpers,
        "_get_token_encoding",
        lambda: (_ for _ in ()).throw(RuntimeError("encoding unavailable")),
    )

    result = truncate_text_to_tokens("🙂你" * 100, 40)

    assert result.endswith("\n... (truncated)")
    assert len(result.encode("utf-8")) <= 40


def test_estimate_prompt_tokens_caches_tools_encoding(monkeypatch) -> None:
    helpers._get_token_encoding.cache_clear()
    helpers._TOOLS_TOKEN_CACHE.clear()

    class FakeEncoding:
        def __init__(self) -> None:
            self.encoded: list[str] = []

        def encode(self, text: str) -> list[int]:
            self.encoded.append(text)
            return list(range(max(1, len(text) // 4)))

    fake_encoding = FakeEncoding()
    get_encoding_calls = 0

    def fake_get_encoding(name: str) -> FakeEncoding:
        nonlocal get_encoding_calls
        assert name == "cl100k_base"
        get_encoding_calls += 1
        return fake_encoding

    monkeypatch.setattr(helpers.tiktoken, "get_encoding", fake_get_encoding)
    tools = [{"type": "function", "function": {"name": "demo", "description": "cached"}}]
    messages = [{"role": "user", "content": "hello"}]

    first = estimate_prompt_tokens(messages, tools)
    second = estimate_prompt_tokens(messages, tools)

    assert first == second
    assert get_encoding_calls == 1
    rendered_tools = "\n" + json.dumps(tools, ensure_ascii=False)
    assert fake_encoding.encoded.count(rendered_tools) == 1


def test_estimate_prompt_tokens_recomputes_when_tool_items_change(monkeypatch) -> None:
    helpers._get_token_encoding.cache_clear()
    helpers._TOOLS_TOKEN_CACHE.clear()

    class FakeEncoding:
        def __init__(self) -> None:
            self.encoded: list[str] = []

        def encode(self, text: str) -> list[int]:
            self.encoded.append(text)
            return list(range(max(1, len(text) // 4)))

    fake_encoding = FakeEncoding()
    monkeypatch.setattr(helpers.tiktoken, "get_encoding", lambda _name: fake_encoding)

    tools = [{"type": "function", "function": {"name": "before"}}]
    messages = [{"role": "user", "content": "hello"}]
    estimate_prompt_tokens(messages, tools)

    tools[0] = {"type": "function", "function": {"name": "after"}}
    estimate_prompt_tokens(messages, tools)

    before_tools = "\n" + json.dumps(
        [{"type": "function", "function": {"name": "before"}}], ensure_ascii=False
    )
    after_tools = "\n" + json.dumps(tools, ensure_ascii=False)
    assert before_tools in fake_encoding.encoded
    assert after_tools in fake_encoding.encoded


def test_model_families_select_and_report_their_tokenizer(monkeypatch) -> None:
    helpers._get_token_encoding.cache_clear()
    selected: list[str] = []

    class FakeEncoding:
        def __init__(self, name: str) -> None:
            self.name = name

        def encode(self, text: str) -> list[int]:
            return list(range(len(text)))

    def fake_get_encoding(name: str) -> FakeEncoding:
        selected.append(name)
        return FakeEncoding(name)

    monkeypatch.setattr(helpers.tiktoken, "get_encoding", fake_get_encoding)
    messages = [{"role": "user", "content": "你好"}]
    o_tokens, o_source = estimate_prompt_tokens_chain(
        _NoCounterProvider(), "gpt-4o-mini", messages
    )
    legacy_tokens, legacy_source = estimate_prompt_tokens_chain(
        _NoCounterProvider(), "vendor-unknown-model", messages
    )

    assert o_tokens > 0 and legacy_tokens > 0
    assert "o200k_base" in selected
    assert "cl100k_base" in selected
    assert o_source == "tiktoken:o200k_base:model_family"
    assert legacy_source == "tiktoken:cl100k_base:generic_fallback"
    assert token_estimation.tokenizer_encoding_name("gpt-4o") == "o200k_base"
    assert token_estimation.tokenizer_encoding_name("vendor-unknown-model") == "cl100k_base"


def test_tool_schema_token_cache_is_separate_per_encoding(monkeypatch) -> None:
    helpers._get_token_encoding.cache_clear()
    helpers._TOOLS_TOKEN_CACHE.clear()

    class FakeEncoding:
        def __init__(self, name: str) -> None:
            self.name = name

        def encode(self, text: str) -> list[int]:
            width = 2 if self.name == "o200k_base" else 1
            return list(range(max(1, len(text) // width)))

    monkeypatch.setattr(
        helpers.tiktoken,
        "get_encoding",
        lambda name: FakeEncoding(name),
    )
    tools = [{"type": "function", "function": {"name": "lookup", "description": "x" * 400}}]
    messages = [{"role": "user", "content": "hi"}]

    o_tokens = estimate_prompt_tokens(messages, tools, model="gpt-4o")
    legacy_tokens = estimate_prompt_tokens(messages, tools, model="unknown-vendor")

    assert o_tokens < legacy_tokens
