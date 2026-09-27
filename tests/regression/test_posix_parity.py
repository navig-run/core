"""Linux/macOS paths that were Windows-shaped — each pinned where it broke.

* ``_verify_daemon_pid`` read ``/proc/<pid>/cmdline``; macOS has no ``/proc``, so every
  live daemon verified as "not ours" → its pidfile was deleted and a SECOND supervisor
  booted.
* ``_force_kill_pid`` promised "+ its tree" and SIGKILLed only the pid on POSIX.
* The tray force-killed with ``subprocess.CREATE_NO_WINDOW`` (absent off Windows — the
  AttributeError was swallowed, nothing died, the state files were deleted anyway), read
  the RAW pidfile integer before a tree kill, and had no POSIX "is the daemon running".
* ``navig tray start`` / ``install`` pointed at ``<checkout>/scripts/…`` files that no
  longer exist anywhere and were never in the wheel — broken on every OS.
* The installer probed ``sc query NavigDaemon`` / ``systemctl is-enabled navig`` — names
  nothing creates — so Linux always read "not installed".
* ``install.sh`` uninstall stopped ``navig-daemon``/``navig`` system units and never the
  real ``navig-agent`` user unit, leaving an enabled unit pointing at a deleted venv.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from navig.daemon.supervisor import NavigDaemon

CORE = Path(__file__).resolve().parents[2]


# ── _verify_daemon_pid ────────────────────────────────────────────────────────


def test_an_unreadable_cmdline_trusts_the_identity_check(monkeypatch) -> None:
    monkeypatch.setattr(NavigDaemon, "_read_cmdline", staticmethod(lambda pid: None))
    assert NavigDaemon._verify_daemon_pid(1234) is True


@pytest.mark.parametrize(
    "cmdline,expected",
    [
        ("/usr/bin/python3 -m navig.daemon.entry", True),
        ("C:\\Python\\pythonw.exe -m navig daemon run", True),
        ("/usr/sbin/sshd -D", False),
        ("", False),  # the process is gone
    ],
)
def test_the_cmdline_decides_when_it_can_be_read(monkeypatch, cmdline, expected) -> None:
    monkeypatch.setattr(NavigDaemon, "_read_cmdline", staticmethod(lambda pid: cmdline))
    assert NavigDaemon._verify_daemon_pid(1234) is expected


def test_macos_reads_the_cmdline_through_ps(monkeypatch, tmp_path) -> None:
    import navig.daemon.supervisor as sup

    monkeypatch.setattr(sup.sys, "platform", "darwin")
    # No /proc on macOS.
    monkeypatch.setattr(sup, "Path", lambda p: tmp_path / "no-proc" / Path(p).name)
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="/opt/venv/bin/python -m navig.daemon.entry\n")

    monkeypatch.setattr(sup.subprocess, "run", fake_run)
    assert NavigDaemon._read_cmdline_os(4321) == "/opt/venv/bin/python -m navig.daemon.entry"
    assert calls == [["ps", "-o", "command=", "-p", "4321"]]


# ── _force_kill_pid kills the tree on POSIX ───────────────────────────────────


def test_posix_force_kill_takes_the_descendants_too(monkeypatch) -> None:
    import navig.core.aio_subprocess as aio
    import navig.daemon.supervisor as sup

    killed: list[str] = []

    class Child:
        def __init__(self, name):
            self.name = name

        def kill(self):
            killed.append(self.name)

    monkeypatch.setattr(sup.sys, "platform", "linux")
    monkeypatch.setattr(sup.signal, "SIGKILL", 9, raising=False)  # absent on Windows
    monkeypatch.setattr(aio, "_snapshot_descendants", lambda pid: [Child("gateway"), Child("bot")])
    monkeypatch.setattr(sup.os, "kill", lambda pid, sig: killed.append(f"pid{pid}"), raising=False)
    NavigDaemon._force_kill_pid(77)
    assert killed == ["pid77", "gateway", "bot"]


# ── the tray ──────────────────────────────────────────────────────────────────


def test_the_tray_files_it_launches_ship_in_the_package() -> None:
    from navig.commands import tray

    assert tray.TRAY_SCRIPT.is_file(), tray.TRAY_SCRIPT
    assert tray.INSTALL_SCRIPT.is_file(), tray.INSTALL_SCRIPT
    assert CORE / "navig" in tray.TRAY_SCRIPT.parents
    import importlib.util

    assert importlib.util.find_spec(tray.TRAY_MODULE) is not None


def test_the_pyw_entry_imports_the_packaged_tray() -> None:
    src = (CORE / "navig" / "desktop" / "tray_app.pyw").read_text(encoding="utf-8")
    assert "from navig.desktop.tray_app import main" in src
    assert "scripts.navig_tray" not in src


def test_the_windows_installer_launches_the_module() -> None:
    src = (CORE / "navig" / "desktop" / "install-tray.ps1").read_text(encoding="utf-8")
    assert "-m navig.desktop.tray_app" in src
    assert 'Join-Path $ProjectRoot "scripts\\navig_tray.pyw"' not in src


def test_tray_start_background_runs_the_module_detached_on_posix(monkeypatch) -> None:
    from typer.testing import CliRunner

    from navig.commands import tray

    seen: dict = {}
    monkeypatch.setattr(tray, "_is_tray_running", lambda: (False, None))
    monkeypatch.setattr(tray.sys, "platform", "linux")
    monkeypatch.setitem(__import__("sys").modules, "pystray", SimpleNamespace())

    def fake_popen(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return SimpleNamespace(pid=1)

    monkeypatch.setattr(tray.subprocess, "Popen", fake_popen)
    result = CliRunner().invoke(tray.tray_app, ["start"])
    assert result.exit_code == 0, result.output
    assert seen["cmd"][1:] == ["-m", "navig.desktop.tray_app"]
    assert seen["kw"].get("start_new_session") is True
    assert "creationflags" not in seen["kw"]


def test_the_tray_asks_the_identity_checked_pidfile(monkeypatch) -> None:
    pytest.importorskip("pystray")
    from navig.desktop import tray_app

    app = tray_app.NavigTray.__new__(tray_app.NavigTray)
    app.daemon = SimpleNamespace(is_alive=False)
    monkeypatch.setattr(NavigDaemon, "read_pid", staticmethod(lambda: 555))
    assert app._is_daemon_running() is True
    monkeypatch.setattr(NavigDaemon, "read_pid", staticmethod(lambda: None))
    assert app._is_daemon_running() is False


def test_tray_source_has_no_unguarded_windows_kill() -> None:
    src = (CORE / "navig" / "desktop" / "tray_app.py").read_text(encoding="utf-8")
    assert '["taskkill", "/F", "/PID", str(daemon_pid)' not in src
    assert 'int(pid_file.read_text(encoding="utf-8").strip())' not in src


# ── installer + install.sh ────────────────────────────────────────────────────


@pytest.mark.parametrize("answer", [True, False])
def test_the_installer_asks_the_code_that_registers_the_service(monkeypatch, answer) -> None:
    import navig.daemon.launch as launch
    from navig.installer.modules import service as svc

    monkeypatch.setattr(svc.sys, "platform", "linux")
    monkeypatch.setattr(launch, "service_is_installed", lambda: answer)
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: pytest.fail("probed a hard-coded service name")
    )
    assert svc._service_installed() is answer


def test_install_sh_uninstall_stops_and_removes_the_real_unit() -> None:
    src = (CORE / "install.sh").read_text(encoding="utf-8")
    assert "systemctl --user stop navig-agent.service" in src
    assert '"$RUNTIME_VENV/bin/navig" service uninstall' in src
