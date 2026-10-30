"""The shim row must not tell a healthy install to reinstall itself.

Found on outpost (2026-10-26): ``~/.local/bin`` is in the login shell's PATH and
``navig`` runs, yet ``navig doctor`` launched from a systemd unit or
``ssh host 'cmd'`` reported "shim dir on PATH — open a new shell / re-run
install". The row read only the *current* process PATH, and non-login shells
never read the login profile, so its advice could not change anything.
"""

from __future__ import annotations

import os
from pathlib import Path

from navig.commands import doctor


def _shim(tmp_path: Path, *, exists: bool = True) -> Path:
    shim = tmp_path / "home" / ".local" / "bin" / ("navig.cmd" if os.name == "nt" else "navig")
    shim.parent.mkdir(parents=True, exist_ok=True)
    if exists:
        shim.write_text("", encoding="utf-8")
    return shim


def test_directory_on_path_is_green_and_names_it(tmp_path, monkeypatch) -> None:
    shim = _shim(tmp_path)
    monkeypatch.setenv("PATH", os.pathsep.join([str(shim.parent), "/usr/bin"]))

    row = doctor._shim_path_row(shim)

    assert row[1] is True, row[2]
    assert row[0] == doctor._OK
    assert row.detail == str(shim.parent)


def test_a_non_login_shell_is_green_and_says_where_navig_resolved(tmp_path, monkeypatch) -> None:
    """The regression: this shell lacks the dir, but the install is healthy."""
    shim = _shim(tmp_path)
    resolved = str(tmp_path / "runtime" / "venv" / "bin" / "navig")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: resolved)

    row = doctor._shim_path_row(shim)

    assert row[1] is True, row[2]
    assert row[0] == doctor._OK
    assert resolved in row.detail, "the row must say where navig actually resolves"
    assert "non-login" in row.detail, "the operator needs to know why this PATH lacks the dir"
    assert "re-run install" not in row[2], "a healthy install must not be told to reinstall"


def test_a_run_through_the_launcher_is_green_without_the_dir(tmp_path, monkeypatch) -> None:
    """The reported case: an absolute-path `navig doctor` from a shell without the dir."""
    shim = _shim(tmp_path)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)
    monkeypatch.setattr(doctor.sys, "argv", [str(shim), "doctor"])

    row = doctor._shim_path_row(shim)

    assert row[1] is True, row[2]
    assert row[0] == doctor._OK
    assert "came through the launcher" in row.detail
    assert str(shim.parent) in row.detail


def test_navig_that_does_not_resolve_warns_with_the_fix(tmp_path, monkeypatch) -> None:
    shim = _shim(tmp_path)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)
    monkeypatch.setattr(doctor.sys, "argv", ["python", "-m", "navig", "doctor"])

    row = doctor._shim_path_row(shim)

    assert row[1] is False
    assert row[0] == doctor._WARN, "an unrunnable navig is a warning, not a green row"
    assert "does not resolve" in row.detail
    assert str(shim.parent) in row.detail


def test_check_runtime_reaches_the_row_without_a_false_warning(tmp_path, monkeypatch) -> None:
    """Wire guard: ``check_runtime`` actually calls the helper, on a managed install."""
    runtime = tmp_path / "runtime"
    venv_bin = runtime / "venv" / ("Scripts" if os.name == "nt" else "bin")
    venv_bin.mkdir(parents=True)
    (venv_bin / ("python.exe" if os.name == "nt" else "python")).write_text("", encoding="utf-8")
    shim = _shim(tmp_path)
    monkeypatch.setattr(doctor, "_runtime_dir", lambda: runtime)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path / "home"))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: str(shim))
    monkeypatch.setattr(doctor, "_daemon_autostart", lambda: (True, "systemd"))

    rows = doctor.check_runtime()

    shim_rows = [row for row in rows if row.label == "shim dir on PATH"]
    assert len(shim_rows) == 1, [row.label for row in rows]
    assert shim_rows[0][1] is True, shim_rows[0][2]
