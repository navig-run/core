import base64
import io
import os
import sys
import time
from pathlib import Path
from textwrap import dedent
from typing import Any

from navig.platform.paths import config_dir

# ─── Profile helper (single definition) ──────────────────────────────────────
_PROFILE_ENV_KEY = "NAVIG_PROFILE_SNAPSHOT"


def _snapshot_profile_enabled() -> bool:
    """Return True when snapshot profiling is requested via env var."""
    return os.environ.get(_PROFILE_ENV_KEY, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


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
            "desktop_snapshot": _tool_desktop_snapshot,
            "desktop_screenshot": _tool_desktop_screenshot,
        }
    )

    # ── Input / interaction tool schemas ────────────────────────────────────
    server.tools.update(
        {
            "desktop_type": {
                "name": "desktop_type",
                "description": (
                    "Type text at the current cursor position (or optionally click "
                    "coordinates first). Supports clearing and pressing Enter. "
                    "Requires desktop_permission in the active mission step."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Text to type."},
                        "x": {
                            "type": "integer",
                            "description": "X coordinate to click before typing (optional).",
                        },
                        "y": {
                            "type": "integer",
                            "description": "Y coordinate to click before typing (optional).",
                        },
                        "clear": {
                            "type": ["boolean", "string"],
                            "default": False,
                            "description": "Select-all then delete before typing.",
                        },
                        "press_enter": {
                            "type": ["boolean", "string"],
                            "default": False,
                            "description": "Press Enter after typing.",
                        },
                    },
                    "required": ["text"],
                },
            },
            "desktop_scroll": {
                "name": "desktop_scroll",
                "description": (
                    "Scroll at the specified screen coordinates. "
                    "Requires desktop_permission in the active mission step."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer", "description": "X coordinate to scroll at."},
                        "y": {"type": "integer", "description": "Y coordinate to scroll at."},
                        "direction": {
                            "type": "string",
                            "enum": ["up", "down", "left", "right"],
                            "default": "down",
                            "description": "Scroll direction.",
                        },
                        "amount": {
                            "type": "integer",
                            "default": 3,
                            "description": "Number of wheel detents to scroll.",
                        },
                    },
                    "required": ["x", "y"],
                },
            },
            "desktop_move": {
                "name": "desktop_move",
                "description": (
                    "Move the mouse to coordinates, with optional drag from a start position. "
                    "Requires desktop_permission in the active mission step."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer", "description": "Destination X coordinate."},
                        "y": {"type": "integer", "description": "Destination Y coordinate."},
                        "from_x": {
                            "type": "integer",
                            "description": "Drag start X (omit for plain move).",
                        },
                        "from_y": {
                            "type": "integer",
                            "description": "Drag start Y (omit for plain move).",
                        },
                    },
                    "required": ["x", "y"],
                },
            },
            "desktop_shortcut": {
                "name": "desktop_shortcut",
                "description": (
                    "Send a keyboard shortcut (e.g. 'Ctrl+C', 'Alt+F4', 'Win+D'). "
                    "Uses AutoHotkey Send syntax. "
                    "Requires desktop_permission in the active mission step."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "keys": {
                            "type": "string",
                            "description": (
                                "Shortcut string in AHK v2 Send notation. "
                                "Examples: '^c' (Ctrl+C), '!{F4}' (Alt+F4), '#d' (Win+D), "
                                "'{Enter}', '{Tab}', '+{Tab}'."
                            ),
                        }
                    },
                    "required": ["keys"],
                },
            },
            "desktop_app": {
                "name": "desktop_app",
                "description": (
                    "Launch, resize, or bring a Windows application to the foreground. "
                    "Requires desktop_permission in the active mission step."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "mode": {
                            "type": "string",
                            "enum": ["launch", "switch", "resize"],
                            "description": "Operation: 'launch' opens an app, 'switch' activates an existing window, 'resize' moves/resizes a window.",
                        },
                        "name": {
                            "type": "string",
                            "description": "App path (launch), window title substring (switch/resize).",
                        },
                        "x": {"type": "integer", "description": "Window X position (resize mode)."},
                        "y": {"type": "integer", "description": "Window Y position (resize mode)."},
                        "width": {
                            "type": "integer",
                            "description": "Window width in pixels (resize mode).",
                        },
                        "height": {
                            "type": "integer",
                            "description": "Window height in pixels (resize mode).",
                        },
                    },
                    "required": ["mode", "name"],
                },
            },
            "desktop_multi_select": {
                "name": "desktop_multi_select",
                "description": (
                    "Click multiple screen coordinates, holding Ctrl between clicks to "
                    "multi-select items (files, checkboxes, list entries). "
                    "Requires desktop_permission in the active mission step."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "locations": {
                            "type": "array",
                            "items": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "minItems": 2,
                                "maxItems": 2,
                            },
                            "description": "List of [x, y] coordinate pairs to click.",
                        },
                        "hold_ctrl": {
                            "type": ["boolean", "string"],
                            "default": True,
                            "description": "Hold Ctrl during clicks for multi-selection.",
                        },
                    },
                    "required": ["locations"],
                },
            },
            "desktop_multi_edit": {
                "name": "desktop_multi_edit",
                "description": (
                    "Enter text into multiple input fields. Provide a list of [x, y, text] triples. "
                    "Requires desktop_permission in the active mission step."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "fields": {
                            "type": "array",
                            "items": {
                                "type": "array",
                                "description": "[x, y, text] triple — x and y are integer coordinates, text is the string to type.",
                                "items": {
                                    "oneOf": [
                                        {"type": "integer"},
                                        {"type": "number"},
                                        {"type": "string"},
                                    ]
                                },
                            },
                            "description": "List of [x, y, text] — click each coordinate and type the text.",
                        }
                    },
                    "required": ["fields"],
                },
            },
        }
    )

    server._tool_handlers.update(
        {
            "desktop_type": _tool_desktop_type,
            "desktop_scroll": _tool_desktop_scroll,
            "desktop_move": _tool_desktop_move,
            "desktop_shortcut": _tool_desktop_shortcut,
            "desktop_app": _tool_desktop_app,
            "desktop_multi_select": _tool_desktop_multi_select,
            "desktop_multi_edit": _tool_desktop_multi_edit,
        }
    )

    # ── Snapshot / Screenshot tool schemas ──────────────────────────────────
    server.tools.update(
        {
            "desktop_snapshot": {
                "name": "desktop_snapshot",
                "description": (
                    "Capture the current desktop state: screenshot, cursor position, "
                    "open windows, and interactive UI elements. "
                    "Set use_vision=true to include a screenshot. "
                    "Set use_ui_tree=false for a faster vision-only pass. "
                    "Requires desktop_permission in the active mission step."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "use_vision": {
                            "type": "boolean",
                            "default": False,
                            "description": "Include a screenshot in the response.",
                        },
                        "use_ui_tree": {
                            "type": "boolean",
                            "default": True,
                            "description": "Extract interactive UI elements (slower but richer).",
                        },
                        "display": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "Limit to specific display indices (0-based). Omit for all screens.",
                        },
                    },
                    "required": [],
                },
            },
            "desktop_screenshot": {
                "name": "desktop_screenshot",
                "description": (
                    "Fast screenshot-only desktop snapshot. Skips UI element extraction. "
                    "Returns cursor position, window list, and a PNG image. "
                    "Use desktop_snapshot when you need interactive element coordinates. "
                    "Requires desktop_permission in the active mission step."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "display": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "Limit to specific display indices (0-based). Omit for all screens.",
                        },
                    },
                    "required": [],
                },
            },
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
        audit_path = str(config_dir() / "logs" / "desktop_audit.jsonl")
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


