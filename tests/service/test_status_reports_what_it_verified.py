"""`navig service status` prints what it VERIFIED, and a reap never takes a
newborn's pid file.

state.json is a snapshot — written at boot and after a child restart, never in
between — so its `alive` flag is what was true THEN. A child that died and sits
in its restart back-off carried `alive: True` and a dead pid, and `service
status` printed ALIVE. The dashboard already verified the pid; status now does
too, says UNKNOWN when it cannot, and shows the boot time and the heartbeat.

And `_reap_stale_pid_file` unlinked whatever pid file was there once handed a
dead NUMBER. A relaunching daemon can write its own pid between the caller's
read and the unlink; the reap then deleted a LIVE daemon's file.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

from navig.daemon import service_manager as sm
from navig.daemon import supervisor as sup


def _dead_pid() -> int:
    import subprocess

    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


# ── service status ───────────────────────────────────────────────────────────


def _fake_daemon(monkeypatch, children, *, last_seen=None, state_extra=None, parent=None):
    class FakeDaemon:
        @staticmethod
        def is_running() -> bool:
            return True

        @staticmethod
        def read_pid() -> int:
            return 4321

        @staticmethod
        def read_state() -> dict:
            return {"children": children, **(state_extra or {})}

        @staticmethod
        def last_seen_alive():
            return last_seen

        @staticmethod
        def parent_of(pid):
            return parent or {"shape": "unknown"}

    monkeypatch.setattr(sup, "NavigDaemon", FakeDaemon)
    monkeypatch.setattr(sm.sys, "platform", "linux")
    monkeypatch.setattr(sm, "has_systemd", lambda: False)


def test_a_child_whose_pid_is_dead_reads_dead_whatever_the_snapshot_says(monkeypatch):
    """The case that lied: died, in back-off, snapshot still says alive."""
    _fake_daemon(
        monkeypatch, [{"name": "telegram", "alive": True, "pid": _dead_pid(), "restart_count": 1}]
    )

    _, detail = sm.status()

    assert "telegram: DEAD" in detail, detail


def test_a_child_whose_pid_is_live_reads_alive(monkeypatch):
    _fake_daemon(
        monkeypatch, [{"name": "gateway", "alive": True, "pid": os.getpid(), "restart_count": 0}]
    )

    _, detail = sm.status()

    assert "gateway: ALIVE (pid=" in detail, detail
    assert "unverified" not in detail


def test_an_unverifiable_pid_is_not_printed_as_plain_alive(monkeypatch):
    monkeypatch.setattr(sm, "_child_is_live", lambda pid: None)
    _fake_daemon(monkeypatch, [{"name": "gateway", "alive": True, "pid": 77, "restart_count": 0}])

    _, detail = sm.status()

    assert "gateway: ALIVE (unverified" in detail, detail
    assert "elevated shell" in detail, "say HOW to verify, or unverified reads as a broken child"


def test_status_shows_since_and_a_fresh_heartbeat(monkeypatch):
    now = datetime.now(timezone.utc)
    _fake_daemon(
        monkeypatch,
        [],
        last_seen=(now - timedelta(seconds=12)).isoformat(),
        state_extra={
            "started_at": (now - timedelta(hours=3, minutes=7)).isoformat(),
            "heartbeat_s": 30.0,
        },
    )

    _, detail = sm.status()

    assert "Since: " in detail and "(up 3h 07m)" in detail, detail
    assert "Heartbeat: 12s ago (every 30s)" in detail, detail


def test_a_stale_heartbeat_reads_stale_not_running(monkeypatch):
    now = datetime.now(timezone.utc)
    _fake_daemon(
        monkeypatch,
        [],
        last_seen=(now - timedelta(seconds=400)).isoformat(),
        state_extra={"heartbeat_s": 30.0},
    )

    _, detail = sm.status()

    assert "Heartbeat: STALE" in detail and "wedged" in detail, detail
    assert "navig service restart" in detail


def test_a_daemon_without_heartbeats_says_so(monkeypatch):
    _fake_daemon(monkeypatch, [], last_seen=None, state_extra={})

    _, detail = sm.status()

    assert "Heartbeat: none" in detail and "predates" in detail, detail


def test_status_survives_a_daemon_class_without_last_seen_alive(monkeypatch):
    """Older fakes and older builds have no `last_seen_alive`; status must not crash."""

    class Bare:
        @staticmethod
        def is_running() -> bool:
            return True

        @staticmethod
        def read_pid() -> int:
            return 1

        @staticmethod
        def read_state() -> dict:
            return {"children": [], "heartbeat_s": 30.0}

    monkeypatch.setattr(sup, "NavigDaemon", Bare)
    monkeypatch.setattr(sm.sys, "platform", "linux")
    monkeypatch.setattr(sm, "has_systemd", lambda: False)

    running, detail = sm.status()

    assert running and "Heartbeat: none" in detail


def test_child_is_live_on_a_dead_pid_is_false():
    assert sm._child_is_live(_dead_pid()) is False


def test_child_is_live_on_our_own_pid_is_true():
    assert sm._child_is_live(os.getpid()) is True


@pytest.mark.parametrize("pid", [None, "", "x", 0, -1])
def test_child_is_live_on_garbage_is_unknown(pid):
    assert sm._child_is_live(pid) is None


def test_status_names_a_service_parent(monkeypatch):
    _fake_daemon(
        monkeypatch,
        [],
        parent={"ppid": 3356, "name": "svchost.exe", "alive": True, "shape": "service"},
    )

    _, detail = sm.status()

    assert "Launched by: svchost.exe (pid=3356)" in detail, detail


def test_status_warns_on_an_orphan_shaped_daemon(monkeypatch):
    """No parent = the shape the hourly sweep kills. Say so, and say the fix."""
    _fake_daemon(
        monkeypatch, [], parent={"ppid": 62344, "name": None, "alive": False, "shape": "orphan"}
    )

    _, detail = sm.status()

    assert "Launched by: NOBODY" in detail and "62344" in detail, detail
    assert "navig service restart" in detail


def test_status_survives_a_daemon_class_without_parent_of(monkeypatch):
    class Bare:
        @staticmethod
        def is_running() -> bool:
            return True

        @staticmethod
        def read_pid() -> int:
            return 1

        @staticmethod
        def read_state() -> dict:
            return {"children": []}

    monkeypatch.setattr(sup, "NavigDaemon", Bare)
    monkeypatch.setattr(sm.sys, "platform", "linux")
    monkeypatch.setattr(sm, "has_systemd", lambda: False)

    running, detail = sm.status()

    assert running and "Launched by: unknown" in detail


# ── the reap re-reads ────────────────────────────────────────────────────────


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(sup, "PID_FILE", tmp_path / "supervisor.pid")
    monkeypatch.setattr(sup, "STATE_FILE", tmp_path / "state.json")
    recorded: list[tuple[str, dict]] = []

    from navig.core import incidents

    monkeypatch.setattr(incidents, "record", lambda ev, **d: recorded.append((ev, d)))
    return tmp_path, recorded


def test_a_pid_file_claimed_by_a_newborn_is_not_reaped(isolated):
    """The race: the caller judged pid X dead; by the time it reaps, a relaunch
    wrote its own live pid. The file — and the newborn — must survive."""
    tmp, recorded = isolated
    dead = _dead_pid()
    pf = tmp / "supervisor.pid"
    pf.write_text(str(os.getpid()), encoding="utf-8")  # the newborn claimed it

    sup.NavigDaemon._reap_stale_pid_file(dead, "process gone")

    assert pf.exists() and pf.read_text(encoding="utf-8") == str(os.getpid())
    assert recorded == [], "no death happened — the number we judged was already replaced"


def test_a_file_already_reaped_records_nothing(isolated):
    """Two concurrent `doctor`s: the second must not report the same death twice."""
    tmp, recorded = isolated

    sup.NavigDaemon._reap_stale_pid_file(_dead_pid(), "process gone")

    assert recorded == []


def test_the_number_we_judged_is_reaped_and_recorded(isolated):
    tmp, recorded = isolated
    dead = _dead_pid()
    pf = tmp / "supervisor.pid"
    pf.write_text(str(dead), encoding="utf-8")
    then = time.time() - 60
    os.utime(pf, (then, then))

    sup.NavigDaemon._reap_stale_pid_file(dead, "process gone")

    assert not pf.exists()
    assert len(recorded) == 1 and recorded[0][1]["previous_pid"] == dead
