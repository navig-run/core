"""
Installer module: add navig to PATH via shell rc files (Linux / macOS).

No-op on Windows — PATH management is handled by the pip installer and
Windows Environment Variables, not by rc files.

Idempotency: checks for the marker comment before appending.
Rollback: strips the appended snippet using the saved undo_data.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from navig.core.yaml_io import atomic_write_text
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
                data={"rc": str(rc), "bin_dir": str(bin_dir), "syntax": _rc_syntax(rc)},
                reversible=True,
            )
        )
    return actions


# ─────────────────────── apply ────────────────────────────────────────────────


def apply(action: Action, ctx: InstallerContext) -> Result:
    rc = Path(action.data["rc"])
    bin_dir = action.data["bin_dir"]
    snippet = _snippet(bin_dir, action.data.get("syntax") or _rc_syntax(rc))

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
        atomic_write_text(rc, content.replace(snippet, ""))
    except OSError:
        pass  # best-effort: skip on IO error
# ─────────────────────── helpers ──────────────────────────────────────────────


def _login_shell_name() -> str:
    """The user's login shell, from ``$SHELL`` then the passwd entry (POSIX only)."""
    shell = os.environ.get("SHELL", "").strip()
    if not shell:
        try:
            import pwd  # noqa: PLC0415 — POSIX-only module

            shell = pwd.getpwuid(os.getuid()).pw_shell or ""
        except Exception:  # noqa: BLE001 — no passwd (Windows) or no entry
            return ""
    return Path(shell).name.lower()


def _shell_rc_candidates() -> list[Path]:
    """The rc files the user's LOGIN shell actually reads.

    macOS Terminal.app and iTerm start a LOGIN shell: bash reads ``.bash_profile`` and
    zsh reads ``.zprofile``, so appending only to ``.bashrc``/``.zshrc`` left a macOS
    install with ``navig`` on PATH only inside shells started from an already-configured
    terminal. fish reads ``~/.config/fish/config.fish`` and never sources ``.profile``.
    """
    home = Path.home()
    shell = _login_shell_name()
    if shell == "zsh":
        candidates = [home / ".zprofile", home / ".zshrc"]
    elif shell in ("bash", "sh", "dash", "ksh", "ash"):
        candidates = [home / ".bash_profile", home / ".bashrc"]
    elif shell == "fish":
        candidates = [home / ".config" / "fish" / "config.fish"]
    else:
        candidates = [home / ".profile", home / ".bashrc", home / ".zshrc"]

    existing = [p for p in candidates if p.exists()]
    if existing:
        return existing
    # A shell whose own file does not exist yet still reads the POSIX default.
    profile = home / ".profile"
    return [profile] if profile.exists() else []


def _rc_syntax(rc: Path) -> str:
    """``fish`` for fish's config file, ``posix`` for every other rc file."""
    return "fish" if rc.name == "config.fish" else "posix"


def _snippet(bin_dir: str, syntax: str) -> str:
    """The PATH line for *syntax*; fish has no ``export`` and fails on one."""
    if syntax == "fish":
        return f'\n{_MARKER}\nfish_add_path "{bin_dir}"\n'
    return f'\n{_MARKER}\nexport PATH="{bin_dir}:$PATH"\n'


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
