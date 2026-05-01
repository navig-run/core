"""Windows-specific MCP tools: process management, registry, and notifications.

These tools are registered only on ``sys.platform == "win32"``; all handlers
return a structured error on other platforms.

Tools exposed:
- ``desktop_process_list``  — list running processes (filter, sort, limit)
- ``desktop_process_kill``  — terminate a process by PID or name
- ``desktop_registry_get``  — read a Windows registry value
- ``desktop_registry_set``  — write a Windows registry value
- ``desktop_registry_delete`` — delete a value or key
- ``desktop_registry_list``  — list sub-keys and values under a path
- ``desktop_notify``         — send a Windows toast notification
"""

from __future__ import annotations

import sys
from typing import Any

try:
    import psutil  # type: ignore[import]
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore[assignment]

if sys.platform == "win32":
    import winreg

    from navig.adapters.automation.powershell import PowerShellExecutor
    from navig.platform.windows_utils import check_pid_exists, ps_quote_for_xml
else:
    winreg = None  # type: ignore[assignment]
    PowerShellExecutor = None  # type: ignore[assignment]
    check_pid_exists = None  # type: ignore[assignment]
    ps_quote_for_xml = None  # type: ignore[assignment]

# ─── Tool schemas ─────────────────────────────────────────────────────────────

_TOOLS: dict[str, dict] = {
    "desktop_process_list": {
        "name": "desktop_process_list",
        "description": (
            "List running Windows processes.  "
            "Optionally filter by name substring. "
            "Returns pid, name, cpu_percent, memory_mb, and status for each match."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name_filter": {
                    "type": "string",
                    "description": "Case-insensitive substring filter on process name.",
                },
                "sort_by": {
                    "type": "string",
                    "enum": ["memory", "cpu", "name"],
                    "default": "memory",
                    "description": "Field to sort results by.",
                },
                "limit": {
                    "type": "integer",
                    "default": 25,
                    "description": "Maximum number of processes to return.",
                },
            },
            "required": [],
        },
    },
    "desktop_process_kill": {
        "name": "desktop_process_kill",
        "description": (
            "Terminate a running Windows process by PID *or* name.  "
            "When ``force`` is true, SIGKILL is used instead of SIGTERM."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pid": {
                    "type": "integer",
                    "description": "Process ID to terminate.",
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Process name (e.g. 'notepad.exe'); terminates the first matching process."
                    ),
                },
                "force": {
                    "type": ["boolean", "string"],
                    "default": False,
                    "description": "Use force-kill (SIGKILL / TerminateProcess) instead of graceful.",
                },
            },
            "required": [],
        },
    },
    "desktop_registry_get": {
        "name": "desktop_registry_get",
        "description": (
            "Read a single Windows Registry value. "
            "Path format: ``HKCU:\\\\Software\\\\MyApp`` or ``HKLM:\\\\SOFTWARE\\\\...``."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Registry key path (PowerShell format).",
                },
                "name": {
                    "type": "string",
                    "description": "Value name; use empty string for the default value.",
                },
            },
            "required": ["path", "name"],
        },
    },
    "desktop_registry_set": {
        "name": "desktop_registry_set",
        "description": "Create or update a Windows Registry value.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Registry key path."},
                "name": {"type": "string", "description": "Value name."},
                "value": {"type": "string", "description": "Value data (as string)."},
                "reg_type": {
                    "type": "string",
                    "enum": ["String", "DWord", "QWord", "Binary", "MultiString", "ExpandString"],
                    "default": "String",
                    "description": "Registry value type.",
                },
            },
            "required": ["path", "name", "value"],
        },
    },
    "desktop_registry_delete": {
        "name": "desktop_registry_delete",
        "description": (
            "Delete a Windows Registry value or key. "
            "If ``name`` is omitted, the entire key is deleted."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Registry key path."},
                "name": {
                    "type": "string",
                    "description": "Value name to delete; omit to delete the key itself.",
                },
            },
            "required": ["path"],
        },
    },
    "desktop_registry_list": {
        "name": "desktop_registry_list",
        "description": "List sub-keys and values under a Windows Registry key path.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Registry key path."},
            },
            "required": ["path"],
        },
    },
    "desktop_notify": {
        "name": "desktop_notify",
        "description": "Send a Windows toast notification with a title and message body.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Notification title."},
                "message": {"type": "string", "description": "Notification body text."},
                "app_id": {
                    "type": "string",
                    "description": "Application User Model ID (AUMID). Defaults to 'navig'.",
                    "default": "navig",
                },
            },
            "required": ["title", "message"],
        },
    },
    "desktop_powershell": {
        "name": "desktop_powershell",
        "description": (
            "Execute a PowerShell command or script on the local machine and return its output. "
            "stdout and stderr are captured and returned together with the exit code."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "PowerShell command or script text to execute.",
                },
                "timeout": {
                    "type": "number",
                    "default": 30,
                    "description": "Maximum execution time in seconds.",
                },
            },
            "required": ["command"],
        },
    },
    "desktop_clipboard_get": {
        "name": "desktop_clipboard_get",
        "description": "Read the current Windows clipboard contents as text.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    "desktop_clipboard_set": {
        "name": "desktop_clipboard_set",
        "description": "Write text to the Windows clipboard.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to place on the clipboard.",
                }
            },
            "required": ["text"],
        },
    },
}

