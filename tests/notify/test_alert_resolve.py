"""An alert can be resolved from its own card, and stays resolved.

Before this, nothing in the Telegram layer recorded that a person had *seen* an
alert: Pushover alone tracked ``acknowledged``. So a monitor whose condition the
operator had already cleared re-sent the same words on its next run, and there was
no button to say otherwise. The record is keyed on the alert's own words, so a
different alert is a different key and still arrives.
"""

from __future__ import annotations

import asyncio

import pytest

from navig.notify import acks, notify_card


@pytest.fixture
def store(tmp_path, monkeypatch):
    """An isolated notify.db, so a test never writes the operator's own acks."""
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    from navig.notify import store as notify_store

    monkeypatch.setattr(notify_store, "_initialised", False)
    notify_store.init_db()
    assert str(notify_store._db_path()).startswith(str(tmp_path))
    return notify_store


def test_an_alert_card_offers_resolve_and_a_brief_does_not():
    key = acks.key_for("nmdb.service failed", "the unit restarted 4 times")
    rows = notify_card.keyboard("system_alert", "nmdb.service failed", key=key)
    assert rows is not None
    assert rows[-1][0]["text"] == "✓ Resolve"
    assert rows[-1][0]["callback_data"] == f"cd:nak:done:{key}"

    # A brief is a report; "resolve" is not a thing you do to it.
    brief = notify_card.keyboard("briefing", "TASKS  3 due", key=key)
    assert all("✓ Resolve" not in button["text"] for row in brief or [] for button in row)


def test_the_resolve_button_is_absent_without_a_key():
    # No key means the alert was not resolvable (or the caller did not ask), and a
    # button that records nothing is worse than no button.
    assert notify_card.keyboard("system_alert", "x") is None


def test_only_a_condition_type_is_resolvable():
    assert notify_card.resolvable("system_alert")
    assert notify_card.resolvable("security_alert")
    assert notify_card.resolvable("agent_failed")
    assert not notify_card.resolvable("briefing")
    assert not notify_card.resolvable("sale")


def test_the_press_records_the_resolution_where_the_card_was(store):
    from navig.telegram import notice_cards

    key = acks.key_for("nmdb.service failed", "4 restarts")
    assert not acks.acknowledged(key)

    screen = asyncio.run(notice_cards.handle_ack(type("A", (), {"key": key})()))
    assert screen.title.startswith("✓ Resolved")
    assert acks.acknowledged(key)
    assert screen.body == "This alert will not be sent again unless it changes."


def test_a_different_alert_is_not_swallowed_by_an_old_resolution(store):
    first = acks.key_for("nmdb.service failed", "4 restarts")
    second = acks.key_for("nmdb.service failed", "9 restarts")
    assert first != second
    assert acks.acknowledge(first)
    assert acks.acknowledged(first)
    assert not acks.acknowledged(second)


def test_a_resolution_can_be_forgotten_when_the_condition_returns(store):
    key = acks.key_for("disk", "91% full")
    assert acks.acknowledge(key)
    acks.forget(key)
    assert not acks.acknowledged(key)


def test_the_router_skips_an_alert_it_has_already_seen_resolved(store):
    from navig.notify.router import _alert_resolution

    key, resolved = _alert_resolution("system_alert", "nmdb.service failed", "4 restarts")
    assert (key, resolved) == (acks.key_for("nmdb.service failed", "4 restarts"), False)

    assert acks.acknowledge(key)
    assert _alert_resolution("system_alert", "nmdb.service failed", "4 restarts")[1] is True
    # Same unit, different evidence: a new alert, and it must arrive.
    assert _alert_resolution("system_alert", "nmdb.service failed", "9 restarts")[1] is False


def test_a_non_alert_type_is_never_resolved(store):
    from navig.notify.router import _alert_resolution

    assert _alert_resolution("briefing", "Brief 2026-10-26", "TASKS  3 due") == ("", False)
