"""Tests for the Business cards: the shared list, whois, dns — and the no-callback rule."""

from __future__ import annotations

import pytest

from navig.telegram import biz_card

ITEMS = [
    {"list_seq": 1, "title": "buy paper", "done": False, "when_label": "fri"},
    {"list_seq": 2, "title": "call the bank", "done": False, "created_by": 7},
    {"list_seq": 3, "title": "send invoice", "done": True},
]


def _callbacks(card) -> list[str]:
    return [
        btn.get("callback_data")
        for row in (card.keyboard() or [])
        for btn in row
        if btn.get("callback_data")
    ]


def _urls(card) -> list[str]:
    return [btn.get("url", "") for row in (card.keyboard() or []) for btn in row if btn.get("url")]


def test_the_list_is_a_table_with_both_halves_counted() -> None:
    card = biz_card.todo_card(items=ITEMS, owner_id=7)
    html = card.rich_html()
    assert "<h2>📋 To-do</h2>" in html
    assert "2 open · 1 done" in html
    assert "<th>#</th><th>Item</th><th>When</th>" in html
    assert "<s>☑ send invoice</s>" in html
    assert "<i>fri</i>" in html


def test_an_item_the_contact_added_says_who_added_it() -> None:
    card = biz_card.todo_card(items=ITEMS, owner_id=99, peer_name="Sam")
    assert "added by Sam" in card.rich_html()


def test_the_owners_own_items_carry_no_provenance() -> None:
    card = biz_card.todo_card(items=[ITEMS[1]], owner_id=7, peer_name="Sam")
    assert "added by Sam" not in card.rich_html()


def test_an_empty_list_says_how_to_start_one() -> None:
    html = biz_card.todo_card(items=[]).rich_html()
    assert "type todo" in html
    assert "<table" not in html


def test_a_business_card_never_offers_a_callback_button() -> None:
    """The contact's presses never reach the bot, so a callback button would be half-dead."""
    for card in (
        biz_card.todo_card(items=ITEMS, board_url="https://deck.example/board"),
        biz_card.whois_card(domain="example.com", fields={"registrar": "ACME"}),
        biz_card.dns_card(domain="example.com", records=[("A", "93.184.216.34")]),
    ):
        assert _callbacks(card) == [], _callbacks(card)


def test_the_copy_button_carries_the_list_as_plain_text() -> None:
    card = biz_card.todo_card(items=ITEMS)
    payload = card.keyboard()[0][0]["copy_text"]["text"]
    assert payload.startswith("[ ] #1 buy paper (fri)")
    assert "[x] #3 send invoice" in payload


def test_the_board_link_is_a_url_button_when_a_deck_exists() -> None:
    card = biz_card.todo_card(items=ITEMS, board_url="https://deck.example/board")
    assert _urls(card) == ["https://deck.example/board"]
    without = biz_card.todo_card(items=ITEMS)
    assert _urls(without) == []


def test_the_hint_states_that_everything_is_typed() -> None:
    html = biz_card.todo_card(items=ITEMS).rich_html()
    assert "done N" in html
    assert "Everything works by typing" in html


def test_a_whois_card_skips_the_keys_that_did_not_answer() -> None:
    card = biz_card.whois_card(
        domain="example.com",
        fields={"registrar": "ACME Registrar", "expires": "2027-01-01"},
    )
    html = card.rich_html()
    assert "<td>Domain</td><td><code>example.com</code></td>" in html
    assert "ACME Registrar" in html
    assert "2027-01-01" in html
    assert "Created" not in html  # absent field is omitted, not blank


def test_a_whois_card_with_no_facts_says_so() -> None:
    card = biz_card.whois_card(domain="nowhere.test", fields={})
    assert "No registry answer" in card.rich_html()


def test_a_dns_card_is_a_type_value_table() -> None:
    card = biz_card.dns_card(
        domain="example.com", records=[("A", "93.184.216.34"), ("MX", "mail.example.com")]
    )
    html = card.rich_html()
    assert "<th>Type</th><th>Value</th>" in html
    assert "<code>A</code>" in html and "93.184.216.34" in html
    assert "mail.example.com" in html


def test_a_dns_card_with_no_records_claims_nothing() -> None:
    html = biz_card.dns_card(domain="example.com", records=[]).rich_html()
    assert "No records answered" in html
    assert "<table" not in html


def test_no_business_card_ships_rich_tags_in_its_classic_projection() -> None:
    for card in (
        biz_card.todo_card(items=ITEMS, board_url="https://deck.example/b"),
        biz_card.whois_card(domain="example.com", fields={"registrar": "ACME"}),
        biz_card.dns_card(domain="example.com", records=[("A", "1.2.3.4")]),
    ):
        classic = card.classic_html()
        for tag in ("<h2", "<table", "<aside", "<footer"):
            assert tag not in classic, (tag, classic[:160])


@pytest.mark.asyncio
async def test_the_dns_lookup_asks_doh_and_parses_answers(monkeypatch) -> None:
    from navig.telegram import biz_lookups

    calls: list[str] = []

    async def fake_get_json(url, *, timeout=8.0, headers=None):
        calls.append(url)
        if "type=A&" not in url and not url.endswith("type=A"):
            return {}
        return {"Answer": [{"data": "93.184.216.34"}, {"data": "93.184.216.34"}]}

    monkeypatch.setattr(biz_lookups, "_get_json", fake_get_json)
    records = await biz_lookups.dns_records("example.com")

    assert records == [("A", "93.184.216.34")]  # the duplicate is dropped
    assert all("cloudflare-dns.com" in url for url in calls)
    assert any("type=MX" in url for url in calls)


@pytest.mark.asyncio
async def test_the_dns_lookup_refuses_a_bare_word(monkeypatch) -> None:
    from navig.telegram import biz_lookups

    async def boom(*a, **k):
        raise AssertionError("a bare word must not reach the network")

    monkeypatch.setattr(biz_lookups, "_get_json", boom)
    assert await biz_lookups.dns_records("localhost") == []

def test_a_reminder_card_states_when_and_how_it_repeats() -> None:
    card = biz_card.reminder_card(
        text="⏰ Reminder — about the invoice",
        when="tomorrow at 10:00",
        repeat="every week",
    )
    html = card.rich_html()
    assert "<h2>⏰ Reminder</h2>" in html
    assert "tomorrow at 10:00" in html
    assert "every week" in html
    assert "Snooze works for the owner" in html
    assert card.keyboard() is None  # the caller attaches the owner's rows


def test_a_one_off_reminder_has_no_repeat_row() -> None:
    html = biz_card.reminder_card(text="x", when="today at 09:00").rich_html()
    assert "Repeats" not in html


def test_the_reminder_card_keeps_its_classic_projection_clean() -> None:
    classic = biz_card.reminder_card(text="x", when="today", repeat="daily").classic_html()
    for tag in ("<h2", "<table", "<footer"):
        assert tag not in classic
