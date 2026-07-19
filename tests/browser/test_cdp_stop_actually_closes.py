"""`navig cdp stop` reported success while leaving the browser running.

The chain (Windows, but the honesty bug is cross-platform):

  1. `launch_app` Popens `chrome.exe` and records `proc.pid`. On Windows that binary
     is a LAUNCHER: it starts the real browser as a SEPARATE process and exits within
     ~100 ms. The recorded PID is a corpse before anyone calls `stop`.
  2. `stop_launched` called `_terminate_pid(tracked_pid)`, which treats a vanished
     PID as success ("already gone") — correct for a process that really died, fatal
     here: it killed a dead launcher and reported `{"closed": true}` while the actual
     browser ran on.
  3. It then DELETED the registry entry, so the leaked browser could not even be
     found by port afterwards — `cdp stop --all` could never clean it up.

Every leak was silent, and each one holds a window and a profile directory. This is
the same failure that once buried the operator under ~24 blank grey Chrome windows.

`closed` must now mean closed — proven by probing the port, not by assuming a kill.
"""

from __future__ import annotations

import pytest

from navig.browser import targets as t


@pytest.fixture()
def registry(tmp_path, monkeypatch):
    """Isolate the launched-browser registry (never touch the real one)."""
    path = tmp_path / "cdp-launched.json"
    monkeypatch.setattr(t, "_launched_registry_path", lambda: path)
    return path


# ── the bug: a dead launcher PID made `stop` claim success ───────────────────


def test_stop_does_not_claim_success_while_the_browser_still_serves_cdp(registry, monkeypatch):
    """The exact leak: tracked PID is gone, but the browser is alive on the port."""
    t.record_launched(9222, pid=999_999, app="chrome", user_data_dir="/tmp/cdp-profiles/x")

    # The tracked pid is a corpse — terminating it "succeeds" (already gone).
    monkeypatch.setattr(t, "_terminate_pid", lambda _pid: True)
    # We cannot find/kill the real process (simulates psutil missing, or a stubborn browser).
    monkeypatch.setattr(t, "_debug_browser_pids", lambda _p, _u: [], raising=False)
    # …and the port is STILL answering: the browser is alive.
    monkeypatch.setattr(t, "probe_port", lambda _p, timeout=1.0: object())

    res = t.stop_launched(9222)

    assert res["closed"] is False, "stop reported a browser closed while it was still running"
    assert res["ok"] is False
    assert "still answering" in res["error"]


def test_a_browser_it_could_not_close_stays_in_the_registry(registry, monkeypatch):
    """The old code deleted the entry regardless, orphaning the leak beyond reach."""
    t.record_launched(9222, pid=999_999, app="chrome", user_data_dir="/tmp/cdp-profiles/x")

    monkeypatch.setattr(t, "_terminate_pid", lambda _pid: True)
    monkeypatch.setattr(t, "_debug_browser_pids", lambda _p, _u: [], raising=False)
    monkeypatch.setattr(t, "probe_port", lambda _p, timeout=1.0: object())  # still alive

    t.stop_launched(9222)

    assert "9222" in t.get_launched(), "a browser we failed to kill was dropped from the registry"


# ── it must kill the REAL browser, not just the tracked corpse ───────────────


def test_stop_kills_the_process_actually_serving_the_port(registry, monkeypatch):
    killed: list[int] = []

    t.record_launched(9222, pid=111, app="chrome", user_data_dir="/tmp/cdp-profiles/x")
    monkeypatch.setattr(t, "_terminate_pid", lambda pid: (killed.append(pid), True)[1])
    # 111 is the dead launcher; 222 is the browser genuinely serving the port.
    monkeypatch.setattr(t, "_debug_browser_pids", lambda _p, _u: [222], raising=False)
    monkeypatch.setattr(t, "probe_port", lambda _p, timeout=1.0: None)  # gone after the kill

    res = t.stop_launched(9222)

    assert 222 in killed, "the real browser process was never killed"
    assert res["closed"] is True
    assert "9222" not in t.get_launched()  # only removed once genuinely closed


