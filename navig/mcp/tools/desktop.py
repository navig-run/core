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
            cursor_raw = client.get_cursor_position() if hasattr(client, "get_cursor_position") else None
            interactive_raw = None
            if use_ui_tree:
                tree = client.get_window_tree(depth=4) if hasattr(client, "get_window_tree") else None
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
            cursor_raw = client.get_cursor_position() if hasattr(client, "get_cursor_position") else None
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
