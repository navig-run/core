"""Tests for navig.mcp.protocol — message types and enums."""
from __future__ import annotations

import json

import pytest

from navig.mcp.protocol import (
    JSONRPCRequest,
    JSONRPCResponse,
    MCPCapabilities,
    MCPMethod,
    MCPPrompt,
    MCPResource,
    MCPTool,
)


# ── MCPMethod ─────────────────────────────────────────────────


class TestMCPMethod:
    def test_initialize_value(self):
        assert MCPMethod.INITIALIZE == "initialize"

    def test_tools_list_value(self):
        assert MCPMethod.TOOLS_LIST == "tools/list"

    def test_tools_call_value(self):
        assert MCPMethod.TOOLS_CALL == "tools/call"

    def test_ping_value(self):
        assert MCPMethod.PING == "ping"

    def test_resources_list_value(self):
        assert MCPMethod.RESOURCES_LIST == "resources/list"

    def test_prompts_get_value(self):
        assert MCPMethod.PROMPTS_GET == "prompts/get"


# ── JSONRPCRequest ────────────────────────────────────────────


class TestJSONRPCRequest:
    def test_defaults(self):
        req = JSONRPCRequest(method="ping")
        assert req.jsonrpc == "2.0"
        assert req.params == {}
        assert req.id is None

    def test_to_json_includes_method(self):
        req = JSONRPCRequest(method="tools/list")
        data = json.loads(req.to_json())
        assert data["method"] == "tools/list"
        assert data["jsonrpc"] == "2.0"

    def test_to_json_omits_empty_params(self):
        req = JSONRPCRequest(method="ping")
        data = json.loads(req.to_json())
        assert "params" not in data

    def test_to_json_includes_params(self):
        req = JSONRPCRequest(method="tools/call", params={"name": "bash"})
        data = json.loads(req.to_json())
        assert data["params"]["name"] == "bash"

    def test_to_json_includes_id_when_set(self):
        req = JSONRPCRequest(method="ping", id=42)
        data = json.loads(req.to_json())
        assert data["id"] == 42

    def test_to_json_omits_none_id(self):
        req = JSONRPCRequest(method="ping", id=None)
        data = json.loads(req.to_json())
        assert "id" not in data

    def test_to_dict_matches_to_json(self):
        req = JSONRPCRequest(method="tools/list", params={"x": 1}, id="abc")
        d = req.to_dict()
        assert d["method"] == "tools/list"
        assert d["params"] == {"x": 1}
        assert d["id"] == "abc"

    def test_to_dict_omits_empty_params(self):
        req = JSONRPCRequest(method="ping")
        d = req.to_dict()
        assert "params" not in d

    def test_string_id_supported(self):
        req = JSONRPCRequest(method="ping", id="req-123")
        assert json.loads(req.to_json())["id"] == "req-123"


# ── JSONRPCResponse ───────────────────────────────────────────


class TestJSONRPCResponse:
    def test_from_json_basic(self):
        raw = json.dumps({"id": 1, "result": {"tools": []}, "jsonrpc": "2.0"})
        resp = JSONRPCResponse.from_json(raw)
        assert resp.id == 1
        assert resp.result == {"tools": []}
        assert resp.jsonrpc == "2.0"

    def test_from_json_error(self):
        raw = json.dumps({"id": 2, "error": {"code": -32601, "message": "Method not found"}, "jsonrpc": "2.0"})
        resp = JSONRPCResponse.from_json(raw)
        assert resp.is_error is True
        assert resp.get_error_message() == "Method not found"

    def test_from_dict(self):
        data = {"id": "req-1", "result": "ok", "jsonrpc": "2.0"}
        resp = JSONRPCResponse.from_dict(data)
        assert resp.id == "req-1"
        assert resp.result == "ok"

    def test_is_error_false_when_no_error(self):
        resp = JSONRPCResponse(id=1, result="ok")
        assert resp.is_error is False

    def test_is_error_true_when_error_set(self):
        resp = JSONRPCResponse(id=1, error={"code": -1, "message": "fail"})
        assert resp.is_error is True

    def test_get_error_message_empty_when_no_error(self):
        resp = JSONRPCResponse(id=1, result="ok")
        assert resp.get_error_message() == ""

    def test_from_json_defaults_jsonrpc(self):
        raw = json.dumps({"id": 1, "result": "ok"})
        resp = JSONRPCResponse.from_json(raw)
        assert resp.jsonrpc == "2.0"