# ─── Registration ─────────────────────────────────────────────────────────────


def register(server: Any) -> None:
    """Register Windows-only MCP tools on *server*."""
    server.tools.update(_TOOLS)
    server._tool_handlers.update(
        {
            "desktop_process_list": _tool_process_list,
            "desktop_process_kill": _tool_process_kill,
            "desktop_registry_get": _tool_registry_get,
            "desktop_registry_set": _tool_registry_set,
            "desktop_registry_delete": _tool_registry_delete,
            "desktop_registry_list": _tool_registry_list,
            "desktop_notify": _tool_notify,
            "desktop_powershell": _tool_powershell,
            "desktop_clipboard_get": _tool_clipboard_get,
            "desktop_clipboard_set": _tool_clipboard_set,
        }
    )


# ─── Shared helpers ───────────────────────────────────────────────────────────


def _windows_only(tool_name: str) -> dict[str, str] | None:
    """Return an error dict when not running on Windows, else None."""
    if sys.platform != "win32":
        return {"error": "windows_only", "tool": tool_name, "platform": sys.platform}
    return None


def _coerce_bool(value: bool | str | None, default: bool = False) -> bool:
    """Coerce MCP boolean/string inputs to a Python bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return default


def _parse_reg_path(path: str) -> tuple[Any, str]:
    """Parse a PowerShell-style registry path into ``(hive, subkey)``.

    Supports both ``HKCU:\\Software\\...`` and ``HKCU:\\Software\\...``
    PowerShell alias formats.

    Raises:
        ValueError: When the hive prefix is unrecognised.
        ImportError: On non-Windows platforms.
    """
    import winreg  # type: ignore[import]  # noqa: PLC0415

    _HIVES = {
        "HKCU": winreg.HKEY_CURRENT_USER,
        "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
        "HKLM": winreg.HKEY_LOCAL_MACHINE,
        "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE,
        "HKCR": winreg.HKEY_CLASSES_ROOT,
        "HKEY_CLASSES_ROOT": winreg.HKEY_CLASSES_ROOT,
        "HKU": winreg.HKEY_USERS,
        "HKEY_USERS": winreg.HKEY_USERS,
        "HKCC": winreg.HKEY_CURRENT_CONFIG,
        "HKEY_CURRENT_CONFIG": winreg.HKEY_CURRENT_CONFIG,
    }

    # Normalise: strip leading/trailing whitespace and trailing backslash.
    path = path.strip().rstrip("\\")

    # Split on the first ':' or '\' separator.
    for sep in (":\\", "\\"):
        if "\\" in path:
            prefix, rest = path.split("\\", 1)
            prefix = prefix.rstrip(":").upper()
            break
    else:
        prefix = path.rstrip(":").upper()
        rest = ""

    if prefix not in _HIVES:
        raise ValueError(
            f"Unrecognised registry hive {prefix!r}. Expected one of: {', '.join(sorted(_HIVES))}."
        )
    return _HIVES[prefix], rest


def _reg_type_constant(reg_type: str) -> int:
    """Map a type name string to a ``winreg.REG_*`` constant."""
    import winreg  # type: ignore[import]  # noqa: PLC0415

    _MAP = {
        "String": winreg.REG_SZ,
        "DWord": winreg.REG_DWORD,
        "QWord": winreg.REG_QWORD,
        "Binary": winreg.REG_BINARY,
        "MultiString": winreg.REG_MULTI_SZ,
        "ExpandString": winreg.REG_EXPAND_SZ,
    }
    if reg_type not in _MAP:
        raise ValueError(f"Unknown registry type {reg_type!r}. Expected one of: {', '.join(_MAP)}.")
    return _MAP[reg_type]


# ─── Process tools ────────────────────────────────────────────────────────────


def _tool_process_list(server: Any, args: dict[str, Any]) -> Any:
    err = _windows_only("desktop_process_list")
    if err:
        return err

    if psutil is None:
        return {"error": "psutil_missing", "hint": "Run: pip install psutil"}

    name_filter = (args.get("name_filter") or "").strip().lower()
    sort_by = args.get("sort_by", "memory")
    limit = int(args.get("limit", 25))

    procs: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info", "status"]):
        try:
            info = proc.info
            pname: str = (info.get("name") or "").strip()
            if name_filter and name_filter not in pname.lower():
                continue
            mem_mb = round((info.get("memory_info") or proc.memory_info()).rss / 1_048_576, 2)
            procs.append(
                {
                    "pid": info["pid"],
                    "name": pname,
                    "cpu_percent": info.get("cpu_percent", 0.0) or 0.0,
                    "memory_mb": mem_mb,
                    "status": info.get("status", ""),
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if sort_by == "memory":
        procs.sort(key=lambda p: p["memory_mb"], reverse=True)
    elif sort_by == "cpu":
        procs.sort(key=lambda p: p["cpu_percent"], reverse=True)
    else:
        procs.sort(key=lambda p: p["name"].lower())

    return {
        "processes": procs[:limit],
        "total_shown": min(len(procs), limit),
        "total_found": len(procs),
    }


def _tool_process_kill(server: Any, args: dict[str, Any]) -> Any:
    err = _windows_only("desktop_process_kill")
    if err:
        return err

    pid: int | None = args.get("pid")
    name: str | None = args.get("name")
    force = _coerce_bool(args.get("force"), default=False)

    if pid is None and not name:
        return {"error": "pid or name is required"}

    if psutil is None:
        return {"error": "psutil_missing", "hint": "Run: pip install psutil"}

    try:
        if pid is not None:
            if not check_pid_exists(pid):
                return {"error": f"No process found with PID {pid}"}
            proc = psutil.Process(pid)
        else:
            # Find first process matching the name.
            matched = [
                p
                for p in psutil.process_iter(["pid", "name"])
                if (p.info.get("name") or "").lower() == (name or "").lower()
            ]
            if not matched:
                return {"error": f"No process found with name {name!r}"}
            proc = matched[0]

        if force:
            proc.kill()
            action = "killed"
        else:
            proc.terminate()
            action = "terminated"

        return {"success": True, "pid": proc.pid, "name": proc.name(), "action": action}

    except psutil.NoSuchProcess:
        return {"error": f"Process no longer exists"}
    except psutil.AccessDenied as exc:
        return {"error": f"Access denied: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


# ─── Registry tools ───────────────────────────────────────────────────────────


def _tool_registry_get(server: Any, args: dict[str, Any]) -> Any:
    err = _windows_only("desktop_registry_get")
    if err:
        return err

    path = args.get("path", "")
    name = args.get("name", "")

    try:
        import winreg  # type: ignore[import]  # noqa: PLC0415

        hive, subkey = _parse_reg_path(path)
        with winreg.OpenKey(hive, subkey, access=winreg.KEY_READ) as key:
            data, reg_type = winreg.QueryValueEx(key, name)
        return {"path": path, "name": name, "value": data, "type": reg_type}
    except FileNotFoundError:
        return {"error": f"Registry value not found: {path}\\{name}"}
    except PermissionError:
        return {"error": f"Access denied to registry path: {path}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _tool_registry_set(server: Any, args: dict[str, Any]) -> Any:
    err = _windows_only("desktop_registry_set")
    if err:
        return err

    path = args.get("path", "")
    name = args.get("name", "")
    value_str = args.get("value", "")
    reg_type_name = args.get("reg_type", "String")

    try:
        import winreg  # type: ignore[import]  # noqa: PLC0415

        hive, subkey = _parse_reg_path(path)
        reg_type = _reg_type_constant(reg_type_name)

        # Coerce string value to the appropriate Python type.
        if reg_type == winreg.REG_DWORD:
            native_value: Any = int(value_str)
        elif reg_type == winreg.REG_QWORD:
            native_value = int(value_str)
        elif reg_type == winreg.REG_BINARY:
            native_value = bytes.fromhex(value_str)
        elif reg_type == winreg.REG_MULTI_SZ:
            native_value = value_str.split("\n")
        else:
            native_value = value_str

        with winreg.CreateKeyEx(hive, subkey, access=winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, reg_type, native_value)

        return {"success": True, "path": path, "name": name, "type": reg_type_name}
    except PermissionError:
        return {"error": f"Access denied to registry path: {path}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _tool_registry_delete(server: Any, args: dict[str, Any]) -> Any:
    err = _windows_only("desktop_registry_delete")
    if err:
        return err

    path = args.get("path", "")
    name: str | None = args.get("name")

    try:
        import winreg  # type: ignore[import]  # noqa: PLC0415

        hive, subkey = _parse_reg_path(path)
        if name is not None:
            with winreg.OpenKey(hive, subkey, access=winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
            return {"success": True, "deleted": "value", "path": path, "name": name}
        else:
            # Delete the key itself (must be empty).
            winreg.DeleteKey(hive, subkey)
            return {"success": True, "deleted": "key", "path": path}
    except FileNotFoundError:
        return {"error": f"Registry path not found: {path}"}
    except PermissionError:
        return {"error": f"Access denied to registry path: {path}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _tool_registry_list(server: Any, args: dict[str, Any]) -> Any:
    err = _windows_only("desktop_registry_list")
    if err:
        return err

    path = args.get("path", "")

    try:
        import winreg  # type: ignore[import]  # noqa: PLC0415

        hive, subkey = _parse_reg_path(path)
        with winreg.OpenKey(hive, subkey, access=winreg.KEY_READ) as key:
            num_subkeys, num_values, _ = winreg.QueryInfoKey(key)

            subkeys: list[str] = []
            for i in range(num_subkeys):
                try:
                    subkeys.append(winreg.EnumKey(key, i))
                except OSError:
                    break

            values: list[dict[str, Any]] = []
            for i in range(num_values):
                try:
                    vname, vdata, vtype = winreg.EnumValue(key, i)
                    values.append({"name": vname, "value": vdata, "type": vtype})
                except OSError:
                    break

        return {"path": path, "subkeys": subkeys, "values": values}
    except FileNotFoundError:
        return {"error": f"Registry key not found: {path}"}
    except PermissionError:
        return {"error": f"Access denied to registry path: {path}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


# ─── Notification tool ────────────────────────────────────────────────────────


def _tool_notify(server: Any, args: dict[str, Any]) -> Any:
    err = _windows_only("desktop_notify")
    if err:
        return err

    title = args.get("title", "")
    message = args.get("message", "")
    app_id = args.get("app_id", "navig")

    try:
        return _send_toast(title, message, app_id)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Notification failed: {exc}"}


def _send_toast(title: str, message: str, app_id: str) -> dict[str, Any]:
    """Send a Windows toast notification via the best available method."""
    # Try win10toast if installed.
    try:
        from win10toast import ToastNotifier  # type: ignore[import]  # noqa: PLC0415

        toaster = ToastNotifier()
        toaster.show_toast(title, message, app_id=app_id, threaded=True, duration=5)
        return {"success": True, "method": "win10toast"}
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001
        # Fall through to PowerShell method.
        import logging  # noqa: PLC0415

        logging.getLogger(__name__).debug("win10toast failed: %s", exc)

    # Fallback: PowerShell WinRT toast.
    ps_title = ps_quote_for_xml(title)
    ps_message = ps_quote_for_xml(message)
    ps_app_id = ps_quote_for_xml(app_id)

    script = f"""
