"""Tests for navig.mcp.tools.inventory — register and tool handlers."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from navig.mcp.tools.inventory import (
    _tool_app_info,
    _tool_host_info,
    _tool_list_apps,
    _tool_list_hosts,
    register,
)


# ── helpers ──────────────────────────────────────────────────


def _server(hosts: dict | None = None, apps: dict | None = None) -> MagicMock:
    server = MagicMock()
    server.tools = {}
    server._tool_handlers = {}
    server._config.get_hosts.return_value = hosts or {}
    server._config.get_apps.return_value = apps or {}
    return server


# ── register ──────────────────────────────────────────────────


class TestRegister:
    def test_registers_tool_defs(self):
        server = _server()
        register(server)
        assert "navig_list_hosts" in server.tools
        assert "navig_list_apps" in server.tools
        assert "navig_host_info" in server.tools
        assert "navig_app_info" in server.tools

    def test_registers_handlers(self):
        server = _server()
        register(server)
        assert "navig_list_hosts" in server._tool_handlers
        assert "navig_list_apps" in server._tool_handlers
        assert "navig_host_info" in server._tool_handlers
        assert "navig_app_info" in server._tool_handlers

    def test_tool_schema_has_input_schema(self):
        server = _server()
        register(server)
        assert "inputSchema" in server.tools["navig_list_hosts"]

    def test_tool_schema_has_description(self):
        server = _server()
        register(server)
        assert server.tools["navig_list_hosts"]["description"]


# ── _tool_list_hosts ──────────────────────────────────────────


class TestListHosts:
    def _hosts(self):
        return {
            "web": {"host": "192.168.1.1", "user": "admin", "port": 22},
            "db": {"host": "192.168.1.2", "user": "root", "port": 5432},
        }

    def test_returns_all_hosts(self):
        server = _server(hosts=self._hosts())
        result = _tool_list_hosts(server, {})
        names = [r["name"] for r in result]
        assert "web" in names
        assert "db" in names

    def test_no_hosts_returns_empty_list(self):
        server = _server(hosts={})
        result = _tool_list_hosts(server, {})
        assert result == []

    def test_filter_matches_substring(self):
        server = _server(hosts=self._hosts())
        result = _tool_list_hosts(server, {"filter": "we"})  # matches "web"
        assert len(result) == 1
        assert result[0]["name"] == "web"

    def test_filter_no_match_returns_empty(self):
        server = _server(hosts=self._hosts())
        result = _tool_list_hosts(server, {"filter": "zzz"})
        assert result == []

    def test_result_has_expected_keys(self):
        server = _server(hosts=self._hosts())
        result = _tool_list_hosts(server, {})
        for item in result:
            assert "name" in item
            assert "host" in item
            assert "port" in item

    def test_default_port_22(self):
        server = _server(hosts={"x": {"host": "1.2.3.4", "user": "u"}})
        result = _tool_list_hosts(server, {})
        assert result[0]["port"] == 22

    def test_apps_key_included(self):
        server = _server(hosts={"h": {"host": "1.1.1.1", "user": "u", "apps": {"myapp": {}}}})
        result = _tool_list_hosts(server, {})
        assert "myapp" in result[0]["apps"]

    def test_empty_apps_is_empty_list(self):
        server = _server(hosts={"h": {"host": "1.1.1.1", "user": "u"}})
        result = _tool_list_hosts(server, {})
        assert result[0]["apps"] == []


# ── _tool_list_apps ───────────────────────────────────────────


class TestListApps:
    def _setup(self):
        hosts = {
            "web": {
                "host": "1.1.1.1",
                "user": "u",
                "apps": {"frontend": {"type": "node", "path": "/var/www"}},
            }
        }
        apps = {
            "backend": {"type": "python", "host": "web", "path": "/opt/api"},
        }
        return _server(hosts=hosts, apps=apps)

    def test_returns_global_apps(self):
        server = self._setup()
        result = _tool_list_apps(server, {})
        names = [r["name"] for r in result]
        assert "backend" in names

    def test_returns_host_embedded_apps(self):
        server = self._setup()
        result = _tool_list_apps(server, {})
        names = [r["name"] for r in result]
        assert "frontend" in names

    def test_host_filter_applied_to_global_apps(self):
        server = self._setup()
        result = _tool_list_apps(server, {"host": "web"})
        names = [r["name"] for r in result]
        assert "backend" in names

    def test_host_filter_excludes_wrong_host(self):
        hosts = {"web": {"host": "1.1.1.1", "user": "u", "apps": {}}}
        apps = {"backend": {"type": "python", "host": "other"}}
        server = _server(hosts=hosts, apps=apps)
        result = _tool_list_apps(server, {"host": "web"})
        names = [r["name"] for r in result]
        assert "backend" not in names

    def test_result_has_name_type_host_fields(self):
        server = self._setup()
        result = _tool_list_apps(server, {})
        for item in result:
            assert "name" in item


# ── _tool_host_info ───────────────────────────────────────────


class TestHostInfo:
    def test_known_host_returns_full_info(self):
        server = _server(hosts={"web": {"host": "1.2.3.4", "user": "admin"}})
        result = _tool_host_info(server, {"name": "web"})
        assert result["name"] == "web"
        assert result["host"] == "1.2.3.4"

    def test_unknown_host_returns_error(self):
        server = _server(hosts={"web": {"host": "1.2.3.4"}})
        result = _tool_host_info(server, {"name": "unknown"})
        assert "error" in result

    def test_error_message_includes_name(self):
        server = _server(hosts={})
        result = _tool_host_info(server, {"name": "missing"})
        assert "missing" in result["error"]


# ── _tool_app_info ────────────────────────────────────────────


class TestAppInfo:
    def test_global_app_found(self):
        apps = {"api": {"type": "python", "host": "web", "path": "/opt/api"}}
        server = _server(apps=apps)
        result = _tool_app_info(server, {"name": "api"})
        assert result["name"] == "api"
        assert result["type"] == "python"

    def test_host_embedded_app_found(self):
        hosts = {"web": {"host": "1.1.1.1", "user": "u", "apps": {"fe": {"type": "node"}}}}
        server = _server(hosts=hosts)
        result = _tool_app_info(server, {"name": "fe"})
        assert result["name"] == "fe"
        assert result["host"] == "web"

    def test_unknown_app_returns_error(self):
        server = _server()
        result = _tool_app_info(server, {"name": "nope"})
        assert "error" in result

    def test_global_app_takes_priority_over_host_app(self):
        apps = {"shared": {"type": "global"}}
        hosts = {"h": {"host": "x", "user": "u", "apps": {"shared": {"type": "local"}}}}
        server = _server(hosts=hosts, apps=apps)
        result = _tool_app_info(server, {"name": "shared"})
        # global apps config is checked first
        assert result["type"] == "global"
