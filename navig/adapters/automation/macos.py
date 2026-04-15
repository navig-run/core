"""
macOS Automation Adapter using AppleScript and osascript
"""

import subprocess
import sys

from navig.adapters.automation.types import ExecutionResult, WindowInfo

# Seconds for osascript / shell-command subprocesses.
_MACOS_SCRIPT_TIMEOUT: int = 10


class MacOSAdapter:
    """Automation adapter for macOS using AppleScript and cliclick."""

    def __init__(self):
        self._available = None
        self._has_cliclick = None

    def is_available(self) -> bool:
        """Check if running on macOS."""
        if self._available is not None:
            return self._available

        if sys.platform != "darwin":
            self._available = False
            return False

        # Check for cliclick (optional but helpful)
        self._has_cliclick = self._check_command("cliclick")
        self._available = True
        return True

    def _check_command(self, cmd: str) -> bool:
        try:
            result = subprocess.run(["which", cmd], capture_output=True, text=True)
            return result.returncode == 0
        except Exception:
            return False

    def _run_applescript(self, script: str) -> ExecutionResult:
        """Execute AppleScript."""
        try:
            result = subprocess.run(
                ["osascript", "-e", script], capture_output=True, text=True, timeout=_MACOS_SCRIPT_TIMEOUT
            )
            return ExecutionResult(
                success=result.returncode == 0,
                stdout=result.stdout.strip(),
                stderr=result.stderr.strip(),
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(False, stderr="Command timeout")
        except Exception as e:
            return ExecutionResult(False, stderr=str(e))

    def _run_command(self, cmd: list) -> ExecutionResult:
        """Run shell command."""
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=_MACOS_SCRIPT_TIMEOUT)
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
            return self._run_command(["open", target])
        else:
            # Try as app name first, then as path
            script = f'tell application "{target}" to activate'
            result = self._run_applescript(script)
            if not result.success:
                # Try as path
                return self._run_command(["open", target])
            return result

    def click(self, x: int, y: int, button: str = "left") -> ExecutionResult:
        """Click at coordinates."""
        if self._has_cliclick:
            button_map = {"left": "c", "right": "rc", "middle": "mc"}
            btn = button_map.get(button, "c")
            return self._run_command(["cliclick", f"{btn}:{x},{y}"])
        else:
            # Use AppleScript (less reliable)
            script = f"""
            tell application "System Events"
                click at {{{x}, {y}}}
            end tell
            """
            return self._run_applescript(script)

    def type_text(self, text: str, delay: int = 50) -> ExecutionResult:
        """Type text."""
        # Escape quotes
        safe_text = text.replace('"', '\\"')
        script = f"""
        tell application "System Events"
            keystroke "{safe_text}"
        end tell
        """
        return self._run_applescript(script)

    def send_keys(self, keys: str) -> ExecutionResult:
        """Send key sequence (AppleScript key codes)."""
        # Convert common keys to AppleScript
        # This is simplified - full implementation would need key mapping
        script = f"""
        tell application "System Events"
            key code {keys}
        end tell
        """
        return self._run_applescript(script)

    def mouse_move(self, x: int, y: int, speed: int = 2) -> ExecutionResult:
        """Move mouse."""
        if self._has_cliclick:
            return self._run_command(["cliclick", f"m:{x},{y}"])
        else:
            return ExecutionResult(
                False, stderr="Mouse move requires cliclick (brew install cliclick)"
            )

    def get_focused_window(self) -> WindowInfo | None:
        """Get currently focused window."""
        script = """
        tell application "System Events"
            set frontApp to name of first application process whose frontmost is true
            tell process frontApp
                set frontWindow to front window
                set windowName to name of frontWindow
                set windowPosition to position of frontWindow
                set windowSize to size of frontWindow
                return frontApp & "|" & windowName & "|" & item 1 of windowPosition & "|" & item 2 of windowPosition & "|" & item 1 of windowSize & "|" & item 2 of windowSize
            end tell
        end tell
        """
        result = self._run_applescript(script)
        if not result.success:
            return None

        try:
            parts = result.stdout.split("|")
            return WindowInfo(
                id="0",  # macOS doesn't expose window IDs easily
                title=parts[1],
                x=int(parts[2]),
                y=int(parts[3]),
                width=int(parts[4]),
                height=int(parts[5]),
                pid=0,
                process_name=parts[0],
            )
        except Exception:
            return None

    def activate_window(self, selector: str) -> ExecutionResult:
        """Activate window by title or app name."""
        script = f"""
        tell application "{selector}" to activate
        """
        result = self._run_applescript(script)
        if not result.success:
            # Try as window title
            script = f"""
            tell application "System Events"
                set frontmost of first process whose name contains "{selector}" to true
            end tell
            """
            result = self._run_applescript(script)
        return result

    def close_window(self, selector: str) -> ExecutionResult:
        """Close window."""
        script = f"""
        tell application "{selector}"
            close front window
        end tell
        """
        return self._run_applescript(script)

    def move_window(
        self, selector: str, x: int, y: int, width: int = None, height: int = None
    ) -> ExecutionResult:
        """Move and resize window."""
        if width and height:
            script = f"""
            tell application "{selector}"
                set bounds of front window to {{{x}, {y}, {x + width}, {y + height}}}
            end tell
            """
        else:
            script = f"""
            tell application "{selector}"
                set position of front window to {{{x}, {y}}}
            end tell
            """
        return self._run_applescript(script)

    def maximize_window(self, selector: str) -> ExecutionResult:
        """Maximize window (zoom)."""
        script = f"""
        tell application "{selector}"
            tell front window
                set zoomed to true
            end tell
        end tell
        """
        return self._run_applescript(script)

    def minimize_window(self, selector: str) -> ExecutionResult:
        """Minimize window."""
        script = f"""
        tell application "{selector}"
            set miniaturized of front window to true
        end tell
        """
        return self._run_applescript(script)

    def snap_window(self, selector: str, position: str) -> ExecutionResult:
        """Snap window to screen position."""
        # Get screen dimensions
        script = """
        tell application "Finder"
            set screenBounds to bounds of window of desktop
            return item 3 of screenBounds & "," & item 4 of screenBounds
        end tell
        """
        screen_result = self._run_applescript(script)
        if not screen_result.success:
            return ExecutionResult(False, stderr="Failed to get screen size")

        screen_w, screen_h = map(int, screen_result.stdout.split(","))

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
        result = self._run_command(["pbpaste"])
        return result.stdout if result.success else ""

    def set_clipboard(self, text: str) -> ExecutionResult:
        """Set clipboard content."""
        try:
            proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE, text=True)
            proc.communicate(input=text)
            return ExecutionResult(success=proc.returncode == 0)
        except Exception as e:
            return ExecutionResult(False, stderr=str(e))

    def get_all_windows(self):
        """Get list of all windows."""
        script = """
        tell application "System Events"
            set windowList to {}
            repeat with proc in application processes
                if background only of proc is false then
                    set procName to name of proc
                    repeat with win in windows of proc
                        set winName to name of win
                        set winPos to position of win
                        set winSize to size of win
                        set end of windowList to procName & "|" & winName & "|" & item 1 of winPos & "|" & item 2 of winPos & "|" & item 1 of winSize & "|" & item 2 of winSize
                    end repeat
                end if
            end repeat
            return windowList as string
        end tell
        """
        result = self._run_applescript(script)
        if not result.success:
            return []

        windows = []
        for line in result.stdout.split(","):
            if not line.strip():
                continue
            try:
                parts = line.strip().split("|")
                if len(parts) >= 6:
                    windows.append(
                        WindowInfo(
                            id="0",
                            title=parts[1],
                            x=int(parts[2]),
                            y=int(parts[3]),
                            width=int(parts[4]),
                            height=int(parts[5]),
                            pid=0,
                            process_name=parts[0],
                        )
                    )
            except Exception:
                continue

        return windows
