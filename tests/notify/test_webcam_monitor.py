"""Tests for the webcam monitor's debounced transition logic (pure, no registry)."""

from __future__ import annotations

from navig.notify.monitors.webcam import (
    MIN_ON_SECONDS,
    WebcamMonitor,
    _friendly_name,
)


def test_rising_edge_is_debounced():
    m = WebcamMonitor()
    app = {"app1": "Zoom"}

    # First sighting: nothing yet (needs ≥2 polls AND ≥MIN_ON_SECONDS).
    assert m.step(app, now=0.0) == []
    # Second poll but still under the min on-duration → still silent.
    assert m.step(app, now=1.0) == []
    # Past the threshold → announce.
    events = m.step(app, now=MIN_ON_SECONDS + 0.1)
    assert events == [("webcam_on", "Zoom")]
    # Idempotent while it stays on.
    assert m.step(app, now=MIN_ON_SECONDS + 5) == []


def test_short_blip_never_announces():
    m = WebcamMonitor()
    # One poll on, then gone for two polls → no on, no off (never announced).
    assert m.step({"a": "Hello"}, now=0.0) == []
    assert m.step({}, now=1.0) == []
    assert m.step({}, now=2.0) == []
    assert not m.has_live_sessions


def test_release_after_off_debounce():
    m = WebcamMonitor()
    app = {"cam": "Camera"}
    m.step(app, now=0.0)
    m.step(app, now=MIN_ON_SECONDS + 1)  # announced on by here
    assert m.step(app, now=MIN_ON_SECONDS + 2) == []

    # Absent for one poll — not yet (flicker debounce).
    assert m.step({}, now=MIN_ON_SECONDS + 3) == []
    # Absent for a second consecutive poll → release.
    assert m.step({}, now=MIN_ON_SECONDS + 4) == [("webcam_off", "Camera")]
    assert not m.has_live_sessions


def test_two_apps_tracked_independently():
    m = WebcamMonitor()
    t = 0.0
    m.step({"a": "A"}, now=t)
    events = m.step({"a": "A", "b": "B"}, now=MIN_ON_SECONDS + 1)
    # A crossed the threshold (seen at t=0 and now); B only just appeared.
    assert ("webcam_on", "A") in events
    assert ("webcam_on", "B") not in events


def test_friendly_name():
    assert _friendly_name("Microsoft.WindowsCamera_8wekyb3d8bbwe", nonpackaged=False) == "Microsoft.WindowsCamera"
    assert _friendly_name(r"C:#Program Files#Zoom#bin#Zoom.exe", nonpackaged=True) == "Zoom.exe"
