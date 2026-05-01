"""Tests for navig.mcp.tools.filesystem MCP tool bundle."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from navig.mcp.tools.filesystem import _tool_filesystem, register

# ── Mock server ───────────────────────────────────────────────────────────────


def _make_server() -> Any:
    s = SimpleNamespace()
    s.tools = {}
    s._tool_handlers = {}
    return s


# ── register ──────────────────────────────────────────────────────────────────


def test_register_adds_tool() -> None:
    server = _make_server()
    register(server)
    assert "desktop_filesystem" in server.tools
    assert "desktop_filesystem" in server._tool_handlers


# ── handler: read ─────────────────────────────────────────────────────────────


def test_read_mode(tmp_path: Path) -> None:
    f = tmp_path / "r.txt"
    f.write_text("hello world")
    server = _make_server()
    result = _tool_filesystem(server, {"mode": "read", "path": str(f)})
    assert "hello world" in result


def test_read_missing(tmp_path: Path) -> None:
    server = _make_server()
    result = _tool_filesystem(server, {"mode": "read", "path": str(tmp_path / "x")})
    assert "Error" in result


# ── handler: write ────────────────────────────────────────────────────────────


def test_write_mode(tmp_path: Path) -> None:
    f = tmp_path / "w.txt"
    server = _make_server()
    result = _tool_filesystem(server, {"mode": "write", "path": str(f), "content": "hi"})
    assert "Written to" in result
    assert f.read_text() == "hi"


def test_write_no_content(tmp_path: Path) -> None:
    server = _make_server()
    result = _tool_filesystem(server, {"mode": "write", "path": str(tmp_path / "w.txt")})
    assert "Error" in result
    assert "content" in result


# ── handler: copy / move / delete ─────────────────────────────────────────────


def test_copy_mode(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("data")
    server = _make_server()
    result = _tool_filesystem(server, {"mode": "copy", "path": str(src), "destination": str(dst)})
    assert "Copied" in result


def test_copy_no_destination(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    src.write_text("x")
    server = _make_server()
    result = _tool_filesystem(server, {"mode": "copy", "path": str(src)})
    assert "Error" in result
    assert "destination" in result


def test_delete_mode(tmp_path: Path) -> None:
    f = tmp_path / "del.txt"
    f.write_text("bye")
    server = _make_server()
    result = _tool_filesystem(server, {"mode": "delete", "path": str(f)})
    assert "Deleted" in result
    assert not f.exists()


# ── handler: list / search / info ─────────────────────────────────────────────


def test_list_mode(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("")
    server = _make_server()
    result = _tool_filesystem(server, {"mode": "list", "path": str(tmp_path)})
    assert "f.txt" in result


def test_search_mode(tmp_path: Path) -> None:
    (tmp_path / "find_me.py").write_text("")
    server = _make_server()
    result = _tool_filesystem(server, {"mode": "search", "path": str(tmp_path), "pattern": "*.py"})
    assert "find_me.py" in result


def test_search_no_pattern(tmp_path: Path) -> None:
    server = _make_server()
    result = _tool_filesystem(server, {"mode": "search", "path": str(tmp_path)})
    assert "Error" in result
    assert "pattern" in result


def test_info_mode(tmp_path: Path) -> None:
    f = tmp_path / "i.txt"
    f.write_text("abc")
    server = _make_server()
    result = _tool_filesystem(server, {"mode": "info", "path": str(f)})
    assert "File" in result


def test_unknown_mode(tmp_path: Path) -> None:
    server = _make_server()
    result = _tool_filesystem(server, {"mode": "dance", "path": str(tmp_path)})
    assert "Error" in result
    assert "Unknown mode" in result


# ── coerce_bool ───────────────────────────────────────────────────────────────


def test_recursive_string_coerce(tmp_path: Path) -> None:
    """recursive='true' string should be accepted."""
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.txt").write_text("")
    server = _make_server()
    result = _tool_filesystem(
        server,
        {"mode": "list", "path": str(tmp_path), "recursive": "true"},
    )
    assert "nested.txt" in result
