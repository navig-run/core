"""launchd supervises the daemon on macOS — and lifecycle commands must go through it.

The systemd side has had this since the supervisor work: `supervising_systemd_unit()` tells
`navig service stop/restart` that an init system owns the daemon, so they ask it to act
instead of killing a process it will respawn. macOS had no counterpart, so
`navig service stop` killed a `KeepAlive` job and launchd restarted it immediately —
stop looked broken and restart raced itself.

These tests stub `launchctl` (never called on this host) and pin the identity to macOS.
"""

from __future__ import annotations

import subprocess

import pytest
from typer.testing import CliRunner

from navig.commands.service import service_app
from navig.daemon import service_manager as sm

DOMAIN = "gui/501"
TARGET = f"{DOMAIN}/{sm.LAUNCHD_LABEL}"


@pytest.fixture
def plist_path(tmp_path, monkeypatch):
    """macOS with the LaunchAgent installed: launchctl stubbed, the plist on disk."""
    path = tmp_path / f"{sm.LAUNCHD_LABEL}.plist"
    path.write_text("<plist/>", encoding="utf-8")
    monkeypatch.setattr(sm, "has_launchd", lambda: True)
    monkeypatch.setattr(sm, "_launchd_plist_path", lambda: path)
    monkeypatch.setattr(sm, "_launchd_domain", lambda: DOMAIN)
    return path


def _stub_launchctl(monkeypatch, results: dict[tuple[str, ...], tuple[int, str, str]]):
    """Record every launchctl argv and answer from *results* (default: success)."""
    calls: list[tuple[str, ...]] = []

    def fake(*args: str) -> subprocess.CompletedProcess:
        calls.append(args)
        rc, out, err = results.get(args, (0, "", ""))
        return subprocess.CompletedProcess(["launchctl", *args], rc, out, err)

    monkeypatch.setattr(sm, "_launchctl", fake)
    return calls


def test_a_loaded_job_is_the_supervisor(plist_path, monkeypatch) -> None:
    calls = _stub_launchctl(monkeypatch, {})
    assert sm.supervising_launchd_job() == sm.LAUNCHD_LABEL
    assert calls == [("print", TARGET)]


def test_an_installed_but_unloaded_job_supervises_nothing(plist_path, monkeypatch) -> None:
    _stub_launchctl(monkeypatch, {("print", TARGET): (113, "", "Could not find service")})
    assert sm.supervising_launchd_job() is None


def test_a_missing_plist_means_no_supervisor(plist_path, monkeypatch) -> None:
    plist_path.unlink()
    calls = _stub_launchctl(monkeypatch, {})
    assert sm.supervising_launchd_job() is None
    assert calls == []  # nothing to ask about


def test_a_host_without_launchctl_has_no_supervisor(plist_path, monkeypatch) -> None:
    monkeypatch.setattr(sm, "has_launchd", lambda: False)
    assert sm.supervising_launchd_job() is None


def test_stop_boots_the_job_out(plist_path, monkeypatch) -> None:
    calls = _stub_launchctl(monkeypatch, {})
    ok, msg = sm.launchd_job_action("stop")
    assert ok is True
    assert calls == [("bootout", TARGET)]
    assert "bootout" in msg


def test_restart_kickstarts_in_place(plist_path, monkeypatch) -> None:
    calls = _stub_launchctl(monkeypatch, {})
    ok, _ = sm.launchd_job_action("restart")
    assert ok is True
    assert calls == [("kickstart", "-k", TARGET)]


def test_start_bootstraps_an_unloaded_job_then_kickstarts(plist_path, monkeypatch) -> None:
    calls = _stub_launchctl(monkeypatch, {("print", TARGET): (113, "", "not loaded")})
    ok, _ = sm.launchd_job_action("start")
    assert ok is True
    assert calls == [
        ("print", TARGET),
        ("bootstrap", DOMAIN, str(plist_path)),
        ("kickstart", TARGET),
    ]


def test_a_failure_names_the_command_to_run(plist_path, monkeypatch) -> None:
    _stub_launchctl(monkeypatch, {("bootout", TARGET): (1, "", "No such process")})
    ok, msg = sm.launchd_job_action("stop")
    assert ok is False
    assert "No such process" in msg
    assert f"run: launchctl bootout {TARGET}" in msg


def test_an_unknown_action_is_refused(plist_path, monkeypatch) -> None:
    _stub_launchctl(monkeypatch, {})
    ok, msg = sm.launchd_job_action("reload")
    assert ok is False
    assert "unknown launchd action" in msg


def test_service_stop_on_macos_asks_launchd_instead_of_killing(monkeypatch) -> None:
    """The CLI must not kill a KeepAlive job — launchd would restart it in a second."""
    runner = CliRunner()
    acted: list[str] = []
    monkeypatch.setattr("navig.daemon.service_manager.supervising_systemd_unit", lambda: None)
    monkeypatch.setattr("navig.commands.service.sys.platform", "darwin")
    monkeypatch.setattr(sm, "supervising_launchd_job", lambda: sm.LAUNCHD_LABEL)
    monkeypatch.setattr(
        sm, "launchd_job_action", lambda action: (acted.append(action) or (True, "launchctl ok"))
    )
    killed: list[bool] = []
    monkeypatch.setattr(
        "navig.daemon.supervisor.NavigDaemon.stop_running_daemon",
        lambda *a, **k: (killed.append(True), True)[1],
    )

    result = runner.invoke(service_app, ["stop"])

    assert result.exit_code == 0, result.output
    assert acted == ["stop"]  # through launchd …
    assert killed == []  # … and never by killing the process
    assert "run.navig.daemon" in result.output
