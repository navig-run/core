"""macOS autostart: a per-user LaunchAgent, driven through `launchctl`.

macOS had no service backend at all — `detect_best_method()` returned "manual", so
`navig service install` failed on every Mac and install.sh silently registered nothing.
These are pure tests (plist content, command construction, dispatch); the real
`launchctl bootstrap` path is exercised on macos-latest by the Portability workflow.
"""

from __future__ import annotations

import plistlib
from types import SimpleNamespace

import pytest

from navig.daemon import service_manager as sm


@pytest.fixture
def mac(monkeypatch, tmp_path):
    monkeypatch.setattr(sm.sys, "platform", "darwin")
    monkeypatch.setattr(sm.shutil, "which", lambda name: "/bin/launchctl" if name == "launchctl" else None)
    monkeypatch.setattr(sm.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(sm, "_ensure_dirs", lambda: None)
    monkeypatch.setattr(sm, "_navig_home", lambda: tmp_path / ".navig")
    monkeypatch.setattr(sm, "_log_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(sm, "_python_exe", lambda: "/opt/navig/runtime/bin/python3")
    monkeypatch.setattr(sm, "_launchd_domain", lambda: "gui/501")
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        if cmd[:2] == ["launchctl", "print"]:
            return SimpleNamespace(returncode=0, stdout="\tstate = running\n\tpid = 4242\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sm.subprocess, "run", fake_run)
    return SimpleNamespace(home=tmp_path, calls=calls)


def test_macos_picks_launchd(mac) -> None:
    assert sm.detect_best_method() == "launchd"


def test_the_plist_is_valid_and_runs_the_daemon(mac) -> None:
    data = plistlib.loads(sm._launchd_plist_content().encode())
    assert data["Label"] == sm.LAUNCHD_LABEL
    assert data["ProgramArguments"] == ["/opt/navig/runtime/bin/python3", "-m", "navig.daemon.entry"]
    assert data["EnvironmentVariables"]["NAVIG_SERVICE"] == "1"
    assert data["RunAtLoad"] is True
    # A clean `navig service stop` exit must NOT be restarted; a crash must be.
    assert data["KeepAlive"] == {"SuccessfulExit": False}


def test_install_writes_the_agent_and_bootstraps_it(mac) -> None:
    ok, msg = sm.install("launchd")
    assert ok, msg
    plist = mac.home / "Library" / "LaunchAgents" / f"{sm.LAUNCHD_LABEL}.plist"
    assert plist.exists()
    assert ["launchctl", "bootstrap", "gui/501", str(plist)] in mac.calls
    # re-install is idempotent: the old load is booted out first
    assert mac.calls[0] == ["launchctl", "bootout", f"gui/501/{sm.LAUNCHD_LABEL}"]


def test_status_reads_launchctl_print(mac) -> None:
    sm.install("launchd")
    running, detail = sm.launchd_status()
    assert running is True and "4242" in detail


def test_uninstall_boots_out_and_removes(mac) -> None:
    sm.install("launchd")
    ok, _ = sm.uninstall("launchd")
    assert ok
    assert not (mac.home / "Library" / "LaunchAgents" / f"{sm.LAUNCHD_LABEL}.plist").exists()


def test_the_launcher_sees_the_installed_agent(mac) -> None:
    from navig.daemon import launch

    assert launch.service_is_installed() is False
    sm.install("launchd")
    assert launch.service_is_installed() is True


def test_launchd_refused_off_macos(monkeypatch) -> None:
    monkeypatch.setattr(sm.sys, "platform", "linux")
    ok, msg = sm.install("launchd")
    assert not ok and "macOS" in msg
