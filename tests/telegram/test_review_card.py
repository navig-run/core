"""Tests for the /cards review index."""

from __future__ import annotations

import pytest

from navig.telegram import review_card


def _callbacks(card) -> list[str]:
    return [b["callback_data"] for row in (card.keyboard() or []) for b in row]


def test_the_index_names_every_family_it_holds() -> None:
    total = len(review_card.FAMILIES)
    assert total >= 12
    assert review_card.page_count() == -(-total // review_card.PER_PAGE)


def test_the_first_page_states_its_numbers_and_its_families() -> None:
    card = review_card.review_card(0)
    html = card.rich_html()
    assert "<h2>🗂️ Card index</h2>" in html
    assert f"page 1 of {review_card.page_count()}" in html
    assert f"{len(review_card.FAMILIES)} families" in html
    assert "<th>Family</th><th>What it shows</th>" in html
    first_label, first_command, _ = review_card.FAMILIES[0]
    assert first_label in html
    # The command behind each family is printed, so the index teaches as well as opens.
    assert first_command in html


def test_every_visible_family_has_a_press_that_runs_its_command() -> None:
    first_page = review_card.page_families(0)
    callbacks = _callbacks(review_card.review_card(0))
    for _label, command, _what in first_page:
        assert f"nav:cmd:{command}" in callbacks


def test_the_press_payload_is_the_command_not_a_private_copy() -> None:
    callbacks = [cb for cb in _callbacks(review_card.review_card(0)) if cb.startswith("nav:cmd:")]
    commands = {f"nav:cmd:{command}" for _l, command, _w in review_card.page_families(0)}
    # Family presses name their command verbatim; the only other nav:cmd press is paging.
    assert commands <= set(callbacks)
    assert {cb for cb in callbacks if cb.startswith("nav:cmd:/cards")} == {"nav:cmd:/cards 1"}


def test_paging_carries_the_page_number_and_rewrites_in_place() -> None:
    first = _callbacks(review_card.review_card(0))
    assert "nav:cmd:/cards 1" in first  # next page, 1-based
    assert "nav:cmd:/cards -1" not in first
    assert not any(cb == "nav:cmd:/cards 0" for cb in first)  # no "prev" on page 1


def test_a_middle_page_offers_both_directions() -> None:
    middle = _callbacks(review_card.review_card(1))
    assert "nav:cmd:/cards 0" in middle  # 1-based 1 → index 0
    assert "nav:cmd:/cards 2" in middle


def test_the_last_page_offers_no_next() -> None:
    last = review_card.page_count() - 1
    callbacks = _callbacks(review_card.review_card(last))
    assert f"nav:cmd:/cards {last}" not in callbacks
    assert f"nav:cmd:/cards {last - 1}" in callbacks


def test_a_page_beyond_the_end_is_clamped_not_blank() -> None:
    card = review_card.review_card(99)
    assert "page" in card.rich_html()
    assert len(review_card.page_families(99)) > 0


def test_the_index_always_offers_the_way_home() -> None:
    assert "nav:home" in _callbacks(review_card.review_card(0))


@pytest.mark.parametrize(
    ("text", "expected"),
    [("/cards", 0), ("/cards 2", 1), ("/cards 0", 0), ("/cards nonsense", 0), ("", 0)],
)
def test_the_page_parser(text: str, expected: int) -> None:
    assert review_card.parse_page(text) == expected


def test_the_index_keeps_its_classic_projection_clean() -> None:
    classic = review_card.review_card(0).classic_html()
    for tag in ("<h2", "<table", "<aside", "<footer"):
        assert tag not in classic, (tag, classic[:160])


@pytest.mark.asyncio
async def test_the_handler_edits_the_pressed_message(monkeypatch) -> None:
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin
    from navig.telegram import card_transport

    recorded: dict[str, list] = {"sends": [], "edits": []}

    async def fake_send(channel, chat_id, card, **kw):
        recorded["sends"].append(card)
        return {"message_id": 1}

    async def fake_edit(channel, chat_id, message_id, card, **kw):
        recorded["edits"].append((message_id, card))
        return True

    monkeypatch.setattr(card_transport, "send_card", fake_send)
    monkeypatch.setattr(card_transport, "edit_card", fake_edit)

    await TelegramCommandsMixin._handle_cards_index(object(), 1, 2, "/cards 2", message_id=77)

    assert recorded["sends"] == []
    assert recorded["edits"][0][0] == 77
    assert "page 2 of" in recorded["edits"][0][1].rich_html()
