"""`navig doctor` must name the file the running daemon actually logs to.

The trap this closes (2026-09-14): a daemon restarted from inside the repo logged
to `<repo>/.navig/navig.log`, because `ConfigManager.base_dir` follows the cwd
into a project's `.navig/`. `~/.navig/navig.log` stopped advancing — and a reader
grepped it for a warning, found a clean absence, and nearly reported that as
proof. In a file that had received zero lines.

Two halves: the supervisor records where it logs (mirroring the logging setup's
own resolution, never re-deriving it), and the doctor row reads that back — ✓
for the global file, ⚠ naming the actual file otherwise, ⚠ "unknown" for a
daemon that predates the tracking. Never ✓ over an unknown.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.commands import doctor


def _row(results):
    assert len(results) == 1, results
    return results[0]


@pytest.fixture
def running(monkeypatch):
    """A daemon that is running, with a controllable state.json."""
    from navig.daemon import supervisor as sup

    holder: dict = {"state": {}}
    monkeypatch.setattr(sup.NavigDaemon, "is_running", staticmethod(lambda: True))
    monkeypatch.setattr(sup.NavigDaemon, "read_state", staticmethod(lambda: holder["state"]))
    return holder


def test_stopped_daemon_contributes_no_row(monkeypatch):
    from navig.daemon import supervisor as sup

    monkeypatch.setattr(sup.NavigDaemon, "is_running", staticmethod(lambda: False))
    assert doctor.check_daemon_log_location() == []


def test_the_global_log_is_a_green_row(running, monkeypatch, tmp_path):
    monkeypatch.setattr("navig.platform.paths.config_dir", lambda: tmp_path)
    running["state"] = {"log_file": str(tmp_path / "navig.log")}

    row = _row(doctor.check_daemon_log_location())

    assert row.label == "Daemon log" and row[1] is True
    assert "navig.log" in row.detail


def test_a_project_log_is_a_warning_that_names_both_files(running, monkeypatch, tmp_path):
    """The case that bit: the daemon writes somewhere else, and the row must say
    where — AND which file will therefore not advance."""
    global_dir = tmp_path / "global"
    project_dir = tmp_path / "repo" / ".navig"
    global_dir.mkdir()
    project_dir.mkdir(parents=True)
    monkeypatch.setattr("navig.platform.paths.config_dir", lambda: global_dir)
    running["state"] = {
        "log_file": str(project_dir / "navig.log"),
        "cwd": str(tmp_path / "repo"),
    }

    row = _row(doctor.check_daemon_log_location())

    assert row.label == "Daemon log"
    assert row[1] is False, "a daemon logging somewhere else must not be ✓"
    assert str((project_dir / "navig.log").resolve()) in row.detail
    assert str((global_dir / "navig.log").resolve()) in row.detail
    assert "will not advance" in row.detail
    assert str(tmp_path / "repo") in row.detail, "the cwd it was restarted from is the fix"
    # ⚠, not ✗ — the daemon is healthy, only the reader is misdirected.
    assert row[0] == doctor._WARN


def test_a_daemon_without_tracking_is_unknown_not_fine(running, monkeypatch, tmp_path):
    """The doctor-honesty rule: ✓ means verified, never "I could not look"."""
    monkeypatch.setattr("navig.platform.paths.config_dir", lambda: tmp_path)
    running["state"] = {"pid": 1}  # an older daemon: no log_file key

    row = _row(doctor.check_daemon_log_location())

    assert row[1] is False
    assert "unknown" in row.detail
    assert row[0] == doctor._WARN


def test_the_supervisor_records_the_same_path_logging_uses(monkeypatch, tmp_path):
    """Mirrored, not re-derived: the recorder must resolve through the SAME
    `ConfigManager.base_dir` the logging setup reads, or the row could name a
    file the daemon does not write."""
    from navig.daemon import supervisor as sup

    class _CM:
        base_dir = tmp_path / "somewhere" / ".navig"

    monkeypatch.setattr("navig.config.get_config_manager", lambda: _CM())

    assert Path(sup._resolved_log_file()) == _CM.base_dir / "navig.log"


def test_the_recorder_reports_unknown_rather_than_guessing(monkeypatch):
    from navig.daemon import supervisor as sup

    def _boom():
        raise RuntimeError("config unavailable")

    monkeypatch.setattr("navig.config.get_config_manager", _boom)

    assert sup._resolved_log_file() is None
