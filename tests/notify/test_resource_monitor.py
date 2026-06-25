"""Tests for the resource monitor's hysteresis threshold tracker."""

from __future__ import annotations

from navig.notify.monitors.resources import ThresholdTracker, Thresholds, _band


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
