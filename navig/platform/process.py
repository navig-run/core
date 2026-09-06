"""Spawning a child process without flashing a console window at the operator.

**Why a console appears at all.** NAVIG's daemon is *correctly* windowless: it runs under
``pythonw.exe`` (``service_manager._pythonw_exe``), its Task Scheduler action is
``<Hidden>true</Hidden>``, and every daemon-start path already passes ``CREATE_NO_WINDOW``.
That is exactly why its *children* flash. A process with **no console cannot lend one**, so
when a windowless parent spawns a console program — ``git.exe``, ``icacls.exe``,
``taskkill.exe``, ``powershell.exe``, ``npx.CMD``, ``ffmpeg.exe`` — Windows allocates it a
**brand-new console**, which appears and vanishes. Under a normal ``python.exe`` the same
child silently inherits the terminal and nothing is visible.

Measured over ``core/navig`` + ``plugins``: 486 direct spawn sites, of which **30** passed
any ``creationflags``. The flag was applied by hand, per site, with no helper — and the
worst tell is ``windows_utils.run_with_graceful_timeout``, a *shared* Windows-aware wrapper
that ORs in ``CREATE_NEW_PROCESS_GROUP`` (which does **not** suppress a window) while its own
internal ``taskkill`` call, 23 lines below, does pass ``CREATE_NO_WINDOW``.

**Two flags that look interchangeable and are not.**

``CREATE_NEW_PROCESS_GROUP``
    Lets you send ``CTRL_BREAK_EVENT``. Does nothing about windows.
``DETACHED_PROCESS``
    The child gets **no console at all** — so it is already silent, and ORing
    ``CREATE_NO_WINDOW`` onto it is redundant rather than harmful. (``commands/service.py``
    has shipped ``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`` for the
    stop-watchdog and it works.) The genuine conflict is ``DETACHED_PROCESS`` vs
    ``CREATE_NEW_CONSOLE``, which are mutually exclusive.

**What must NOT be silenced.** ``desktop/tray_app.py`` deliberately uses
``CREATE_NEW_CONSOLE`` for its "open a terminal" menu items — the window *is* the feature.
Anything that reads a password from the user (an ``ssh`` prompt, ``gh auth login``) should
pass ``interactive=True``: turning a visible prompt into an invisible one converts a flash
into a silent hang, which is strictly worse.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

__all__ = [
    "CREATE_NO_WINDOW",
    "CREATE_NEW_CONSOLE",
    "DETACHED_PROCESS",
    "CREATE_NEW_PROCESS_GROUP",
    "IS_WINDOWS",
    "creation_flags",
    "spawn_kwargs",
    "process_has_console",
    "install_windowless_spawn_default",
]

IS_WINDOWS = sys.platform == "win32"

# `getattr` rather than a bare attribute: these constants do not exist on POSIX, and this
# module is imported from cross-platform code paths. 0 is the correct no-op there.
CREATE_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CREATE_NEW_CONSOLE: int = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
DETACHED_PROCESS: int = getattr(subprocess, "DETACHED_PROCESS", 0)
CREATE_NEW_PROCESS_GROUP: int = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

# Flags that already answer the "does this child get a window?" question. If a caller set
# any of them, they have decided, and we must not override that decision.
_WINDOW_DECIDING = CREATE_NO_WINDOW | CREATE_NEW_CONSOLE | DETACHED_PROCESS


def creation_flags(
    *,
    interactive: bool = False,
    new_group: bool = False,
    base: int = 0,
) -> int:
    """Build ``creationflags`` for a child that should not flash a console.

    Args:
        interactive: The child talks to a human through its console (an ``ssh`` password
            prompt, ``gh auth login``). Suppressing its window would hide the prompt and
            hang forever, so no window flag is added.
        new_group: Also request ``CREATE_NEW_PROCESS_GROUP`` (needed to send
            ``CTRL_BREAK_EVENT`` later). Composes with the window flag; it is not a
            substitute for it.
        base: Flags the caller already decided on. Preserved, and if they already contain
            a window-deciding flag this function adds none.

    Returns:
        The flags to pass as ``creationflags``. Always ``0`` on POSIX, where the parameter
        is not merely useless but rejected by ``subprocess``.
    """
    if not IS_WINDOWS:
        return 0
    flags = base
    if new_group:
        flags |= CREATE_NEW_PROCESS_GROUP
    if interactive or (flags & _WINDOW_DECIDING):
        return flags
    return flags | CREATE_NO_WINDOW


def spawn_kwargs(
    *,
    interactive: bool = False,
    new_group: bool = False,
    base: int = 0,
) -> dict[str, Any]:
    """``**kwargs`` for ``subprocess.run``/``Popen`` that never flash a console.

    Returns an **empty dict** on POSIX rather than ``{"creationflags": 0}``: passing
    ``creationflags`` at all is a ``ValueError`` there, so an empty mapping is what makes
    ``subprocess.run(cmd, **spawn_kwargs())`` portable at every call site.

    Example:
        >>> subprocess.run(["git", "status"], **spawn_kwargs())  # doctest: +SKIP
    """
    flags = creation_flags(interactive=interactive, new_group=new_group, base=base)
    return {"creationflags": flags} if flags else {}


def process_has_console() -> bool:
    """Does *this* process own a console?

    ``False`` for the daemon (``pythonw.exe``), a Task Scheduler run, and the tray —
    precisely the parents whose children flash. ``True`` for a normal CLI invocation,
    where children correctly inherit the operator's terminal and nothing is visible.

    ⚠ **Not** ``GetConsoleWindow()``. That returns the console *window* handle, which is
    ``NULL`` under ConPTY — Windows Terminal, VS Code's terminal — even though the process
    genuinely has a console. Measured on this machine: ``GetConsoleWindow()`` returned 0
    for **both** ``python.exe`` in a terminal and ``pythonw.exe``, so using it would have
    classified every ordinary CLI run as windowless and silenced children the operator was
    watching. ``GetConsoleCP()`` is documented to fail (return 0) when the process has no
    console at all, and it separates the two cleanly: **65001** under ``python.exe`` vs
    **0** under ``pythonw.exe``.

    Returns ``True`` on POSIX and whenever the answer cannot be determined: a wrong
    ``True`` costs a console flash, a wrong ``False`` silences a foreground command.
    """
    if not IS_WINDOWS:
        return True
    try:
        import ctypes  # noqa: PLC0415 — only needed on Windows, only on this path

        return bool(ctypes.windll.kernel32.GetConsoleCP())
    except Exception:  # noqa: BLE001 — an unanswerable question is not a reason to go silent
        return True


_default_installed = False


def install_windowless_spawn_default(*, force: bool = False) -> bool:
    """Make every child of a **windowless** process default to no console window.

    Call this once from a daemon/worker/tray entry point. It is a deliberate, narrow bit
    of action-at-a-distance and the trade is worth stating plainly:

    * The alternative is editing 456 unsuppressed call sites — including 108 in plugins
      and any third-party library that shells out — and keeping every future one correct.
    * The condition is exact. It applies **only** when this process has no console, which
      is the only situation in which a console window can appear at all. In a windowless
      parent the child's brand-new console is not usable by anyone anyway: nobody is
      watching a background daemon, and the window closes with the child. So nothing that
      worked before stops working.
    * It never overrides a caller who decided: a ``creationflags`` already carrying
      ``CREATE_NO_WINDOW``, ``CREATE_NEW_CONSOLE`` or ``DETACHED_PROCESS`` is left alone,
      so ``tray_app``'s deliberate "open a terminal" windows still open.

    ``asyncio``'s subprocess support on Windows goes through ``asyncio.windows_utils.Popen``,
    which subclasses ``subprocess.Popen`` — so patching the base ``__init__`` covers
    ``create_subprocess_exec``/``_shell`` too, which is where the agent tool layer spawns.

    Set ``NAVIG_SHOW_CONSOLES=1`` to opt out when debugging a child that hangs.

    Returns:
        ``True`` if the default was installed; ``False`` when it was skipped (POSIX, a
        process that has a console, already installed, or the env opt-out).
    """
    global _default_installed
    import os  # noqa: PLC0415

    if _default_installed and not force:
        return False
    if not IS_WINDOWS:
        return False
    # Idempotent even under `force`, and even across a module reload that reset the module
    # global: the marker lives on the installed function, not in this module's state.
    # Without it, a second call would wrap the ALREADY-WRAPPED __init__, and every further
    # call would add another frame to every subprocess spawn in the process.
    if getattr(subprocess.Popen.__init__, "_navig_windowless", False):
        _default_installed = True
        return False
    if os.environ.get("NAVIG_SHOW_CONSOLES", "").strip() in {"1", "true", "yes", "on"}:
        return False
    if process_has_console():
        # A foreground CLI run. Children inherit the operator's terminal; there is nothing
        # to suppress, and suppressing it would hide output they are watching.
        return False

    original_init = subprocess.Popen.__init__

    def _windowless_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        flags = kwargs.get("creationflags", 0) or 0
        if not (flags & _WINDOW_DECIDING):
            kwargs["creationflags"] = flags | CREATE_NO_WINDOW
        return original_init(self, *args, **kwargs)

    _windowless_init._navig_windowless = True  # type: ignore[attr-defined]  # idempotence marker
    subprocess.Popen.__init__ = _windowless_init  # type: ignore[method-assign]
    _default_installed = True
    return True
