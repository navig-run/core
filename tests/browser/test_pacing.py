"""Stage 6 — human-cadence pacing: jitter, backoff, per-host throttle."""

from __future__ import annotations

from navig.browser import pacing


def test_jittered_zero_and_bounds(monkeypatch):
    assert pacing.jittered(0) == 0.0
    assert pacing.jittered(-5) == 0.0
    monkeypatch.setattr(pacing.random, "uniform", lambda a, b: b)   # max jitter
    assert pacing.jittered(2.0, 0.25) == 2.5
    monkeypatch.setattr(pacing.random, "uniform", lambda a, b: a)   # min jitter
    assert pacing.jittered(2.0, 0.25) == 1.5


def test_jittered_never_negative(monkeypatch):
    monkeypatch.setattr(pacing.random, "uniform", lambda a, b: -5)  # absurd negative
    assert pacing.jittered(1.0, 0.25) == 0.0


# ── Backoff ───────────────────────────────────────────────────────────────────

def test_backoff_grows_and_caps(monkeypatch):
    monkeypatch.setattr(pacing, "jittered", lambda base, jitter=0.25: base)  # no jitter
    b = pacing.Backoff(base=1.0, factor=2.0, cap=10.0)
    assert b.next_delay() == 1.0   # 1 * 2^0
    assert b.next_delay() == 2.0   # 1 * 2^1
    assert b.next_delay() == 4.0
    assert b.next_delay() == 8.0
    assert b.next_delay() == 10.0  # 16 capped to 10
    assert b.attempt == 5


def test_backoff_honours_retry_after():
    b = pacing.Backoff(base=1.0, cap=30.0)
    assert b.next_delay(retry_after=12) == 12.0
    assert b.next_delay(retry_after=999) == 30.0  # capped
    assert b.attempt == 2


def test_backoff_reset():
    b = pacing.Backoff()
    b.next_delay()
    b.next_delay()
    b.reset()
    assert b.attempt == 0


# ── HostThrottle ──────────────────────────────────────────────────────────────

def test_host_throttle_first_hit_no_wait():
    clock = {"t": 100.0}
    th = pacing.HostThrottle(min_interval=2.0, jitter=0.0, clock=lambda: clock["t"])
    assert th.wait_needed("h") == 0.0
    th.record("h")


def test_host_throttle_enforces_gap(monkeypatch):
    clock = {"t": 100.0}
    th = pacing.HostThrottle(min_interval=2.0, jitter=0.0, clock=lambda: clock["t"])
    monkeypatch.setattr(pacing, "jittered", lambda base, jitter=0.25: base)
    th.record("h")
    clock["t"] = 100.5  # 0.5s later
    assert th.wait_needed("h") == 1.5  # need 2.0, waited 0.5
    clock["t"] = 103.0  # well past
    assert th.wait_needed("h") == 0.0


def test_host_throttle_per_host_isolation():
    clock = {"t": 0.0}
    th = pacing.HostThrottle(min_interval=5.0, jitter=0.0, clock=lambda: clock["t"])
    th.record("a")
    assert th.wait_needed("b") == 0.0  # different host unaffected
