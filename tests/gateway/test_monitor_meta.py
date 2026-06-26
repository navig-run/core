"""The deck Monitors card shows a truthful requirement per monitor — webcam needs
Windows, connectivity needs Lighthouse, resources needs psutil."""

from __future__ import annotations

from navig.gateway.deck.routes.notify import _monitor_availability


def test_webcam_requires_windows():
    assert _monitor_availability("webcam", mode="lighthouse", is_win=True, has_psutil=True) == (True, None)
    avail, req = _monitor_availability("webcam", mode="lighthouse", is_win=False, has_psutil=True)
    assert avail is False and req == "Windows only"


def test_connectivity_requires_lighthouse():
    assert _monitor_availability("connectivity", mode="lighthouse", is_win=True, has_psutil=True) == (True, None)
    avail, req = _monitor_availability("connectivity", mode="", is_win=True, has_psutil=True)
    assert avail is False and "Lighthouse" in req


def test_resources_requires_psutil():
    avail, req = _monitor_availability("resources", mode="", is_win=True, has_psutil=False)
    assert avail is False and req == "Needs psutil"
    assert _monitor_availability("resources", mode="", is_win=True, has_psutil=True) == (True, None)


def test_self_errors_always_available():
    assert _monitor_availability("self_errors", mode="", is_win=False, has_psutil=False) == (True, None)
