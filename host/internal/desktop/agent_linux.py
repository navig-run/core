# navig-core/host/internal/desktop/agent_linux.py
"""NAVIG Desktop Agent — Linux (AT-SPI2 + xdotool) sidecar.

Implements UI Automation for Linux using the AT-SPI2 accessibility framework
and xdotool for input synthesis. Communicates over newline-delimited JSON on
stdin/stdout, using the same JSON-RPC protocol as agent.py (Windows).

Request shape:  {"id": <int>, "method": "<name>", "params": {...}}
Response shape: {"id": <int>, "result": <any>}   (success)
               {"id": <int>, "error": "<string>"}  (failure)

Methods:
    ping            — health check
    find_element    — search AT-SPI2 tree by name/role
    click           — click element by AT-SPI2 path ref
    set_value       — type text into focused element
    get_window_tree — dump recursive AT-SPI2 tree
    run_script      — execute a bash script (replaces ahk_run on Linux)
    get_action_tree — numbered Markdown tree optimized for LLM consumption

Dependencies (install on Linux):
    pip install pyatspi
    apt-get install xdotool wmctrl  (or equivalent)
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

if platform.system() != "Linux":
    sys.stderr.write("error: navig-desktop-agent-linux is Linux only\n")
    sys.exit(1)

# ───────────────────────── pyatspi import (deferred) ─────────────────────────

try:
    import pyatspi  # type: ignore
    _has_atspi = True
except ImportError:
    pyatspi = None  # type: ignore[assignment]
    _has_atspi = False

# ───────────────────────── type aliases ──────────────────────────────────────

_Params = Dict[str, Any]
_Result = Any

# ─────────────────────────── handle registry ─────────────────────────────────
# AT-SPI2 objects can't be serialised. We keep a UUID → object registry and
# expose handles (UUID strings) to the Go client.

_handle_registry: Dict[str, Any] = {}  # handle_str → pyatspi accessible


def _register(obj: Any) -> str:
    """Register an AT-SPI2 accessible and return its handle string."""
    handle = str(uuid.uuid4())[:8]
    _handle_registry[handle] = obj
    return handle


def _resolve(handle: str) -> Optional[Any]:
    return _handle_registry.get(handle)

# ─────────────────────────── helpers ─────────────────────────────────────────


def _get_bounding_box(acc: Any) -> Dict[str, int]:
    """Get the bounding box of an accessible object via AT-SPI2."""
    try:
        comp = acc.queryComponent()
        ext = comp.getExtents(pyatspi.DESKTOP_COORDS)
        return {"left": ext.x, "top": ext.y,
                "right": ext.x + ext.width, "bottom": ext.y + ext.height}
    except Exception:
        return {"left": 0, "top": 0, "right": 0, "bottom": 0}


def _acc_to_dict(acc: Any, register: bool = True) -> Dict[str, Any]:
    """Convert an AT-SPI2 Accessible to a serialisable dict."""
    try:
        name = acc.name or ""
    except Exception:
        name = ""
    try:
        role = acc.getRoleName() or ""
    except Exception:
        role = ""
    try:
        app = acc.getApplication().name or ""
    except Exception:
        app = ""

    handle = _register(acc) if register else ""
    rect = _get_bounding_box(acc) if _has_atspi else {"left": 0, "top": 0, "right": 0, "bottom": 0}

    return {
        "handle": handle,
        "name": name,
        "class_name": app,
        "control_type": role,
        "rect": rect,
    }


def _walk_tree(acc: Any, depth: int, index: Optional[list] = None) -> Dict[str, Any]:
    """Recursively walk AT-SPI2 tree to given depth."""
    if index is None:
        index = [0]

    node = _acc_to_dict(acc)
    node["index"] = index[0]
    index[0] += 1

    if depth > 0:
        children = []
        try:
            for i in range(acc.childCount):
                child = acc.getChildAtIndex(i)
                if child:
                    children.append(_walk_tree(child, depth - 1, index))
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
        node["children"] = children

    return node


def _xdotool(*args: str) -> subprocess.CompletedProcess:
    """Run an xdotool command and return the result."""
    return subprocess.run(
        ["xdotool"] + list(args),
        capture_output=True,
        text=True,
        timeout=10,
    )

# ─────────────────────────── method implementations ──────────────────────────


def _method_ping(_params: _Params) -> _Result:
    return {"ok": True, "platform": "linux", "atspi": _has_atspi}


def _method_find_element(params: _Params) -> _Result:
    if not _has_atspi:
        raise RuntimeError("pyatspi is not installed (pip install pyatspi)")

    name: Optional[str] = params.get("name")
    role_filter: Optional[str] = params.get("control_type")
    depth: int = int(params.get("depth", 5))

    desktop = pyatspi.Registry.getDesktop(0)
    results: List[Dict[str, Any]] = []

    def _visit(acc: Any, current_depth: int) -> None:
        if current_depth < 0:
            return
        try:
            match = True
            if name and acc.name != name:
                match = False
            if match and role_filter and acc.getRoleName().lower() != role_filter.lower():
                match = False
            if match and (name or role_filter):
                results.append(_acc_to_dict(acc))
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
        try:
            for i in range(acc.childCount):
                child = acc.getChildAtIndex(i)
                if child:
                    _visit(child, current_depth - 1)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

    _visit(desktop, depth)
    return results


def _method_click(params: _Params) -> _Result:
    handle: str = str(params.get("handle", ""))
    if not handle:
        raise ValueError("handle must be a non-empty string")

    acc = _resolve(handle)
    if acc is None:
        raise ValueError(f"no element found for handle '{handle}'")

    # Try AT-SPI2 action first
    try:
        action = acc.queryAction()
        for i in range(action.nActions):
            if action.getName(i).lower() in ("click", "press", "activate"):
                action.doAction(i)
                return {"clicked": True, "method": "atspi_action"}
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    # Fallback: use xdotool to click the bounding box center
    rect = _get_bounding_box(acc)
    cx = (rect["left"] + rect["right"]) // 2
    cy = (rect["top"] + rect["bottom"]) // 2

    result = _xdotool("mousemove", str(cx), str(cy), "click", "1")
    if result.returncode != 0:
        raise RuntimeError(f"xdotool click failed: {result.stderr}")

    return {"clicked": True, "method": "xdotool", "x": cx, "y": cy}


def _method_set_value(params: _Params) -> _Result:
    handle: str = str(params.get("handle", ""))
    value: str = str(params.get("value", ""))

    if not handle:
        raise ValueError("handle must be a non-empty string")

    acc = _resolve(handle)
    if acc is None:
        raise ValueError(f"no element found for handle '{handle}'")

    # Try AT-SPI2 EditableText interface first
    try:
        etext = acc.queryEditableText()
        etext.setTextContents(value)
        return {"method": "atspi_editable_text"}
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    # Fallback: focus the element and use xdotool type
    try:
        acc.grabFocus()
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    # Clear existing content then type
    _xdotool("key", "ctrl+a")
    result = _xdotool("type", "--clearmodifiers", value)
    if result.returncode != 0:
        raise RuntimeError(f"xdotool type failed: {result.stderr}")

    return {"method": "xdotool_type"}


def _method_get_window_tree(params: _Params) -> _Result:
    if not _has_atspi:
        raise RuntimeError("pyatspi is not installed (pip install pyatspi)")

    depth: int = int(params.get("depth", 3))
    desktop = pyatspi.Registry.getDesktop(0)
    return _walk_tree(desktop, depth)


def _method_run_script(params: _Params) -> _Result:
    """Execute a bash script (Linux replacement for AHK)."""
    script: str = str(params.get("script", ""))
    if not script:
        raise ValueError("script must be a non-empty string")

    import tempfile
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".sh", prefix="navig_desktop_")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/bash\n")
            fh.write(script)
        os.chmod(tmp_path, 0o700)
        proc = subprocess.run(
            ["/bin/bash", tmp_path],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        raise RuntimeError("Script timed out after 60 seconds")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass  # best-effort cleanup


def _method_get_action_tree(params: _Params) -> _Result:
    """
    Return a compact numbered Markdown action tree for LLM consumption.
    
    Only includes interactive elements (buttons, inputs, links, menus).
    Each element is assigned a sequential integer ID.
    The LLM emits {"action": "click", "target": 3} to reference element [3].
    """
    if not _has_atspi:
        raise RuntimeError("pyatspi is not installed (pip install pyatspi)")

    depth: int = int(params.get("depth", 4))
    window_filter: Optional[str] = params.get("window")  # optional window name filter

    # Interactive AT-SPI2 roles we care about
    INTERACTIVE_ROLES = {
        "push button", "button", "toggle button", "check box", "radio button",
        "text", "entry", "password text", "combo box", "list item", "menu item",
        "menu", "link", "tree item", "spin button", "slider",
    }

    desktop = pyatspi.Registry.getDesktop(0)
    lines: List[str] = []
    idx = [1]  # mutable counter

    def _visit_interactive(acc: Any, current_depth: int) -> None:
        if current_depth < 0:
            return
        try:
            role = acc.getRoleName().lower()
            name = acc.name or ""

            if role == "frame" or role == "window" or role == "dialog":
                if window_filter and window_filter.lower() not in name.lower():
                    return
                lines.append(f"\n# Window: \"{name}\"")
            elif role in INTERACTIVE_ROLES and (name or role):
                rect = _get_bounding_box(acc)
                handle = _register(acc)
                value = ""
                try:
                    txt = acc.queryText()
                    value = txt.getText(0, txt.characterCount)[:40]
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical

                detail = f" value=\"{value}\"" if value else ""
                detail += f" (rect: {rect['left']},{rect['top']} - {rect['right']},{rect['bottom']})"
                lines.append(f"[{idx[0]}] {role} \"{name}\"{detail}  <!-- handle:{handle} -->")
                idx[0] += 1

        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        try:
            for i in range(acc.childCount):
                child = acc.getChildAtIndex(i)
                if child:
                    _visit_interactive(child, current_depth - 1)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

    _visit_interactive(desktop, depth)

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
    # Alias for Windows compat
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
