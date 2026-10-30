"""What this operating system gives NAVIG — asked in one place, not re-decided everywhere.

**Why this module exists.** Platform facts were re-derived at every call site: ``navig``
carried ~250 ``sys.platform``/``os.name`` branches, so the same question ("which shell runs
a command string?", "what is the launcher called?", "which supervisor owns the daemon?")
was answered independently in a dozen files and could disagree. Windows-only surfaces
(``navig ahk``, the registry, PowerShell) then needed a hand-written guard at each site,
and a new OS could not be reasoned about at all.

**The rule.** Code that behaves differently per OS asks this module instead of writing
``sys.platform == ...`` again. Identity answers come from :mod:`navig.platform.paths`
(one cached detection, including WSL), because a second detection is a second truth.

Two habits this module exists to remove:

- ``os.name`` as a platform test. Patching ``os.name`` in a test corrupts ``pathlib``
  (``cannot instantiate 'WindowsPath' on your system``) before the code under test runs;
  read ``sys.platform`` — the functions here do, through :func:`paths.current_os`.
- Re-inventing "is this feature available?". :func:`supports` names the feature, so a
  guard reads as intent (``supports("ahk")``) instead of as an identity check that the
  reader has to decode.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any, Literal

from navig.platform import paths

OSName = Literal["windows", "macos", "linux", "wsl"]

#: Which OS families have each named capability.
#:
#: A feature is named here when a *code path* depends on it, so a guard can ask
#: ``supports("ahk")`` instead of ``sys.platform == "win32"``. Features that ship a
#: plugin (browser, voice, social) are not listed — their availability is the plugin's
#: own `available()` answer, and duplicating it here would be a second truth.
_FEATURES: dict[str, frozenset[str]] = {
    # Windows-only surfaces
    "ahk": frozenset({"windows"}),
    "registry": frozenset({"windows"}),
    "powershell": frozenset({"windows"}),
    "schtasks": frozenset({"windows"}),
    "nssm": frozenset({"windows"}),
    "win32-console": frozenset({"windows"}),
    "winsound": frozenset({"windows"}),
    "startfile": frozenset({"windows"}),
    "tasklist": frozenset({"windows"}),
    # macOS surfaces
    "launchd": frozenset({"macos"}),
    "osascript": frozenset({"macos"}),
    "pbcopy": frozenset({"macos"}),
    "open": frozenset({"macos"}),
    # Linux surfaces (a WSL host is a Linux host with a Windows kernel underneath)
    "systemd": frozenset({"linux", "wsl"}),
    "xdg-open": frozenset({"linux", "wsl"}),
    "notify-send": frozenset({"linux", "wsl"}),
    "xdotool": frozenset({"linux", "wsl"}),
    # Everywhere
    "shell": frozenset({"windows", "macos", "linux", "wsl"}),
}

#: launcher filename the installer writes into :func:`user_bin_dir`
_LAUNCHER_NAMES = {"windows": "navig.cmd"}


def _identity(explicit: str | None = None) -> str:
    """The OS name to answer for — the host's, unless a caller pins one."""
    if explicit is not None:
        return explicit
    return paths.current_os()


def os_name(explicit: str | None = None) -> str:
    """``'windows'`` | ``'macos'`` | ``'linux'`` | ``'wsl'``."""
    return _identity(explicit)


def is_windows(explicit: str | None = None) -> bool:
    return _identity(explicit) == "windows"


def is_macos(explicit: str | None = None) -> bool:
    return _identity(explicit) == "macos"


def is_linux(explicit: str | None = None) -> bool:
    """Linux, or WSL — the same userland with a Windows kernel underneath."""
    return _identity(explicit) in ("linux", "wsl")


def is_wsl(explicit: str | None = None) -> bool:
    return _identity(explicit) == "wsl"


def is_posix(explicit: str | None = None) -> bool:
    """Everything that is not Windows — the split most file/process code needs."""
    return not is_windows(explicit)


def supports(feature: str, explicit: str | None = None) -> bool:
    """Does this OS have *feature*? Names come from :data:`_FEATURES`.

    An unknown name raises instead of answering False: a typo in a guard would
    otherwise silently disable a real capability on every platform.
    """
    families = _FEATURES.get(feature)
    if families is None:
        known = ", ".join(sorted(_FEATURES))
        raise ValueError(f"unknown capability {feature!r}; known: {known}")
    return _identity(explicit) in families


def known_features() -> tuple[str, ...]:
    """Every name :func:`supports` accepts, sorted."""
    return tuple(sorted(_FEATURES))


# ── Executables and directories ─────────────────────────────────────────────


def executable_suffix(explicit: str | None = None) -> str:
    """``'.exe'`` on Windows, ``''`` elsewhere."""
    return ".exe" if is_windows(explicit) else ""


def launcher_name(explicit: str | None = None) -> str:
    """The launcher the installer writes: ``navig.cmd`` on Windows, ``navig`` elsewhere.

    The ``.cmd`` suffix is load-bearing: ``CreateProcess`` appends only ``.exe`` while
    searching, so a ``navig`` without it is never found (see
    :mod:`navig.platform.navig_cli`).
    """
    return _LAUNCHER_NAMES.get(_identity(explicit), "navig")


