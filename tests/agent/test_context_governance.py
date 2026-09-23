from pawbot.agent.context_governance import ContextGovernanceConfig, ContextGovernor
from pawbot.agent.tools.registry import ToolRegistry


class _CharacterCounter:
    class _Generation:
        max_tokens = 0

    generation = _Generation()

    @staticmethod
    def estimate_prompt_tokens(messages, tools=None, model=None):
        del model
        return sum(
            len(str(message.get("content") or ""))
            + len(str(message.get("tool_calls") or ""))
            for message in messages
        ) + len(str(tools or [])), "fixture_character_counter"


def _assistant_tool_call(call_id: str) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": "exec", "arguments": "{}"},
        }],
    }


def test_drop_orphan_tool_results_drops_missing_tool_call_id() -> None:
    messages = [
        _assistant_tool_call("call_1"),
        {"role": "tool", "name": "exec", "content": "missing id"},
        {"role": "tool", "tool_call_id": "call_1", "name": "exec", "content": "ok"},
    ]

    result = ContextGovernor.drop_orphan_tool_results(messages)

    assert [m.get("tool_call_id") for m in result if m.get("role") == "tool"] == ["call_1"]


def test_drop_orphan_tool_results_drops_duplicate_tool_result() -> None:
    messages = [
        _assistant_tool_call("call_1"),
        {"role": "tool", "tool_call_id": "call_1", "name": "exec", "content": "first"},
        {"role": "tool", "tool_call_id": "call_1", "name": "exec", "content": "duplicate"},
    ]

    result = ContextGovernor.drop_orphan_tool_results(messages)

    tool_results = [m for m in result if m.get("role") == "tool"]
    assert len(tool_results) == 1
    assert tool_results[0]["content"] == "first"


def test_context_eval_preserves_current_constraint_and_legal_tool_tail(tmp_path) -> None:
    governor = ContextGovernor()
    messages = [
        {"role": "system", "content": "Follow the safety policy."},
        {"role": "user", "content": "old request " + "x" * 700},
        {"role": "assistant", "content": "old answer " + "y" * 700},
        {
            "role": "user",
            "content": "Current constraint: inspect only; do not write files.",
        },
        _assistant_tool_call("current-read"),
        {
            "role": "tool",
            "name": "read_file",
            "tool_call_id": "current-read",
            "content": "current file evidence",
        },
    ]
    config = ContextGovernanceConfig(
        provider=_CharacterCounter(),
        model="fixture-model",
        tools=ToolRegistry(),
        workspace=tmp_path,
        session_key="eval:context",
        max_tool_result_chars=4_096,
        context_window_tokens=1_400,
        max_tokens=0,
    )

    compacted = governor.snip_history(config, messages)
    rendered = str(compacted)
    tool_call_ids = {
        call["id"]
        for message in compacted
        for call in message.get("tool_calls", [])
    }
    tool_result_ids = {
        message.get("tool_call_id")
        for message in compacted
        if message.get("role") == "tool"
    }

    assert "old request" not in rendered
    assert "Current constraint: inspect only; do not write files." in rendered
    assert "current file evidence" in rendered
    assert "current-read" in tool_call_ids & tool_result_ids
