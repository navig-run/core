"""
Regression: provider clients must serialize tool-call history correctly, or a
multi-turn native tool round-trip 400s on turn 2.

- OpenAI: assistant messages keep `tool_calls`; `tool` messages keep
  `tool_call_id` (OpenAI 400s a 'tool' message without it).
- Anthropic: assistant `tool_calls` → `tool_use` blocks (arguments JSON parsed
  to `input`); `role='tool'` → `tool_result` blocks merged into ONE `user`
  message ('tool' is not a valid Anthropic role, and results must share a turn).
"""

from __future__ import annotations

from navig.providers.clients import (
    Message,
    _anthropic_wire_messages,
    _openai_tool_choice,
    _openai_wire_messages,
)


def _tool_turn():
    return [
        Message(role="user", content="hi"),
        Message(role="assistant", content="", tool_calls=[
            {"id": "c1", "type": "function", "function": {"name": "foo", "arguments": '{"x": 1}'}},
        ]),
        Message(role="tool", content="RESULT-A", tool_call_id="c1"),
        Message(role="tool", content="RESULT-B", tool_call_id="c2"),
    ]


def test_openai_preserves_tool_fields():
    wire = _openai_wire_messages(_tool_turn())
    assert wire[1]["tool_calls"][0]["id"] == "c1"       # assistant keeps tool_calls
    assert wire[2]["tool_call_id"] == "c1"              # tool message keeps id
    assert wire[3]["tool_call_id"] == "c2"


def test_anthropic_translates_tool_calls_and_results():
    wire = _anthropic_wire_messages(_tool_turn())
    # roles: user, assistant(tool_use), user(tool_result x2) — no bare 'tool' role
    assert [m["role"] for m in wire] == ["user", "assistant", "user"]
    assert all(m["role"] != "tool" for m in wire)

    assistant = wire[1]
    tool_use = [b for b in assistant["content"] if b.get("type") == "tool_use"]
    assert tool_use and tool_use[0]["input"] == {"x": 1}  # arguments parsed to a dict
    assert tool_use[0]["name"] == "foo"

    results_msg = wire[2]
    blocks = results_msg["content"]
    assert len(blocks) == 2                              # both results in ONE user message
    assert {b["tool_use_id"] for b in blocks} == {"c1", "c2"}
    assert all(b["type"] == "tool_result" for b in blocks)


def test_anthropic_bad_arguments_do_not_crash():
    msgs = [Message(role="assistant", content="", tool_calls=[
        {"id": "c1", "type": "function", "function": {"name": "foo", "arguments": "not-json"}},
    ])]
    wire = _anthropic_wire_messages(msgs)
    tu = [b for b in wire[0]["content"] if b["type"] == "tool_use"][0]
    assert tu["input"] == {}  # unparseable args degrade to empty dict, not a crash


def test_openai_tool_choice_named_vs_bare():
    assert _openai_tool_choice("auto") == "auto"
    assert _openai_tool_choice("none") == "none"
    assert _openai_tool_choice(None) is None
    assert _openai_tool_choice("foo") == {"type": "function", "function": {"name": "foo"}}


def test_plain_messages_unaffected():
    msgs = [Message(role="user", content="hi"), Message(role="assistant", content="yo")]
    assert _openai_wire_messages(msgs) == [
        {"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"},
    ]
    assert _anthropic_wire_messages(msgs) == [
        {"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"},
    ]
