"""
NAVIG AutoHotkey v2 Adapter
"""

import asyncio
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from navig.console_helper import console, error, info, success, warning


@dataclass
class AHKStatus:
    detected: bool = False
    version: Optional[str] = None
    executable_path: Optional[Path] = None
    detection_method: Optional[str] = None

    def to_dict(self):
        return {
            "detected": self.detected,
            "version": self.version,
            "executable_path": (
                str(self.executable_path) if self.executable_path else None
            ),
            "detection_method": self.detection_method,
        }


@dataclass
class WindowInfo:
    title: str
    id: str  # HWND
    pid: int
    class_name: str
    x: int
    y: int
    width: int
    height: int
    process_name: Optional[str] = None
    is_minimized: bool = False
    is_maximized: bool = False

    def to_dict(self):
        return {
            "title": self.title,
            "id": self.id,
            "pid": self.pid,
            "class_name": self.class_name,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "process_name": self.process_name,
            "state": (
                "minimized"
                if self.is_minimized
                else ("maximized" if self.is_maximized else "normal")
            ),
        }


@dataclass
class ExecutionResult:
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration_seconds: float = 0.0
    status: str = "COMPLETED"


class AHKAdapter:
    """Adapter for interacting with AutoHotkey v2."""

    def __init__(self):
        self._executable: Optional[Path] = None
        self._version: Optional[str] = None
        self._detected = False
        self._detection_method: Optional[str] = None

        # Paths
        # Use config or default locations
        self._navig_root = Path(__file__).parent.parent.parent.parent
        self._templates_dir = self._navig_root / "store" / "templates" / "ahk"
        self._scripts_dir = self._templates_dir

        # Primary primitives path
        self._primitives_path = self._templates_dir / "primitives"
        self._workflows_path = self._templates_dir / "workflows"

        # Detect on init
        self.detect()

    def detect(self) -> AHKStatus:
        """Detect AHK installation."""
        # 1. Check PATH
        exe_name = "AutoHotkey64.exe" if "64" in sys.version else "AutoHotkey32.exe"

        # Try finding standard executable name "AutoHotkey.exe" too (v2 often installs as this)
        candidates = ["AutoHotkey64.exe", "AutoHotkey32.exe", "AutoHotkey.exe"]

        found_path = None

        for name in candidates:
            path = shutil.which(name)
            if path:
                found_path = Path(path)
                self._detection_method = "PATH"
                break

        # 2. Check standard install locations
        if not found_path:
            common_paths = [
                Path(os.environ.get("ProgramFiles", "C:/Program Files"))
                / "AutoHotkey"
                / "v2"
                / "AutoHotkey64.exe",
                Path(os.environ.get("ProgramFiles", "C:/Program Files"))
                / "AutoHotkey"
                / "v2"
                / "AutoHotkey.exe",
                Path(os.environ.get("LOCALAPPDATA", ""))
                / "Programs"
                / "AutoHotkey"
                / "v2"
                / "AutoHotkey64.exe",
            ]
            for p in common_paths:
                if p.exists():
                    found_path = p
                    self._detection_method = "Standard Directory"
                    break

        if found_path:
            self._executable = found_path
            self._detected = True
            self._version = self._get_version(found_path)
        else:
            self._detected = False

        return self.get_status()

    def _get_version(self, exe_path: Path) -> str:
        """Get version string from executable."""
        try:
            # We can't easily get version via CLI flag in standard AHK without running a script
            # Running a tiny script to print version
            # A_AhkVersion
            cmd = [str(exe_path), "/ErrorStdOut", "*"]
            process = subprocess.run(
                cmd,
                input='FileAppend A_AhkVersion, "*"',
                capture_output=True,
                text=True,
                timeout=2,
            )
            if process.returncode == 0:
                return process.stdout.strip()
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
        return "Unknown"

    def get_status(self) -> AHKStatus:
        return AHKStatus(
            detected=self._detected,
            version=self._version,
            executable_path=self._executable,
            detection_method=self._detection_method,
        )

    def is_available(self) -> bool:
        return self._detected

    def refresh_detection(self):
        self.detect()

    def get_install_instructions(self) -> str:
        return """
To install AutoHotkey v2:

1. Download the installer from https://www.autohotkey.com/
2. Run the installer and select 'v2.0' (current stable)
3. Ensure 'Add to PATH' is checked if offered, or use default settings.

NAVIG will automatically detect it in standard locations.
"""

    def _run_ahk_subprocess(
        self,
        script_path_or_content: Union[Path, str],
        is_file: bool = True,
        args: List[str] = None,
        timeout: float = None,
    ) -> ExecutionResult:
        if not self._executable:
            return ExecutionResult(False, stderr="AutoHotkey executable not found")

        cmd = [str(self._executable), "/ErrorStdOut"]

        # If config forces UTF-8, we might want /CP65001 but AHK v2 defaults to UTF-8 usually
        cmd.append("/CP65001")

        input_str = None

        if is_file:
            cmd.append(str(script_path_or_content))
        else:
            cmd.append("*")  # Read from stdin
            input_str = script_path_or_content

        if args:
            cmd.extend(args)

        import time

        start_time = time.time()

        try:
            process = subprocess.run(
                cmd,
                input=input_str,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout or 30,
            )

            duration = time.time() - start_time

            return ExecutionResult(
                success=process.returncode == 0,
                stdout=process.stdout or "",
                stderr=process.stderr or "",
                exit_code=process.returncode,
                duration_seconds=duration,
                status="COMPLETED" if process.returncode == 0 else "FAILED",
            )

        except subprocess.TimeoutExpired:
            return ExecutionResult(
                False,
                stderr="Execution timed out",
                status="TIMEOUT",
                duration_seconds=time.time() - start_time,
            )
        except Exception as e:
            return ExecutionResult(
                False,
                stderr=str(e),
                status="FAILED",
                duration_seconds=time.time() - start_time,
            )

    def execute(
        self, code: str, timeout: float = None, force: bool = False
    ) -> ExecutionResult:
        """Execute inline AHK code."""
        # Wrap in V2 requirement just in case
        full_code = "#Requires AutoHotkey v2.0\n#SingleInstance Force\n" + code
        return self._run_ahk_subprocess(full_code, is_file=False, timeout=timeout)

    def execute_file(
        self, script_path: Path, args: List[str] = None, timeout: float = None
    ) -> ExecutionResult:
        """Execute an AHK script file."""
        return self._run_ahk_subprocess(
            script_path, is_file=True, args=args, timeout=timeout
        )

    # --- Primitives ---

    def _run_primitive(self, name: str, args: List[str]) -> ExecutionResult:
        script_path = self._primitives_path / f"{name}.ahk"
        if not script_path.exists():
            return ExecutionResult(
                False, stderr=f"Primitive script not found: {script_path}"
            )

        return self.execute_file(script_path, args=args)

    def _run_workflow(self, name: str, args: List[str]) -> ExecutionResult:
        script_path = self._workflows_path / f"{name}.ahk"
        if not script_path.exists():
            return ExecutionResult(
                False, stderr=f"Workflow script not found: {script_path}"
            )

        return self.execute_file(script_path, args=args)

    def click(
        self, x: int, y: int, button: str = "left", clicks: int = 1
    ) -> ExecutionResult:
        return self._run_primitive("click", [str(x), str(y), button, str(clicks)])

    def type_text(self, text: str, delay: int = 0) -> ExecutionResult:
        # Note: delay isn't directly supported by the simple type.ahk primitive yet,
        # but we iterate here if needed or update primitive.
        # For now, just pass text.
        return self._run_primitive("type", [text])

    def send_keys(self, keys: str) -> ExecutionResult:
        return self._run_primitive("send", [keys])

    def open_app(self, target: str) -> ExecutionResult:
        # Use app_launcher workflow
        return self._run_workflow("app_launcher", [target])

    def close_window(self, selector: str) -> ExecutionResult:
        return self._run_primitive("window_close", [selector])

    def move_window(
        self, selector: str, x: int, y: int, width: int = None, height: int = None
    ) -> ExecutionResult:
        args = [selector, str(x), str(y)]
        if width is not None and height is not None:
            args.extend([str(width), str(height)])
        return self._run_primitive("window_move", args)

    def maximize_window(self, selector: str) -> ExecutionResult:
        code = f'WinMaximize "{selector}"'
        return self.execute(code)

    def minimize_window(self, selector: str) -> ExecutionResult:
        code = f'WinMinimize "{selector}"'
        return self.execute(code)

    def activate_window(self, selector: str) -> ExecutionResult:
        code = f'WinActivate "{selector}"'
        return self.execute(code)

    def get_clipboard(self) -> Optional[str]:
        # AHK script to print clipboard
        res = self.execute('FileAppend A_Clipboard, "*"')
        if res.success:
            return res.stdout
        return None

    def set_clipboard(self, content: str) -> ExecutionResult:
        # Escape quotes for AHK string
        safe_content = content.replace("`", "``").replace('"', '`"')
        code = f'A_Clipboard := "{safe_content}"'
        return self.execute(code)

    def get_all_windows(self) -> List[WindowInfo]:
        """Get list of all visible windows."""
        # We need a robust script to dump window info in JSON or CSV
        # Using a custom inline script for this to ensure we get structured data
        script = r"""
        windows := WinGetList()
        result := ""
        for hwnd in windows {
            try {
                title := WinGetTitle(hwnd)
                if (title != "") {
                    class_name := WinGetClass(hwnd)
                    pid := WinGetPID(hwnd)
                    try {
                        process_name := WinGetProcessName(hwnd)
                    } catch {
                        process_name := ""
                    }
                    WinGetPos(&x, &y, &w, &h, hwnd)
                    style := WinGetStyle(hwnd)
                    minimized := (style & 0x20000000) ? 1 : 0
                    maximized := (style & 0x1000000) ? 1 : 0

                    ; Check visibility (0x10000000 is WS_VISIBLE)
                    if (style & 0x10000000) {
                        line := title "|" hwnd "|" pid "|" class_name "|" x "|" y "|" w "|" h "|" process_name "|" minimized "|" maximized
                        result .= line "`n"
                    }
                }
            }
        }
        try {
            FileAppend(result, "*")
        }
        """

        res = self.execute(script)
        windows = []
        if res.success:
            for line in res.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("|")
                if len(parts) >= 11:
                    try:
                        windows.append(
                            WindowInfo(
                                title=parts[0],
                                id=parts[1],
                                pid=int(parts[2]),
                                class_name=parts[3],
                                x=int(parts[4]),
                                y=int(parts[5]),
                                width=int(parts[6]),
                                height=int(parts[7]),
                                process_name=parts[8],
                                is_minimized=parts[9] == "1",
                                is_maximized=parts[10] == "1",
                            )
                        )
                    except (ValueError, IndexError):
                        continue
        return windows

    def resize_window(self, selector: str, width: int, height: int) -> ExecutionResult:
        """Resize window without moving."""
        code = f"""
        try {{
            WinMove ,, {width}, {height}, "{selector}"
        }} catch Error as e {{
            FileAppend e.Message, "*"
            ExitApp 1
        }}
        """
        return self.execute(code)

    def restore_window(self, selector: str) -> ExecutionResult:
        """Restore window."""
        code = f'WinRestore "{selector}"'
        return self.execute(code)

    def mouse_move(self, x: int, y: int, speed: int = 2) -> ExecutionResult:
        """Move mouse to coordinates."""
        # Speed: 0 (fastest) to 100 (slowest)
        code = f"MouseMove {x}, {y}, {speed}"
        return self.execute(code)

    def drag_drop(
        self, x1: int, y1: int, x2: int, y2: int, button: str = "Left"
    ) -> ExecutionResult:
        """Drag and drop from (x1,y1) to (x2,y2)."""
        code = f'MouseClickDrag "{button}", {x1}, {y1}, {x2}, {y2}'
        return self.execute(code)

    def get_screen_size(self) -> Tuple[int, int]:
        """Get primary screen resolution."""
        script = 'FileAppend A_ScreenWidth "|" A_ScreenHeight, "*"'
        res = self.execute(script)
        if res.success and "|" in res.stdout:
            try:
                w, h = map(int, res.stdout.split("|"))
                return (w, h)
            except Exception:  # noqa: BLE001
                pass  # best-effort; suppress all errors
        return (1920, 1080)  # Default fallback

    def read_text(self, selector: str, control_id: str = "") -> str:
        """Extract text from window or control."""
        if control_id:
            code = f'try {{ FileAppend ControlGetText("{control_id}", "{selector}"), "*" }}'
        else:
            code = f'try {{ FileAppend WinGetText("{selector}"), "*" }}'

        res = self.execute(code)
        return res.stdout if res.success else ""

    def get_control_value(self, selector: str, control_id: str) -> str:
        """Get value of a UI control."""
        return self.read_text(selector, control_id)

    def set_control_value(
        self, selector: str, control_id: str, value: str
    ) -> ExecutionResult:
        """Set text of a UI control (Edit/ComboBox)."""
        safe_val = value.replace('"', '`"')
        code = f'ControlSetText "{safe_val}", "{control_id}", "{selector}"'
        return self.execute(code)

    def click_control(
        self, selector: str, control_id: str, button: str = "Left", click_count: int = 1
    ) -> ExecutionResult:
        """Click a UI control."""
        code = f'ControlClick "{control_id}", "{selector}",, "{button}", {click_count}'
        return self.execute(code)
