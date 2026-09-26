"""The hardened engine must kill the BROWSER it launched, not just the handle it holds.

Sibling of the same assertions on ``SystemChromeController``. Both classes had the
identical defect and only one was fixed: on Windows a Chromium ``chrome.exe`` is often a
**launcher** that starts the real browser as a separate process and exits within ~100 ms,
so ``self._proc`` is a corpse by teardown — ``terminate()`` reaps nothing and the browser
keeps its window, its debug port and its profile dir. ``targets.py`` learned this long ago
and re-resolves the PID from the port; these engine classes never did.
"""

from __future__ import annotations

import pytest

from navig.browser import targets as t


@pytest.fixture
def controller(monkeypatch):
    """A HardenedController with no real browser behind it."""
    from navig.browser.hardened import HardenedController

    c = HardenedController.__new__(HardenedController)  # skip __init__'s binary discovery
    c.debug_port = 9333
    c._user_data_dir = r"C:\Users\x\.navig\hardened\userdata"
    c._proc = None
    return c


def test_it_kills_the_real_browser_not_only_the_handle(controller, monkeypatch):
    resolved: list[tuple] = []
    killed: list[int] = []
    monkeypatch.setattr(t, "_debug_browser_pids",
                        lambda port, udd, **kw: resolved.append((port, udd)) or [4242])
    monkeypatch.setattr(t, "_terminate_pid", lambda pid: killed.append(pid) or True)

    controller._terminate_proc()

    assert resolved == [(9333, r"C:\Users\x\.navig\hardened\userdata")]
    assert killed == [4242], "the launcher's corpse is not the browser"


def test_it_refuses_to_sweep_when_it_cannot_attribute(controller, monkeypatch):
    """Both signals or nothing — killing what you cannot attribute costs the operator
    their own browser."""
    resolved: list[tuple] = []
    monkeypatch.setattr(t, "_debug_browser_pids",
                        lambda port, udd, **kw: resolved.append((port, udd)) or [])
    controller._user_data_dir = ""  # nothing to attribute with
    controller._terminate_proc()
    assert resolved == []


def test_the_handle_is_still_terminated_and_cleared(controller, monkeypatch):
    """The port scan is an ADDITION, not a replacement: a hardened build that does not use
    a launcher shim is killed by the handle path, and the scan simply finds the same PID."""
    monkeypatch.setattr(t, "_debug_browser_pids", lambda *a, **k: [])
    calls = {"terminate": 0, "kill": 0}

    class _P:
        def terminate(self):
            calls["terminate"] += 1

        def wait(self, timeout=None):
            return 0

        def kill(self):
            calls["kill"] += 1

    controller._proc = _P()
    controller._terminate_proc()
    assert calls["terminate"] == 1 and calls["kill"] == 0
    assert controller._proc is None, "the handle must be cleared so a second stop is a no-op"


def test_a_raising_port_scan_does_not_stop_the_handle_teardown(controller, monkeypatch):
    """Teardown is the last line of defence; one failing half must not skip the other."""
    def _boom(*a, **k):
        raise RuntimeError("psutil exploded")

    monkeypatch.setattr(t, "_debug_browser_pids", _boom)
    calls = {"terminate": 0}

    class _P:
        def terminate(self):
            calls["terminate"] += 1

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    controller._proc = _P()
    controller._terminate_proc()  # must not raise
    assert calls["terminate"] == 1


def test_terminate_is_a_noop_when_nothing_was_launched(controller, monkeypatch):
    monkeypatch.setattr(t, "_debug_browser_pids", lambda *a, **k: [])
    controller._terminate_proc()  # must not raise
    assert controller._proc is None
