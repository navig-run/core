"""
Linux Automation Adapter using xdotool, wmctrl, and xclip
"""

import subprocess
import sys
from dataclasses import dataclass


@dataclass
class ExecutionResult:
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0


@dataclass
class WindowInfo:
    id: str
    title: str
    x: int
    y: int
    width: int
    height: int
    pid: int
    process_name: str = ""
    class_name: str = ""
    is_maximized: bool = False
    is_minimized: bool = False

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "pid": self.pid,
            "process_name": self.process_name,
            "class_name": self.class_name,
            "is_maximized": self.is_maximized,
            "is_minimized": self.is_minimized,
        }


class LinuxAdapter:
    """Automation adapter for Linux using xdotool and wmctrl."""

    def __init__(self):
        self._available = None
        self._has_xdotool = None
        self._has_wmctrl = None
        self._has_xclip = None

    def is_available(self) -> bool:
        """Check if required tools are available."""
        if self._available is not None:
            return self._available

        if sys.platform != "linux":
            self._available = False
            return False

        # Check for xdotool
        self._has_xdotool = self._check_command("xdotool")
        self._has_wmctrl = self._check_command("wmctrl")
        self._has_xclip = self._check_command("xclip")

        self._available = self._has_xdotool and self._has_wmctrl
        return self._available

    def _check_command(self, cmd: str) -> bool:
        try:
            result = subprocess.run(["which", cmd], capture_output=True, text=True)
            return result.returncode == 0
        except Exception:
            return False

    def _run_command(self, cmd: list, capture_output=True) -> ExecutionResult:
        try:
            result = subprocess.run(
                cmd, capture_output=capture_output, text=True, timeout=10
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

    def open_app(self, target: str) -> ExecutionResult:
        """Open application or URL."""
        if target.startswith("http"):
            return self._run_command(["xdg-open", target], capture_output=False)
        else:
            # Try to run as command
            return self._run_command(["sh", "-c", target], capture_output=False)

    def click(self, x: int, y: int, button: str = "left") -> ExecutionResult:
        """Click at coordinates."""
        button_map = {"left": "1", "middle": "2", "right": "3"}
        btn = button_map.get(button, "1")
        return self._run_command(["xdotool", "mousemove", str(x), str(y), "click", btn])

    def type_text(self, text: str, delay: int = 50) -> ExecutionResult:
        """Type text."""
        return self._run_command(["xdotool", "type", "--delay", str(delay), text])

    def send_keys(self, keys: str) -> ExecutionResult:
        """Send key sequence."""
        return self._run_command(["xdotool", "key", keys])

    def mouse_move(self, x: int, y: int, speed: int = 2) -> ExecutionResult:
        """Move mouse."""
        return self._run_command(["xdotool", "mousemove", str(x), str(y)])

    def get_focused_window(self) -> WindowInfo | None:
        """Get currently focused window."""
        result = self._run_command(["xdotool", "getactivewindow"])
        if not result.success:
            return None

        win_id = result.stdout.strip()
        return self._get_window_info(win_id)

    def _get_window_info(self, win_id: str) -> WindowInfo | None:
        """Get window information."""
        # Get geometry
        geom_result = self._run_command(["xdotool", "getwindowgeometry", win_id])
        if not geom_result.success:
            return None

        # Parse geometry
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

        # Get title
        title_result = self._run_command(["xdotool", "getwindowname", win_id])
        title = title_result.stdout.strip() if title_result.success else ""

        # Get PID
        pid_result = self._run_command(["xdotool", "getwindowpid", win_id])
        pid = int(pid_result.stdout.strip()) if pid_result.success else 0

        return WindowInfo(id=win_id, title=title, x=x, y=y, width=w, height=h, pid=pid)

    def activate_window(self, selector: str) -> ExecutionResult:
        """Activate window by title."""
        return self._run_command(
            ["xdotool", "search", "--name", selector, "windowactivate"]
        )

    def close_window(self, selector: str) -> ExecutionResult:
        """Close window."""
        return self._run_command(
            ["xdotool", "search", "--name", selector, "windowkill"]
        )

    def move_window(
        self, selector: str, x: int, y: int, width: int = None, height: int = None
    ) -> ExecutionResult:
        """Move and resize window."""
        # Get window ID
        search_result = self._run_command(["xdotool", "search", "--name", selector])
        if not search_result.success:
            return ExecutionResult(False, stderr="Window not found")

        win_id = search_result.stdout.strip().split("\n")[0]

        # Move
        move_result = self._run_command(
            ["xdotool", "windowmove", win_id, str(x), str(y)]
        )
        if not move_result.success:
            return move_result

        # Resize if dimensions provided
        if width and height:
            return self._run_command(
                ["xdotool", "windowsize", win_id, str(width), str(height)]
            )

        return move_result

    def maximize_window(self, selector: str) -> ExecutionResult:
        """Maximize window."""
        return self._run_command(
            ["wmctrl", "-r", selector, "-b", "add,maximized_vert,maximized_horz"]
        )

    def minimize_window(self, selector: str) -> ExecutionResult:
        """Minimize window."""
        return self._run_command(
            ["xdotool", "search", "--name", selector, "windowminimize"]
        )

    def snap_window(self, selector: str, position: str) -> ExecutionResult:
        """Snap window to screen position."""
        # Get screen dimensions
        screen_result = self._run_command(["xdotool", "getdisplaygeometry"])
        if not screen_result.success:
            return ExecutionResult(False, stderr="Failed to get screen size")

        screen_w, screen_h = map(int, screen_result.stdout.strip().split())

        # Calculate position
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

    def get_clipboard(self) -> str:
        """Get clipboard content."""
        if not self._has_xclip:
            return ""

        result = self._run_command(["xclip", "-selection", "clipboard", "-o"])
        return result.stdout if result.success else ""

    def set_clipboard(self, text: str) -> ExecutionResult:
        """Set clipboard content."""
        if not self._has_xclip:
            return ExecutionResult(False, stderr="xclip not available")

        try:
            proc = subprocess.Popen(
                ["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE, text=True
            )
            proc.communicate(input=text)
            return ExecutionResult(success=proc.returncode == 0)
        except Exception as e:
            return ExecutionResult(False, stderr=str(e))

    def get_all_windows(self):
        """Get list of all windows."""
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
                        id=win_id, title=title, x=x, y=y, width=w, height=h, pid=0
                    )
                )

        return windows
