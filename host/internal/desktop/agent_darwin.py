# navig-core/host/internal/desktop/agent_darwin.py
"""NAVIG Desktop Agent — macOS (PyObjC Accessibility API + osascript) sidecar.

Implements UI Automation for macOS using the Accessibility framework via PyObjC
and AppleScript execution. Same JSON-RPC protocol as agent.py (Windows).

Request shape:  {"id": <int>, "method": "<name>", "params": {...}}
Response shape: {"id": <int>, "result": <any>}   (success)
               {"id": <int>, "error": "<string>"}  (failure)

Methods:
    ping            — health check
    find_element    — search AX tree by name/role
    click           — click element by AX handle ref
    set_value       — set value on a text field
    get_window_tree — dump recursive AX tree
    run_script      — execute an AppleScript (replaces ahk_run on macOS)
    get_action_tree — numbered Markdown tree optimized for LLM consumption

Dependencies (install on macOS):
    pip install pyobjc-framework-ApplicationServices pyobjc-framework-Cocoa
    # macOS 10.9+ Accessibility must be granted in System Preferences → Security & Privacy → Accessibility
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import uuid
from typing import Any, Dict, List, Optional

# ─────────────────────────── OS guard ────────────────────────────────────────

if platform.system() != "Darwin":
    sys.stderr.write("error: navig-desktop-agent-darwin is macOS only\n")
    sys.exit(1)

# ───────────────────────── PyObjC / AX import ────────────────────────────────

try:
    from ApplicationServices import (  # type: ignore
        AXUIElementCreateSystemWide,
        AXUIElementCreateApplication,
        AXUIElementCopyAttributeValue,
        AXUIElementPerformAction,
        AXUIElementSetAttributeValue,
        kAXFocusedUIElementAttribute,
        kAXValueAttribute,
        kAXRoleAttribute,
        kAXTitleAttribute,
        kAXChildrenAttribute,
        kAXPositionAttribute,
        kAXSizeAttribute,
        kAXPressAction,
        kAXFocusedAttribute,
        AXObserver,
    )
    from CoreFoundation import (  # type: ignore
        CFStringCreateWithCString,
        kCFStringEncodingUTF8,
    )
    _has_ax = True
except ImportError:
    _has_ax = False

try:
    from Quartz import CGEventCreateMouseEvent, CGEventPost, kCGEventLeftMouseDown, kCGEventLeftMouseUp, kCGHIDEventTap, CGPoint  # type: ignore
    _has_quartz = True
except ImportError:
    _has_quartz = False

# ─────────────────────────── type aliases ────────────────────────────────────

_Params = Dict[str, Any]
_Result = Any

# ─────────────────────────── handle registry ─────────────────────────────────

_handle_registry: Dict[str, Any] = {}  # handle_str → AXUIElement


def _register(elem: Any) -> str:
    handle = str(uuid.uuid4())[:8]
    _handle_registry[handle] = elem
    return handle


def _resolve(handle: str) -> Optional[Any]:
    return _handle_registry.get(handle)

# ─────────────────────────── AX helpers ──────────────────────────────────────


def _ax_attr(elem: Any, attr: str) -> Optional[Any]:
    """Read an AX attribute, returning None on failure."""
    try:
        err, val = AXUIElementCopyAttributeValue(elem, attr, None)
        if err == 0:
            return val
    except Exception:
        pass
    return None


def _ax_role(elem: Any) -> str:
    v = _ax_attr(elem, kAXRoleAttribute)
    return str(v) if v else ""


def _ax_title(elem: Any) -> str:
    v = _ax_attr(elem, kAXTitleAttribute)
    return str(v) if v else ""


def _ax_value(elem: Any) -> str:
    v = _ax_attr(elem, kAXValueAttribute)
    return str(v) if v else ""


def _ax_children(elem: Any) -> List[Any]:
    v = _ax_attr(elem, kAXChildrenAttribute)
    if v is None:
        return []
    try:
        return list(v)
    except Exception:
        return []


def _ax_bounds(elem: Any) -> Dict[str, int]:
    """Get bounding box of an AX element."""
    try:
        _, pos = AXUIElementCopyAttributeValue(elem, kAXPositionAttribute, None)
        _, size = AXUIElementCopyAttributeValue(elem, kAXSizeAttribute, None)
        x, y = int(pos.x), int(pos.y)
        w, h = int(size.width), int(size.height)
        return {"left": x, "top": y, "right": x + w, "bottom": y + h}
    except Exception:
        return {"left": 0, "top": 0, "right": 0, "bottom": 0}


def _elem_to_dict(elem: Any, register: bool = True) -> Dict[str, Any]:
    handle = _register(elem) if register else ""
    return {
        "handle": handle,
        "name": _ax_title(elem),
        "class_name": "",
        "control_type": _ax_role(elem),
        "rect": _ax_bounds(elem),
    }


def _walk_tree(elem: Any, depth: int, index: Optional[list] = None) -> Dict[str, Any]:
    if index is None:
        index = [0]
    node = _elem_to_dict(elem)
    node["index"] = index[0]
    index[0] += 1

    if depth > 0:
        children = []
        for child in _ax_children(elem):
            children.append(_walk_tree(child, depth - 1, index))
        node["children"] = children

    return node

# ─────────────────────────── method implementations ──────────────────────────


def _method_ping(_params: _Params) -> _Result:
    return {"ok": True, "platform": "darwin", "ax": _has_ax, "quartz": _has_quartz}


def _method_find_element(params: _Params) -> _Result:
    if not _has_ax:
        raise RuntimeError("pyobjc-framework-ApplicationServices not installed")

    name: Optional[str] = params.get("name")
    role_filter: Optional[str] = params.get("control_type")
    depth: int = int(params.get("depth", 5))

    system = AXUIElementCreateSystemWide()
    results: List[Dict[str, Any]] = []

    def _visit(elem: Any, current_depth: int) -> None:
        if current_depth < 0:
            return
        try:
            role = _ax_role(elem)
            title = _ax_title(elem)
            match = True
            if name and title != name:
                match = False
            if match and role_filter and role.lower() != role_filter.lower():
                match = False
            if match and (name or role_filter):
                results.append(_elem_to_dict(elem))
        except Exception:
            pass

        for child in _ax_children(elem):
            _visit(child, current_depth - 1)

    _visit(system, depth)
    return results


def _method_click(params: _Params) -> _Result:
    handle: str = str(params.get("handle", ""))
    if not handle:
        raise ValueError("handle must be a non-empty string")

    elem = _resolve(handle)
    if elem is None:
        raise ValueError(f"no element found for handle '{handle}'")

    # Try AX press action first
    try:
        err = AXUIElementPerformAction(elem, kAXPressAction)
        if err == 0:
            return {"clicked": True, "method": "ax_press"}
    except Exception:
        pass

    # Fallback: CGEventPost synthetic mouse click at element center
    if _has_quartz:
        rect = _ax_bounds(elem)
        cx = (rect["left"] + rect["right"]) / 2
        cy = (rect["top"] + rect["bottom"]) / 2
        pt = CGPoint(cx, cy)
        down = CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, pt, 0)
        up = CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, pt, 0)
        CGEventPost(kCGHIDEventTap, down)
        CGEventPost(kCGHIDEventTap, up)
        return {"clicked": True, "method": "cgevent", "x": cx, "y": cy}

    raise RuntimeError("click failed: no usable mechanism (ax_press failed, quartz not available)")


def _method_set_value(params: _Params) -> _Result:
    handle: str = str(params.get("handle", ""))
    value: str = str(params.get("value", ""))

    if not handle:
        raise ValueError("handle must be a non-empty string")

    elem = _resolve(handle)
    if elem is None:
        raise ValueError(f"no element found for handle '{handle}'")

    # Try AX SetAttributeValue for value attribute
    try:
        err = AXUIElementSetAttributeValue(elem, kAXValueAttribute, value)
        if err == 0:
            return {"method": "ax_set_value"}
    except Exception:
        pass

    # Fallback: focus + osascript keystroke
    try:
        AXUIElementPerformAction(elem, kAXFocusedAttribute)
    except Exception:
        pass

    script = f'tell application "System Events" to keystroke "{value}"'
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"osascript keystroke failed: {result.stderr}")

    return {"method": "osascript_keystroke"}


def _method_get_window_tree(params: _Params) -> _Result:
    if not _has_ax:
        raise RuntimeError("pyobjc-framework-ApplicationServices not installed")

    depth: int = int(params.get("depth", 3))
    system = AXUIElementCreateSystemWide()
    return _walk_tree(system, depth)


def _method_run_script(params: _Params) -> _Result:
    """Execute an AppleScript (macOS replacement for AHK)."""
    script: str = str(params.get("script", ""))
    if not script:
        raise ValueError("script must be a non-empty string")

    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=60,
        )
        return {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        raise RuntimeError("AppleScript timed out after 60 seconds")


def _method_get_action_tree(params: _Params) -> _Result:
    """
    Return a compact numbered Markdown action tree for LLM consumption.
    Only includes interactive AX roles.
    """
    if not _has_ax:
        raise RuntimeError("pyobjc-framework-ApplicationServices not installed")

    depth: int = int(params.get("depth", 4))
    window_filter: Optional[str] = params.get("window")

    INTERACTIVE_ROLES = {
        "AXButton", "AXRadioButton", "AXCheckBox", "AXTextField",
        "AXTextArea", "AXComboBox", "AXPopUpButton", "AXMenuItem",
        "AXMenu", "AXLink", "AXCell", "AXSlider", "AXStaticText",
    }

    system = AXUIElementCreateSystemWide()
    lines: List[str] = []
    idx = [1]

    def _visit_interactive(elem: Any, current_depth: int) -> None:
        if current_depth < 0:
            return
        try:
            role = _ax_role(elem)
            title = _ax_title(elem)

            if role in ("AXWindow", "AXDialog", "AXSheet"):
                if window_filter and window_filter.lower() not in title.lower():
                    return
                lines.append(f"\n# Window: \"{title}\"")
            elif role in INTERACTIVE_ROLES:
                rect = _ax_bounds(elem)
                handle = _register(elem)
                value = _ax_value(elem)[:40] if _ax_value(elem) else ""
                detail = f" value=\"{value}\"" if value else ""
                detail += f" (rect: {rect['left']},{rect['top']} - {rect['right']},{rect['bottom']})"
                lines.append(f"[{idx[0]}] {role} \"{title}\"{detail}  <!-- handle:{handle} -->")
                idx[0] += 1
        except Exception:
            pass

        for child in _ax_children(elem):
            _visit_interactive(child, current_depth - 1)

    _visit_interactive(system, depth)

    return {
        "markdown": "\n".join(lines),
        "element_count": idx[0] - 1,
    }

# ─────────────────────────── dispatch table ──────────────────────────────────

_METHODS: Dict[str, Any] = {
    "ping": _method_ping,
    "find_element": _method_find_element,
    "click": _method_click,
    "set_value": _method_set_value,
    "get_window_tree": _method_get_window_tree,
    "run_script": _method_run_script,
    "get_action_tree": _method_get_action_tree,
    # Windows compat alias
    "ahk_run": _method_run_script,
}

# ─────────────────────────── stdio loop ──────────────────────────────────────


def _respond(req_id: Any, *, result: Any = None, error: Optional[str] = None) -> None:
    if error is not None:
        payload = {"id": req_id, "error": error}
    else:
        payload = {"id": req_id, "result": result}
    sys.stdout.write(json.dumps(payload, separators=(",", ":"), default=str) + "\n")
    sys.stdout.flush()


def main() -> None:
    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue

        req_id: Any = None
        try:
            req = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            _respond(None, error=f"invalid JSON: {exc}")
            continue

        req_id = req.get("id")
        method: str = req.get("method", "")
        params: _Params = req.get("params") or {}

        handler = _METHODS.get(method)
        if handler is None:
            _respond(req_id, error=f"method not found: {method}")
            continue

        try:
            result = handler(params)
            _respond(req_id, result=result)
        except Exception as exc:
            _respond(req_id, error=str(exc))


if __name__ == "__main__":
    main()
