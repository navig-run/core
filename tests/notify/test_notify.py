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
    from navig.notify import feed, prefs
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


# ── a producer's notification type MUST be registered, or its push is dropped ──
#
# The router resolves channels via enabled_channels(type_key), which reads the
# notify_matrix seeded ONLY from NOTIFICATION_TYPES. A producer that dispatches an
# UNREGISTERED type_key gets zero channels → the notification is silently dropped. The
# config-incident producer (#279) shipped exactly this bug: it pushed "config_incident",
# which was never registered, so every config-rescue alert went nowhere.


def test_config_incident_type_is_registered_and_routes(notify):
    prefs, _feed, _router = notify
    from navig.notify.types import TYPE_KEYS

    assert "config_incident" in TYPE_KEYS, "the config-incident push type must be registered"
    # Seeded to the same channels as the other NAVIG self-health alerts.
    assert prefs.enabled_channels("config_incident") == ["deck", "telegram"]


def test_the_config_incident_producer_dispatches_a_registered_type(notify):
    """Pin the producer's declared type against the registry — the two cannot drift into
    the dropped-notification bug again."""
    prefs, _feed, _router = notify
    from navig.notify.producers.config_incidents import NOTIFY_TYPE
    from navig.notify.types import TYPE_KEYS

    assert NOTIFY_TYPE in TYPE_KEYS
    assert prefs.enabled_channels(NOTIFY_TYPE), "the producer's type must route to ≥1 channel"


def test_every_first_party_producer_type_is_registered(notify):
    """Every type a NAVIG producer emits must be a known, routable notify type — the class
    guard for the whole dropped-notification bug."""
    prefs, _feed, _router = notify
    from navig.notify.types import TYPE_KEYS

    # self_error (self_errors), connectivity (ConnectivityReporter), deploy (events),
    # config_incident (config_incidents) — the first-party producers that dispatch.
    for t in ("self_error", "connectivity", "deploy", "config_incident"):
        assert t in TYPE_KEYS, f"producer type {t!r} is not registered — its push would drop"
        assert prefs.enabled_channels(t), f"producer type {t!r} routes to no channel"


def test_config_incident_has_its_own_emoji():
    from navig.notify.types import emoji_for_type

    assert emoji_for_type("config_incident") == "🩺"
    assert emoji_for_type("config_incident") != emoji_for_type("does_not_exist")  # not the fallback


# ── the deletion alert: a live dispatch of an unregistered type (was dropped) ──


def test_message_deleted_type_is_registered_and_dms_telegram(notify):
    """The business-chat deletion alert dispatches 'message_deleted' with
    only_channels=['telegram']. It was UNREGISTERED, so it resolved to zero channels and
    the owner's 'message deleted' DM never arrived."""
    prefs, _feed, _router = notify
    from navig.notify.types import TYPE_KEYS

    assert "message_deleted" in TYPE_KEYS
    assert "telegram" in prefs.enabled_channels("message_deleted")


def test_the_deletion_alert_dispatches_a_registered_type():
    """Cross-check the caller in telegram/business.py against the registry — the two
    can't drift back into the silent-drop bug."""
    import inspect

    from navig.notify.types import TYPE_KEYS
    from navig.telegram import business

    src = inspect.getsource(business.handle_deleted_business_messages)
    import re
    m = re.search(r"dispatch\(\s*[\"']([a-z_]+)[\"']", src)
    assert m, "expected a literal dispatch type in the deletion alert"
    assert m.group(1) in TYPE_KEYS, f"deletion alert dispatches unregistered type {m.group(1)!r}"


# ── router fail-safe: an unregistered type is delivered + warned, never dropped ─


async def test_unregistered_type_falls_back_to_deck_and_warns(notify, caplog):
    _prefs, _feed, router = notify
    import logging

    with caplog.at_level(logging.WARNING, logger="navig.notify.router"):
        res = await router.dispatch("some_unregistered_type", "T", "b")

    assert [c["channel"] for c in res["channels"]] == ["deck"], "must not be silently dropped"
    assert any("UNREGISTERED" in r.message for r in caplog.records), "the drop must be LOUD"


async def test_registered_but_fully_muted_type_stays_silent(notify):
    """The fail-safe must NEVER override a user's explicit choice. A registered type the
    user disabled on every channel legitimately delivers nowhere."""
    prefs, _feed, router = notify
    for ch in ("deck", "telegram", "email", "sms"):
        try:
            prefs.set_cell("reminder", ch, False)
        except Exception:
            pass

    res = await router.dispatch("reminder", "T", "b")

    assert res["channels"] == [], "a user-muted registered type must not be fallback-delivered"
