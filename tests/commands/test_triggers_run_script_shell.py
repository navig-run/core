"""A `.sh` trigger action runs through a real POSIX shell, resolved by full path.

`TriggerManager._run_script` spawned ``["bash", path]``. On Windows that is the WSL
launcher — ``CreateProcess`` searches System32 before PATH, whatever ``shutil.which``
reports — which cannot run a script at a Windows path: the action "failed" with
``/bin/bash: E:projectsapps…: No such file`` on a machine with Git Bash installed.
Now the shell is ``navig.platform.process.posix_shell()`` — Git Bash derived from the git
install on Windows, ``bash``/``sh`` elsewhere — and its absence is a named failure, not a
crash.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from navig.commands.triggers import TriggerManager
from navig.platform import process as proc_mod
from tests.fixtures.posix_shell import POSIX_SHELL, needs_posix_shell


def _mgr() -> TriggerManager:
    # `__init__` reads config; `_run_script` needs none of it.
    return TriggerManager.__new__(TriggerManager)


@needs_posix_shell
def test_a_sh_script_runs_and_sees_its_params_as_env(tmp_path: Path) -> None:
    script = tmp_path / "hello.sh"
    script.write_bytes(b'#!/bin/sh\n[ "$GREETING" = "hi" ] || exit 3\nexit 0\n')
    ok, msg = _mgr()._run_script(str(script), {"GREETING": "hi"})
    assert (ok, msg) == (True, ""), msg
    ok, msg = _mgr()._run_script(str(script), {"GREETING": "no"})
    assert ok is False, "a non-zero exit is reported, not swallowed"


def test_the_shell_is_resolved_by_full_path_never_a_bare_bash(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, list[str]] = {}
    import subprocess

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd

        class R:
            returncode = 0
            stderr = ""

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(proc_mod, "posix_shell", lambda: str(tmp_path / "git" / "bin" / "bash.exe"))
    script = tmp_path / "t.sh"
    script.write_text("exit 0\n", encoding="utf-8")
    ok, _ = _mgr()._run_script(str(script), {})
    assert ok is True
    assert seen["cmd"][0] == str(tmp_path / "git" / "bin" / "bash.exe"), seen
    assert seen["cmd"][0] != "bash"


def test_no_shell_is_a_named_failure_not_a_traceback(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(proc_mod, "posix_shell", lambda: None)
    script = tmp_path / "t.sh"
    script.write_text("exit 0\n", encoding="utf-8")
    ok, msg = _mgr()._run_script(str(script), {})
    assert ok is False and "No POSIX shell" in msg and "t.sh" in msg


@pytest.mark.skipif(sys.platform != "win32", reason="the System32 trap is a Windows shape")
def test_posix_shell_never_returns_the_wsl_launcher(monkeypatch) -> None:
    """With System32 FIRST on PATH, shutil.which('bash') is the WSL launcher; the resolver is not."""
    import os
    import shutil

    monkeypatch.setenv("PATH", r"C:\Windows\System32" + os.pathsep + os.environ.get("PATH", ""))
    which = shutil.which("bash")
    if which and "system32" in which.lower():
        found = proc_mod.posix_shell()
        assert found is None or "system32" not in found.lower(), found
    assert POSIX_SHELL is None or "system32" not in POSIX_SHELL.lower()
