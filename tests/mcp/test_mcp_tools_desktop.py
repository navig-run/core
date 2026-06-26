"""Tests for the 7 AHK-backed input tools added to navig.mcp.tools.desktop.

Covers _tool_desktop_type, _tool_desktop_scroll, _tool_desktop_move,
_tool_desktop_shortcut, _tool_desktop_app, _tool_desktop_multi_select,
_tool_desktop_multi_edit, and the _coerce_bool / _run_ahk helpers.

All tests run cross-platform by patching audit init, permission check, and
_desktop_client so that the AHK script text can be verified without a real
Windows desktop session.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from navig.mcp.tools.desktop import (
    _coerce_bool,
    _tool_desktop_app,
    _tool_desktop_move,
    _tool_desktop_multi_edit,
    _tool_desktop_multi_select,
    _tool_desktop_scroll,
    _tool_desktop_shortcut,
    _tool_desktop_type,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

_AUDIT_OK = "navig.mcp.tools.desktop._desktop_audit_initialized"
_PERM_OK = "navig.mcp.tools.desktop._desktop_permission_check"
_CLIENT = "navig.mcp.tools.desktop._desktop_client"

_SENTINEL = object()


def _make_server() -> Any:
    s = SimpleNamespace()
    s.tools = {}
    s._tool_handlers = {}
    return s


def _mock_client(result: Any = _SENTINEL) -> MagicMock:
    """Return a mock _DesktopClient whose ahk_run() returns result or the script itself."""
    mc = MagicMock()
    if result is _SENTINEL:
        mc.ahk_run.side_effect = lambda script: {"ok": True, "script": script}
    else:
        mc.ahk_run.return_value = result
    mc.__enter__ = lambda s: s
    mc.__exit__ = MagicMock(return_value=False)
    return mc


def _run(fn, args: dict[str, Any]) -> Any:
    """Run tool fn with audit + permission patched to allow access."""
    server = _make_server()
    client = _mock_client()
    with (
        patch(_AUDIT_OK, return_value=None),
        patch(_PERM_OK, return_value=None),
        patch(_CLIENT, return_value=client),
    ):
        return fn(server, args), client


# ── _coerce_bool ──────────────────────────────────────────────────────────────


def test_coerce_bool_true_values() -> None:
    for v in (True, "true", "True", "1", "yes", "YES"):
        assert _coerce_bool(v) is True


def test_coerce_bool_false_values() -> None:
    for v in (False, "false", "0", "no", "NO", ""):
        assert _coerce_bool(v) is False


def test_coerce_bool_none_uses_default() -> None:
    assert _coerce_bool(None, default=True) is True
    assert _coerce_bool(None, default=False) is False


# ── Permission / audit gates ──────────────────────────────────────────────────


def test_type_returns_audit_error_when_log_unavailable() -> None:
    server = _make_server()
    with patch(_AUDIT_OK, return_value={"error": "audit_log_unavailable", "reason": "x"}):
        result = _tool_desktop_type(server, {"text": "hi"})
    assert result["error"] == "audit_log_unavailable"


def test_type_returns_perm_error_when_no_permission() -> None:
    server = _make_server()
    with (
        patch(_AUDIT_OK, return_value=None),
        patch(_PERM_OK, return_value={"error": "permission_denied", "tool": "desktop_type"}),
    ):
        result = _tool_desktop_type(server, {"text": "hi"})
    assert result["error"] == "permission_denied"


# ── desktop_type ──────────────────────────────────────────────────────────────


def test_type_basic_sends_text() -> None:
    result, client = _run(_tool_desktop_type, {"text": "hello world"})
    script: str = client.ahk_run.call_args[0][0]
    assert "SendText 'hello world'" in script


def test_type_with_coordinates_clicks_first() -> None:
    result, client = _run(_tool_desktop_type, {"text": "abc", "x": 100, "y": 200})
    script: str = client.ahk_run.call_args[0][0]
    assert "Click 100, 200" in script
    assert script.index("Click 100, 200") < script.index("SendText")


def test_type_clear_adds_select_all() -> None:
    result, client = _run(_tool_desktop_type, {"text": "new", "clear": True})
    script: str = client.ahk_run.call_args[0][0]
    assert "'^a'" in script
    assert "'{Delete}'" in script


def test_type_press_enter_appends_enter() -> None:
    result, client = _run(_tool_desktop_type, {"text": "x", "press_enter": True})
    script: str = client.ahk_run.call_args[0][0]
    assert "'{Enter}'" in script
    assert script.rindex("SendText") < script.rindex("'{Enter}'")


def test_type_escapes_backtick_and_single_quote() -> None:
    result, client = _run(_tool_desktop_type, {"text": "it's `special`"})
    script: str = client.ahk_run.call_args[0][0]
    # backtick doubled, single quote doubled
    assert "``" in script
    assert "''" in script


# ── desktop_scroll ────────────────────────────────────────────────────────────


def test_scroll_default_direction_is_down() -> None:
    result, client = _run(_tool_desktop_scroll, {"x": 300, "y": 400})
    script: str = client.ahk_run.call_args[0][0]
    assert "WheelDown" in script


def test_scroll_up() -> None:
    result, client = _run(_tool_desktop_scroll, {"x": 0, "y": 0, "direction": "up"})
    script: str = client.ahk_run.call_args[0][0]
    assert "WheelUp" in script


def test_scroll_amount_included() -> None:
    result, client = _run(_tool_desktop_scroll, {"x": 0, "y": 0, "amount": 5})
    script: str = client.ahk_run.call_args[0][0]
    assert ", 5" in script


# ── desktop_move ──────────────────────────────────────────────────────────────


def test_move_plain_uses_mousemove() -> None:
    result, client = _run(_tool_desktop_move, {"x": 50, "y": 60})
    script: str = client.ahk_run.call_args[0][0]
    assert "MouseMove 50, 60" in script
    assert "MouseClickDrag" not in script


def test_move_drag_uses_mouseclickdrag() -> None:
    result, client = _run(_tool_desktop_move, {"x": 200, "y": 300, "from_x": 10, "from_y": 20})
    script: str = client.ahk_run.call_args[0][0]
    assert "MouseClickDrag" in script
    assert "10, 20, 200, 300" in script


# ── desktop_shortcut ──────────────────────────────────────────────────────────


def test_shortcut_sends_keys() -> None:
    result, client = _run(_tool_desktop_shortcut, {"keys": "^c"})
    script: str = client.ahk_run.call_args[0][0]
    assert "Send '^c'" in script


def test_shortcut_empty_keys_returns_error() -> None:
    server = _make_server()
    with (
        patch(_AUDIT_OK, return_value=None),
        patch(_PERM_OK, return_value=None),
    ):
        result = _tool_desktop_shortcut(server, {"keys": ""})
    assert "error" in result


# ── desktop_app ───────────────────────────────────────────────────────────────


def test_app_launch_uses_run() -> None:
    result, client = _run(_tool_desktop_app, {"mode": "launch", "name": "notepad.exe"})
    script: str = client.ahk_run.call_args[0][0]
    assert 'Run "notepad.exe"' in script


def test_app_switch_uses_winactivate() -> None:
    result, client = _run(_tool_desktop_app, {"mode": "switch", "name": "Notepad"})
    script: str = client.ahk_run.call_args[0][0]
    assert "WinActivate" in script


def test_app_resize_uses_winmove() -> None:
    result, client = _run(
        _tool_desktop_app,
        {"mode": "resize", "name": "Notepad", "x": 0, "y": 0, "width": 1920, "height": 1080},
    )
    script: str = client.ahk_run.call_args[0][0]
    assert "WinMove" in script
    assert "1920" in script


def test_app_unknown_mode_returns_error() -> None:
    server = _make_server()
    with (
        patch(_AUDIT_OK, return_value=None),
        patch(_PERM_OK, return_value=None),
    ):
        result = _tool_desktop_app(server, {"mode": "badmode", "name": "x"})
    assert "error" in result


def test_app_empty_name_returns_error() -> None:
    server = _make_server()
    with (
        patch(_AUDIT_OK, return_value=None),
        patch(_PERM_OK, return_value=None),
    ):
        result = _tool_desktop_app(server, {"mode": "launch", "name": ""})
    assert "error" in result


# ── desktop_multi_select ──────────────────────────────────────────────────────


def test_multi_select_holds_ctrl_by_default() -> None:
    result, client = _run(_tool_desktop_multi_select, {"locations": [[10, 20], [30, 40]]})
    script: str = client.ahk_run.call_args[0][0]
    assert "'{Ctrl down}'" in script
    assert "'{Ctrl up}'" in script


def test_multi_select_no_ctrl_when_false() -> None:
    result, client = _run(_tool_desktop_multi_select, {"locations": [[5, 5]], "hold_ctrl": False})
    script: str = client.ahk_run.call_args[0][0]
    assert "Ctrl down" not in script


def test_multi_select_empty_locations_returns_error() -> None:
    server = _make_server()
    with (
        patch(_AUDIT_OK, return_value=None),
        patch(_PERM_OK, return_value=None),
    ):
        result = _tool_desktop_multi_select(server, {"locations": []})
    assert "error" in result


def test_multi_select_clicks_all_locations() -> None:
    result, client = _run(_tool_desktop_multi_select, {"locations": [[10, 20], [30, 40], [50, 60]]})
    script: str = client.ahk_run.call_args[0][0]
    assert "Click 10, 20" in script
    assert "Click 30, 40" in script
    assert "Click 50, 60" in script


# ── desktop_multi_edit ────────────────────────────────────────────────────────


def test_multi_edit_clicks_and_types() -> None:
    result, client = _run(
        _tool_desktop_multi_edit, {"fields": [[100, 200, "Alice"], [300, 400, "Bob"]]}
    )
    script: str = client.ahk_run.call_args[0][0]
    assert "Click 100, 200" in script
    assert "SendText 'Alice'" in script
    assert "Click 300, 400" in script
    assert "SendText 'Bob'" in script


def test_multi_edit_empty_fields_returns_error() -> None:
    server = _make_server()
    with (
        patch(_AUDIT_OK, return_value=None),
        patch(_PERM_OK, return_value=None),
    ):
        result = _tool_desktop_multi_edit(server, {"fields": []})
    assert "error" in result


def test_multi_edit_skips_incomplete_items() -> None:
    # A two-element list (missing text) should be skipped, not crash.
    result, client = _run(_tool_desktop_multi_edit, {"fields": [[10, 20], [30, 40, "hello"]]})
    script: str = client.ahk_run.call_args[0][0]
    assert "Click 10, 20" not in script  # incomplete skipped
    assert "SendText 'hello'" in script


# ── MCP schema validation (array types must carry 'items') ───────────────────

def _check_array_items(schema: Any, path: str = "") -> list[str]:
    """Recursively find array-typed schema nodes that are missing 'items'.

    The MCP spec (and VS Code's Copilot validator) require every
    ``{"type": "array"}`` node to declare an ``items`` sub-schema.
    """
    violations: list[str] = []
    if isinstance(schema, dict):
        if schema.get("type") == "array" and "items" not in schema:
            violations.append(path or "<root>")
        for key, value in schema.items():
            violations.extend(_check_array_items(value, f"{path}.{key}" if path else key))
    elif isinstance(schema, list):
        for idx, item in enumerate(schema):
            violations.extend(_check_array_items(item, f"{path}[{idx}]"))
    return violations


def test_all_desktop_tool_schemas_have_items_on_arrays() -> None:
    """Every registered desktop tool schema must not contain bare array types.

    Regression test for GitHub issue: 'tool parameters array type must have
    items' validation error raised by VS Code Copilot when connecting to the
    navig MCP server (desktop_multi_edit was missing items on its inner array).
    """
    from types import SimpleNamespace as NS
    import navig.mcp.tools.desktop as desktop_mod

    server = NS()
    server.tools = {}
    server._tool_handlers = {}
    desktop_mod.register(server)

    for tool_name, tool_def in server.tools.items():
        schema = tool_def.get("inputSchema", {})
        violations = _check_array_items(schema, tool_name)
        assert not violations, (
            f"Tool '{tool_name}' has array schema node(s) without 'items': {violations}"
        )


def test_desktop_multi_edit_fields_inner_array_has_items() -> None:
    """The inner 'items' of fields (the [x, y, text] triple) must declare its own items."""
    from types import SimpleNamespace as NS
    import navig.mcp.tools.desktop as desktop_mod

    server = NS()
    server.tools = {}
    server._tool_handlers = {}
    desktop_mod.register(server)

    tool_schema = server.tools["desktop_multi_edit"]["inputSchema"]
    fields_schema = tool_schema["properties"]["fields"]

    assert fields_schema["type"] == "array", "fields must be array type"
    assert "items" in fields_schema, "fields must have items"

    inner = fields_schema["items"]
    assert inner["type"] == "array", "fields items must be array type"
    assert "items" in inner, "fields items (inner triple array) must declare 'items'"