# ─── Snapshot / Screenshot helpers ───────────────────────────────────────────

_SCREENSHOT_SCALE_ENV = "NAVIG_SCREENSHOT_SCALE"
_MAX_IMAGE_WIDTH = 1920
_MAX_IMAGE_HEIGHT = 1080


def _screenshot_scale() -> float:
    """Read NAVIG_SCREENSHOT_SCALE from env; clamp to [0.1, 1.0]."""
    raw = os.environ.get(_SCREENSHOT_SCALE_ENV, "1.0")
    try:
        scale = float(raw)
    except ValueError:
        scale = 1.0
    return max(0.1, min(1.0, scale))


def _build_snapshot_text(
    windows: list[dict],
    cursor: tuple[int, int] | None,
    interactive: list[dict] | None,
    backend_name: str | None,
    include_ui: bool,
) -> str:
    """Build the text portion of a snapshot response."""
    lines: list[str] = []
    if cursor:
        lines.append(f"Cursor Position: {cursor[0]},{cursor[1]}")
    if backend_name:
        lines.append(f"Screenshot Backend: {backend_name}")
    lines.append("")
    lines.append("Open Windows:")
    if windows:
        for w in windows:
            title = w.get("title", "")
            pid = w.get("pid", "")
            lines.append(f"  [{pid}] {title}")
    else:
        lines.append("  (none)")

    if include_ui and interactive is not None:
        lines.append("")
        lines.append("Interactive Elements:")
        if interactive:
            for el in interactive:
                lines.append(f"  {el}")
        else:
            lines.append("  (none found)")

    return "\n".join(lines)


