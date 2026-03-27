# navig-core/host/internal/desktop/agent.py
"""NAVIG Desktop Agent — JSON-RPC-over-stdio sidecar.

Implements UI Automation and AutoHotkey execution for the NAVIG desktop
integration layer. Communicates over newline-delimited JSON on stdin/stdout.

Request shape:  {"id": <int>, "method": "<name>", "params": {...}}
Response shape: {"id": <int>, "result": <any>}   (success)
               {"id": <int>, "error": "<string>"}  (failure)

Methods:
    ping           — health check
    find_element   — search UI element tree
    click          — click element by handle
    set_value      — set value on element (ValuePattern → SendKeys fallback)
    get_window_tree — dump recursive UI tree
    ahk_run        — execute AHK script via AutoHotkey.exe
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional

# ───────────────────────────── OS guard ──────────────────────────────────────

if platform.system() != "Windows":
    sys.stderr.write("error: navig-desktop-agent is Windows only\n")
    sys.exit(1)

# ────────────────────── uiautomation import (deferred error) ─────────────────

try:
    import uiautomation as auto
except ImportError:
    auto = None  # type: ignore[assignment]

# ─────────────────────────── type aliases ────────────────────────────────────

_Params = Dict[str, Any]
_Result = Any

# ─────────────────────────── helpers ─────────────────────────────────────────

_CONTROL_TYPE_MAP: Dict[str, int] = {}


def _get_control_type_id(name: str) -> Optional[int]:
    """Resolve control-type name (e.g. 'Button') to its uiautomation int ID."""
    if auto is None:
        return None
    # Build map lazily from uiautomation constants
    global _CONTROL_TYPE_MAP
    if not _CONTROL_TYPE_MAP:
        for attr in dir(auto):
            if attr.endswith("Control") and isinstance(getattr(auto, attr, None), type):
                ctrl_cls = getattr(auto, attr)
                ct_id = getattr(ctrl_cls, "ControlType", None)
                if ct_id is not None:
                    label = attr.replace("Control", "")
                    _CONTROL_TYPE_MAP[label.lower()] = ct_id
    return _CONTROL_TYPE_MAP.get(name.lower())


def _rect_to_dict(rect: Any) -> Dict[str, int]:
    """Convert a uiautomation Rect to a plain dict."""
    if rect is None:
        return {"left": 0, "top": 0, "right": 0, "bottom": 0}
    return {
        "left": int(rect.left),
        "top": int(rect.top),
        "right": int(rect.right),
        "bottom": int(rect.bottom),
    }


def _element_to_dict(elem: Any) -> Dict[str, Any]:
    """Convert a uiautomation Control to a serialisable dict."""
    try:
        handle = elem.NativeWindowHandle
    except Exception:
        handle = 0
    try:
        name = elem.Name
    except Exception:
        name = ""
    try:
        class_name = elem.ClassName
    except Exception:
        class_name = ""
    try:
        control_type = elem.ControlTypeName
    except Exception:
        control_type = ""
    try:
        rect = _rect_to_dict(elem.BoundingRectangle)
    except Exception:
        rect = {"left": 0, "top": 0, "right": 0, "bottom": 0}
    return {
        "handle": int(handle),
        "name": name or "",
        "class_name": class_name or "",
        "control_type": control_type or "",
        "rect": rect,
    }


def _walk_tree(elem: Any, depth: int) -> Dict[str, Any]:
    """Recursively walk UI element tree to given depth."""
    node = _element_to_dict(elem)
    if depth > 0:
        children: List[Dict[str, Any]] = []
        try:
            for child in elem.GetChildren():
                children.append(_walk_tree(child, depth - 1))
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
        node["children"] = children
    return node


# ─────────────────────────── method implementations ──────────────────────────


def _method_ping(_params: _Params) -> _Result:
    return {"ok": True}


def _method_find_element(params: _Params) -> _Result:
    if auto is None:
        raise RuntimeError("uiautomation is not installed")

    name: Optional[str] = params.get("name")
    class_name: Optional[str] = params.get("class_name")
    control_type_str: Optional[str] = params.get("control_type")
    depth: int = int(params.get("depth", 5))

    # Build uiautomation search condition
    search_kwargs: Dict[str, Any] = {}
    if name:
        search_kwargs["Name"] = name
    if class_name:
        search_kwargs["ClassName"] = class_name
    if control_type_str:
        ctrl_id = _get_control_type_id(control_type_str)
        if ctrl_id is not None:
            search_kwargs["ControlType"] = ctrl_id

    results: List[Dict[str, Any]] = []

    def _visit(elem: Any, current_depth: int) -> None:
        if current_depth < 0:
            return
        try:
            match = True
            if "Name" in search_kwargs and elem.Name != search_kwargs["Name"]:
                match = False
            if (
                match
                and "ClassName" in search_kwargs
                and elem.ClassName != search_kwargs["ClassName"]
            ):
                match = False
            if (
                match
                and "ControlType" in search_kwargs
                and elem.ControlType != search_kwargs["ControlType"]
            ):
                match = False
            if match and (name or class_name or control_type_str):
                results.append(_element_to_dict(elem))
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
        try:
            for child in elem.GetChildren():
                _visit(child, current_depth - 1)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

    root = auto.GetRootControl()
    _visit(root, depth)
    return results


def _method_click(params: _Params) -> _Result:
    if auto is None:
        raise RuntimeError("uiautomation is not installed")

    handle: int = int(params.get("handle", 0))
    if handle == 0:
        raise ValueError("handle must be a non-zero native window handle")

    elem = auto.ControlFromHandle(handle)
    if elem is None:
        raise ValueError(f"no element found for handle {handle}")

    try:
        elem.Click()
    except Exception:
        # Fallback: use Win32 SendMessage WM_LBUTTONUP/DOWN via automation Click
        try:
            elem.DoubleClick()
        except Exception as exc:
            raise RuntimeError(f"click failed: {exc}") from exc

    return {"clicked": True}


def _method_set_value(params: _Params) -> _Result:
    if auto is None:
        raise RuntimeError("uiautomation is not installed")

    handle: int = int(params.get("handle", 0))
    value: str = str(params.get("value", ""))

    if handle == 0:
        raise ValueError("handle must be a non-zero native window handle")

    elem = auto.ControlFromHandle(handle)
    if elem is None:
        raise ValueError(f"no element found for handle {handle}")

    # Try ValuePattern first
    try:
        vp = elem.GetValuePattern()
        if vp is not None:
            vp.SetValue(value)
            return {"method": "ValuePattern"}
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    # Fall back to SetFocus + SendKeys
    try:
        elem.SetFocus()
        # Clear existing content and type new value
        import uiautomation as _auto

        _auto.SendKeys("{Ctrl}a", waitTime=0.05)
        _auto.SendKeys(value, waitTime=0.0)
        return {"method": "SendKeys"}
    except Exception as exc:
        raise RuntimeError(f"set_value failed: {exc}") from exc


def _method_get_window_tree(params: _Params) -> _Result:
    if auto is None:
        raise RuntimeError("uiautomation is not installed")

    depth: int = int(params.get("depth", 3))
    root = auto.GetRootControl()
    return _walk_tree(root, depth)


def _method_ahk_run(params: _Params) -> _Result:
    script: str = str(params.get("script", ""))
    if not script:
        raise ValueError("script must be a non-empty string")

    # Locate AutoHotkey.exe on PATH (prefer AutoHotkey64.exe)
    ahk_exe: Optional[str] = None
    for candidate in ("AutoHotkey64.exe", "AutoHotkey.exe"):
        result = subprocess.run(
            ["where", candidate],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            ahk_exe = result.stdout.strip().splitlines()[0]
            break

    if ahk_exe is None:
        raise RuntimeError(
            "AutoHotkey.exe not found on PATH; install AutoHotkey v2 and ensure it is in PATH"
        )

    # Write script to a named temp file with .ahk extension
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".ahk", prefix="navig_desktop_")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            fh.write(script)

        proc = subprocess.run(
            [ahk_exe, tmp_path],
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
        raise RuntimeError("AHK script timed out after 60 seconds")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass  # best-effort cleanup


# ─────────────────────────── dispatch table ──────────────────────────────────

_METHODS: Dict[str, Any] = {
    "ping": _method_ping,
    "find_element": _method_find_element,
    "click": _method_click,
    "set_value": _method_set_value,
    "get_window_tree": _method_get_window_tree,
    "ahk_run": _method_ahk_run,
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
