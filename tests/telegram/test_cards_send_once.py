"""A Telegram card is an event, not a broadcast: never deliver the same card twice.

The 2026-10-22 review card is sent by ``navig tasks review`` (outpost cron job_26,
08:30 local) through :func:`navig.telegram.cards.send_card` — a separate CLI process
with no memory of what it already sent. Two identical tasks in the digest made that
card present the same Telegram contact twice, and a duplicated/retried run would
deliver the whole card twice. The transport boundary now owns "one card, one
delivery": a byte-identical card to the same chat inside the window is withheld;
a changed card or another chat is a fresh delivery.
"""

from __future__ import annotations

import asyncio

import pytest

from navig.telegram import cards
from navig.telegram.cards import CardButton, Context, Screen


@pytest.fixture(autouse=True)
def _fresh_notify_db():
    """Each test gets its own data dir (conftest), but notify.store.init_db() is gated
    by a module-global flag — without this reset the second test's fresh DB has no
    tables and the claim fails open (sends the duplicate)."""
    from navig.notify import store

    store._initialised = False
    yield
    store._initialised = False


def _screen(
    title: str = "NAVIG · 2 task(s) due",
    body: str | None = None,
    *,
    kind: str = cards.SUMMARY,
) -> Screen:
    body = body or "· Reply to Rakib (Telegram 1131897) — overdue\n· Reply to Rakib (Telegram 1131897) — overdue"
    return Screen(
        title=title,
        body=body,
        kind=kind,
        rows=((CardButton("Done", action="done", callback_data="cd:nav:done:t1"),),),
    )


def _capture(monkeypatch) -> list[dict]:
    sent: list[dict] = []

    async def fake_api(channel, method, payload):
        sent.append(dict(payload))
        return {"message_id": len(sent)}

    monkeypatch.setattr(cards, "_api", fake_api)
    return sent


def test_an_identical_card_to_the_same_chat_is_sent_once(monkeypatch):
    sent = _capture(monkeypatch)
    ctx = Context("nav", "review")

    first = asyncio.run(cards.send_card(990001, _screen(), ctx))
    second = asyncio.run(cards.send_card(990001, _screen(), ctx))

    assert first, "the first delivery must reach Telegram"
    assert second == {}, "the duplicate must be withheld"
    assert len(sent) == 1


def test_a_changed_card_is_a_new_delivery(monkeypatch):
    sent = _capture(monkeypatch)
    ctx = Context("nav", "review")

    asyncio.run(cards.send_card(990002, _screen(kind=cards.DETAIL), ctx))
    asyncio.run(cards.send_card(990002, _screen(title="NAVIG · 1 task(s) due", kind=cards.DETAIL), ctx))

    assert len(sent) == 2


def test_another_chat_gets_its_own_card(monkeypatch):
    sent = _capture(monkeypatch)
    ctx = Context("nav", "review")

    asyncio.run(cards.send_card(990003, _screen(), ctx))
    asyncio.run(cards.send_card(990004, _screen(), ctx))

    assert len(sent) == 2


def test_another_card_kind_with_the_same_text_is_a_new_delivery(monkeypatch):
    """``kind``/``key`` name the owning event; two different card owners may legitimately
    render the same text (e.g. a generic header)."""
    sent = _capture(monkeypatch)
    screen = _screen(body="hello")

    asyncio.run(cards.send_card(990005, screen, Context("nav", "review")))
    asyncio.run(cards.send_card(990005, screen, Context("mr", "draft-1")))

    assert len(sent) == 2


def test_a_changed_summary_card_is_withheld_the_same_day(monkeypatch):
    """The review digest is built from live rows, so a second run changes its text.
    A digest is one per chat per local day — the 10-minute fingerprint would miss it."""
    sent = _capture(monkeypatch)
    ctx = Context("nav", "review")

    first = asyncio.run(cards.send_card(990006, _screen(), ctx))
    changed = asyncio.run(
        cards.send_card(990006, _screen(title="NAVIG · 1 task(s) due"), ctx)
    )

    assert first
    assert changed == {}
    assert len(sent) == 1


def test_the_same_summary_card_is_sent_again_the_next_day(monkeypatch):
    sent = _capture(monkeypatch)
    ctx = Context("nav", "review")
    asyncio.run(cards.send_card(990007, _screen(), ctx))

    monkeypatch.setattr(cards, "_local_day", lambda: "2999-01-01")
    # A new day with a changed digest: both the daily key and the text fingerprint differ.
    again = asyncio.run(cards.send_card(990007, _screen(title="NAVIG · 1 task(s) due"), ctx))

    assert again
    assert len(sent) == 2


def test_a_detail_card_is_not_day_limited(monkeypatch):
    sent = _capture(monkeypatch)
    ctx = Context("probe", "draft-9")
    asyncio.run(cards.send_card(990008, _screen(kind=cards.DETAIL), ctx))
    asyncio.run(cards.send_card(990008, _screen(title="changed", kind=cards.DETAIL), ctx))
    assert len(sent) == 2
