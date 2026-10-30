"""Resolving the NAVIG CLI for a child process — never the bare word.

**Why this exists.** ``subprocess``/``CreateProcess`` resolves a *bare* program
name against the **parent process's** environment, not the install's. Measured
2026-10-20: a gateway started by hand (``py -3.13 -m navig gateway start`` from
an agent shell) carried a PATH with neither ``…\\Python313\\Scripts`` nor
``…\\Local\\NAVIG\\bin``, so every CLI-shaped cron job failed 25–45 ms in with
``[WinError 2] The system cannot find the file specified`` while the in-process
jobs (habit reminders, check-ins) kept succeeding.

Windows adds a second trap a *correct* PATH does not fix: the first ``navig``
on the user PATH is the ``navig.cmd`` shim, and ``CreateProcess`` appends only
``.exe`` while searching, so the shim is skipped silently.

**The rule:** a child that re-enters NAVIG starts from an **absolute** launcher
— this interpreter with ``-m navig`` (the same code and version the parent is
already executing), or the install's own entry-point executable. Never the bare
word, never a ``.cmd``.

Two preference orders, one per caller's history:

- default — the running interpreter first. A scheduler, deck route or tool
  inside a live gateway must run the SAME install it is part of; PATH can point
  at another one.
- ``prefer_entrypoint=True`` — the installed entry point first (uv-managed
  envs, a shim that carries its own interpreter), falling back to ``-m navig``.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

from navig.platform import capabilities

# The launcher name is the seam's answer, not a second copy of the same guess: a
# `.exe` suffix is a Windows fact, and `capabilities.launcher_name()` already owns it.
_LAUNCHER_NAME = capabilities.launcher_name()


def _importable() -> bool:
    """Can THIS interpreter import the ``navig`` package?"""
    try:
        return importlib.util.find_spec("navig") is not None
    except (ImportError, ValueError):  # broken or namespace navig must not raise here
        return False


def _on_path() -> list[str] | None:
    """A real ``navig`` executable on PATH — never the ``.cmd`` shim."""
    found = shutil.which(_LAUNCHER_NAME)
    if found and not found.lower().endswith((".cmd", ".bat")):
        return [found]
    return None


def _entrypoint() -> list[str] | None:
    """The install's own launcher, when CreateProcess can actually execute it."""
    exe = Path(sys.executable)
    if exe.stem.lower() == "navig":
        return [str(exe)]  # frozen install: the executable IS the CLI

    sibling = exe.with_name(_LAUNCHER_NAME)
    if sibling.is_file():
        return [str(sibling)]

    return _on_path()


def navig_launcher(*, prefer_entrypoint: bool = False) -> list[str]:
    """Return the argv prefix that re-enters THIS NAVIG install.

    Always an absolute program (or ``sys.executable -m navig``), so a spawn
    cannot depend on who started the parent process.
    """
    entrypoint = _on_path() if prefer_entrypoint else None
    if entrypoint is None:
        entrypoint = _entrypoint()
    if prefer_entrypoint and entrypoint:
        return entrypoint
    if _importable():
        return [sys.executable, "-m", "navig"]
    if entrypoint:
        return entrypoint
    # Nothing is launchable — return the module form so the failure names a
    # real program instead of the bare word.
    return [sys.executable, "-m", "navig"]
