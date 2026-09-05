"""Tests for first-party notification producers: the self-error log handler's
throttle/recursion guards, and the deploy reporter's dispatch."""

from __future__ import annotations

import asyncio
import logging

import pytest

from navig.notify.producers.self_errors import NotifyErrorHandler, _Throttle

# ── spawn() — GC-safe fire-and-forget ─────────────────────────────────────────


async def test_spawn_survives_gc_and_runs_to_completion():
    """A bare create_task()/ensure_future() may be garbage-collected before it
    runs — asyncio only holds a WEAK ref — silently dropping a producer's push.
    spawn() keeps a strong ref, so it completes even when the caller keeps none."""
    import gc

    from navig.notify.producers import spawn

    ran = asyncio.Event()

    async def work():
        await asyncio.sleep(0)
        ran.set()

    spawn(work())  # caller deliberately keeps NO reference
    gc.collect()  # an untracked task could be collected here
    await asyncio.sleep(0.02)
    assert ran.is_set(), "spawn() must keep the task alive so it runs to completion"


async def test_spawn_releases_the_ref_once_done():
    from navig.notify.producers import _bg_tasks, spawn

    async def work():
        await asyncio.sleep(0)

    task = spawn(work())
    assert task in _bg_tasks  # strong ref held while pending
    await task
    await asyncio.sleep(0)  # let the done-callback fire
    assert task not in _bg_tasks  # released — no unbounded growth

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


def test_config_incidents_sample_uses_the_producer_type():
    # The Test sample must dispatch the SAME type the real reporter does, so it routes
    # through the real config_incident matrix (deck + telegram), not a made-up type.
    from navig.notify.producers.config_incidents import NOTIFY_TYPE
    from navig.notify.producers.samples import monitor_sample

    sample = monitor_sample("config_incidents")
    assert sample is not None  # was None → Test button 400'd
    assert sample[0] == NOTIFY_TYPE  # "config_incident"


def test_every_monitor_with_a_test_button_has_a_sample():
    """Every monitor the deck exposes renders a 'Test' button, so each MUST have a sample
    or pressing Test 400s 'no sample for monitor' (config_incidents — the default-ON guard
    monitor — did). This parity guard fails the build if a new monitor forgets its sample."""
    from navig.gateway.deck.routes.notify import _MONITOR_KEYS
    from navig.notify.producers.samples import _SAMPLES

    missing = _MONITOR_KEYS - set(_SAMPLES)
    assert not missing, f"monitors with a Test button but no sample: {sorted(missing)}"


async def test_monitor_test_dispatches_labelled_sample(notify_db):
    from navig.notify.producers.samples import dispatch_monitor_test

    r = await dispatch_monitor_test("self_errors")
    assert r["type"] == "self_error"
    items = notify_db.list_items()
    assert items and items[0]["type"] == "self_error"
    assert items[0]["title"].startswith("[Test]")
    assert items[0]["data"]["_test"] is True
    assert await dispatch_monitor_test("nope") is None


async def test_config_incidents_test_button_dispatches(notify_db):
    # The previously-dead Test button now fires a real config_incident notification.
    from navig.notify.producers.samples import dispatch_monitor_test

    r = await dispatch_monitor_test("config_incidents")
    assert r is not None and r["type"] == "config_incident"
    items = notify_db.list_items()
    assert items and items[0]["type"] == "config_incident"
    assert items[0]["title"].startswith("[Test]")
    assert items[0]["data"]["_test"] is True


# ── The throttle is a RESERVATION, not a record of delivery ────────────────────
#
# `allow()` must be consulted before the send (that is the whole point — avoid a
# storm), so a send that reaches nobody used to burn both the per-key cooldown and a
# slot in the rate-limit window. The next identical error was then suppressed for up
# to cooldown_s, losing the event twice over. And denials were discarded outright, so
# a storm reporting 5 of 500 errors looked like a system with 5 errors.


def test_rollback_returns_the_reservation():
    t = _Throttle(window_s=1000, max_per_window=1, cooldown_s=1000)
    assert t.allow("k", 0.0) is True
    assert t.allow("k", 1.0) is False, "precondition: the key is now on cooldown"

    t.rollback("k")

    assert t.allow("k", 2.0) is True, "rollback must free the per-key cooldown"


def test_rollback_frees_a_rate_limit_slot():
    t = _Throttle(window_s=1000, max_per_window=1, cooldown_s=0)
    assert t.allow("a", 0.0) is True
    assert t.allow("b", 1.0) is False, "precondition: the window is full"

    t.rollback("a")

    assert t.allow("b", 2.0) is True, "rollback must return the window slot too"


def test_rollback_without_a_reservation_is_a_no_op():
    t = _Throttle(window_s=1000, max_per_window=2, cooldown_s=1000)
    t.rollback("never-seen")          # must not raise
    assert t.allow("k", 0.0) is True  # and must not have consumed anything


def test_suppressed_denials_are_counted_and_drained():
    t = _Throttle(window_s=1000, max_per_window=1, cooldown_s=0)
    assert t.drain_suppressed() == 0
    t.allow("a", 0.0)                 # allowed
    t.allow("b", 1.0)                 # denied — window full
    t.allow("c", 2.0)                 # denied
    assert t.drain_suppressed() == 2
    assert t.drain_suppressed() == 0, "draining resets the counter"


