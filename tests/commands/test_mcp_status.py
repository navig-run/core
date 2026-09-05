"""Regression: `navig mcp` interactive "Server status" crashed.

The interactive menu called `mcp.status_mcp_cmd({})`, but the signature is
`status_mcp_cmd(name, options)` — so `{}` landed in `name` and `options` was
missing → TypeError, every time. Every sibling (start/stop/uninstall) prompts for
a name first; status alone was missed. These lock the correct call shape (and that
a normal status dict renders without raising).
"""

from __future__ import annotations

import pytest
import typer

import navig.commands.mcp as mcp_mod
from navig.commands.mcp import status_mcp_cmd


class _FakeServer:
    def __init__(self, status, config=None):
        self._status = status
        # Mirror the real MCPServer: the detail view reads `.config` for the
        # optional package row (get_status() only carries type/command).
        self.config = config or {}

    def get_status(self):
        return self._status


class _FakeManager:
    def __init__(self, servers):
        self._servers = servers

    def get_server(self, name):
        return self._servers.get(name)


def test_status_correct_signature_renders_without_crashing(monkeypatch):
    status = {
        "name": "everything", "type": "stdio", "command": "npx -y srv",
        "enabled": True, "running": False,
    }
    monkeypatch.setattr(mcp_mod, "_get_mcp_manager", lambda: _FakeManager({"everything": _FakeServer(status)}))
    # The correct call — (name, options). The interactive bug passed `{}` as name
    # with no options, which raised TypeError before reaching here.
    status_mcp_cmd("everything", {})


def test_status_running_server_renders(monkeypatch):
    status = {
        "name": "x", "type": "stdio", "command": "c",
        "enabled": True, "running": True, "pid": 4242,
    }
    monkeypatch.setattr(mcp_mod, "_get_mcp_manager", lambda: _FakeManager({"x": _FakeServer(status)}))
    status_mcp_cmd("x", {})  # exercises the running/pid branch


def test_status_unknown_server_exits_two(monkeypatch, capsys):
    """An unknown server is the usage class — it must not exit 0.

    It used to print "not found" and return, so `navig mcp status api && <use it>`
    proceeded against a server that does not exist.
    """
    monkeypatch.setattr(mcp_mod, "_get_mcp_manager", lambda: _FakeManager({}))
    with pytest.raises(typer.Exit) as excinfo:
        status_mcp_cmd("nope", {})
    assert excinfo.value.exit_code == 2
    assert "not found" in capsys.readouterr().out.lower()


def test_status_unknown_server_still_prints_json_null_first(monkeypatch, capsys):
    """`--json` keeps its parseable answer — the exit code is the extra signal."""
    monkeypatch.setattr(mcp_mod, "_get_mcp_manager", lambda: _FakeManager({}))
    with pytest.raises(typer.Exit) as excinfo:
        status_mcp_cmd("nope", {"json": True})
    assert excinfo.value.exit_code == 2
    assert capsys.readouterr().out.strip() == "null"
