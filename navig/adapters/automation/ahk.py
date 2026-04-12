"""
NAVIG AutoHotkey v2 Adapter
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from navig.adapters.automation.types import ExecutionResult, WindowInfo


@dataclass
class AHKStatus:
    detected: bool = False
    version: str | None = None
    executable_path: Path | None = None
    detection_method: str | None = None

    def to_dict(self):
        return {
            "detected": self.detected,
            "version": self.version,
            "executable_path": (str(self.executable_path) if self.executable_path else None),
            "detection_method": self.detection_method,
        }


class AHKAdapter:
    """Adapter for interacting with AutoHotkey v2."""

    def __init__(self):
        self._executable: Path | None = None
        self._version: str | None = None
        self._detected = False
        self._detection_method: str | None = None

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
                Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
                / "AutoHotkey"
                / "v2"
                / "AutoHotkey64.exe",
                Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
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
            process = subprocess.run(  # noqa: S603
                cmd,
                input='FileAppend A_AhkVersion, "*"',
                capture_output=True,
                text=True,
                timeout=2,
            )
            if process.returncode == 0:
                return process.stdout.strip()
        except Exception:  # noqa: BLE001,S110,S110
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
        script_path_or_content: Path | str,
        is_file: bool = True,
        args: list[str] = None,
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
            process = subprocess.run(  # noqa: S603
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

    def execute(self, code: str, timeout: float = None, force: bool = False) -> ExecutionResult:
        """Execute inline AHK code."""
        # Wrap in V2 requirement and provide a lightweight JSON shim for inline scripts.
        # Some inline snippets use JSON.stringify(...), which is not built into AHK v2.
        # We rewrite to NavigJsonStringify(...) at runtime to avoid undefined-global warnings.
        transformed_code = code.replace("JSON.stringify(", "NavigJsonStringify(")
        full_code = (
            "#Requires AutoHotkey v2.0\n"
            "#SingleInstance Force\n"
            "NavigJsonEscape(str) {\n"
            "    str := StrReplace(str, \\\"\\\\\\\", \\\"\\\\\\\\\\\\\\\")\n"
            "    str := StrReplace(str, \"\\\"\", \"\\\\\\\"\")\n"
            "    str := StrReplace(str, \"`r\", \"\\\\r\")\n"
            "    str := StrReplace(str, \"`n\", \"\\\\n\")\n"
            "    str := StrReplace(str, \"`t\", \"\\\\t\")\n"
            "    return \"\"\"\" str \"\"\"\"\n"
            "}\n"
            "\n"
            "NavigJsonStringify(value) {\n"
            "    if IsObject(value) {\n"
            "        if (value is Map) {\n"
            "            out := \"{\"\n"
            "            first := true\n"
            "            for key, item in value {\n"
            "                if !first\n"
            "                    out .= \",\"\n"
            "                first := false\n"
            "                out .= NavigJsonEscape(String(key)) \":\" NavigJsonStringify(item)\n"
            "            }\n"
            "            out .= \"}\"\n"
            "            return out\n"
            "        }\n"
            "\n"
            "        if (value is Array) {\n"
            "            out := \"[\"\n"
            "            first := true\n"
            "            for _, item in value {\n"
            "                if !first\n"
            "                    out .= \",\"\n"
            "                first := false\n"
            "                out .= NavigJsonStringify(item)\n"
            "            }\n"
            "            out .= \"]\"\n"
            "            return out\n"
            "        }\n"
            "    }\n"
            "\n"
            "    t := Type(value)\n"
            "    if (t = \"String\")\n"
            "        return NavigJsonEscape(value)\n"
            "    if (t = \"Integer\" or t = \"Float\")\n"
            "        return value\n"
            "    if (t = \"Object\")\n"
            "        return NavigJsonEscape(String(value))\n"
            "    return NavigJsonEscape(String(value))\n"
            "}\n"
        ) + transformed_code
        return self._run_ahk_subprocess(full_code, is_file=False, timeout=timeout)

    def execute_file(
        self, script_path: Path, args: list[str] = None, timeout: float = None
    ) -> ExecutionResult:
        """Execute an AHK script file."""
        return self._run_ahk_subprocess(script_path, is_file=True, args=args, timeout=timeout)

    def run_detached(self, script_path: Path, args: list[str] = None) -> int:
        """Run script in background (detached). Returns PID."""
        if not self.executable.exists():
            return 0

        cmd = [str(self.executable), str(script_path)]
        if args:
            cmd.extend(args)

        try:
            # Popen without waiting, capturing nothing to detach
            proc = subprocess.Popen(  # noqa: S603
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(script_path.parent),
                creationflags=(subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0),
            )
            return proc.pid
        except Exception:
            return 0

    # --- Primitives ---

    def _run_primitive(self, name: str, args: list[str]) -> ExecutionResult:
        script_path = self._primitives_path / f"{name}.ahk"
        if not script_path.exists():
            return ExecutionResult(False, stderr=f"Primitive script not found: {script_path}")

        return self.execute_file(script_path, args=args)

    def _run_workflow(self, name: str, args: list[str]) -> ExecutionResult:
        script_path = self._workflows_path / f"{name}.ahk"
        if not script_path.exists():
            return ExecutionResult(False, stderr=f"Workflow script not found: {script_path}")

        return self.execute_file(script_path, args=args)

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> ExecutionResult:
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

    def get_all_windows(self) -> list[WindowInfo]:
        """Get list of all visible windows."""
        # Use JSON for robust data transfer
        script = r"""
        windows := WinGetList()
        jsonArray := "["
        first := true

        for hwnd in windows {
            try {
                style := WinGetStyle(hwnd)
                ; Check visibility (0x10000000 is WS_VISIBLE) but ignore if minimized (sometimes hidden)
                ; Actually WinGetList returns hidden windows too? Check default.
                ; Default WinGetList mode is DetectHiddenWindows Off.
                ; But style check is good.

                if (style & 0x10000000) {
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
                        minimized := (style & 0x20000000) ? 1 : 0
                        maximized := (style & 0x1000000) ? 1 : 0

                        if !first
                            jsonArray .= ","
                        first := false

                        ; Escape strings for JSON
                        titleEsc := StrReplace(title, "\", "\\")
                        titleEsc := StrReplace(titleEsc, '"', '\"')
                        titleEsc := StrReplace(titleEsc, "`n", " ")
                        titleEsc := StrReplace(titleEsc, "`r", "")

                        procEsc := StrReplace(process_name, "\", "\\")
                        procEsc .= "" ; Ensure string

                        jsonObj := '{"hwnd":' . hwnd
                        jsonObj .= ',"title":"' . titleEsc . '"'
                        jsonObj .= ',"class_name":"' . class_name . '"'
                        jsonObj .= ',"pid":' . pid
                        jsonObj .= ',"x":' . x
                        jsonObj .= ',"y":' . y
                        jsonObj .= ',"w":' . w
                        jsonObj .= ',"h":' . h
                        jsonObj .= ',"process_name":"' . procEsc . '"'
                        jsonObj .= ',"minimized":' . minimized
                        jsonObj .= ',"maximized":' . maximized
                        jsonObj .= '}'

                        jsonArray .= jsonObj
                    }
                }
            }
        }
        jsonArray .= "]"
        try {
            FileAppend(jsonArray, "*")
        }
        """

        res = self.execute(script)
        windows = []
        if res.success and res.stdout:
            try:
                import json

                data = json.loads(res.stdout)
                for item in data:
                    windows.append(
                        WindowInfo(
                            title=item.get("title", ""),
                            id=str(item.get("hwnd", "")),
                            pid=item.get("pid", 0),
                            class_name=item.get("class_name", ""),
                            x=item.get("x", 0),
                            y=item.get("y", 0),
                            width=item.get("w", 0),
                            height=item.get("h", 0),
                            process_name=item.get("process_name", ""),
                            is_minimized=item.get("minimized") == 1,
                            is_maximized=item.get("maximized") == 1,
                        )
                    )
            except Exception:  # noqa: S110
                # Log error if JSON parsing fails
                pass

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

    def get_screen_size(self) -> tuple[int, int]:
        """Get primary screen resolution."""
        script = 'FileAppend A_ScreenWidth "|" A_ScreenHeight, "*"'
        res = self.execute(script)
        if res.success and "|" in res.stdout:
            try:
                w, h = map(int, res.stdout.split("|"))
                return (w, h)
            except Exception:  # noqa: BLE001,S110
                pass  # best-effort; failure is non-critical
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

    def set_control_value(self, selector: str, control_id: str, value: str) -> ExecutionResult:
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

    def get_focused_window(self) -> WindowInfo | None:
        """Get the currently focused window."""
        script = r"""
        try {
            hwnd := WinGetID("A")
            title := WinGetTitle("ahk_id " hwnd)
            class_name := WinGetClass("ahk_id " hwnd)
            pid := WinGetPID("ahk_id " hwnd)
            try {
                WinGetPos(&x, &y, &w, &h, "ahk_id " hwnd)
            } catch {
                x := 0, y := 0, w := 0, h := 0
            }

            titleEsc := StrReplace(title, "\", "\\")
            titleEsc := StrReplace(titleEsc, '"', '\"')
            titleEsc := StrReplace(titleEsc, "`n", "\n")
            titleEsc := StrReplace(titleEsc, "`r", "")

            jsonObj := '{"hwnd":' . hwnd
            jsonObj .= ',"title":"' . titleEsc . '"'
            jsonObj .= ',"class_name":"' . class_name . '"'
            jsonObj .= ',"pid":' . pid
            jsonObj .= ',"x":' . x
            jsonObj .= ',"y":' . y
            jsonObj .= ',"w":' . w
            jsonObj .= ',"h":' . h
            jsonObj .= '}'

            FileAppend(jsonObj, "*")
        } catch {
            FileAppend("{}", "*")
        }
        """
        res = self.execute(script)
        if res.success and res.stdout and res.stdout != "{}":
            try:
                import json

                data = json.loads(res.stdout)
                return WindowInfo(
                    title=data.get("title", ""),
                    id=str(data.get("hwnd", "")),
                    pid=data.get("pid", 0),
                    class_name=data.get("class_name", ""),
                    x=data.get("x", 0),
                    y=data.get("y", 0),
                    width=data.get("w", 0),
                    height=data.get("h", 0),
                    process_name="",
                )
            except Exception:  # noqa: BLE001,S110
                pass  # best-effort; failure is non-critical
        return None

    def snap_window(self, selector: str, position: str) -> ExecutionResult:
        """
        Snap window to a screen position.
        Position: left, right, top, bottom, top-left, top-right, bottom-left, bottom-right, center
        """
        return self._run_primitive("snap", [selector, position])

    def toggle_always_on_top(self, selector: str) -> ExecutionResult:
        """Toggle AlwaysOnTop for window."""
        sel = selector.replace('"', '`"')
        if not sel:
            sel = "A"
        script = f'try WinSetAlwaysOnTop -1, "{sel}"'
        return self.execute(script)

    def get_clipboard(self) -> str:
        """Get current clipboard content."""
        res = self.execute('try FileAppend(A_Clipboard, "*")')
        return res.stdout if res.success else ""

    def set_clipboard(self, text: str) -> ExecutionResult:
        """Set clipboard content."""
        safe = text.replace("`", "``").replace('"', '`"')
        return self.execute(f'try A_Clipboard := "{safe}"')

    # === Process Management ===

    def get_processes(self) -> list[dict[str, Any]]:
        """Get list of running processes with details."""
        script = r"""
        processes := ComObjGet("winmgmts:").ExecQuery("SELECT * FROM Win32_Process")
        result := []
        for proc in processes {
            try {
                obj := Map()
                obj["name"] := proc.Name
                obj["pid"] := proc.ProcessId
                obj["exe"] := proc.ExecutablePath ? proc.ExecutablePath : ""
                obj["memory"] := proc.WorkingSetSize
                result.Push(obj)
            }
        }
        FileAppend(JSON.stringify(result), "*")
        """
        res = self.execute(script)
        if res.success:
            import json

            try:
                return json.loads(res.stdout)
            except Exception:
                return []
        return []

    def kill_process(self, identifier: str) -> ExecutionResult:
        """Kill process by name or PID."""
        # Try as PID first (numeric)
        if identifier.isdigit():
            script = f"try ProcessClose({identifier})"
        else:
            script = f'try ProcessClose("{identifier}")'
        return self.execute(script)

    def start_process(self, exe_path: str, args: str = "", wait: bool = False) -> ExecutionResult:
        """Start a process."""
        safe_path = exe_path.replace('"', '`"')
        safe_args = args.replace('"', '`"') if args else ""

        if wait:
            script = f'try RunWait("{safe_path}" "{safe_args}")'
        else:
            script = f'try Run("{safe_path}" "{safe_args}")'
        return self.execute(script)

    def process_exists(self, name: str) -> bool:
        """Check if process is running."""
        script = (
            f'try {{ if ProcessExist("{name}") {{'
            ' FileAppend("true", "*") }} else {{ FileAppend("false", "*") }} }}'
        )
        res = self.execute(script)
        return res.success and res.stdout.strip() == "true"

    # === Multi-Monitor Support ===

    def get_monitors(self) -> list[dict[str, Any]]:
        """Get information about all monitors."""
        script = r"""
        monitors := []
        Loop MonitorGetCount() {
            MonitorGet(A_Index, &left, &top, &right, &bottom)
            MonitorGetWorkArea(A_Index, &wleft, &wtop, &wright, &wbottom)
            mon := Map()
            mon["index"] := A_Index
            mon["left"] := left
            mon["top"] := top
            mon["width"] := right - left
            mon["height"] := bottom - top
            mon["work_left"] := wleft
            mon["work_top"] := wtop
            mon["work_width"] := wright - wleft
            mon["work_height"] := wbottom - wtop
            mon["primary"] := (A_Index = MonitorGetPrimary()) ? 1 : 0
            monitors.Push(mon)
        }
        FileAppend(JSON.stringify(monitors), "*")
        """
        res = self.execute(script)
        if res.success:
            import json

            try:
                return json.loads(res.stdout)
            except Exception:
                return []
        return []

    def move_window_to_monitor(self, selector: str, monitor_index: int) -> ExecutionResult:
        """Move window to specific monitor."""
        script = f"""
        try {{
            hwnd := WinExist("{selector}")
            if hwnd {{
                MonitorGet({monitor_index}, &left, &top, &right, &bottom)
                WinMove(left + 50, top + 50, , , hwnd)
            }}
        }}
        """
        return self.execute(script)

    # === Window State & Transparency ===

    def set_window_transparency(self, selector: str, opacity: int) -> ExecutionResult:
        """
        Set window transparency (0-255).
        0 = fully transparent, 255 = fully opaque
        """
        opacity = max(0, min(255, opacity))
        script = f'try WinSetTransparent({opacity}, "{selector}")'
        return self.execute(script)

    def get_window_state(self, selector: str) -> dict[str, bool]:
        """Get detailed window state."""
        script = f"""
        try {{
            hwnd := WinExist("{selector}")
            if hwnd {{
                state := Map()
                state["exists"] := 1
                state["minimized"] := WinGetMinMax(hwnd) = -1 ? 1 : 0
                state["maximized"] := WinGetMinMax(hwnd) = 1 ? 1 : 0
                state["active"] := WinActive(hwnd) ? 1 : 0
                style := WinGetStyle(hwnd)
                state["visible"] := (style & 0x10000000) ? 1 : 0
                FileAppend(JSON.stringify(state), "*")
            }}
        }}
        """
        res = self.execute(script)
        if res.success:
            import json

            try:
                data = json.loads(res.stdout)
                return {k: bool(v) for k, v in data.items()}
            except Exception:  # noqa: BLE001,S110
                pass  # best-effort; failure is non-critical
        return {"exists": False}

    # === Notifications ===

    def show_notification(self, title: str, message: str, duration: int = 3) -> ExecutionResult:
        """Show Windows toast notification."""
        safe_title = title.replace('"', '`"')
        safe_msg = message.replace('"', '`"')
        duration_ms = duration * 1000

        script = f"""
        try {{
            TrayTip("{safe_msg}", "{safe_title}", "Iconi Mute")
            Sleep({duration_ms})
            try TrayTip()
        }}
        """
        return self.execute(script)

    # === Sound Control ===

    def set_volume(self, level: int) -> ExecutionResult:
        """Set master volume (0-100)."""
        level = max(0, min(100, level))
        script = f"try SoundSetVolume({level})"
        return self.execute(script)

    def get_volume(self) -> int:
        """Get master volume level."""
        script = 'try FileAppend(Round(SoundGetVolume()), "*")'
        res = self.execute(script)
        if res.success:
            try:
                return int(res.stdout.strip())
            except Exception:
                return 0
        return 0

    def mute(self, muted: bool = True) -> ExecutionResult:
        """Mute or unmute system audio."""
        value = 1 if muted else 0
        script = f"try SoundSetMute({value})"
        return self.execute(script)

    def is_muted(self) -> bool:
        """Check if system audio is muted."""
        script = 'try FileAppend(SoundGetMute(), "*")'
        res = self.execute(script)
        return res.success and res.stdout.strip() == "1"

    # === Advanced Focus Control ===

    def activate_window(self, selector: str, force: bool = False) -> ExecutionResult:
        """Activate/focus a window."""
        if force:
            # Use more aggressive activation
            script = f"""
            try {{
                hwnd := WinExist("{selector}")
                if hwnd {{
                    if WinGetMinMax(hwnd) = -1
                        WinRestore(hwnd)
                    WinActivate(hwnd)
                    WinWaitActive(hwnd, , 2)
                }}
            }}
            """
        else:
            script = f'try WinActivate("{selector}")'
        return self.execute(script)

    def get_active_window(self) -> WindowInfo | None:
        """Get currently active window."""
        script = r"""
        try {
            hwnd := WinGetID("A")
            title := WinGetTitle(hwnd)
            class_name := WinGetClass(hwnd)
            pid := WinGetPID(hwnd)
            WinGetPos(&x, &y, &w, &h, hwnd)

            state := Map()
            state["title"] := title
            state["hwnd"] := hwnd
            state["pid"] := pid
            state["class_name"] := class_name
            state["x"] := x
            state["y"] := y
            state["w"] := w
            state["h"] := h

            FileAppend(JSON.stringify(state), "*")
        }
        """
        res = self.execute(script)
        if res.success and res.stdout:
            try:
                import json

                data = json.loads(res.stdout)
                return WindowInfo(
                    title=data.get("title", ""),
                    id=str(data.get("hwnd", "")),
                    pid=data.get("pid", 0),
                    class_name=data.get("class_name", ""),
                    x=data.get("x", 0),
                    y=data.get("y", 0),
                    width=data.get("w", 0),
                    height=data.get("h", 0),
                    process_name="",
                )
            except Exception:  # noqa: BLE001,S110
                pass  # best-effort; failure is non-critical
        return None

    def find_windows(self, title_pattern: str = "", class_pattern: str = "") -> list[WindowInfo]:
        """Find windows matching title or class pattern."""
        title_esc = title_pattern.replace('"', '`"')
        class_esc = class_pattern.replace('"', '`"')

        script = f"""
        try {{
            windows := WinGetList("{title_esc}", "{class_esc}")
            result := []
            for hwnd in windows {{
                try {{
                    title := WinGetTitle(hwnd)
                    if title {{
                        win := Map()
                        win["title"] := title
                        win["hwnd"] := hwnd
                        win["pid"] := WinGetPID(hwnd)
                        win["class_name"] := WinGetClass(hwnd)
                        WinGetPos(&x, &y, &w, &h, hwnd)
                        win["x"] := x
                        win["y"] := y
                        win["w"] := w
                        win["h"] := h
                        result.Push(win)
                    }}
                }}
            }}
            FileAppend(JSON.stringify(result), "*")
        }}
        """
        res = self.execute(script)
        if res.success:
            import json

            try:
                data = json.loads(res.stdout)
                return [
                    WindowInfo(
                        title=w.get("title", ""),
                        id=str(w.get("hwnd", "")),
                        pid=w.get("pid", 0),
                        class_name=w.get("class_name", ""),
                        x=w.get("x", 0),
                        y=w.get("y", 0),
                        width=w.get("w", 0),
                        height=w.get("h", 0),
                        process_name="",
                    )
                    for w in data
                ]
            except Exception:  # noqa: BLE001,S110
                pass  # best-effort; failure is non-critical
        return []