# ── self-error reporter: settle the reservation on VERIFIED delivery ──────────


def _failed_fanout() -> dict:
    return {"type": "self_error", "channels": [{"channel": "telegram", "ok": False}]}


def _delivered_fanout() -> dict:
    return {"type": "self_error", "channels": [{"channel": "deck", "ok": True}]}


async def test_failed_delivery_lets_the_same_error_fire_again():
    from unittest.mock import AsyncMock, patch

    loop = _DummyLoop()
    h = NotifyErrorHandler(loop)
    h.emit(_record("navig.gateway", "kaboom"))
    assert len(loop.scheduled) == 1
    _, (key, title, body) = loop.scheduled[0]

    with patch("navig.notify.dispatch", new=AsyncMock(return_value=_failed_fanout())):
        await h._send(key, title, body)

    h.emit(_record("navig.gateway", "kaboom"))
    assert len(loop.scheduled) == 2, (
        "the error reached nobody, so it must not stay suppressed for a full cooldown"
    )


async def test_delivered_error_stays_deduped():
    from unittest.mock import AsyncMock, patch

    loop = _DummyLoop()
    h = NotifyErrorHandler(loop)
    h.emit(_record("navig.gateway", "kaboom"))
    _, (key, title, body) = loop.scheduled[0]

    with patch("navig.notify.dispatch", new=AsyncMock(return_value=_delivered_fanout())):
        await h._send(key, title, body)

    h.emit(_record("navig.gateway", "kaboom"))
    assert len(loop.scheduled) == 1, "a delivered error must still be deduped"


async def test_a_raising_dispatch_keeps_the_rate_limit():
    """No rollback on an exception — that would turn one fault into a storm.

    dispatch() is contracted not to raise (a down channel returns {ok: False}), so
    reaching the except branch means the dispatch path itself is broken. An error
    that fails *every* time must stay rate-limited.
    """
    from unittest.mock import AsyncMock, patch

    loop = _DummyLoop()
    h = NotifyErrorHandler(loop)
    h.emit(_record("navig.gateway", "kaboom"))
    _, (key, title, body) = loop.scheduled[0]

    with patch("navig.notify.dispatch", new=AsyncMock(side_effect=RuntimeError("dispatch bug"))):
        await h._send(key, title, body)

    h.emit(_record("navig.gateway", "kaboom"))
    assert len(loop.scheduled) == 1, "a deterministically-failing error must stay throttled"


def test_the_body_reports_what_the_rate_limit_ate():
    loop = _DummyLoop()
    h = NotifyErrorHandler(loop)
    h._throttle = _Throttle(window_s=1000.0, max_per_window=1, cooldown_s=0.0)

    h.emit(_record("navig.gateway", "first"))     # allowed
    h.emit(_record("navig.gateway", "second"))    # suppressed — window full
    h.emit(_record("navig.gateway", "third"))     # suppressed
    assert len(loop.scheduled) == 1

    h._throttle.max_per_window = 5                # storm subsides
    h.emit(_record("navig.gateway", "fourth"))
    assert len(loop.scheduled) == 2

    _, (_, _, body) = loop.scheduled[1]
    assert "+2 further error(s) suppressed" in body, (
        f"the drop count must be surfaced, not discarded; body was {body!r}"
    )


# ── deploy reporter robustness ────────────────────────────────────────────────


@pytest.mark.parametrize("status", ["ok", "ok ", "ok\n", " OK "])
async def test_untrimmed_ok_status_is_still_a_success(status):
    """CLI args and CI variables carry stray whitespace; a trailing newline used to
    turn a successful deploy into a high-priority "Deploy ok : edge" failure alert."""
    from unittest.mock import AsyncMock, patch

    import navig.notify as notify_pkg
    from navig.notify.producers.events import report_deploy

    with patch.object(notify_pkg, "dispatch", new=AsyncMock(return_value={"channels": []})) as d:
        await report_deploy("edge", "1.0", status=status)

    title = d.await_args.args[1]
    assert title == "Deployed edge 1.0", f"status={status!r} produced {title!r}"
    assert d.await_args.kwargs["priority"] == "normal"


async def test_report_deploy_sync_inside_a_loop_leaves_no_unawaited_coroutine():
    """It used to hand a fresh coroutine to asyncio.run, which raises before awaiting
    it — leaving a "coroutine was never awaited" RuntimeWarning on every in-loop deploy."""
    import gc
    import warnings
    from unittest.mock import AsyncMock, patch

    import navig.notify as notify_pkg
    from navig.notify.producers.events import report_deploy_sync

    with patch.object(notify_pkg, "dispatch", new=AsyncMock(return_value={"channels": []})):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            report_deploy_sync("edge", "1.0", status="ok")
            await asyncio.sleep(0.05)
            gc.collect()

    unawaited = [w for w in caught if "never awaited" in str(w.message)]
    assert not unawaited, f"leaked an un-awaited coroutine: {[str(w.message) for w in unawaited]}"


# ── the shared delivery helper lives in one place ─────────────────────────────


def test_all_channels_failed_is_re_exported_for_backward_compatibility():
    """It moved to navig.notify.delivery (producers need it too, so living in the
    monitors package made the dependency read backwards). The old path must keep
    working — external tests and plugins import it from there."""
    from navig.notify.delivery import all_channels_failed as canonical
    from navig.notify.monitors import all_channels_failed as legacy

    assert legacy is canonical
