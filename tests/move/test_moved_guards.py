"""After ``navig move out``, nothing may start this NAVIG here again until ``move undo``.

Every start path is checked: the daemon entry (what the scheduled task and tray run),
``navig gateway start`` and ``navig service start``. A second brain on the same relay
tenant would fight the new machine for every Telegram update.
"""

from __future__ import annotations

import json
import os

import pytest
from typer.testing import CliRunner

import navig.daemon.entry as entry
from navig.move import marker


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "navig"))
    monkeypatch.chdir(os.getcwd())  # entry.main() chdir's to its home by design
    return tmp_path / "navig"


def _never(*a, **k):  # pragma: no cover - the point is that it is not called
    raise AssertionError("a start path went on past the moved marker")


def test_the_marker_round_trips_and_lives_with_the_runtime_state(home) -> None:
    assert marker.read_moved() is None
    path = marker.write_moved({"bundle": "/tmp/x.navigmove"})
    assert path == home / "daemon" / "moved.json"
    info = marker.read_moved()
    assert info is not None and info["bundle"] == "/tmp/x.navigmove" and info["moved_at"]
    assert "navig move undo" in marker.moved_message(info)
    assert marker.clear_moved() is True
    assert marker.read_moved() is None


def test_an_unreadable_marker_still_counts_as_moved(home) -> None:
    (home / "daemon").mkdir(parents=True)
    (home / "daemon" / "moved.json").write_text("{not json", encoding="utf-8")
    assert marker.read_moved() == {"unreadable": True}


def test_the_daemon_entry_returns_before_any_boot_work(home, monkeypatch) -> None:
    import navig.daemon.supervisor as supervisor

    marker.write_moved({})
    monkeypatch.setattr(supervisor.NavigDaemon, "is_running", staticmethod(_never))
    monkeypatch.setattr(entry, "_load_config", _never)
    assert entry.main() is None


def test_gateway_start_refuses(home, monkeypatch) -> None:
    from navig.commands import gateway

    marker.write_moved({})
    monkeypatch.setattr(gateway, "_free_port", _never)
    monkeypatch.setattr(gateway, "_supersede_other_gateways", _never)
    result = CliRunner().invoke(gateway.gateway_app, ["start"], env={"COLUMNS": "200"})
    assert result.exit_code == 1
    assert "navig move undo" in result.output


def test_service_start_refuses(home, monkeypatch) -> None:
    import navig.daemon.supervisor as supervisor
    from navig.commands import service

    marker.write_moved({})
    monkeypatch.setattr(supervisor.NavigDaemon, "is_running", staticmethod(_never))
    result = CliRunner().invoke(service.service_app, ["start"], env={"COLUMNS": "200"})
    assert result.exit_code == 1
    assert "navig move undo" in result.output


def test_status_and_undo(home) -> None:
    from navig.commands.move import move_app

    runner = CliRunner()
    assert json.loads(runner.invoke(move_app, ["status", "--json"]).output) == {"moved": False}
    marker.write_moved({"bundle": "b.navigmove"})
    status = json.loads(runner.invoke(move_app, ["status", "--json"]).output)
    assert status["moved"] is True and status["bundle"] == "b.navigmove"
    undone = runner.invoke(move_app, ["undo", "--yes"], env={"COLUMNS": "200"})
    assert undone.exit_code == 0
    assert marker.read_moved() is None


def test_undo_without_a_terminal_asks_for_yes(home) -> None:
    from navig.commands.move import move_app

    marker.write_moved({})
    result = CliRunner().invoke(move_app, ["undo"], env={"COLUMNS": "200"})
    assert result.exit_code == 1
    assert marker.read_moved() is not None


def test_an_unresolvable_home_reads_as_not_moved(monkeypatch) -> None:
    """The daemon entry boots even when the home cannot be resolved; the marker check
    must not be what stops it (tests/daemon/test_entry_runs_from_its_home.py)."""
    from navig.platform import paths

    def _no_home():
        raise RuntimeError("no home")

    monkeypatch.setattr(paths, "config_dir", _no_home)
    assert marker.read_moved() is None
