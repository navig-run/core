"""Daily briefing freshness — the cache used to never expire.

`_load_cache()` returned the on-disk briefing forever, so the "Daily" briefing
froze at whenever it was first built: it went on quoting a stale revenue figure
while the Finance app showed the real one, and only the Regenerate button ever
moved it. A briefing is now stale (→ rebuilt on the next read) when it is from a
previous calendar day or older than `_MAX_AGE`.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("aiohttp")

from navig.gateway.deck.routes import briefing as br


def _made(delta: timedelta) -> dict:
    return {"generated_at": (datetime.now() - delta).isoformat(), "headline": "x"}


# ── freezing "now" — without this, freshness tests are a time-bomb ───────────
#
# _is_stale has TWO rules: older than _MAX_AGE, OR from a previous calendar day.
# A test that builds a "_MAX_AGE - 1min" stamp from the real clock therefore lands
# on YESTERDAY whenever the suite runs between 00:00 and _MAX_AGE (06:00) — the
# briefing is then correctly stale, and the "is kept" assertion blows up. It passed
# all day and failed at night; it sat red on main for exactly that reason. Freeze
# the clock at midday so a 6h-old stamp stays on the same calendar day and each rule
# is asserted on purpose rather than by accident of wall-clock time.

_NOON = datetime(2026, 1, 15, 12, 0, 0)
_JUST_AFTER_MIDNIGHT = datetime(2026, 1, 15, 0, 5, 0)


def _freeze(monkeypatch, when: datetime) -> datetime:
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: D102
            return when if tz is None else when.replace(tzinfo=tz)

    monkeypatch.setattr(br, "datetime", _Frozen)
    return when


@pytest.fixture
def at_noon(monkeypatch):
    """Midday: a stamp up to _MAX_AGE old still belongs to the SAME calendar day, so
    the max-age rule can be asserted without the day rule interfering."""
    return _freeze(monkeypatch, _NOON)


def _made_at(now: datetime, delta: timedelta) -> dict:
    return {"generated_at": (now - delta).isoformat(), "headline": "x"}


def test_fresh_briefing_is_kept(at_noon):
    assert br._is_stale(_made_at(at_noon, timedelta(minutes=5))) is False
    assert br._is_stale(_made_at(at_noon, br._MAX_AGE - timedelta(minutes=1))) is False


def test_the_day_rule_beats_max_age(monkeypatch):
    """The time-bomb, pinned as behaviour: a briefing YOUNGER than _MAX_AGE is still
    stale once it belongs to a previous calendar day. Rebuilding a "Daily" briefing on
    a new day is the whole point — so do NOT "fix" a red freshness test by deleting the
    day rule. Only reachable just after midnight, which is exactly why it lurked."""
    now = _freeze(monkeypatch, _JUST_AFTER_MIDNIGHT)
    yesterday_late = now.replace(hour=23, minute=59) - timedelta(days=1)

    assert now - yesterday_late < br._MAX_AGE, "fixture must be younger than _MAX_AGE"
    assert br._is_stale({"generated_at": yesterday_late.isoformat()}) is True


def test_briefing_past_max_age_is_stale():
    assert br._is_stale(_made(br._MAX_AGE + timedelta(minutes=1))) is True


def test_briefing_from_a_previous_day_is_stale():
    """The bug in one line: a briefing built yesterday must not be served today,
    even if _MAX_AGE has not elapsed (e.g. 23:59 → 00:05)."""
    now = datetime.now()
    yesterday_late = (now - timedelta(days=1)).replace(hour=23, minute=59, second=0, microsecond=0)
    assert br._is_stale({"generated_at": yesterday_late.isoformat()}) is True


def test_missing_or_broken_timestamp_is_stale():
    # Better one extra build than serving a frozen briefing forever.
    assert br._is_stale(None) is True
    assert br._is_stale({}) is True
    assert br._is_stale({"generated_at": ""}) is True
    assert br._is_stale({"generated_at": "not-a-date"}) is True
    assert br._is_stale("nonsense") is True


def test_timezone_aware_timestamp_is_tolerated():
    """Old cache files may carry a tz-aware stamp — must not raise."""
    aware = datetime.now(timezone.utc).astimezone().isoformat()
    assert br._is_stale({"generated_at": aware}) is False


async def test_stale_briefing_is_served_immediately_and_refreshed_behind(monkeypatch):
    """Building blocks on an LLM polish that can take MINUTES (measured: >4min on
    a real daemon). A stale read must return the cached copy at once and refresh
    in the background — never hang the dashboard's briefing card."""
    import asyncio

    stale = {"generated_at": (datetime.now() - timedelta(days=2)).isoformat(), "headline": "old"}
    monkeypatch.setattr(br, "_CACHE", stale)
    monkeypatch.setattr(br, "_cache_path", lambda: None)

    built = asyncio.Event()

    def slow_build(gw):
        built.set()
        return {"generated_at": datetime.now().isoformat(), "headline": "new"}

    monkeypatch.setattr(br, "_build", slow_build)
    monkeypatch.setattr(br, "_BUILD_LOCK", None)  # fresh lock on this loop

    class _Req:
        app: dict = {}

    resp = await br.handle_deck_briefing(_Req())
    body = json.loads(resp.body.decode())

    # Served the STALE copy, without waiting for the rebuild.
    assert body["ok"] is True
    assert body["data"]["headline"] == "old"

    # …and a background refresh really was kicked off.
    await asyncio.wait_for(built.wait(), timeout=5)
    for task in list(br._BG_TASKS):
        await task


