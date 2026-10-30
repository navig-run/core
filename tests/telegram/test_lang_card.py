"""Tests for the /lang cards: the picker, the receipt, and the mismatch."""

from __future__ import annotations

import pytest

from navig.telegram import lang_card


def _callbacks(card) -> list[str]:
    return [b["callback_data"] for row in (card.keyboard() or []) for b in row]


def test_the_picker_states_the_current_value_and_offers_every_choice() -> None:
    card = lang_card.picker_card(current="Russian")
    html = card.rich_html()
    assert "<h2>🌍 Output language</h2>" in html
    assert "<i>Russian</i>" in html  # the meta line carries the current value
    assert "<td>Current</td><td><b>Russian</b></td>" in html
    callbacks = _callbacks(card)
    assert "nav:cmd:/lang auto" in callbacks
    assert "nav:cmd:/lang Russian" in callbacks


def test_only_the_current_language_is_marked_and_styled() -> None:
    card = lang_card.picker_card(current="Russian")
    html = card.rich_html()
    assert html.count("✅") == 1
    assert html.count('style="success"') == 1
    marked = [
        b["text"] for row in card.keyboard() for b in row if b["text"].startswith("✅")
    ]
    assert marked == ["✅ Русский"]


def test_auto_is_the_marked_choice_when_nothing_is_set() -> None:
    card = lang_card.picker_card(current="")
    assert "auto (follow the content)" in card.rich_html()
    marked = [b["text"] for row in card.keyboard() for b in row if b["text"].startswith("✅")]
    assert marked == ["✅ Auto"]


def test_the_picker_puts_every_choice_in_a_press_two_per_row() -> None:
    rows = lang_card.picker_card(current="English").keyboard()
    assert len(rows) == 2
    assert len(rows[0]) == 2 and len(rows[1]) == 2


def test_the_receipt_shows_requested_beside_saved() -> None:
    card = lang_card.saved_card(requested="Russian", saved="Russian")
    html = card.rich_html()
    assert "<h2>🌍 Language saved</h2>" in html
    assert "<td>Requested</td><td>Russian</td>" in html
    assert "<td>Saved</td><td><b>Russian</b></td>" in html
    assert "nav:cmd:/lang" in _callbacks(card)


def test_saving_auto_says_auto_not_an_empty_cell() -> None:
    html = lang_card.saved_card(requested="auto", saved="").rich_html()
    assert "auto (follow the content)" in html


def test_a_write_that_did_not_land_is_not_dressed_up_as_success() -> None:
    card = lang_card.mismatch_card(requested="Russian", saved="English")
    html = card.rich_html()
    assert "<h2>⚠️ Language not saved</h2>" in html
    assert "did not persist" in html
    assert "<td>Saved</td><td>English</td>" in html
    assert "Language saved" not in html


def test_no_lang_card_ships_rich_tags_in_its_classic_projection() -> None:
    for card in (
        lang_card.picker_card(current="Russian"),
        lang_card.saved_card(requested="Russian", saved="Russian"),
        lang_card.mismatch_card(requested="Russian", saved="English"),
    ):
        classic = card.classic_html()
        for tag in ("<h2", "<table", "<aside", "<footer"):
            assert tag not in classic, (tag, classic[:160])


@pytest.mark.asyncio
async def test_the_handler_sends_the_picker_with_the_saved_value(monkeypatch) -> None:
    import navig.config as config_mod
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin
    from navig.telegram import card_transport

    recorded: list = []

    async def fake_send(channel, chat_id, card, **kw):
        recorded.append(card)
        return {"message_id": 1}

    monkeypatch.setattr(card_transport, "send_card", fake_send)

    class _CM:
        def get(self, key, default=None):
            return "Russian"

        def set_global(self, key, value):
            return None

    monkeypatch.setattr(config_mod, "ConfigManager", _CM)

    await TelegramCommandsMixin._handle_lang(object(), 1, 2, "/lang")

    assert len(recorded) == 1
    assert "Russian" in recorded[0].rich_html()
