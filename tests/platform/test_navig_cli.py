"""A child that re-enters NAVIG starts from an ABSOLUTE launcher, never the bare word.

Measured 2026-10-20: a gateway started by hand carried a PATH with no ``navig``
entry at all, so every CLI-shaped cron job died 25–45 ms in with
``[WinError 2] The system cannot find the file specified`` while in-process jobs
kept working. On Windows the first ``navig`` on a *correct* PATH is also the
``navig.cmd`` shim, which ``CreateProcess`` cannot execute. Both are the same
rule: resolve the launcher, do not ask the environment to.
"""

from __future__ import annotations

import sys
from pathlib import Path

from navig.platform import navig_cli


def test_launcher_is_never_the_bare_word():
    argv = navig_cli.navig_launcher()
    assert argv[0] != "navig"
    assert Path(argv[0]).is_absolute()


def test_default_prefers_this_interpreter_when_navig_is_importable(monkeypatch):
    monkeypatch.setattr(navig_cli, "_importable", lambda: True)
    monkeypatch.setattr(navig_cli, "_entrypoint", lambda: ["C:/some/navig.exe"])

    assert navig_cli.navig_launcher() == [sys.executable, "-m", "navig"]


def test_entrypoint_order_survives_when_asked(monkeypatch):
    # `_on_path` is what runs FIRST for this preference, and on a developer box whose PATH
    # carries a real `navig` it wins — which made this assertion pass on Windows (where the
    # only candidate is the `.cmd` shim `_on_path` rejects) and fail on Linux, measuring the
    # host instead of the order under test (2026-10-30, outpost).
    monkeypatch.setattr(navig_cli, "_on_path", lambda: None)
    monkeypatch.setattr(navig_cli, "_importable", lambda: True)
    monkeypatch.setattr(navig_cli, "_entrypoint", lambda: ["C:/some/navig.exe"])

    assert navig_cli.navig_launcher(prefer_entrypoint=True) == ["C:/some/navig.exe"]


def test_falls_back_to_the_entrypoint_when_the_interpreter_cannot_import(monkeypatch):
    monkeypatch.setattr(navig_cli, "_importable", lambda: False)
    monkeypatch.setattr(navig_cli, "_entrypoint", lambda: ["C:/some/navig.exe"])

    assert navig_cli.navig_launcher() == ["C:/some/navig.exe"]


def test_module_form_is_the_last_resort_so_a_failure_names_a_program(monkeypatch):
    monkeypatch.setattr(navig_cli, "_importable", lambda: False)
    monkeypatch.setattr(navig_cli, "_entrypoint", lambda: None)

    assert navig_cli.navig_launcher() == [sys.executable, "-m", "navig"]


def test_a_cmd_shim_on_path_is_refused(monkeypatch, tmp_path):
    """CreateProcess appends only `.exe`, so a `.cmd` hit is not launchable."""
    shim = tmp_path / "navig.cmd"
    shim.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr(navig_cli.sys, "executable", str(tmp_path / "python.exe"))
    monkeypatch.setattr(navig_cli.shutil, "which", lambda _name: str(shim))

    assert navig_cli._entrypoint() is None


def test_a_real_executable_on_path_is_accepted(monkeypatch, tmp_path):
    exe = tmp_path / "navig.exe"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(navig_cli.sys, "executable", str(tmp_path / "python.exe"))
    monkeypatch.setattr(navig_cli.shutil, "which", lambda _name: str(exe))

    assert navig_cli._entrypoint() == [str(exe)]


def test_a_frozen_interpreter_named_navig_is_its_own_launcher(monkeypatch, tmp_path):
    frozen = tmp_path / "navig.exe"
    frozen.write_text("", encoding="utf-8")
    monkeypatch.setattr(navig_cli.sys, "executable", str(frozen))

    assert navig_cli._entrypoint() == [str(frozen)]
