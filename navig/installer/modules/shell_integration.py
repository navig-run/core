"""
Installer module: add navig to PATH via shell rc files (Linux / macOS).

No-op on Windows — PATH management is handled by the pip installer and
Windows Environment Variables, not by rc files.

Idempotency: checks for the marker comment before appending.
Rollback: strips the appended snippet using the saved undo_data.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from navig.installer.contracts import Action, InstallerContext, ModuleState, Result

name = "shell_integration"
description = "Add navig to PATH in shell rc files"

_MARKER = "# navig shell integration"


# ─────────────────────── plan ─────────────────────────────────────────────────


def plan(ctx: InstallerContext) -> list[Action]:
    if sys.platform == "win32":
        return []  # No-op on Windows

    bin_dir = _navig_bin_dir()
    if bin_dir is None:
        return []

    actions: list[Action] = []
    for rc in _shell_rc_candidates():
        content = rc.read_text(encoding="utf-8", errors="replace")
        if _MARKER in content:
            continue  # already integrated
        if str(bin_dir) in content:
            continue  # already on PATH via another mechanism
        actions.append(
            Action(
                id=f"shell_integration.{rc.name}",
                description=f"Add navig to PATH in ~/{rc.name}",
                module=name,
                data={"rc": str(rc), "bin_dir": str(bin_dir)},
                reversible=True,
            )
        )
    return actions


# ─────────────────────── apply ────────────────────────────────────────────────


def apply(action: Action, ctx: InstallerContext) -> Result:
    rc = Path(action.data["rc"])
    bin_dir = action.data["bin_dir"]
    snippet = f'\n{_MARKER}\nexport PATH="{bin_dir}:$PATH"\n'

    try:
        with open(rc, "a", encoding="utf-8") as fh:
            fh.write(snippet)
        return Result(
            action_id=action.id,
            state=ModuleState.APPLIED,
            message=f"Added navig to PATH in {rc.name}",
            undo_data={"rc": str(rc), "snippet": snippet},
        )
    except OSError as exc:
        return Result(
            action_id=action.id,
            state=ModuleState.FAILED,
            error=str(exc),
        )


# ─────────────────────── rollback ─────────────────────────────────────────────


def rollback(action: Action, result: Result, ctx: InstallerContext) -> None:
    rc_path = result.undo_data.get("rc", "")
    snippet = result.undo_data.get("snippet", "")
    if not (rc_path and snippet):
        return
    rc = Path(rc_path)
    if not rc.exists():
        return
    try:
        content = rc.read_text(encoding="utf-8", errors="replace")
        rc.write_text(content.replace(snippet, ""), encoding="utf-8")
    except OSError:
        pass


# ─────────────────────── helpers ──────────────────────────────────────────────


def _shell_rc_candidates() -> list[Path]:
    home = Path.home()
    candidates = [home / ".bashrc", home / ".zshrc", home / ".profile"]
    return [p for p in candidates if p.exists()]


def _navig_bin_dir() -> Path | None:
    """Find the directory that contains the navig executable."""
    navig_exe = shutil.which("navig")
    if navig_exe:
        return Path(navig_exe).parent

    # pip --user install puts scripts in ~/.local/bin on Linux
    try:
        import site

        site_packages = site.getusersitepackages() or ""
        if site_packages:
            # site-packages is something like ~/.local/lib/python3.x/site-packages
            # bin is ~/.local/bin
            guessed = Path(site_packages).parent.parent.parent / "bin"
            if guessed.exists():
                return guessed
    except Exception:  # noqa: BLE001
        pass

    return None
