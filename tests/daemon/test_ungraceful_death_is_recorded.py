"""A daemon that dies without shutting down must leave more than a stale pid file.

2026-09-14 20:01: the operator's daemon died — whole tree, no shutdown line in any
of three logs — and the scheduled task relaunched it four minutes later. Nothing
recorded that it happened. The one piece of evidence, a pid file pointing at a
dead process, was deleted silently by the next `is_running()` call.

A clean stop removes the pid file, so a pid file whose process is gone IS the
fingerprint of an ungraceful death. `is_running()` now records it as an incident
— the same channel the config layer's rescues use, so it reaches the operator on
Telegram — exactly once, because the file is gone after the first detection.
"""

from __future__ import annotations

import os
import sys

import pytest

from navig.daemon import supervisor as sup


@pytest.fixture
def isolated_pid_file(tmp_path, monkeypatch):
    """Point the supervisor at a throwaway pid file and capture incidents."""
    pf = tmp_path / "supervisor.pid"
    monkeypatch.setattr(sup, "PID_FILE", pf)
    recorded: list[tuple[str, dict]] = []

    from navig.core import incidents

    monkeypatch.setattr(incidents, "record", lambda ev, **d: recorded.append((ev, d)))
    return pf, recorded


def _dead_pid() -> int:
    """A pid that is not running. Spawn and reap a child so the number is real
    but its process is gone — a guessed large number could collide."""
    import subprocess

    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def test_a_stale_pid_file_is_recorded_as_an_ungraceful_death(isolated_pid_file):
    pf, recorded = isolated_pid_file
    pid = _dead_pid()
    pf.write_text(str(pid), encoding="utf-8")

    assert sup.NavigDaemon.is_running() is False
    assert not pf.exists(), "the stale pid file must still be reaped"
    assert len(recorded) == 1, recorded
    event, data = recorded[0]
    assert event == "daemon_died_ungracefully"
    assert data["previous_pid"] == pid
    assert data["started_at"], "when that daemon started (the pid file's mtime) is the useful fact"
    assert data["reason"]


def test_the_death_is_recorded_exactly_once(isolated_pid_file):
    """The second status check finds no pid file, so it must not record again —
    a daemon that died once must not read as a daemon dying on every `doctor`."""
    pf, recorded = isolated_pid_file
    pf.write_text(str(_dead_pid()), encoding="utf-8")

    sup.NavigDaemon.is_running()
    sup.NavigDaemon.is_running()
    sup.NavigDaemon.is_running()

    assert len(recorded) == 1


def test_a_live_daemon_records_nothing(isolated_pid_file):
    """Our own pid is a live Python process — the healthy case must be silent."""
    pf, recorded = isolated_pid_file
    pf.write_text(str(os.getpid()), encoding="utf-8")

    assert sup.NavigDaemon.is_running() is True
    assert pf.exists()
    assert recorded == []


def test_no_pid_file_records_nothing(isolated_pid_file):
    """A fresh install, or a clean stop: no file, no death, no incident."""
    pf, recorded = isolated_pid_file

    assert sup.NavigDaemon.is_running() is False
    assert recorded == []


def test_a_failing_incident_write_cannot_break_the_status_check(tmp_path, monkeypatch):
    """`is_running()` is called from doctor, from every service command and from
    the relaunch guard in entry.py — an observation must never break the observed."""
    pf = tmp_path / "supervisor.pid"
    monkeypatch.setattr(sup, "PID_FILE", pf)
    pf.write_text(str(_dead_pid()), encoding="utf-8")

    from navig.core import incidents

    def _boom(*a, **k):
        raise RuntimeError("incident log unavailable")

    monkeypatch.setattr(incidents, "record", _boom)

    assert sup.NavigDaemon.is_running() is False  # must not raise
    assert not pf.exists()


def test_the_incident_has_an_operator_facing_description():
    """doctor prints DESCRIPTIONS, and the config_incidents producer pushes any
    event that has one — an id without a description is invisible on both."""
    from navig.core import incidents

    assert incidents.DAEMON_DIED_UNGRACEFULLY in incidents.DESCRIPTIONS
    text = incidents.DESCRIPTIONS[incidents.DAEMON_DIED_UNGRACEFULLY].lower()
    assert "without shutting down" in text
