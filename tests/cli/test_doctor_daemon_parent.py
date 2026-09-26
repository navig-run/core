"""`navig doctor` must say whether the running daemon has a living parent.

A daemon spawned detached from a CLI that then exited is orphan-shaped, and an
orphan-reaping process sweep kills that shape — the operator's hourly cleanup did
so twice on 2026-09-14, invisibly until its log was read. Every navig launch now
goes through the scheduled task; this row is the detector for the older daemon,
the fallback spawn, and the next machine with a sweeper. ✓ names the parent; ⚠
for orphan-shaped and for unknown. Never ✓ over an unknown.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from navig.commands import doctor


def _row(results):
    assert len(results) == 1, results
    return results[0]


@pytest.fixture
def running(monkeypatch):
    from navig.daemon import supervisor as sup

    holder: dict = {"info": {"shape": "unknown"}}
    monkeypatch.setattr(sup.NavigDaemon, "is_running", staticmethod(lambda: True))
    monkeypatch.setattr(sup.NavigDaemon, "read_pid", staticmethod(lambda: 4242))
    monkeypatch.setattr(sup.NavigDaemon, "parent_of", staticmethod(lambda pid: holder["info"]))
    return holder


def test_stopped_daemon_contributes_no_row(monkeypatch):
    from navig.daemon import supervisor as sup

    monkeypatch.setattr(sup.NavigDaemon, "is_running", staticmethod(lambda: False))
    assert doctor.check_daemon_parent() == []


def test_a_service_parent_is_green_and_named(running):
    running["info"] = {"ppid": 3356, "name": "svchost.exe", "alive": True, "shape": "service"}

    row = _row(doctor.check_daemon_parent())

    assert row.label == "Daemon parent" and row[1] is True
    assert "svchost.exe" in row.detail and "3356" in row.detail


def test_an_orphan_shaped_daemon_is_a_warning_with_the_remedy(running):
    """The case that killed the daemon: no parent, and nothing said so."""
    running["info"] = {"ppid": 62344, "name": None, "alive": False, "shape": "orphan"}

    row = _row(doctor.check_daemon_parent())

    assert row[0] == doctor._WARN and row[1] is False
    assert "62344" in row.detail and "orphan" in row.detail.lower()
    assert "navig service restart" in row.detail


def test_a_live_ordinary_parent_is_green_but_says_so(running):
    running["info"] = {"ppid": 9, "name": "pwsh.exe", "alive": True, "shape": "process"}

    row = _row(doctor.check_daemon_parent())

    assert row[1] is True and "pwsh.exe" in row.detail and "lives while" in row.detail


def test_unknown_is_a_warning_not_a_tick(running):
    running["info"] = {"ppid": None, "name": None, "alive": None, "shape": "unknown"}

    row = _row(doctor.check_daemon_parent())

    assert row[0] == doctor._WARN and "unknown" in row.detail


def test_a_failed_reading_is_a_warning(monkeypatch):
    from navig.daemon import supervisor as sup

    monkeypatch.setattr(sup.NavigDaemon, "is_running", staticmethod(lambda: True))

    def boom():
        raise OSError("pid file vanished")

    monkeypatch.setattr(sup.NavigDaemon, "read_pid", staticmethod(boom))

    row = _row(doctor.check_daemon_parent())

    assert row[0] == doctor._WARN and "COULD NOT VERIFY" in row.detail


def test_the_row_is_wired_into_the_daemon_section():
    src = Path(doctor.__file__).read_text(encoding="utf-8")
    called = {
        n.func.id
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "check_daemon_parent" in called
