"""Tests for the cross-channel notification system: the per-type×channel matrix,
the deck feed, and the router's fan-out gating (master toggle + quiet hours)."""

from __future__ import annotations

from datetime import datetime

import pytest


@pytest.fixture
def notify(tmp_path, monkeypatch):
    """Isolate notify.db and reset the module init flag."""
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    from navig.notify import store
    monkeypatch.setattr(store, "_initialised", False)
    store.init_db()
    from navig.notify import prefs, feed
    from navig.notify.router import get_notification_router
    return prefs, feed, get_notification_router()


def test_matrix_defaults_and_toggle(notify):
    prefs, _feed, _router = notify
    m = prefs.get_matrix()
    # Seeded from the type defaults.
    assert m["reminder"]["deck"] is True and m["reminder"]["telegram"] is True
    assert m["reminder"]["sms"] is False
    assert prefs.is_enabled("reminder", "deck") is True
    assert prefs.is_enabled("reminder", "sms") is False

    prefs.set_cell("reminder", "sms", True)
    assert prefs.is_enabled("reminder", "sms") is True
    assert "sms" in prefs.enabled_channels("reminder")

    with pytest.raises(ValueError):
        prefs.set_cell("nope", "deck", True)


def test_feed_crud(notify):
    _prefs, feed, _router = notify
    a = feed.append("reminder", "A", "body a")
    feed.append("briefing", "B", "body b")
    assert feed.unread_count() == 2
    items = feed.list_items()
    assert [i["title"] for i in items] == ["B", "A"]  # newest first
    assert feed.mark_read(a["id"]) is True
    assert feed.unread_count() == 1
    assert feed.mark_all_read() == 1
    assert feed.unread_count() == 0
    assert feed.list_items(unread_only=True) == []


async def test_router_deck_only(notify):
    prefs, feed, router = notify
    # reminder defaults to deck+telegram; restrict to deck for a deterministic test.
    prefs.set_cell("reminder", "telegram", False)
    r = await router.dispatch("reminder", "Pay invoice", "Due today")
    assert [c["channel"] for c in r["channels"]] == ["deck"]
    assert r["channels"][0]["ok"] is True
    items = feed.list_items()
    assert len(items) == 1 and items[0]["title"] == "Pay invoice"


async def test_router_master_off(notify):
    prefs, feed, router = notify
    prefs.set_setting("master_enabled", False)
    r = await router.dispatch("reminder", "X", "y")
    assert r.get("skipped") == "master_off"
    assert feed.unread_count() == 0


async def test_router_quiet_hours_mutes_non_deck(notify):
    prefs, feed, router = notify
    prefs.set_cell("reminder", "telegram", True)
    h = datetime.now().hour
    prefs.set_setting("quiet_hours_enabled", True)
    prefs.set_setting("quiet_hours_start", h)
    prefs.set_setting("quiet_hours_end", (h + 1) % 24)

    # Non-critical during quiet hours → only the silent deck channel fires.
    r = await router.dispatch("reminder", "quiet", "shh", priority="normal")
    assert [c["channel"] for c in r["channels"]] == ["deck"]

    # Critical bypasses quiet hours → telegram is attempted (fails gracefully,
    # since no NotificationManager is configured in the test).
    r2 = await router.dispatch("reminder", "loud", "now", priority="critical")
    chans = {c["channel"] for c in r2["channels"]}
    assert "deck" in chans and "telegram" in chans
    tg = next(c for c in r2["channels"] if c["channel"] == "telegram")
    assert tg["ok"] is False  # not configured in tests


async def test_router_settings_targets_roundtrip(notify):
    prefs, _feed, _router = notify
    prefs.set_setting("target_sms", "+15551234567")
    prefs.set_setting("briefing_channels", ["deck", "email"])
    s = prefs.get_settings()
    assert s["targets"]["sms"] == "+15551234567"
    assert s["briefing_channels"] == ["deck", "email"]
    assert prefs.get_target("sms") == "+15551234567"