def _tool_desktop_snapshot(server: Any, args: dict[str, Any]) -> Any:
    """Capture desktop state with optional screenshot and UI tree."""
    audit_err = _desktop_audit_initialized()
    if audit_err:
        return audit_err
    perm_err = _desktop_permission_check("desktop_snapshot")
    if perm_err:
        return perm_err

    use_vision = bool(args.get("use_vision", False))
    use_ui_tree = bool(args.get("use_ui_tree", True))

    t0 = time.perf_counter() if _snapshot_profile_enabled() else None

    try:
        client = _desktop_client()
        try:
            windows_raw = client.get_window_list() if hasattr(client, "get_window_list") else []
            cursor_raw = (
                client.get_cursor_position() if hasattr(client, "get_cursor_position") else None
            )
            interactive_raw = None
            if use_ui_tree:
                tree = (
                    client.get_window_tree(depth=4) if hasattr(client, "get_window_tree") else None
                )
                if isinstance(tree, dict):
                    interactive_raw = tree.get("elements", [])
        finally:
            client.close()
    except Exception as exc:
        return {"error": f"Desktop capture failed: {exc}"}

    screenshot_b64: str | None = None
    backend_name: str | None = None
    if use_vision:
        try:
            import PIL.Image as Image  # type: ignore[import]  # noqa: PLC0415

            from navig.adapters.automation.screenshot import capture_full_screen  # noqa: PLC0415

            img, backend_name = capture_full_screen()
            scale = _screenshot_scale()
            if scale < 1.0:
                nw = max(1, int(img.width * scale))
                nh = max(1, int(img.height * scale))
                img = img.resize((nw, nh), Image.LANCZOS)
            if img.width > _MAX_IMAGE_WIDTH or img.height > _MAX_IMAGE_HEIGHT:
                img.thumbnail((_MAX_IMAGE_WIDTH, _MAX_IMAGE_HEIGHT), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            screenshot_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception as exc:  # noqa: BLE001
            import logging  # noqa: PLC0415

            logging.getLogger(__name__).debug("Screenshot failed: %s", exc)

    if t0 is not None:
        import logging  # noqa: PLC0415

        logging.getLogger(__name__).info(
            "desktop_snapshot total_ms=%.1f use_vision=%s use_ui_tree=%s",
            (time.perf_counter() - t0) * 1000,
            use_vision,
            use_ui_tree,
        )

    text = _build_snapshot_text(
        windows=windows_raw if isinstance(windows_raw, list) else [],
        cursor=cursor_raw,
        interactive=interactive_raw,
        backend_name=backend_name,
        include_ui=use_ui_tree,
    )

    result: dict[str, Any] = {"text": text}
    if screenshot_b64:
        result["screenshot"] = {"format": "png", "data": screenshot_b64}
    return result


def _tool_desktop_screenshot(server: Any, args: dict[str, Any]) -> Any:
    """Fast screenshot-only desktop snapshot. Skips UI tree extraction."""
    audit_err = _desktop_audit_initialized()
    if audit_err:
        return audit_err
    perm_err = _desktop_permission_check("desktop_screenshot")
    if perm_err:
        return perm_err

    t0 = time.perf_counter() if _snapshot_profile_enabled() else None

    try:
        client = _desktop_client()
        try:
            windows_raw = client.get_window_list() if hasattr(client, "get_window_list") else []
            cursor_raw = (
                client.get_cursor_position() if hasattr(client, "get_cursor_position") else None
            )
        finally:
            client.close()
    except Exception as exc:
        return {"error": f"Desktop state failed: {exc}"}

    screenshot_b64: str | None = None
    backend_name: str | None = None
    try:
        import PIL.Image as Image  # type: ignore[import]  # noqa: PLC0415

        from navig.adapters.automation.screenshot import capture_full_screen  # noqa: PLC0415

        img, backend_name = capture_full_screen()
        scale = _screenshot_scale()
        if scale < 1.0:
            nw = max(1, int(img.width * scale))
            nh = max(1, int(img.height * scale))
            img = img.resize((nw, nh), Image.LANCZOS)
        if img.width > _MAX_IMAGE_WIDTH or img.height > _MAX_IMAGE_HEIGHT:
            img.thumbnail((_MAX_IMAGE_WIDTH, _MAX_IMAGE_HEIGHT), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        screenshot_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as exc:  # noqa: BLE001
        import logging  # noqa: PLC0415

        logging.getLogger(__name__).debug("Screenshot failed: %s", exc)

    if t0 is not None:
        import logging  # noqa: PLC0415

        logging.getLogger(__name__).info(
            "desktop_screenshot total_ms=%.1f", (time.perf_counter() - t0) * 1000
        )

    text = _build_snapshot_text(
        windows=windows_raw if isinstance(windows_raw, list) else [],
        cursor=cursor_raw,
        interactive=None,
        backend_name=backend_name,
        include_ui=False,
    )
    result: dict[str, Any] = {"text": text}
    if screenshot_b64:
        result["screenshot"] = {"format": "png", "data": screenshot_b64}
    return result


# ─── Input / interaction helpers ─────────────────────────────────────────────


def _coerce_bool(value: bool | str | None, default: bool = False) -> bool:
    """Coerce MCP boolean/string inputs to a Python bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return default


def _run_ahk(script: str, tool_name: str) -> Any:
    """Run an AHK script with the standard permission + audit gates."""
    audit_err = _desktop_audit_initialized()
    if audit_err:
        return audit_err
    perm_err = _desktop_permission_check(tool_name)
    if perm_err:
        return perm_err
    try:
        client = _desktop_client()
        try:
            return client.ahk_run(script)
        finally:
            client.close()
    except Exception as exc:
        return {"error": str(exc)}


def _tool_desktop_type(server: Any, args: dict[str, Any]) -> Any:
    """Type text at the current cursor position or at optional (x, y) coordinates."""
    text: str = args.get("text", "")
    x: int | None = args.get("x")
    y: int | None = args.get("y")
    clear = _coerce_bool(args.get("clear"), default=False)
    press_enter = _coerce_bool(args.get("press_enter"), default=False)

    # Escape AHK special characters for SendRaw/SendText-like usage.
    # We use SendText which treats the string literally.
    lines: list[str] = []
    if x is not None and y is not None:
        lines.append(f"Click {x}, {y}")
        lines.append("Sleep 50")
    if clear:
        lines.append("Send '^a'")
        lines.append("Sleep 30")
        lines.append("Send '{Delete}'")
        lines.append("Sleep 30")
    # SendText sends the literal string (no AHK special chars interpreted).
    escaped = text.replace("`", "``").replace("'", "''")
    lines.append(f"SendText '{escaped}'")
    if press_enter:
        lines.append("Send '{Enter}'")

    return _run_ahk("\n".join(lines), "desktop_type")


def _tool_desktop_scroll(server: Any, args: dict[str, Any]) -> Any:
    """Scroll at (x, y) in the given direction."""
    x: int = int(args.get("x", 0))
    y: int = int(args.get("y", 0))
    direction: str = args.get("direction", "down").lower()
    amount: int = max(1, int(args.get("amount", 3)))

    _DIR_MAP = {
        "up": "WheelUp",
        "down": "WheelDown",
        "left": "WheelLeft",
        "right": "WheelRight",
    }
    wheel = _DIR_MAP.get(direction, "WheelDown")
    script = f"Click {x}, {y}, 0, {wheel}, {amount}"
    return _run_ahk(script, "desktop_scroll")


def _tool_desktop_move(server: Any, args: dict[str, Any]) -> Any:
    """Move the mouse to (x, y), optionally dragging from (from_x, from_y)."""
    x: int = int(args.get("x", 0))
    y: int = int(args.get("y", 0))
    from_x: int | None = args.get("from_x")
    from_y: int | None = args.get("from_y")

    lines: list[str] = []
    if from_x is not None and from_y is not None:
        lines.append(f"MouseMove {from_x}, {from_y}")
        lines.append("Sleep 30")
        lines.append("MouseClickDrag 'Left', " + f"{from_x}, {from_y}, {x}, {y}")
    else:
        lines.append(f"MouseMove {x}, {y}")

    return _run_ahk("\n".join(lines), "desktop_move")


def _tool_desktop_shortcut(server: Any, args: dict[str, Any]) -> Any:
    """Send a keyboard shortcut using AHK Send notation."""
    keys: str = args.get("keys", "")
    if not keys:
        audit_err = _desktop_audit_initialized()
        if audit_err:
            return audit_err
        return {"error": "keys must not be empty"}
    # Pass directly to Send — caller is responsible for correct AHK v2 syntax.
    script = f"Send '{keys}'"
    return _run_ahk(script, "desktop_shortcut")


def _tool_desktop_app(server: Any, args: dict[str, Any]) -> Any:
    """Launch, switch to, or resize an application window."""
    mode: str = args.get("mode", "").lower()
    name: str = args.get("name", "")

    if not name:
        audit_err = _desktop_audit_initialized()
        if audit_err:
            return audit_err
        return {"error": "name is required"}

    if mode == "launch":
        script = f'Run "{name}"'
    elif mode == "switch":
        script = f'WinActivate "ahk_exe {name}" 2>'
        # Fallback to title match
        script = (
            f'try {{\n    WinActivate "{name}"\n}} catch {{\n    WinActivate "ahk_exe {name}"\n}}'
        )
    elif mode == "resize":
        x: int = int(args.get("x", 0))
        y: int = int(args.get("y", 0))
        width: int = int(args.get("width", 800))
        height: int = int(args.get("height", 600))
        script = f'WinActivate "{name}"\nWinMove {x}, {y}, {width}, {height}, "{name}"'
    else:
        audit_err = _desktop_audit_initialized()
        if audit_err:
            return audit_err
        return {"error": f'Unknown mode "{mode}". Use: launch, switch, resize'}

    return _run_ahk(script, "desktop_app")


def _tool_desktop_multi_select(server: Any, args: dict[str, Any]) -> Any:
    """Click multiple coordinates while holding Ctrl for multi-selection."""
    locations: list[list[int]] = args.get("locations", [])
    hold_ctrl = _coerce_bool(args.get("hold_ctrl"), default=True)

    if not locations:
        audit_err = _desktop_audit_initialized()
        if audit_err:
            return audit_err
        return {"error": "locations must not be empty"}

    lines: list[str] = []
    if hold_ctrl:
        lines.append("Send '{Ctrl down}'")
        lines.append("Sleep 30")
    for loc in locations:
        if len(loc) < 2:
            continue
        lines.append(f"Click {loc[0]}, {loc[1]}")
        lines.append("Sleep 50")
    if hold_ctrl:
        lines.append("Send '{Ctrl up}'")

    return _run_ahk("\n".join(lines), "desktop_multi_select")


def _tool_desktop_multi_edit(server: Any, args: dict[str, Any]) -> Any:
    """Enter text into multiple fields: list of [x, y, text] triples."""
    fields: list[list] = args.get("fields", [])

    if not fields:
        audit_err = _desktop_audit_initialized()
        if audit_err:
            return audit_err
        return {"error": "fields must not be empty"}

    lines: list[str] = []
    for item in fields:
        if len(item) < 3:
            continue
        fx, fy, ft = int(item[0]), int(item[1]), str(item[2])
        lines.append(f"Click {fx}, {fy}")
        lines.append("Sleep 50")
        lines.append("Send '^a'")
        lines.append("Sleep 20")
        escaped = ft.replace("`", "``").replace("'", "''")
        lines.append(f"SendText '{escaped}'")
        lines.append("Sleep 30")

    return _run_ahk("\n".join(lines), "desktop_multi_edit")