$app = {ps_app_id}
$xml = [Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom,ContentType=WindowsRuntime]::new()
$xml.LoadXml('<toast><visual><binding template="ToastGeneric"><text>{{}}</text><text>{{}}</text></binding></visual></toast>')
$xml.GetElementsByTagName('text')[0].InnerText = {ps_title}
$xml.GetElementsByTagName('text')[1].InnerText = {ps_message}
$toast = [Windows.UI.Notifications.ToastNotification,Windows.UI.Notifications,ContentType=WindowsRuntime]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]::CreateToastNotifier($app).Show($toast)
"""
    result = PowerShellExecutor.execute_command(script.strip(), timeout=10.0)
    if result.returncode != 0:
        return {"error": f"PowerShell toast failed: {result.stderr.strip()}"}
    return {"success": True, "method": "powershell"}


# ── PowerShell tool ───────────────────────────────────────────────────────────


def _tool_powershell(server: Any, args: dict[str, Any]) -> Any:
    err = _windows_only("desktop_powershell")
    if err:
        return err

    if PowerShellExecutor is None:
        return {"error": "PowerShellExecutor unavailable on this platform"}

    command: str = args.get("command", "")
    timeout: float = float(args.get("timeout", 30))

    if not command.strip():
        return {"error": "command must not be empty"}

    try:
        result = PowerShellExecutor.execute_command(command, timeout=timeout)
        return {
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
            "returncode": result.returncode,
            "success": result.returncode == 0,
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


# ── Clipboard tools ───────────────────────────────────────────────────────────


def _tool_clipboard_get(server: Any, args: dict[str, Any]) -> Any:
    err = _windows_only("desktop_clipboard_get")
    if err:
        return err

    # Primary: pywin32
    try:
        import win32clipboard  # type: ignore[import]  # noqa: PLC0415

        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                text = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                return {"text": text}
            return {"text": "", "note": "Clipboard does not contain text data"}
        finally:
            win32clipboard.CloseClipboard()
    except ImportError:
        pass  # fall through to PowerShell fallback

    # Fallback: PowerShell Get-Clipboard
    if PowerShellExecutor is None:
        return {"error": "Neither win32clipboard nor PowerShell is available"}

    try:
        result = PowerShellExecutor.execute_command("Get-Clipboard", timeout=5.0)
        if result.returncode != 0:
            return {"error": f"Get-Clipboard failed: {result.stderr.strip()}"}
        return {"text": (result.stdout or "").rstrip("\r\n")}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _tool_clipboard_set(server: Any, args: dict[str, Any]) -> Any:
    err = _windows_only("desktop_clipboard_set")
    if err:
        return err

    text: str = args.get("text", "")

    # Primary: pywin32
    try:
        import win32clipboard  # type: ignore[import]  # noqa: PLC0415

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()
        return {"success": True, "method": "win32clipboard"}
    except ImportError:
        pass  # fall through to PowerShell fallback

    # Fallback: PowerShell Set-Clipboard
    if PowerShellExecutor is None:
        return {"error": "Neither win32clipboard nor PowerShell is available"}

    try:
        # Use here-string to safely pass arbitrary text
        escaped = text.replace("'", "''")
        result = PowerShellExecutor.execute_command(
            f"Set-Clipboard -Value '{escaped}'",
            timeout=5.0,
        )
        if result.returncode != 0:
            return {"error": f"Set-Clipboard failed: {result.stderr.strip()}"}
        return {"success": True, "method": "powershell"}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
