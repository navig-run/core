"""
Linux automation adapter — X11 and Wayland are not the same machine.

X11: `xdotool` + `wmctrl` give input *and* window management. That is the
classic arrangement and it still works.

Wayland: by design, no client may drive another client's windows. There is no
window list, no geometry, no activate/minimize. What survives is *input*
injection through `/dev/uinput` (`ydotool`), which is enough for "look at the
screenshot, click there, type this" — but not for "bring the Editor window
forward".

Why this matters: gating on `which xdotool` reports **available** on a Wayland
session, and then every call silently no-ops — `getactivewindow` returns
nothing, clicks land nowhere, and `navig auto` looks like it worked. A
capability check must test the session, not the binaries. That is what
`session()` and `diagnosis()` are for, and why the window verbs return a named
error instead of an empty success on Wayland.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from navig.adapters.automation.types import ExecutionResult, WindowInfo
from navig.core.proc_text import decode_console_result

# The guard in tests/quality/test_no_console_window_flash.py rejects any module-level
# name containing NO_WINDOW (it hunts hand-rolled creationflags). This is a message, not
# a flag, so the name deliberately avoids that substring.
# Returned for every window verb on Wayland. Long on purpose: this string is the
# whole error the agent sees, and "command failed" would send it guessing.
_WAYLAND_WINDOW_CONTROL_UNAVAILABLE = (
    "Wayland session: a compositor does not let one app control another app's windows, "
    "so window automation is unavailable here. Input (click/type) still works via ydotool. "
    "For window control use an X11 session (GNOME on Xorg, or Debian's Xorg session) or "
    "KDE Plasma with kdotool."
)


class LinuxAdapter:
    """Automation adapter for Linux, aware of which display protocol it is on."""

    def __init__(self):
        self._available: bool | None = None
        self._session: str | None = None
        self._has_xdotool: bool | None = None
        self._has_wmctrl: bool | None = None
        self._has_xclip: bool | None = None
        self._has_ydotool: bool | None = None
        self._has_wl_clipboard: bool | None = None

    # ── environment ──────────────────────────────────────────────────────────

    def session(self) -> str:
        """`x11`, `wayland`, or `unknown`.

        `XDG_SESSION_TYPE` is authoritative when set. `WAYLAND_DISPLAY` is the
        fallback for a session started outside a login manager, and it wins over
        `DISPLAY` because a Wayland session also exports DISPLAY (XWayland) —
        which is exactly the trap: xdotool there talks to XWayland and cannot
        reach native Wayland windows.
        """
        if self._session is not None:
            return self._session

        kind = (os.environ.get("XDG_SESSION_TYPE") or "").strip().lower()
        if kind in ("x11", "wayland"):
            self._session = kind
        elif os.environ.get("WAYLAND_DISPLAY"):
            self._session = "wayland"
        elif os.environ.get("DISPLAY"):
            self._session = "x11"
        else:
            self._session = "unknown"
        return self._session

    def _check_command(self, cmd: str) -> bool:
        return shutil.which(cmd) is not None

    def is_available(self) -> bool:
        """True when *some* automation is possible on this session."""
        if self._available is not None:
            return self._available

        if sys.platform != "linux":
            self._available = False
            return False

        self._has_xdotool = self._check_command("xdotool")
        self._has_wmctrl = self._check_command("wmctrl")
        self._has_xclip = self._check_command("xclip")
        self._has_ydotool = self._check_command("ydotool")
        self._has_wl_clipboard = self._check_command("wl-copy")

        if self.session() == "wayland":
            # Windows are gone; ydotool is the only input path left.
            self._available = bool(self._has_ydotool)
        elif self.session() == "x11":
            self._available = bool(self._has_xdotool and self._has_wmctrl)
        else:
            # No session variables at all (a container, a cron job, an ssh login).
            # Anything it claimed to do would be blind, so claim nothing.
            self._available = False
        return self._available

    def windows_supported(self) -> bool:
        """False on Wayland: no window list, geometry, activate, or resize.

        Probes first — a caller that asks "can I move a window?" before
        `is_available()` must not be told no because the probe had not run yet.
        Window control needs only `xdotool`; `wmctrl` is for maximize alone.
        """
        self.is_available()
        return self.session() == "x11" and bool(self._has_xdotool)

    def unavailable_reason(self) -> str:
        """One actionable sentence, for a human or for the agent."""
        kind = self.session()
        if kind == "wayland":
            return (
                "Wayland session and ydotool is not installed, so screen input is "
                "impossible. Install ydotool and allow /dev/uinput:\n"
                "  sudo apt install ydotool\n"
                "  sudo sh -c 'echo uinput > /etc/modules-load.d/uinput.conf'\n"
                "  (window control stays unavailable on Wayland by design)"
            )
        if kind == "x11":
            missing = [
                name
                for name, present in (
                    ("xdotool", self._has_xdotool),
                    ("wmctrl", self._has_wmctrl),
                )
                if not present
            ]
            return f"X11 session but missing: {', '.join(missing)}. " "Install: sudo apt install xdotool wmctrl xclip"
        return (
            "No display session detected (no XDG_SESSION_TYPE, WAYLAND_DISPLAY or "
            "DISPLAY). Automation needs a desktop session — nothing runs headless."
        )

    def diagnosis(self) -> list[tuple[str, bool, str]]:
        """Rows for `navig auto status`: (label, ok, detail)."""
        kind = self.session()
        rows: list[tuple[str, bool, str]] = [(f"Session: {kind}", kind != "unknown", "")]

        if kind == "wayland":
            rows.append(
                (
                    "Wayland: window control",
                    False,
                    "by design — no protocol for one app to drive another's windows",
                )
            )
            rows.append(
                (
                    "ydotool (input)",
                    bool(self._has_ydotool),
                    "installed" if self._has_ydotool else "missing — see: navig auto status",
                )
            )
            rows.append(
                (
                    "wl-clipboard",
                    bool(self._has_wl_clipboard),
                    "wl-copy/wl-paste" if self._has_wl_clipboard else "optional: apt install wl-clipboard",
                )
            )
        elif kind == "x11":
            for name, present in (
                ("xdotool", self._has_xdotool),
                ("wmctrl", self._has_wmctrl),
                ("xclip", self._has_xclip),
            ):
                rows.append((name, bool(present), "installed" if present else "missing"))
        return rows

    # ── process helpers ──────────────────────────────────────────────────────

    def _run_command(self, cmd: list, capture_output=True) -> ExecutionResult:
        try:
            result = decode_console_result(
                subprocess.run(cmd, capture_output=capture_output, timeout=10)
            )
            return ExecutionResult(
                success=result.returncode == 0,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(False, stderr="Command timeout")
        except Exception as e:
            return ExecutionResult(False, stderr=str(e))

    def _run_ydotool(self, modern: list[str], legacy: list[str]) -> ExecutionResult:
        """ydotool changed its CLI between 0.1.x and 1.0 (positional vs -x/-y).

        Rather than parse a version string, try the modern form and fall back to
        the legacy one. The second call only happens when the first fails, and
        the error the caller sees is the LAST one, so a broken install still
        reports something true.
        """
        first = self._run_command(["ydotool", *modern])
        if first.success:
            return first
        second = self._run_command(["ydotool", *legacy])
        if second.success:
            return second
        first.stderr = (first.stderr or "") + "\n" + (second.stderr or "")
        return first

    def _need_windows(self) -> ExecutionResult | None:
        """A refusal to return, or None when window verbs may proceed.

        Three different refusals, because they need three different fixes: install
        a tool (X11), accept the platform (Wayland), or get a desktop session.
        """
        if self.windows_supported():
            return None
        kind = self.session()
        if kind == "wayland":
            return ExecutionResult(False, stderr=_WAYLAND_WINDOW_CONTROL_UNAVAILABLE)
        if kind == "x11":
            return ExecutionResult(
                False,
                stderr="X11 session but xdotool is not installed — `sudo apt install xdotool`.",
            )
        return ExecutionResult(
            False,
            stderr="No display session detected (no DISPLAY/WAYLAND_DISPLAY) — "
            "window control needs a desktop session.",
        )

    # ── apps ─────────────────────────────────────────────────────────────────

    def open_app(self, target: str) -> ExecutionResult:
        """Open application or URL."""
        if target.startswith("http"):
            return self._run_command(["xdg-open", target], capture_output=False)
        return self._run_command(["sh", "-c", target], capture_output=False)

    # ── input ────────────────────────────────────────────────────────────────

    BUTTONS = {"left": "1", "middle": "2", "right": "3"}
    # ydotool legacy button masks: 0x40 press / 0x80 release of BTN_LEFT, so 0xC0
    # is a complete left click. Right and middle follow the same pattern.
    _YDO_BUTTONS = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}

    def click(self, x: int, y: int, button: str = "left") -> ExecutionResult:
        """Click at coordinates."""
        if self.session() == "wayland":
            moved = self.mouse_move(x, y)
            if not moved.success:
                return moved
            return self._run_ydotool(
                ["click", "-b", button],
                ["click", self._YDO_BUTTONS.get(button, "0xC0")],
            )

        btn = self.BUTTONS.get(button, "1")
        return self._run_command(["xdotool", "mousemove", str(x), str(y), "click", btn])

    def type_text(self, text: str, delay: int = 50) -> ExecutionResult:
        """Type text. Wayland has no inter-key delay worth setting."""
        if self.session() == "wayland":
            return self._run_ydotool(["type", text], ["type", text])
        return self._run_command(["xdotool", "type", "--delay", str(delay), text])

    def send_keys(self, keys: str) -> ExecutionResult:
        """Send a key sequence.

        X11 takes keysyms (`ctrl+t`). ydotool takes Linux *input event codes*
        (`29:1 20:1 20:0 29:0` for ctrl+t), so an X11-style string cannot be
        translated here without a keymap. Refuse rather than type garbage.
        """
        if self.session() == "wayland":
            return ExecutionResult(
                False,
                stderr=(
                    "Wayland: send_keys needs Linux input keycodes, not X11 keysyms "
                    f"({keys!r}). Pass keycodes for ydotool, or use type_text for text. "
                    "A keysym→keycode table is not implemented in this adapter."
                ),
            )
        return self._run_command(["xdotool", "key", keys])

    def mouse_move(self, x: int, y: int, speed: int = 2) -> ExecutionResult:
        """Move mouse."""
        if self.session() == "wayland":
            return self._run_ydotool(
                ["mousemove", "-a", "-x", str(x), "-y", str(y)],
                ["mousemove", str(x), str(y)],
            )
        return self._run_command(["xdotool", "mousemove", str(x), str(y)])

    # ── windows (X11 only) ───────────────────────────────────────────────────

    def get_focused_window(self) -> WindowInfo | None:
        """Get currently focused window."""
        if self._need_windows():
            return None
        result = self._run_command(["xdotool", "getactivewindow"])
        if not result.success:
            return None
        return self._get_window_info(result.stdout.strip())

    def _get_window_info(self, win_id: str) -> WindowInfo | None:
        """Get window information."""
        geom_result = self._run_command(["xdotool", "getwindowgeometry", win_id])
        if not geom_result.success:
            return None

        x, y, w, h = 0, 0, 0, 0
        for line in geom_result.stdout.split("\n"):
            if "Position:" in line:
                parts = line.split("Position:")[1].strip().split(",")
                x = int(parts[0])
                y = int(parts[1].split()[0])
            elif "Geometry:" in line:
                parts = line.split("Geometry:")[1].strip().split("x")
                w = int(parts[0])
                h = int(parts[1])

        title_result = self._run_command(["xdotool", "getwindowname", win_id])
        title = title_result.stdout.strip() if title_result.success else ""

        pid_result = self._run_command(["xdotool", "getwindowpid", win_id])
        pid = int(pid_result.stdout.strip()) if pid_result.success else 0

        # class_name="" — xdotool isn't queried for WM_CLASS here, and "" is the established
        # unknown-value convention (see ahk.py's `class_name=item.get("class_name", "")`).
        # It is REQUIRED on WindowInfo, so omitting it raised TypeError on every call.
        return WindowInfo(
            id=win_id, title=title, x=x, y=y, width=w, height=h, pid=pid, class_name=""
        )

    def activate_window(self, selector: str) -> ExecutionResult:
        """Activate window by title."""
        if refusal := self._need_windows():
            return refusal
        return self._run_command(["xdotool", "search", "--name", selector, "windowactivate"])

    def close_window(self, selector: str) -> ExecutionResult:
        """Close window."""
        if refusal := self._need_windows():
            return refusal
        return self._run_command(["xdotool", "search", "--name", selector, "windowkill"])

    def move_window(
        self, selector: str, x: int, y: int, width: int = None, height: int = None
    ) -> ExecutionResult:
        """Move and resize window."""
        if refusal := self._need_windows():
            return refusal

        search_result = self._run_command(["xdotool", "search", "--name", selector])
        if not search_result.success:
            return ExecutionResult(False, stderr="Window not found")

        win_id = search_result.stdout.strip().split("\n")[0]
        move_result = self._run_command(["xdotool", "windowmove", win_id, str(x), str(y)])
        if not move_result.success:
            return move_result

        if width and height:
            return self._run_command(["xdotool", "windowsize", win_id, str(width), str(height)])
        return move_result

    def maximize_window(self, selector: str) -> ExecutionResult:
        """Maximize window."""
        if refusal := self._need_windows():
            return refusal
        return self._run_command(
            ["wmctrl", "-r", selector, "-b", "add,maximized_vert,maximized_horz"]
        )

    def minimize_window(self, selector: str) -> ExecutionResult:
        """Minimize window."""
        if refusal := self._need_windows():
            return refusal
        return self._run_command(["xdotool", "search", "--name", selector, "windowminimize"])

    def snap_window(self, selector: str, position: str) -> ExecutionResult:
        """Snap window to screen position."""
        if refusal := self._need_windows():
            return refusal

        screen_result = self._run_command(["xdotool", "getdisplaygeometry"])
        if not screen_result.success:
            return ExecutionResult(False, stderr="Failed to get screen size")

        screen_w, screen_h = map(int, screen_result.stdout.strip().split())

        positions = {
            "left": (0, 0, screen_w // 2, screen_h),
            "right": (screen_w // 2, 0, screen_w // 2, screen_h),
            "top": (0, 0, screen_w, screen_h // 2),
            "bottom": (0, screen_h // 2, screen_w, screen_h // 2),
        }
        if position not in positions:
            return ExecutionResult(False, stderr=f"Unknown position: {position}")

        x, y, w, h = positions[position]
        return self.move_window(selector, x, y, w, h)

    def get_all_windows(self):
        """Get list of all windows (empty on Wayland — there is no such list)."""
        if self._need_windows():
            return []

        result = self._run_command(["wmctrl", "-lG"])
        if not result.success:
            return []

        windows = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split(None, 7)
            if len(parts) >= 8:
                win_id = parts[0]
                x, y, w, h = map(int, parts[2:6])
                title = parts[7] if len(parts) > 7 else ""
                windows.append(
                    WindowInfo(
                        id=win_id, title=title, x=x, y=y, width=w, height=h, pid=0, class_name=""
                    )
                )
        return windows

    # ── clipboard ────────────────────────────────────────────────────────────

    def get_clipboard(self) -> str:
        """Get clipboard content."""
        if self.session() == "wayland":
            if not self._has_wl_clipboard:
                return ""
            result = self._run_command(["wl-paste", "--no-newline"])
            return result.stdout if result.success else ""

        if not self._has_xclip:
            return ""
        result = self._run_command(["xclip", "-selection", "clipboard", "-o"])
        return result.stdout if result.success else ""

    def set_clipboard(self, text: str) -> ExecutionResult:
        """Set clipboard content."""
        if self.session() == "wayland":
            if not self._has_wl_clipboard:
                return ExecutionResult(False, stderr="wl-clipboard not available")
            tool = ["wl-copy"]
        else:
            if not self._has_xclip:
                return ExecutionResult(False, stderr="xclip not available")
            tool = ["xclip", "-selection", "clipboard"]

        try:
            proc = subprocess.Popen(tool, stdin=subprocess.PIPE, text=True)
            proc.communicate(input=text)
            return ExecutionResult(success=proc.returncode == 0)
        except Exception as e:
            return ExecutionResult(False, stderr=str(e))
