"""The conversation must record the operator's words, not the model enrichment.

Incident 2026-10-22 (Telegram DM with chat 159901607): the history JSONL stored the
web-search context, the ``[UI note]``, the ``[Formatting]`` rules and the
``EXPLORE_Q`` mandate as the *user* message (rows 18 and 20, 06:32:58Z and
06:33:17Z). The next turn replayed that enrichment as if the operator had written
it, and the assistant answered about "injected skill instructions and diagnostic
context" instead of the question.

The model-visible turn may stay enriched — that is the prompt-cache design. The
turn persisted for the operator's conversation must be what the operator typed.
"""

from __future__ import annotations

from navig.agent.conv.agent import ConversationalAgent
from navig.gateway.channels.telegram import _mark_raw_user_message

RAW = "Ты можешь читать переписки DeepSeek на компе моем neuron?"
ENRICHED = (
    "[Web context — via deepseek_search]\n"
    "1. Как Google скомпрометировал DeepSeek — ...\n\n"
    "User message: " + RAW + "\n\n"
    "[UI note] '🔎 Read …' button(s) are attached ...\n\n"
    "[Formatting] You are replying on Telegram, which renders Markdown as rich text. "
    "Use it to make the answer scannable ...\n\n"
    "⚠️ MANDATORY — your response MUST end with this line ...\n"
    "EXPLORE_Q: [item 1] | [item 2] | [item 3] | [item 4] | [item 5]"
)


def test_the_channel_marks_the_operators_raw_text_beside_the_enrichment():
    metadata: dict = {"answer_depth": "deep"}
    _mark_raw_user_message(metadata, RAW)
    assert metadata["raw_user_message"] == RAW
    # Marking twice must not overwrite the first (and real) text.
    _mark_raw_user_message(metadata, "different")
    assert metadata["raw_user_message"] == RAW


def test_the_agent_persists_the_raw_text_not_the_enrichment():
    agent = ConversationalAgent.__new__(ConversationalAgent)
    agent._raw_user_message = RAW

    assert agent._consume_raw_user_text(ENRICHED) == RAW
    assert agent._raw_user_message == "", "the raw text must be single use"


def test_a_turn_without_a_channel_mark_is_persisted_as_typed():
    agent = ConversationalAgent.__new__(ConversationalAgent)

    assert agent._consume_raw_user_text("Это что а где ответ ?") == "Это что а где ответ ?"


def test_a_stale_mark_does_not_label_a_later_agent_to_agent_turn():
    """A channel sets the mark per user turn. A delegated/agent turn has none, and
    must not inherit the previous human message."""
    agent = ConversationalAgent.__new__(ConversationalAgent)
    agent._raw_user_message = RAW
    assert agent._consume_raw_user_text(ENRICHED) == RAW
    assert agent._consume_raw_user_text("summarise the inbox") == "summarise the inbox"


def test_the_enrichment_never_reaches_the_history_through_the_agent():
    """The full path: channel marks → router copies → agent consumes. The value the
    history would receive has none of the internal blocks in it."""
    agent = ConversationalAgent.__new__(ConversationalAgent)
    metadata: dict = {}
    _mark_raw_user_message(metadata, RAW)
    agent._raw_user_message = str(metadata.get("raw_user_message") or "").strip()

    to_persist = agent._consume_raw_user_text(ENRICHED)

    assert to_persist == RAW
    for internal in ("[Formatting]", "[Web context", "[UI note]", "EXPLORE_Q"):
        assert internal not in to_persist, f"{internal} leaked into the recorded user turn"
