"""How ``/start`` reads as a card: the greeting, the recap folded away, one way in.

The greeting is still words — one or two warm sentences. These tests pin the envelope:
the card shape, the folded "last time" recap (one bubble, not two), the Menu button in
the bubble, and a real link into the Deck.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from navig.gateway.channels.telegram_commands import TelegramCommandsMixin
from navig.telegram import greeting_card as gc

GREETING = "Good afternoon, Nino. The audit letter is still waiting on your answer."
AWAY = "You were pricing the cabinet renewal and left two drafts open."
DECK = "https://deck.example/connect?key=abc"


def _build(**kwargs: Any) -> tuple[str, str, list[list[dict]] | None]:
    text = kwargs.pop("greeting", GREETING)
    kw = {"name": "nafets", "tod": "afternoon", "away": AWAY, "deck_url": DECK}
    kw.update(kwargs)
    return gc.build(text, **kw)


def test_the_greeting_is_a_card_with_a_time_and_a_handle() -> None:
    _, rich, _ = _build()
    assert rich.startswith("<h2>🧭 NAVIG</h2>")
    assert "<p><i>Good afternoon · @nafets</i></p>" in rich
    assert f"<p>{GREETING}</p>" in rich


def test_the_recap_folds_away_instead_of_a_second_bubble() -> None:
    classic, rich, _ = _build()
    assert "<details><summary>Last time we talked</summary>" in rich
    assert AWAY in rich
    # The classic projection has no <details>, so it folds inside an expandable quote.
    assert "<blockquote expandable>" in classic
    assert "<details>" not in classic


def test_the_way_in_sits_in_the_bubble_and_points_at_the_hub() -> None:
    _, rich, keyboard = _build()
    assert '<tg-button-row align="left"><tg-button type="callback_data" data="hm:home"' in rich
    assert 'style="primary">🧭 Menu</tg-button>' in rich
    assert keyboard == [[{"text": "🧭 Menu", "callback_data": "hm:home", "style": "primary"}]]


def test_the_deck_link_is_a_real_anchor_in_both_projections() -> None:
    classic, rich, _ = _build()
    assert f'<a href="{DECK}">Open the Deck</a>' in rich
    assert f'<a href="{DECK}">Open the Deck</a>' in classic


def test_no_deck_url_means_no_anchor_but_still_a_card() -> None:
    classic, rich, keyboard = _build(deck_url="")
    assert "<a href=" not in rich and "<a href=" not in classic
    assert "Everything else opens from the Menu" in rich
    assert keyboard  # the Menu button does not depend on the link


def test_a_greeting_with_a_pause_keeps_both_paragraphs() -> None:
    two = f"{GREETING}\n\nWant me to keep an eye on the mailroom today?"
    _, rich, _ = _build(greeting=two)
    assert rich.count("<p>") >= 2
    assert "<p>Want me to keep an eye on the mailroom today?</p>" in rich


def test_an_unknown_time_of_day_is_simply_not_said() -> None:
    _, rich, _ = _build(tod="whenever")
    assert "@nafets" in rich
    assert "Good whenever" not in rich and "None" not in rich


def test_the_classic_projection_carries_no_rich_only_tag() -> None:
    classic, _, _ = _build()
    for tag in ("<h2>", "<details>", "<tg-button", "<table"):
        assert tag not in classic


class _Channel:
    """A channel that remembers what left it."""

    def __init__(self, *, rich: bool = True) -> None:
        self.rich = rich
        self.rich_sent: list[dict[str, Any]] = []
        self.plain_sent: list[dict[str, Any]] = []

    async def send_rich_message(self, chat_id: int, **kwargs: Any) -> dict[str, Any] | None:
        if not self.rich:
            return None
        self.rich_sent.append({"chat": chat_id, **kwargs})
        return {"message_id": 41}

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> dict[str, Any]:
        self.plain_sent.append({"chat": chat_id, "text": text, **kwargs})
        return {"message_id": 42}


def _send(channel: _Channel, **kwargs: Any) -> None:
    kw = {"username": "@nafets", "tod": "afternoon", "away": AWAY}
    kw.update(kwargs)
    asyncio.run(
        TelegramCommandsMixin._send_greeting_card(channel, 7, GREETING, **kw)  # type: ignore[arg-type]
    )


@pytest.fixture(autouse=True)
def _deck_url_from_config(monkeypatch):
    """The link comes from config; a test must not depend on the machine's install."""
    monkeypatch.setattr("navig.cloud.rotation.menu_button_url", lambda *a, **k: DECK, raising=False)


def test_the_handler_sends_one_rich_message_and_no_plain_one() -> None:
    channel = _Channel()
    _send(channel)
    assert len(channel.rich_sent) == 1 and channel.plain_sent == []
    payload = channel.rich_sent[0]
    assert payload["fallback_html"]
    assert "<h2>🧭 NAVIG</h2>" in payload["html"]
    assert payload["keyboard"][0][0]["callback_data"] == "hm:home"


def test_a_channel_without_rich_support_still_gets_the_card_as_html() -> None:
    channel = _Channel(rich=False)
    _send(channel)
    assert channel.rich_sent == []
    assert len(channel.plain_sent) == 1
    assert channel.plain_sent[0]["parse_mode"] == "HTML"
    assert "🧭 <b>NAVIG</b>" in channel.plain_sent[0]["text"]


def test_the_channel_can_reach_the_greeting_helper() -> None:
    """/start runs with the CHANNEL as ``self``, and the channel has no mixins.

    Without this delegation the helper is an AttributeError in production while every
    mixin-level test above still passes, because the mixin owns the method.
    """
    from navig.gateway.channels.telegram import TelegramChannel

    assert hasattr(TelegramChannel, "_send_greeting_card")
    assert asyncio.iscoroutinefunction(TelegramChannel._send_greeting_card)


def test_a_broken_card_falls_back_to_the_words() -> None:
    channel = _Channel()

    def _boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("card kit exploded")

    original = gc.greeting_card
    gc.greeting_card = _boom  # type: ignore[assignment]
    try:
        _send(channel)
    finally:
        gc.greeting_card = original  # type: ignore[assignment]
    assert len(channel.plain_sent) == 1
    assert channel.plain_sent[0]["text"] == GREETING
    assert channel.plain_sent[0]["parse_mode"] is None
