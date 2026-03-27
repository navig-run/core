import os
import sys
from pathlib import Path
from typing import Any


def register(server: Any) -> None:
    """Register desktop automation tools."""
    server.tools.update(
        {
            "desktop_find": {
                "name": "desktop_find",
                "description": "Search the Windows UI Automation element tree for elements matching name, class_name, and/or control_type.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Filter by element Name property",
                        },
                        "class_name": {
                            "type": "string",
                            "description": "Filter by ClassName property",
                        },
                        "control_type": {
                            "type": "string",
                            "description": "Filter by control type name (e.g. Button, Edit)",
                        },
                        "depth": {
                            "type": "integer",
                            "description": "Search depth (default 5)",
                            "default": 5,
                        },
                    },
                    "required": [],
                },
            },
            "desktop_tree": {
                "name": "desktop_tree",
                "description": "Dump the Windows UI element tree to the specified depth.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "depth": {
                            "type": "integer",
                            "description": "Tree depth (default 3)",
                            "default": 3,
                        }
                    },
                    "required": [],
                },
            },
            "desktop_click": {
                "name": "desktop_click",
                "description": "Click a Windows UI element by its native window handle. Requires desktop_permission in the active mission step metadata.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "handle": {
                            "type": "integer",
                            "description": "Native window handle of the element to click",
                        }
                    },
                    "required": ["handle"],
                },
            },
            "desktop_set_value": {
                "name": "desktop_set_value",
                "description": "Set the value of a Windows UI element by handle. Requires desktop_permission in active mission step.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "handle": {
                            "type": "integer",
                            "description": "Native window handle",
                        },
                        "value": {
                            "type": "string",
                            "description": "Value to set on the element",
                        },
                    },
                    "required": ["handle", "value"],
                },
            },
            "desktop_ahk": {
                "name": "desktop_ahk",
                "description": "Execute an AutoHotkey v2 script via AutoHotkey.exe. Requires desktop_permission in active mission step.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "script": {
                            "type": "string",
                            "description": "AutoHotkey v2 script text to execute",
                        }
                    },
                    "required": ["script"],
                },
            },
        }
    )

    server._tool_handlers.update(
        {
            "desktop_find": _tool_desktop_find,
            "desktop_tree": _tool_desktop_tree,
            "desktop_click": _tool_desktop_click,
            "desktop_set_value": _tool_desktop_set_value,
            "desktop_ahk": _tool_desktop_ahk,
        }
    )


def _desktop_client():
    """Return a live _DesktopClient, raising structured errors on failure."""
    if sys.platform != "win32":
        raise ValueError("desktop tools are Windows only")
    from navig.commands.desktop import _DesktopClient

    return _DesktopClient()


def _desktop_permission_check(tool_name: str) -> dict[str, Any] | None:
    """Return a structured error dict if no desktop_permission in active mission step, else None."""
    # Retrieve active mission step from runtime store if available.
    try:
        from navig.contracts.store import get_runtime_store

        store = get_runtime_store()
        missions = store.list_missions(status=None, limit=1)
        if missions:
            step_meta = missions[0].payload.get("step_metadata", {}) if missions[0].payload else {}
            if step_meta.get("desktop_permission") is True:
                return None  # permission granted
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical
    return {
        "error": "permission_denied",
        "reason": "active mission step does not have desktop_permission: true",
        "tool": tool_name,
    }


def _desktop_audit_initialized() -> dict[str, Any] | None:
    """Return a structured error dict if the audit log path is not configured, else None."""
    audit_path = os.environ.get("NAVIG_DESKTOP_AUDIT_LOG", "")
    if not audit_path:
        audit_path = str(Path.home() / ".navig" / "logs" / "desktop_audit.jsonl")
    # Probe: ensure the file can be opened for append.
    try:
        Path(audit_path).parent.mkdir(parents=True, exist_ok=True)
        with open(audit_path, "a", encoding="utf-8"):
            pass
    except Exception as exc:
        return {
            "error": "audit_log_unavailable",
            "reason": str(exc),
            "hint": "Set NAVIG_DESKTOP_AUDIT_LOG to a writable path.",
        }
    return None


def _tool_desktop_find(server: Any, args: dict[str, Any]) -> Any:
    """Find Windows UI elements matching the given criteria."""
    audit_err = _desktop_audit_initialized()
    if audit_err:
        return audit_err
    try:
        client = _desktop_client()
        try:
            return client.find_element(
                name=args.get("name"),
                class_name=args.get("class_name"),
                control_type=args.get("control_type"),
                depth=int(args.get("depth", 5)),
            )
        finally:
            client.close()
    except Exception as exc:
        return {"error": str(exc)}


def _tool_desktop_tree(server: Any, args: dict[str, Any]) -> Any:
    """Dump the Windows UI element tree."""
    audit_err = _desktop_audit_initialized()
    if audit_err:
        return audit_err
    try:
        client = _desktop_client()
        try:
            return client.get_window_tree(depth=int(args.get("depth", 3)))
        finally:
            client.close()
    except Exception as exc:
        return {"error": str(exc)}


def _tool_desktop_click(server: Any, args: dict[str, Any]) -> Any:
    """Click a Windows UI element. Requires desktop_permission."""
    audit_err = _desktop_audit_initialized()
    if audit_err:
        return audit_err
    perm_err = _desktop_permission_check("desktop_click")
    if perm_err:
        return perm_err
    handle = args.get("handle")
    if handle is None:
        return {"error": "handle is required"}
    try:
        client = _desktop_client()
        try:
            return client.click(int(handle))
        finally:
            client.close()
    except Exception as exc:
        return {"error": str(exc)}


def _tool_desktop_set_value(server: Any, args: dict[str, Any]) -> Any:
    """Set the value of a Windows UI element by handle. Requires desktop_permission."""
    audit_err = _desktop_audit_initialized()
    if audit_err:
        return audit_err
    perm_err = _desktop_permission_check("desktop_set_value")
    if perm_err:
        return perm_err
    handle = args.get("handle")
    value = args.get("value", "")
    if handle is None:
        return {"error": "handle is required"}
    try:
        client = _desktop_client()
        try:
            return client.set_value(int(handle), str(value))
        finally:
            client.close()
    except Exception as exc:
        return {"error": str(exc)}


def _tool_desktop_ahk(server: Any, args: dict[str, Any]) -> Any:
    """Execute an AHK script via AutoHotkey.exe. Requires desktop_permission."""
    audit_err = _desktop_audit_initialized()
    if audit_err:
        return audit_err
    perm_err = _desktop_permission_check("desktop_ahk")
    if perm_err:
        return perm_err
    script = args.get("script", "")
    if not script:
        return {"error": "script is required"}
    try:
        client = _desktop_client()
        try:
            return client.ahk_run(str(script))
        finally:
            client.close()
    except Exception as exc:
        return {"error": str(exc)}