# ── MCPTool ───────────────────────────────────────────────────


class TestMCPTool:
    def _tool(self, **kwargs) -> MCPTool:
        defaults = dict(name="bash", description="Run bash", input_schema={}, server_id="s1")
        defaults.update(kwargs)
        return MCPTool(**defaults)

    def test_to_dict_has_input_schema_key(self):
        d = self._tool().to_dict()
        assert "inputSchema" in d

    def test_to_dict_name(self):
        d = self._tool(name="grep").to_dict()
        assert d["name"] == "grep"

    def test_from_dict(self):
        raw = {"name": "search", "description": "Search", "inputSchema": {"type": "object"}}
        tool = MCPTool.from_dict(raw, server_id="srv")
        assert tool.name == "search"
        assert tool.server_id == "srv"
        assert tool.input_schema == {"type": "object"}

    def test_from_dict_missing_description_defaults_empty(self):
        raw = {"name": "x", "inputSchema": {}}
        tool = MCPTool.from_dict(raw)
        assert tool.description == ""

    def test_from_dict_missing_schema_defaults_empty_dict(self):
        raw = {"name": "x", "description": ""}
        tool = MCPTool.from_dict(raw)
        assert tool.input_schema == {}


# ── MCPResource ───────────────────────────────────────────────


class TestMCPResource:
    def test_to_dict_includes_uri(self):
        r = MCPResource(uri="file:///etc/hosts", name="hosts")
        d = r.to_dict()
        assert d["uri"] == "file:///etc/hosts"

    def test_to_dict_mime_type_key(self):
        r = MCPResource(uri="x", name="y", mime_type="text/plain")
        d = r.to_dict()
        assert d["mimeType"] == "text/plain"

    def test_from_dict(self):
        data = {"uri": "file:///a.txt", "name": "A", "description": "desc", "mimeType": "text/plain"}
        r = MCPResource.from_dict(data, server_id="s1")
        assert r.uri == "file:///a.txt"
        assert r.mime_type == "text/plain"
        assert r.server_id == "s1"

    def test_from_dict_optional_fields_none(self):
        data = {"uri": "x", "name": "y"}
        r = MCPResource.from_dict(data)
        assert r.description is None
        assert r.mime_type is None


# ── MCPPrompt ─────────────────────────────────────────────────


class TestMCPPrompt:
    def test_to_dict_name(self):
        p = MCPPrompt(name="code-review")
        d = p.to_dict()
        assert d["name"] == "code-review"

    def test_arguments_default_empty_list(self):
        p = MCPPrompt(name="x")
        d = p.to_dict()
        assert d["arguments"] == []

    def test_with_arguments(self):
        p = MCPPrompt(name="gen", arguments=[{"name": "lang"}])
        d = p.to_dict()
        assert len(d["arguments"]) == 1


# ── MCPCapabilities ───────────────────────────────────────────


class TestMCPCapabilities:
    def test_all_false_by_default(self):
        caps = MCPCapabilities()
        assert caps.tools is False
        assert caps.resources is False
        assert caps.prompts is False

    def test_from_dict_all_present(self):
        data = {"capabilities": {"tools": {}, "resources": {}, "prompts": {}}}
        caps = MCPCapabilities.from_dict(data)
        assert caps.tools is True
        assert caps.resources is True
        assert caps.prompts is True

    def test_from_dict_partial(self):
        data = {"capabilities": {"tools": {}}}
        caps = MCPCapabilities.from_dict(data)
        assert caps.tools is True
        assert caps.resources is False
        assert caps.prompts is False

    def test_from_dict_empty_capabilities(self):
        caps = MCPCapabilities.from_dict({"capabilities": {}})
        assert caps.tools is False

    def test_from_dict_missing_capabilities_key(self):
        caps = MCPCapabilities.from_dict({})
        assert caps.tools is False