async def test_no_cache_at_all_builds_synchronously(monkeypatch):
    """With nothing cached there is nothing to serve but a fresh build."""
    monkeypatch.setattr(br, "_CACHE", None)
    monkeypatch.setattr(br, "_cache_path", lambda: None)
    monkeypatch.setattr(br, "_BUILD_LOCK", None)
    monkeypatch.setattr(
        br, "_build", lambda gw: {"generated_at": datetime.now().isoformat(), "headline": "first"}
    )

    class _Req:
        app: dict = {}

    resp = await br.handle_deck_briefing(_Req())
    body = json.loads(resp.body.decode())
    assert body["data"]["headline"] == "first"


def test_system_section_never_enumerates_partitions(monkeypatch):
    """THE daemon-wedging bug: `psutil.disk_partitions()` blocks indefinitely on a
    cold/disconnected network drive (measured here: >100s, never returned) and it
    does NOT release the GIL — so building a briefing froze the WHOLE gateway
    (every endpoint timed out until the daemon was restarted). The system section
    must use monitor's fast system-drive path instead.
    """
    import psutil

    def _boom(*a, **kw):  # the call that hangs a real machine
        raise AssertionError("disk_partitions() must never be called from a briefing")

    monkeypatch.setattr(psutil, "disk_partitions", _boom)
    monkeypatch.setattr(
        "navig.commands.monitor.get_system_disk",
        lambda: [{"mountpoint": "C:\\", "percent": 42.0}],
    )

    section = br._system()
    assert section is not None
    assert any("Disk C:" in i for i in section["items"])


def test_finance_section_uses_the_snapshot_currency(monkeypatch):
    """The briefing narrated every ledger in dollars — it must use the snapshot's
    own base currency (the Finance app stopped lying; the briefing must too)."""
    fake = {
        "currency": "EUR",
        "total_cash_cents": 123_45,
        "monthly_revenue_cents": 500_00,
        "net_profit_cents": 250_00,
        "runway_months": 4.0,
        "open_invoices_count": 0,
        "overdue_invoices_count": 0,
    }

    class _FakeBizops:
        @staticmethod
        def get_overview():
            return fake

    import sys
    import types

    mod = types.ModuleType("navig_harbor")
    mod.bizops = _FakeBizops  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "navig_harbor", mod)

    section = br._finance()
    assert section is not None
    text = " ".join(section["items"])
    assert "EUR" in text
    assert "$" not in text
