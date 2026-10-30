"""How to photograph this screen — one answer per session, not per call site.

**Why this module exists.** NAVIG had two capture paths that disagreed about the host:

- the ``screenshot`` builtin tool ran ``scrot`` → ``gnome-screenshot`` → ``import``
  (``navig/builtin/tools/screenshot/worker.py``). All three are **X11** clients, so on a
  Wayland session every one failed — and the message told the operator to install
  ``scrot``, which still would not work. Fedora 40+, Ubuntu 24.04 and GNOME 45+ are
  Wayland by default, so this was the common Linux desktop, not an edge case.
- the automation adapter (``navig/adapters/automation/screenshot.py``) preferred
  ``dxcam`` → ``mss`` → ``PIL.ImageGrab``. ``mss`` was never a declared dependency, and
  Pillow's ``ImageGrab`` needs an X11 display too, so a clean Linux host had no working
  backend at all and no message naming any of those three causes.

Both callers now ask this module: *given this session* (Wayland, X11, macOS, Windows,
headless), which backends exist and which one runs first? Everything except Windows
returns argv, because Windows has no OS command that captures the screen — it stays
in-process in the adapter (``dxcam``/``mss``/Pillow).

Two rules this module keeps:

- The session is read from ``WAYLAND_DISPLAY``/``DISPLAY``, never guessed from the OS: a
  Linux host can be either, and a WSL host with WSLg is a graphical session.
- A failure message names a command that works **in this session** — the whole point of
  the seam is that the advice and the attempt come from the same answer.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from navig.platform import capabilities

#: What kind of session this is. ``headless`` means there is no display to capture.
DisplaySession = Literal["windows", "macos", "wayland", "x11", "headless"]

#: Backend kinds. ``argv`` shells out to a command; ``in-process`` runs Python code.
BackendKind = Literal["argv", "in-process"]


@dataclass(frozen=True)
class CaptureBackend:
    """One way to capture the screen.

    ``argv`` builds a complete command line for ``output`` (and optionally a region);
    it is ``None`` for ``in-process`` backends, which the caller runs itself.
    """

    name: str
    kind: BackendKind
    program: str | None = None
    note: str = ""

    def argv(
        self,
        output: str,
        *,
        monitor: int = 0,
        region: tuple[int, int, int, int] | None = None,
    ) -> list[str] | None:
        """Command line that writes a screenshot to *output*, or None (in-process)."""
        if self.kind == "in-process" or self.program is None:
            return None
        name = self.name
        if name == "screencapture":
            # -x: no shutter sound. -D n: that display (1-based). -R x,y,w,h: a region.
            cmd = [self.program, "-x"]
            if region is not None:
                left, top, right, bottom = region
                cmd.extend(["-R", f"{left},{top},{right - left},{bottom - top}"])
            elif monitor > 0:
                cmd.extend(["-D", str(monitor)])
            cmd.append(output)
            return cmd
        if name == "grim":
            cmd = [self.program]
            if region is not None:
                left, top, right, bottom = region
                cmd.extend(["-g", f"{left},{top} {right - left}x{bottom - top}"])
            cmd.append(output)
            return cmd
        if name == "scrot":
            cmd = [self.program]
            if region is not None:
                left, top, right, bottom = region
                cmd.extend(["-a", f"{left},{top},{right - left},{bottom - top}"])
            cmd.append(output)
            return cmd
        if name == "gnome-screenshot":
            cmd = [self.program]
            if region is not None:
                left, top, right, bottom = region
                cmd.extend(["-a", f"{left},{top},{right - left},{bottom - top}"])
            elif monitor > 0:
                cmd.extend(["-d", str(monitor)])
            cmd.extend(["-f", output])
            return cmd
        if name == "spectacle":
            # KDE's own tool: -b background, -n no notification, -o output.
            cmd = [self.program, "-b", "-n", "-o", output]
            if region is not None:
                left, top, right, bottom = region
                cmd.extend(["-r", f"{left},{top},{right - left},{bottom - top}"])
            return cmd
        if name == "import":
            # ImageMagick. -window root is the whole X11 root window.
            cmd = [self.program, "-window", "root"]
            if region is not None:
                left, top, right, bottom = region
                cmd.extend(["-crop", f"{right - left}x{bottom - top}+{left}+{top}"])
            cmd.append(output)
            return cmd
        raise ValueError(f"no argv recipe for capture backend {name!r}")


#: Windows stays in-process: there is no screen-capture command in the OS.
_WINDOWS = (CaptureBackend("in-process", "in-process", note="dxcam → mss → PIL.ImageGrab"),)

_MACOS = (
    CaptureBackend("screencapture", "argv", program="screencapture"),
)

#: Wayland order: grim is the reference implementation; gnome-screenshot works from the
#: GNOME session bus; spectacle is KDE's. All three work without XWayland.
_WAYLAND = (
    CaptureBackend("grim", "argv", program="grim"),
    CaptureBackend("gnome-screenshot", "argv", program="gnome-screenshot"),
    CaptureBackend("spectacle", "argv", program="spectacle"),
)

_X11 = (
    CaptureBackend("scrot", "argv", program="scrot"),
    CaptureBackend("gnome-screenshot", "argv", program="gnome-screenshot"),
    CaptureBackend("spectacle", "argv", program="spectacle"),
    CaptureBackend("import", "argv", program="import"),
)

_BACKENDS: dict[str, tuple[CaptureBackend, ...]] = {
    "windows": _WINDOWS,
    "macos": _MACOS,
    "wayland": _WAYLAND,
    "x11": _X11,
    "headless": (),
}

#: Install advice that actually works for the session that failed.
_HINTS: dict[str, str] = {
    "wayland": (
        "This is a Wayland session and no Wayland capture tool is installed. "
        "Install one of: grim (`sudo apt install grim` / `sudo dnf install grim` / "
        "`sudo pacman -S grim`), gnome-screenshot, or spectacle (KDE). "
        "`scrot` cannot capture Wayland — it only works under XWayland."
    ),
    "x11": (
        "No X11 capture tool is installed. Install one of: scrot "
        "(`sudo apt install scrot`), gnome-screenshot, spectacle, or ImageMagick "
        "(`import`)."
    ),
    "headless": (
        "This host has no display (no WAYLAND_DISPLAY and no DISPLAY). Run NAVIG under "
        "`xvfb-run -a …` to capture a virtual screen, or capture on a graphical host."
    ),
    "macos": (
        "`screencapture` ships with macOS. If it exists but produces nothing, grant "
        "Screen Recording to the app running NAVIG in System Settings → Privacy & "
        "Security → Screen Recording."
    ),
    "windows": (
        "In-process capture needs a library: `pip install mss` (or Pillow for the "
        "slower fallback)."
    ),
}


def session(
    *,
    os_name: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> DisplaySession:
    """Which kind of session this is — the one fact every backend choice follows.

    ``os_name`` pins the OS (tests), ``environ`` pins the display variables (tests).
    """
    import os

    env = os.environ if environ is None else environ
    family = capabilities.os_name(os_name)
    if family == "windows":
        return "windows"
    if family == "macos":
        return "macos"
    if env.get("WAYLAND_DISPLAY"):
        return "wayland"
    if env.get("DISPLAY"):
        return "x11"
    return "headless"


def backends(for_session: DisplaySession | None = None) -> tuple[CaptureBackend, ...]:
    """Every backend that *could* work in this session, best first."""
    return _BACKENDS[for_session or session()]


def available(
    for_session: DisplaySession | None = None,
    *,
    which: object = shutil.which,
) -> tuple[CaptureBackend, ...]:
    """The backends whose program is actually present, best first.

    ``which`` is injectable so a test can describe a host without installing anything.
    """
    found: list[CaptureBackend] = []
    for backend in backends(for_session):
        if backend.kind == "in-process":
            found.append(backend)  # availability is the adapter's own import probe
            continue
        if backend.program and which(backend.program):  # type: ignore[operator]
            found.append(backend)
    return tuple(found)


def choose(
    for_session: DisplaySession | None = None,
    *,
    which: object = shutil.which,
) -> CaptureBackend | None:
    """The backend to use now, or None when this session has none."""
    found = available(for_session, which=which)
    return found[0] if found else None


def install_hint(for_session: DisplaySession | None = None) -> str:
    """What the operator can install to make capture work — for THIS session."""
    return _HINTS[for_session or session()]


def describe() -> dict[str, object]:
    """A snapshot for ``navig doctor`` and for error messages."""
    current = session()
    return {
        "session": current,
        "backends": [b.name for b in backends(current)],
        "available": [b.name for b in available(current)],
        "hint": install_hint(current),
    }
