"""The noise report and per-channel delivery health.

Two things that were invisible before: how much NAVIG has been sending (the feed
counts existed but nothing read them), and whether a channel's LAST delivery
worked (the router computed ok/detail and threw it away — the broken SMS sender
hid behind that for weeks).
"""

from __future__ import annotations

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    from navig.notify import store

    monkeypatch.setattr(store, "_initialised", False)
    store.init_db()
    assert str(store._db_path()).startswith(str(tmp_path))
    return store


# ── noise report ─────────────────────────────────────────────────────────────


def test_noise_counts_the_last_seven_days_per_type(env):
    from datetime import datetime, timedelta, timezone

    from navig.notify import feed

    feed.append("sale", "a")
    feed.append("sale", "b")
    feed.append("reminder", "c")
    # An old row must NOT count: the report is "how noisy is it right now".
    store = env
    c = store.conn()
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
    with c:
        c.execute("UPDATE notify_feed SET created_at = ? WHERE title = ?", (old, "b"))

    stats = feed.stats(days=7)
    assert stats["days"] == 7
    assert stats["total"] == 2, stats  # the 30-day-old row is excluded
    assert stats["by_type"][0] == {
        "type": "sale",
        "count": 1,
        "first": stats["by_type"][0]["first"],
        "last": stats["by_type"][0]["last"],
    }
    counts = {r["type"]: r["count"] for r in stats["by_type"]}
    assert counts == {"sale": 1, "reminder": 1}


def test_noise_is_empty_on_a_fresh_install(env):
    from navig.notify import feed

    assert feed.stats()["total"] == 0 and feed.stats()["by_type"] == []


def test_noise_is_presented_to_the_mini_app(env):
    from navig.notify import feed
    from navig.notify.types import NOTIFICATION_TYPES

    feed.append("sale", "x")
    for t in NOTIFICATION_TYPES:  # the surface every card rows out of
        assert t["key"], t


# ── delivery health ──────────────────────────────────────────────────────────


def test_health_records_state_changes_only(env):
    from navig.notify import health

    health.record("telegram", True, "sent", now=1000.0)
    first = health.snapshot()["telegram"]
    assert first["ok"] is True and first["last_ok_at"] == 1000.0

    # Same outcome → no write (the hot path must not write per notification).
    health.record("telegram", True, "sent", now=2000.0)
    assert health.snapshot()["telegram"]["updated_at"] == 1000.0

    # …but a failure is always worth recording, and it starts a new state.
    health.record("telegram", False, "chat not found", now=3000.0)
    row = health.snapshot()["telegram"]
    assert row["ok"] is False
    assert row["detail"] == "chat not found"
    assert row["state_since"] == 3000.0
    assert row["last_ok_at"] == 1000.0, "last success must survive a later failure"

    health.record("telegram", False, "chat not found", now=4000.0)
    assert health.snapshot()["telegram"]["state_since"] == 3000.0, "state did not re-start"


def test_a_healthy_channel_refreshes_at_most_hourly(env):
    from navig.notify import health

    health.record("deck", True, "sent", now=0.0)
    health.record("deck", True, "sent", now=10.0)  # 10s later: nothing to do
    assert health.snapshot()["deck"]["last_ok_at"] == 0.0
    health.record("deck", True, "sent", now=7200.0)  # 2h later: refresh the marker
    assert health.snapshot()["deck"]["last_ok_at"] == 7200.0


def test_an_unreadable_store_never_costs_a_delivery(env, monkeypatch):
    from navig.notify import health

    monkeypatch.setattr(health, "_db", lambda: (_ for _ in ()).throw(RuntimeError("disk gone")))
    health.record("sms", False, "x")  # must not raise
    assert health.snapshot() == {}


def test_the_router_records_health_for_every_channel(env, monkeypatch):
    import asyncio

    from navig.gateway import notifications
    from navig.notify import feed, health, prefs, router

    prefs.set_setting("master_enabled", True)
    prefs.set_cell("reminder", "deck", True)
    prefs.set_cell("reminder", "telegram", False)

    class _NM:
        def get_channel(self, _name):
            return None

    monkeypatch.setattr(notifications, "get_notification_manager", lambda: _NM())

    out = asyncio.run(router.get_notification_router().dispatch("reminder", "hi"))
    assert [c["channel"] for c in out["channels"]] == ["deck"]
    assert health.snapshot()["deck"]["ok"] is True
    assert feed.unread_count() >= 1  # it also landed in the feed
