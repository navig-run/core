"""Doctor must not read a pid file as a running process.

Measured on this machine on 2026-10-25: ``gateway.pid`` was written on 22 Oct and its
pid number now belonged to a **Chrome renderer**. Doctor then reported a second
authority ("client of outpost-server AND a local brain is running") against a browser,
and the runtime inventory printed that browser as the daemon. A pid is a number, not a
process: the identity has to be checked, and a stale file has to be named rather than
silently ignored.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

from navig.commands import doctor

# ── fixtures ─────────────────────────────────────────────────────────────────────────

_ROOT = Path("C:/") if sys.platform == "win32" else Path("/")
_VENV_BIN = "Scripts" if sys.platform == "win32" else "bin"


def _host_exe(name: str, *dirs: str) -> str:
    """An executable path shaped like this host's — separator and ``.exe`` included.

    The fixtures used literal Windows paths (``C:\\runtime\\venv\\Scripts\\pythonw.exe``).
    Off Windows ``Path(that).name`` is the ENTIRE string, so ``_pid_still_ours`` never saw a
    ``python`` basename and called a live NAVIG process foreign — the test measured the
    host, not the check (2026-10-30, outpost).
    """
    leaf = f"{name}.exe" if sys.platform == "win32" else name
    return str(_ROOT.joinpath(*dirs, leaf))


_PYTHON = _host_exe("python", "runtime", "venv", _VENV_BIN)
_PYTHONW = _host_exe("pythonw", "runtime", "venv", _VENV_BIN)
_CHROME = _host_exe("chrome", "Program Files", "Google", "Chrome", "Application")


class _Proc:
    def __init__(self, exe: str, cmdline: list[str]) -> None:
        self._exe = exe
        self._cmdline = cmdline

    def exe(self) -> str:
        return self._exe

    def cmdline(self) -> list[str]:
        return list(self._cmdline)

    def name(self) -> str:
        return Path(self._exe).name


def _install_fake_psutil(monkeypatch, proc: _Proc | None) -> None:
    """A psutil where ``pid`` is *proc* (or gone when None)."""

    def _process(_pid: int) -> _Proc:
        if proc is None:
            raise RuntimeError("no such process")
        return proc

    fake = types.SimpleNamespace(Process=_process)
    monkeypatch.setitem(sys.modules, "psutil", fake)


def _pid_file(monkeypatch, tmp_path: Path, name: str, pid: int) -> None:
    (tmp_path / name).write_text(str(pid), encoding="utf-8")
    monkeypatch.setattr(doctor, "_managed_pid_files_dir", lambda: tmp_path)


# ── identity ────────────────────────────────────────────────────────────────────────


def test_a_python_running_navig_is_ours(monkeypatch):
    _install_fake_psutil(
        monkeypatch,
        _Proc(_PYTHONW, [_PYTHONW, "-m", "navig", "gateway", "run"]),
    )
    assert doctor._pid_still_ours(4242) is True


def test_a_browser_that_reused_the_pid_is_not(monkeypatch):
    _install_fake_psutil(
        monkeypatch,
        _Proc(_CHROME, [_CHROME, "--type=renderer"]),
    )
    assert doctor._pid_still_ours(35196) is False


def test_a_pid_with_no_process_is_not_ours(monkeypatch):
    _install_fake_psutil(monkeypatch, None)
    assert doctor._pid_still_ours(35196) is False


# ── what the rows make of it ────────────────────────────────────────────────────────


def test_a_recycled_pid_is_not_a_managed_daemon(monkeypatch, tmp_path):
    _pid_file(monkeypatch, tmp_path, "gateway.pid", 35196)
    _install_fake_psutil(
        monkeypatch,
        _Proc(_CHROME, [_CHROME]),
    )
    assert doctor._managed_daemon_pids() == set()


def test_a_live_gateway_pid_is_a_managed_daemon(monkeypatch, tmp_path):
    _pid_file(monkeypatch, tmp_path, "gateway.pid", 4242)
    _install_fake_psutil(
        monkeypatch, _Proc(_PYTHON, [_PYTHON, "-m", "navig", "gateway"])
    )
    assert doctor._managed_daemon_pids() == {4242}


def test_a_stale_pid_file_is_named_with_its_new_owner(monkeypatch, tmp_path):
    _pid_file(monkeypatch, tmp_path, "gateway.pid", 35196)
    _install_fake_psutil(
        monkeypatch,
        _Proc(_CHROME, [_CHROME]),
    )
    row = doctor._stale_pid_files_row()
    assert row is not None
    assert row[0] == doctor._WARN and row[1] is False  # could-not-trust is a warning, not a red
    assert "gateway.pid" in row.detail
    assert "35196" in row.detail
    assert Path(_CHROME).name in row.detail  # `chrome.exe` on Windows, `chrome` on POSIX


def test_no_stale_pid_file_no_row(monkeypatch, tmp_path):
    _pid_file(monkeypatch, tmp_path, "gateway.pid", 4242)
    _install_fake_psutil(
        monkeypatch, _Proc(_PYTHON, [_PYTHON, "-m", "navig", "gateway"])
    )
    assert doctor._stale_pid_files_row() is None
