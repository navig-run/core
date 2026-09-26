"""`navig doctor` must tell a supervisor that is SUPERVISING from one that is
merely alive.

Every daemon check asks "is the pid alive?" — a supervisor stuck in a blocking
call says yes while it restarts nothing, so a dead bot child stays dead with
every light green. The heartbeat (the supervisor touching state.json every
``heartbeat_s``) is the one signal that comes from the loop itself. ✓ only for a
fresh beat; ⚠ for a stale one, for a daemon that predates the tracking, and for
a reading that could not be taken. Never ✓ over an unknown.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from navig.commands import doctor


def _row(results):
    assert len(results) == 1, results
    return results[0]


@pytest.fixture
def running(monkeypatch):
    from navig.daemon import supervisor as sup

    holder: dict = {"state": {"heartbeat_s": 30.0}, "last": None}
    monkeypatch.setattr(sup.NavigDaemon, "is_running", staticmethod(lambda: True))
    monkeypatch.setattr(sup.NavigDaemon, "read_state", staticmethod(lambda: holder["state"]))
    monkeypatch.setattr(sup.NavigDaemon, "last_seen_alive", staticmethod(lambda: holder["last"]))
    return holder


def _ago(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def test_stopped_daemon_contributes_no_row(monkeypatch):
    from navig.daemon import supervisor as sup

    monkeypatch.setattr(sup.NavigDaemon, "is_running", staticmethod(lambda: False))
    assert doctor.check_daemon_heartbeat() == []


def test_a_fresh_beat_is_green(running):
    running["last"] = _ago(12)

    row = _row(doctor.check_daemon_heartbeat())

    assert row.label == "Daemon heartbeat" and row[1] is True
    assert "12s ago" in row.detail


def test_a_stale_beat_on_a_live_process_is_a_wedge(running):
    """The case this row exists for: pid alive, loop stuck."""
    running["last"] = _ago(200)

    row = _row(doctor.check_daemon_heartbeat())

    assert row[0] == doctor._WARN and row[1] is False
    assert "wedged" in row.detail and "200s" in row.detail
    assert "navig service restart" in row.detail, "the row must say what to do"


def test_three_missed_beats_is_the_line(running):
    running["last"] = _ago(80)  # under 3 × 30
    assert _row(doctor.check_daemon_heartbeat())[1] is True
    running["last"] = _ago(100)  # over
    assert _row(doctor.check_daemon_heartbeat())[1] is False


def test_the_interval_comes_from_the_daemon_not_the_doctor(running):
    """A daemon declaring a 120 s beat is not wedged at 200 s."""
    running["state"] = {"heartbeat_s": 120.0}
    running["last"] = _ago(200)

    assert _row(doctor.check_daemon_heartbeat())[1] is True


def test_a_daemon_without_heartbeats_is_unknown_not_fine(running):
    running["state"] = {"log_file": "x"}  # a state file from before heartbeats
    running["last"] = _ago(3600)  # its mtime is old — that is NOT a wedge

    row = _row(doctor.check_daemon_heartbeat())

    assert row[0] == doctor._WARN and row[1] is False
    assert "predates" in row.detail and "wedged" not in row.detail


def test_a_failed_reading_is_a_warning_not_a_tick(monkeypatch):
    from navig.daemon import supervisor as sup

    monkeypatch.setattr(sup.NavigDaemon, "is_running", staticmethod(lambda: True))

    def boom():
        raise OSError("state dir vanished")

    monkeypatch.setattr(sup.NavigDaemon, "read_state", staticmethod(boom))

    row = _row(doctor.check_daemon_heartbeat())

    assert row[0] == doctor._WARN and "COULD NOT VERIFY" in row.detail


def test_the_row_is_wired_into_the_daemon_section():
    """A check nobody calls is documentation."""
    src = Path(doctor.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "check_daemon_heartbeat" in called
