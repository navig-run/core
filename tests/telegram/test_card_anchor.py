"""The anchor registry and ``publish_card`` — the fix for cards that repost.

The defect these pin down: a scheduled surface rebuilt its card every run and sent a new
message, because nothing remembered the message the previous run had sent. On 2026-10-26/27
that produced duplicate cards 22:00/22:28, 22:03/22:29 and 00:57/00:58.
"""

from __future__ import annotations

import json

import pytest

from navig.telegram import card_anchor, card_transport
from navig.telegram.card_doc import Card, Para


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "anchors.json"
    monkeypatch.setenv("NAVIG_CARD_ANCHOR_PATH", str(path))
    yield path
    card_anchor.clear_all()


def _card(text: str = "Vacation 2026") -> Card:
    card = Card(text)
    card.add(Para("same body"))
    return card


# ── the registry ─────────────────────────────────────────────────────────────


def test_an_anchor_round_trips(ledger) -> None:
    card_anchor.set("vacation", 42, 900, sha="abc", fails=0)
    entry = card_anchor.get("vacation", 42)
    assert entry == {"message_id": 900, "sha": "abc", "fails": 0}
    assert json.loads(ledger.read_text(encoding="utf-8"))["42:vacation"]["message_id"] == 900


def test_anchors_are_scoped_per_chat_and_surface() -> None:
    card_anchor.set("tasks", 1, 111)
    card_anchor.set("tasks", 2, 222)
    card_anchor.set("reminders", 1, 333)
    assert card_anchor.get("tasks", 1)["message_id"] == 111
    assert card_anchor.get("tasks", 2)["message_id"] == 222
    assert card_anchor.get("reminders", 1)["message_id"] == 333
    assert card_anchor.get("reminders", 2) is None


def test_forget_reports_whether_it_removed_anything() -> None:
    card_anchor.set("vacation", 42, 900)
    assert card_anchor.forget("vacation", 42) is True
    assert card_anchor.forget("vacation", 42) is False
    assert card_anchor.get("vacation", 42) is None


def test_a_corrupt_ledger_is_ignored_not_fatal(ledger) -> None:
    ledger.write_text("{not json", encoding="utf-8")
    assert card_anchor.get("vacation", 42) is None  # no raise
    card_anchor.set("vacation", 42, 7)  # and it can be written again
    assert card_anchor.get("vacation", 42)["message_id"] == 7


def test_entries_missing_a_field_are_dropped(ledger) -> None:
    ledger.write_text(json.dumps({"42:vacation": {"message_id": 5}}), encoding="utf-8")
    assert card_anchor.get("vacation", 42) is None


# ── publish_card ─────────────────────────────────────────────────────────────


class _Calls:
    def __init__(self) -> None:
        self.sent: list[Card] = []
        self.edited: list[tuple[int, Card]] = []
        self.next_id = 500
        self.edit_ok = True

    def install(self, monkeypatch) -> None:
        async def send(channel, chat_id, card, **kw):
            self.sent.append(card)
            self.next_id += 1
            return {"message_id": self.next_id}

        async def edit(channel, chat_id, message_id, card, **kw):
            self.edited.append((message_id, card))
            return self.edit_ok

        monkeypatch.setattr(card_transport, "send_card", send)
        monkeypatch.setattr(card_transport, "edit_card", edit)


@pytest.mark.asyncio
async def test_the_first_publish_sends_and_remembers(monkeypatch) -> None:
    calls = _Calls()
    calls.install(monkeypatch)

    result = await card_transport.publish_card(object(), 42, _card(), surface="vacation")

    assert result["edited"] is False and result["skipped"] is False
    assert len(calls.sent) == 1 and calls.edited == []
    entry = card_anchor.get("vacation", 42)
    assert entry is not None and entry["message_id"] == result["message_id"]
    assert entry["sha"], "the fingerprint must be stored with the message id"


@pytest.mark.asyncio
async def test_an_unchanged_card_is_not_sent_again(monkeypatch) -> None:
    calls = _Calls()
    calls.install(monkeypatch)

    first = await card_transport.publish_card(object(), 42, _card(), surface="vacation")
    second = await card_transport.publish_card(object(), 42, _card(), surface="vacation")

    assert second["skipped"] is True
    assert second["reason"] == "unchanged"
    assert second["message_id"] == first["message_id"]
    assert len(calls.sent) == 1
    assert calls.edited == [], "an unchanged card must not even be edited"


@pytest.mark.asyncio
async def test_a_changed_card_edits_the_anchored_message(monkeypatch) -> None:
    calls = _Calls()
    calls.install(monkeypatch)

    first = await card_transport.publish_card(object(), 42, _card(), surface="vacation")
    second = await card_transport.publish_card(object(), 42, _card("Vacation 2026 · day 2"), surface="vacation")

    assert second["edited"] is True
    assert second["message_id"] == first["message_id"]
    assert len(calls.sent) == 1, "no second message"
    assert [mid for mid, _ in calls.edited] == [first["message_id"]]


@pytest.mark.asyncio
async def test_a_keyboard_change_alone_still_edits(monkeypatch) -> None:
    from navig.telegram.card_doc import Button

    calls = _Calls()
    calls.install(monkeypatch)

    one = _card()
    one.buttons(Button("A", data="a"))
    two = _card()
    two.buttons(Button("B", data="b"))

    await card_transport.publish_card(object(), 42, one, surface="vacation")
    result = await card_transport.publish_card(object(), 42, two, surface="vacation")

    assert result["edited"] is True
    assert len(calls.sent) == 1


@pytest.mark.asyncio
async def test_a_single_edit_failure_does_not_repost(monkeypatch) -> None:
    calls = _Calls()
    calls.install(monkeypatch)

    first = await card_transport.publish_card(object(), 42, _card(), surface="vacation")
    calls.edit_ok = False
    second = await card_transport.publish_card(object(), 42, _card("day 2"), surface="vacation")

    assert second["skipped"] is True
    assert "edit failed" in second["reason"]
    assert len(calls.sent) == 1, "a transient failure must never duplicate the card"
    entry = card_anchor.get("vacation", 42)
    assert entry is not None and entry["fails"] == 1
    assert entry["message_id"] == first["message_id"]


@pytest.mark.asyncio
async def test_the_second_consecutive_failure_replaces_a_dead_anchor(monkeypatch) -> None:
    calls = _Calls()
    calls.install(monkeypatch)

    first = await card_transport.publish_card(object(), 42, _card(), surface="vacation")
    calls.edit_ok = False
    await card_transport.publish_card(object(), 42, _card("day 2"), surface="vacation")
    third = await card_transport.publish_card(object(), 42, _card("day 3"), surface="vacation")

    assert third["skipped"] is False
    assert len(calls.sent) == 2, "the dead message is replaced exactly once"
    entry = card_anchor.get("vacation", 42)
    assert entry is not None and entry["message_id"] == third["message_id"]
    assert entry["message_id"] != first["message_id"]


@pytest.mark.asyncio
async def test_a_send_that_returns_nothing_records_no_anchor(monkeypatch) -> None:
    async def send(channel, chat_id, card, **kw):
        return None

    async def edit(channel, chat_id, message_id, card, **kw):
        raise AssertionError("nothing to edit")

    monkeypatch.setattr(card_transport, "send_card", send)
    monkeypatch.setattr(card_transport, "edit_card", edit)

    result = await card_transport.publish_card(object(), 42, _card(), surface="vacation")

    assert result["message_id"] == 0
    assert card_anchor.get("vacation", 42) is None
