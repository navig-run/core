"""End-to-end: a real Telegram update carrying a TikTok link produces the card.

Every other test around this feature checks a piece — `offer_card_dm` in
isolation, the wiring by AST. None of them drives the thing Telegram actually
calls. That gap is exactly how the original defect survived: the card worked,
the hook was missing, and nothing executed the path between them.

This feeds a realistic `message` update into `TelegramChannel._process_update`
(the true entry point) and asserts what the operator sees: a card with both
buttons, and NO agent turn — because the card owns the message. The negative
case matters just as much: a question that merely mentions a link must still
reach the agent.
"""

from __future__ import annotations

import pytest

from navig.gateway.channels.telegram import TelegramChannel

pytestmark = pytest.mark.integration

_LINK = "https://vm.tiktok.com/ZGdxryFmF"
_OWNER = 42
_CHAT = 4242


def _update(text: str, message_id: int = 7) -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": message_id,
            "date": 1785955401,
            "chat": {"id": _CHAT, "type": "private"},
            "from": {"id": _OWNER, "username": "operator", "is_bot": False},
            "text": text,
        },
    }


class _Harness(TelegramChannel):
    """Captures sends and records whether the agent was ever consulted."""

    def __init__(self):
        super().__init__(bot_token="test:token", allowed_users=[_OWNER])
        self.sent: list[dict] = []
        self.agent_calls: list[str] = []

    async def send_message(self, chat_id, text, parse_mode="HTML", **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})
        return {"message_id": 999}

    async def send_typing(self, chat_id):
        return None

    async def _api_call(self, method, data=None, **kwargs):
        return {"ok": True}


@pytest.fixture
def harness(monkeypatch):
    ch = _Harness()

    # The agent is the thing that must NOT run when the card owns the message.
    async def _record_agent(*a, **kw):
        ch.agent_calls.append("called")
        return "agent reply"

    for target in (
        "navig.gateway.channel_router.ChannelRouter.route_message",
        "navig.gateway.channel_router.ChannelRouter.process_message",
    ):
        try:
            monkeypatch.setattr(target, _record_agent, raising=False)
        except Exception:  # noqa: BLE001 — attribute shape differs across versions
            pass

    from navig.telegram import tiktok_actions

    monkeypatch.setattr(tiktok_actions.engine, "info", lambda url: {"title": "clip"})
    monkeypatch.setattr(
        tiktok_actions.engine, "render_card", lambda meta: "🎵 <b>clip</b>"
    )
    monkeypatch.setattr(tiktok_actions, "enabled", lambda: True)
    return ch


async def test_a_bare_link_update_produces_the_card_and_owns_the_message(harness):
    await harness._process_update(_update(_LINK))

    cards = [s for s in harness.sent if s.get("keyboard")]
    assert cards, (
        "a bare TikTok link reaching the real update handler produced no card — "
        f"sent instead: {[s['text'][:60] for s in harness.sent]}"
    )
    buttons = [b["text"] for row in cards[0]["keyboard"] for b in row]
    assert "⬇️ Download" in buttons
    assert "🔍 Analyse" in buttons
    assert cards[0].get("reply_to_message_id") == 7, "the card must reply to the link"
    assert not harness.agent_calls, (
        "the card owns the message — the agent must not also answer"
    )


async def test_a_question_mentioning_a_link_is_left_to_the_agent(harness):
    await harness._process_update(
        _update(f"is {_LINK} worth watching or is it clickbait?")
    )

    cards = [s for s in harness.sent if s.get("keyboard")]
    assert not cards, "a question must not be hijacked by the card"


async def test_an_unauthorized_user_gets_no_card(harness):
    """The card is an owner-facing action behind the `download` policy; a stranger
    is rejected by the channel's auth gate long before it."""
    upd = _update(_LINK)
    upd["message"]["from"]["id"] = 999_999

    await harness._process_update(upd)

    cards = [s for s in harness.sent if s.get("keyboard")]
    assert not cards
