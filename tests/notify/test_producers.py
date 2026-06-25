"""Tests for first-party notification producers: the self-error log handler's
throttle/recursion guards, and the deploy reporter's dispatch."""

from __future__ import annotations

import logging

import pytest

from navig.notify.producers.self_errors import NotifyErrorHandler, _Throttle


# ── Throttle (pure) ───────────────────────────────────────────────────────────


def test_throttle_dedupes_same_key():
    t = _Throttle(window_s=300, max_per_window=10, cooldown_s=600)
    assert t.allow("a", now=0.0) is True
    assert t.allow("a", now=100.0) is False          # within cooldown
    assert t.allow("a", now=700.0) is True            # cooldown elapsed
    assert t.allow("b", now=700.0) is True            # different key independent


def test_throttle_rate_limits():
    t = _Throttle(window_s=100, max_per_window=3, cooldown_s=0)
    assert [t.allow(f"k{i}", now=0.0) for i in range(4)] == [True, True, True, False]
    # After the window slides, capacity frees up.
    assert t.allow("k4", now=101.0) is True


# ── Handler filtering / scheduling ────────────────────────────────────────────


class _DummyLoop:
    def __init__(self):
        self.scheduled: list[tuple] = []

    def call_soon_threadsafe(self, fn, *args):
        self.scheduled.append((fn, args))


def _record(name: str, msg: str, level: int = logging.ERROR) -> logging.LogRecord:
    return logging.LogRecord(name, level, __file__, 1, msg, None, None)


def test_handler_skips_notify_namespace_to_avoid_recursion():
    loop = _DummyLoop()
    h = NotifyErrorHandler(loop)
    h.emit(_record("navig.notify.router", "delivery failed"))
    assert loop.scheduled == []  # would otherwise loop forever


def test_handler_schedules_and_dedupes():
    loop = _DummyLoop()
    h = NotifyErrorHandler(loop)
    h.emit(_record("navig.gateway", "kaboom"))
    assert len(loop.scheduled) == 1
    h.emit(_record("navig.gateway", "kaboom"))  # same → deduped
    assert len(loop.scheduled) == 1
    h.emit(_record("navig.gateway", "different boom"))
    assert len(loop.scheduled) == 2


def test_handler_respects_no_notify_flag():
    loop = _DummyLoop()
    h = NotifyErrorHandler(loop)
    rec = _record("navig.gateway", "quiet")
    rec._no_notify = True
    h.emit(rec)
    assert loop.scheduled == []


# ── Deploy reporter ───────────────────────────────────────────────────────────


@pytest.fixture
def notify_db(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    from navig.notify import store

    monkeypatch.setattr(store, "_initialised", False)
    store.init_db()
    from navig.notify import feed

    return feed


async def test_report_deploy_dispatches_to_feed(notify_db):
    from navig.notify.producers.events import report_deploy

    r = await report_deploy("Lighthouse edge", note="Live at https://x.workers.dev")
    assert r["type"] == "deploy"
    items = notify_db.list_items()
    assert len(items) == 1
    assert items[0]["type"] == "deploy"
    assert "Lighthouse edge" in items[0]["title"]


def test_monitor_sample_lookup():
    from navig.notify.producers.samples import monitor_sample

    assert monitor_sample("self_errors")[0] == "self_error"
    assert monitor_sample("resources")[0] == "system_alert"
    assert monitor_sample("nope") is None


async def test_monitor_test_dispatches_labelled_sample(notify_db):
    from navig.notify.producers.samples import dispatch_monitor_test

    r = await dispatch_monitor_test("self_errors")
    assert r["type"] == "self_error"
    items = notify_db.list_items()
    assert items and items[0]["type"] == "self_error"
    assert items[0]["title"].startswith("[Test]")
    assert items[0]["data"]["_test"] is True
    assert await dispatch_monitor_test("nope") is None