def user_bin_dir() -> Path:
    """Where the installer puts the ``navig`` launcher — ``~/.local/bin`` on every OS.

    Deliberately NOT ``~/.local/bin`` on POSIX and something else on Windows: one
    convention keeps ``navig doctor`` (which checks this directory is on ``PATH``),
    the installer and the docs describing the same place.
    """
    return Path.home() / ".local" / "bin"


def venv_bin_dir(venv_root: Path, explicit: str | None = None) -> Path:
    """``<venv>/Scripts`` on Windows, ``<venv>/bin`` elsewhere."""
    return Path(venv_root) / ("Scripts" if is_windows(explicit) else "bin")


def python_in_venv(venv_root: Path, explicit: str | None = None) -> Path:
    """The interpreter inside a venv, spelled for this OS."""
    name = "python" + executable_suffix(explicit)
    return venv_bin_dir(venv_root, explicit) / name


# ── Shell ───────────────────────────────────────────────────────────────────


def shell_argv(command: str, explicit: str | None = None) -> list[str]:
    """argv that runs a *command string* in this OS's shell, without re-quoting.

    ``/bin/sh -lc`` (not ``-c``) because NAVIG's documented commands rely on the login
    shell's PATH — the same reason the docs say "open a new shell". Windows uses
    ``cmd /d /s /c`` — ``/d`` skips AutoRun scripts and ``/s`` keeps the command one
    token, both of which hand-written call sites got wrong in different ways.
    """
    if is_windows(explicit):
        return ["cmd", "/d", "/s", "/c", command]
    return ["/bin/sh", "-lc", command]


def shell_name(explicit: str | None = None) -> str:
    """Human name of :func:`shell_argv`'s shell."""
    return "cmd" if is_windows(explicit) else "/bin/sh"


# ── Service supervision ─────────────────────────────────────────────────────


def service_manager(explicit: str | None = None) -> str:
    """Which supervisor owns a persistent daemon here.

    ``launchd`` (macOS) · ``systemd`` (Linux) · ``schtasks`` or ``nssm`` (Windows) ·
    ``manual`` when the OS has none installed. Delegates to the code that installs the
    service, so this can never disagree with what ``navig service install`` picks.
    """
    if explicit is not None and explicit != paths.current_os():
        # A pinned identity is a test/matrix question, not a live probe: answer the
        # family's default supervisor without shelling out on the wrong host.
        return {
            "windows": "schtasks",
            "macos": "launchd",
            "linux": "systemd",
            "wsl": "systemd",
        }.get(explicit, "manual")
    from navig.daemon.service_manager import detect_best_method

    method = detect_best_method()
    return "schtasks" if method == "task" else method


# ── Notifications and clipboard ─────────────────────────────────────────────


def _which(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _applescript_string(text: str) -> str:
    """Escape *text* for an AppleScript string literal (quotes and backslashes)."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def notify_argv(title: str, body: str, explicit: str | None = None) -> list[str] | None:
    """argv for an OS notification, or ``None`` where NAVIG notifies another way.

    macOS → ``osascript``. Linux/WSL → ``notify-send`` when installed. Windows →
    ``None`` on purpose: Windows toasts go through :mod:`navig.notify.toast` (WinRT),
    not a subprocess, and returning an argv here would create a second path.
    """
    if is_macos(explicit):
        script = (
            f'display notification "{_applescript_string(body)}" '
            f'with title "{_applescript_string(title)}"'
        )
        return ["osascript", "-e", script]
    if is_linux(explicit):
        sender = _which("notify-send")
        return [sender, title, body] if sender else None
    return None


def clipboard_argv(read: bool, explicit: str | None = None) -> list[str] | None:
    """argv that reads (``read=True``) or writes the clipboard, or ``None`` if absent.

    macOS → ``pbpaste``/``pbcopy``; Linux/WSL → ``xclip`` (or ``xsel``); Windows →
    ``None`` because clipboard access there goes through the automation adapter and
    PowerShell's ``Get-/Set-Clipboard``, not a portable binary.
    """
    if is_windows(explicit):
        return None
    if is_macos(explicit):
        tool = _which("pbpaste" if read else "pbcopy")
        return [tool] if tool else None
    if read:
        tool = _which("xclip", "xsel")
        if not tool:
            return None
        return [tool, "-selection", "clipboard", "-o"] if tool.endswith("xclip") else [tool, "--clipboard", "--output"]
    tool = _which("xclip", "xsel")
    if not tool:
        return None
    return [tool, "-selection", "clipboard"] if tool.endswith("xclip") else [tool, "--clipboard", "--input"]


# ── Snapshot ────────────────────────────────────────────────────────────────


def describe(explicit: str | None = None) -> dict[str, Any]:
    """One dict answering "what is this host?" — for bug reports and robot digests."""
    name = _identity(explicit)
    return {
        "os": name,
        "posix": is_posix(explicit),
        "python": sys.version.split()[0],
        "exe_suffix": executable_suffix(explicit),
        "launcher": launcher_name(explicit),
        "bin_dir": str(user_bin_dir()),
        "shell": shell_name(explicit),
        "service_manager": service_manager(explicit),
        "supports": [f for f in known_features() if supports(f, explicit)],
    }
