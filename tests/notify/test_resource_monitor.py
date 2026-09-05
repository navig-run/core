"""Tests for the resource monitor's hysteresis threshold tracker."""

from __future__ import annotations

import asyncio

import pytest

from navig.notify.monitors import all_channels_failed
from navig.notify.monitors.resources import (
    Thresholds,
    ThresholdTracker,
    _band,
    _react,
)


def test_alerts_once_on_rising_edge():
    t = ThresholdTracker(Thresholds(high=90, low=80))
    assert t.update(50) is None
    assert t.update(91) == "alert"
    assert t.update(95) is None   # already alerted — no repeat
    assert t.update(92) is None


def test_clears_only_below_low_band():
    t = ThresholdTracker(Thresholds(high=90, low=80))
    assert t.update(95) == "alert"
    assert t.update(85) is None   # dropped under high but still in the dead-band
    assert t.update(79) == "clear"
    # Re-arms for a fresh alert.
    assert t.update(91) == "alert"


def test_sustain_requires_consecutive_reads():
    t = ThresholdTracker(_band(95, sustain=3))
    assert t.update(96) is None   # 1
    assert t.update(97) is None   # 2
    assert t.update(96) == "alert"  # 3 → fires
    # A dip resets the counter.
    t2 = ThresholdTracker(_band(95, sustain=3))
    assert t2.update(96) is None
    assert t2.update(10) is None   # reset
    assert t2.update(96) is None
    assert t2.update(96) is None
    assert t2.update(96) == "alert"


def test_band_helper():
    b = _band(90)
    assert b.high == 90 and b.low == 80 and b.sustain == 1


# ── delivery-integrity: settle the "alerted" latch on VERIFIED delivery ──────


def test_all_channels_failed_predicate():
    assert all_channels_failed({"channels": [{"channel": "deck", "ok": False}]}) is True
    assert all_channels_failed({"channels": [{"channel": "deck", "ok": True},
                                             {"channel": "email", "ok": False}]}) is False
    assert all_channels_failed({"channels": []}) is False  # muted / no channels
    assert all_channels_failed({"skipped": "master_off", "channels": []}) is False
    assert all_channels_failed(None) is False


def _one_tracker(high=90, low=80):
    return {"disk": ThresholdTracker(Thresholds(high=high, low=low))}, {"disk": "Disk"}


@pytest.mark.asyncio
async def test_failed_alert_rolls_back_latch_and_retries():
    trackers, labels = _one_tracker()
    calls = []

    async def _all_failed(*_a, **_k):
        calls.append(1)
        return {"channels": [{"channel": "deck", "ok": False}]}

    await _react({"disk": 95}, trackers, labels, _all_failed)
    assert trackers["disk"].alerted is False  # not delivered → latch rolled back
    # Still high next poll → re-fires (retry) because the latch was rolled back.
    await _react({"disk": 95}, trackers, labels, _all_failed)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_delivered_alert_stays_latched():
    trackers, labels = _one_tracker()
    calls = []

    async def _ok(*_a, **_k):
        calls.append(1)
        return {"channels": [{"channel": "deck", "ok": True}]}

    await _react({"disk": 95}, trackers, labels, _ok)
    assert trackers["disk"].alerted is True  # delivered → latched
    await _react({"disk": 95}, trackers, labels, _ok)
    assert len(calls) == 1  # no repeat while it stays high


@pytest.mark.asyncio
async def test_muted_alert_stays_latched():
    trackers, labels = _one_tracker()
    calls = []

    async def _muted(*_a, **_k):
        calls.append(1)
        return {"skipped": "master_off", "channels": []}

    await _react({"disk": 95}, trackers, labels, _muted)
    assert trackers["disk"].alerted is True  # intentional mute → don't churn
    await _react({"disk": 95}, trackers, labels, _muted)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_loop_survives_a_dispatch_exception(monkeypatch):
    """A dispatch that raises must NOT kill the monitor — it logs and keeps
    polling (else it dies silently while the deck still shows it enabled).

    Survival is measured by POLL count, not dispatch count: after the first alert
    raises, the tracker stays latched (the rollback is skipped by the raise), so no
    further dispatch fires — but a live loop keeps sampling.
    """
    import navig.notify.monitors.resources as rm

    monkeypatch.setattr(rm, "POLL_S", 0.001)
    polls = []

    def _sample_counting():
        polls.append(1)
        return {"disk": 95, "mem": 0, "cpu": 0}  # crosses the disk band → first poll alerts

    monkeypatch.setattr(rm, "_sample", _sample_counting)

    async def _boom(*_a, **_k):
        raise RuntimeError("dispatch broke")

    monkeypatch.setattr("navig.notify.dispatch", _boom)

    task = asyncio.create_task(rm.run_resource_monitor({"disk_pct": 90}))
    for _ in range(100):
        if len(polls) >= 4:
            break
        await asyncio.sleep(0.005)
    survived = len(polls)
    task.cancel()
    try:
        await task
    except BaseException:  # noqa: BLE001 — swallow cancel; survival already measured
        pass
    # Without the loop-body guard the first poll's raise ends the task at polls == 1.
    assert survived >= 4, f"monitor died after {survived} poll(s) — a raise killed the loop"
