"""The lease heartbeat loop: the thing that keeps a live Brain from expiring."""

from __future__ import annotations

import asyncio

import pytest

from navig.brain import heartbeat
from navig.brain.lease import StaleEpoch


def _drive(monkeypatch: pytest.MonkeyPatch, refresh, *, interval=1.0, max_failures=3, stop_after=10**6):
    """Run the loop on a fake clock until the loop returns or the driver gives up."""
    sleeps = {"n": 0}
    stop = asyncio.Event()
    real_sleep = asyncio.sleep  # captured BEFORE patching, or fake_sleep recurses

    async def fake_sleep(_seconds):
        sleeps["n"] += 1
        if sleeps["n"] > stop_after:
            stop.set()
        await real_sleep(0)

    async def fake_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(heartbeat.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(heartbeat.asyncio, "to_thread", fake_to_thread)
    asyncio.run(heartbeat.heartbeat_loop(
        interval=interval, refresh=refresh, max_failures=max_failures, stop=stop
    ))
    return sleeps["n"]


def test_interval_is_a_third_of_the_ttl_with_a_floor():
    assert heartbeat.refresh_interval(300) == pytest.approx(100.0)
    assert heartbeat.refresh_interval(30) == pytest.approx(heartbeat.MIN_INTERVAL_S)
    assert heartbeat.refresh_interval(90) == pytest.approx(30.0)


def test_loop_renews_until_stopped(monkeypatch):
    calls = {"n": 0}

    def refresh():
        calls["n"] += 1

    slept = _drive(monkeypatch, refresh, interval=0.01, stop_after=4)
    assert calls["n"] >= 3
    assert slept >= calls["n"]


def test_stale_epoch_stops_the_loop(monkeypatch):
    calls = {"n": 0}

    def refresh():
        calls["n"] += 1
        raise StaleEpoch("another machine owns the Brain")

    _drive(monkeypatch, refresh, interval=0.01, stop_after=50)
    assert calls["n"] == 1


def test_a_few_failures_are_tolerated(monkeypatch):
    outcomes = [RuntimeError("502"), RuntimeError("503"), None, None]
    calls = {"n": 0}

    def refresh():
        index = calls["n"]
        calls["n"] += 1
        outcome = outcomes[index] if index < len(outcomes) else None
        if isinstance(outcome, Exception):
            raise outcome

    _drive(monkeypatch, refresh, interval=0.01, max_failures=3, stop_after=6)
    # Two failures then successes: the loop kept going past the first two.
    assert calls["n"] >= 4


def test_gives_up_after_max_consecutive_failures(monkeypatch):
    calls = {"n": 0}

    def refresh():
        calls["n"] += 1
        raise RuntimeError("authority unreachable")

    _drive(monkeypatch, refresh, interval=0.01, max_failures=2, stop_after=50)
    assert calls["n"] == 2


def test_start_thread_runs_the_loop(monkeypatch):
    import threading

    ran = threading.Event()

    async def fake_loop(**_kwargs):
        ran.set()

    monkeypatch.setattr(heartbeat, "heartbeat_loop", fake_loop)
    thread = heartbeat.start_thread()
    try:
        assert ran.wait(timeout=5), "the heartbeat thread never ran the loop"
    finally:
        thread.join(timeout=5)
    assert thread.daemon is True