def test_stop_reports_closed_when_the_port_is_dead(registry, monkeypatch):
    """A browser the user already closed: nothing to kill, honestly reported closed."""
    t.record_launched(9222, pid=111, app="chrome", user_data_dir=None)
    monkeypatch.setattr(t, "_terminate_pid", lambda _pid: True)
    monkeypatch.setattr(t, "_debug_browser_pids", lambda _p, _u: [], raising=False)
    monkeypatch.setattr(t, "probe_port", lambda _p, timeout=1.0: None)

    res = t.stop_launched(9222)
    assert res["ok"] is True and res["closed"] is True
    assert "9222" not in t.get_launched()


# ── selection must never be able to hit the operator's own browser ───────────


def test_only_processes_carrying_our_debug_port_are_selected(monkeypatch):
    """The operator's real Chrome carries no --remote-debugging-port. It must be
    unreachable by this code path, or `cdp stop` would close their actual browser."""

    class _P:
        def __init__(self, pid, cmdline):
            self.info = {"pid": pid, "cmdline": cmdline}

    procs = [
        _P(1, ["chrome.exe"]),                                            # operator's browser
        _P(2, ["chrome.exe", "--remote-debugging-port=9333"]),            # a DIFFERENT debug port
        _P(3, ["chrome.exe", "--remote-debugging-port=9222", "--type=renderer"]),  # child
        _P(4, ["chrome.exe", "--remote-debugging-port=9222"]),            # ← the one
    ]
    fake_psutil = type("psutil", (), {"process_iter": staticmethod(lambda _a: procs)})
    monkeypatch.setitem(__import__("sys").modules, "psutil", fake_psutil)

    assert t._debug_browser_pids(9222, None) == [4]


def test_the_profile_dir_narrows_selection_further(monkeypatch):
    class _P:
        def __init__(self, pid, cmdline):
            self.info = {"pid": pid, "cmdline": cmdline}

    procs = [
        _P(5, ["chrome.exe", "--remote-debugging-port=9222", "--user-data-dir=/other"]),
        _P(6, ["chrome.exe", "--remote-debugging-port=9222", "--user-data-dir=/mine"]),
    ]
    fake_psutil = type("psutil", (), {"process_iter": staticmethod(lambda _a: procs)})
    monkeypatch.setitem(__import__("sys").modules, "psutil", fake_psutil)

    assert t._debug_browser_pids(9222, "/mine") == [6]


# ── the orphans the old bug created must be reclaimable ──────────────────────


def test_stop_all_reclaims_orphans_the_old_stop_left_behind(registry, monkeypatch):
    """Leaks with no registry entry were unreachable by port — and so uncleanable."""
    killed: list[int] = []
    monkeypatch.setattr(t, "_terminate_pid", lambda pid: (killed.append(pid), True)[1])
    monkeypatch.setattr(t, "_orphan_debug_browser_pids", lambda: [777, 888], raising=False)

    res = t.stop_all_launched()

    assert res["orphans_reclaimed"] == 2
    assert killed == [777, 888]


def test_orphan_sweep_only_matches_navig_profiles(registry, monkeypatch):
    """It must never reach a browser the operator debugged with their own profile.

    Selection is anchored to NAVIG's real profile ROOT (`_profile_root()`), not a
    loose "cdp-profiles appears somewhere in the command line" substring — so a
    foreign browser cannot smuggle itself into the reclaim set with a lookalike path.
    """

    class _P:
        def __init__(self, pid, cmdline):
            self.info = {"pid": pid, "cmdline": cmdline, "name": "chrome.exe"}

    monkeypatch.setattr(t, "_profile_root", lambda: "/home/me/.navig/cdp-profiles")

    procs = [
        # The operator's own deliberately-debugged browser, on their real profile.
        _P(1, ["chrome.exe", "--remote-debugging-port=9222", "--user-data-dir=/home/me/Chrome"]),
        # A different tool's browser that merely MENTIONS the words — must not match.
        _P(3, ["chrome.exe", "--remote-debugging-port=9444",
               "--user-data-dir=/tmp/not-navig-cdp-profiles/x"]),
        # One NAVIG actually launched.
        _P(2, ["chrome.exe", "--remote-debugging-port=9333",
               "--user-data-dir=/home/me/.navig/cdp-profiles/abc"]),
    ]
    fake_psutil = type("psutil", (), {"process_iter": staticmethod(lambda _a: procs)})
    monkeypatch.setitem(__import__("sys").modules, "psutil", fake_psutil)

    assert t._orphan_debug_browser_pids() == [2]
