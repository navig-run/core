"""The advertised inputSchema must actually be enforced.

Before this, `_handle_tools_call` ran the handler with whatever the client sent: an
unknown argument was silently dropped, and a wrong type reached the tool and came back as
a Python message (``'int' object has no attribute 'strip'``) that named neither the tool
nor the argument. Both are invisible failures to a model, which is worse than a refusal.
"""

from __future__ import annotations

import json

from navig.mcp.validation import summarise, validate_arguments
from navig.mcp_server import MCPProtocolHandler

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Host name"},
        "count": {"type": "integer", "description": "How many"},
        "ratio": {"type": "number", "description": "A float"},
        "force": {"type": "boolean", "description": "Override"},
    },
    "required": ["name"],
    "additionalProperties": False,
}


def _bare_handler() -> MCPProtocolHandler:
    h = MCPProtocolHandler.__new__(MCPProtocolHandler)
    h.tools = {}
    h._tool_handlers = {}
    h._tool_safety = {}
    return h


def _register(h: MCPProtocolHandler, name: str, schema: dict | None, result=None) -> None:
    tool = {"name": name}
    if schema is not None:
        tool["inputSchema"] = schema
    h.tools[name] = tool
    h._tool_handlers[name] = lambda _server, _args: result if result is not None else {"ok": True}


def _body(resp: dict) -> dict:
    return json.loads(resp["content"][0]["text"])


def test_valid_arguments_pass() -> None:
    assert validate_arguments(SCHEMA, {"name": "web-1", "count": 3, "force": True}) == []


def test_missing_required_is_named() -> None:
    problems = validate_arguments(SCHEMA, {})
    assert [p["field"] for p in problems] == ["name"]
    assert "missing required argument 'name'" in problems[0]["detail"]


def test_unknown_argument_is_rejected_when_strict() -> None:
    problems = validate_arguments(SCHEMA, {"name": "web-1", "hostname": "typo"})
    assert [p["field"] for p in problems] == ["hostname"]
    assert "unknown argument" in problems[0]["detail"]


def test_unknown_argument_is_ignored_when_schema_allows_extras() -> None:
    assert validate_arguments({**SCHEMA, "additionalProperties": True}, {"name": "h", "x": 1}) == []


def test_wrong_type_names_field_and_expected_type() -> None:
    problems = validate_arguments(SCHEMA, {"name": 123})
    assert problems[0]["field"] == "name"
    assert "must be string" in problems[0]["detail"]
    assert "integer" in problems[0]["detail"]


def test_boolean_is_not_an_integer() -> None:
    assert [p["field"] for p in validate_arguments(SCHEMA, {"name": "h", "count": True})] == ["count"]


def test_null_is_treated_as_absent() -> None:
    assert validate_arguments(SCHEMA, {"name": "h", "count": None}) == []


def test_schemaless_tool_is_not_validated() -> None:
    assert validate_arguments({}, {"anything": 1}) == []
    assert validate_arguments(None, {"anything": 1}) == []


def test_malformed_schema_never_raises() -> None:
    assert validate_arguments({"properties": "not-a-dict"}, {"x": 1}) == []


def test_summarise_names_the_first_problem() -> None:
    assert "missing required argument 'name'" in summarise(validate_arguments(SCHEMA, {}))


def test_dispatch_refuses_unknown_argument() -> None:
    h = _bare_handler()
    _register(h, "navig_host_info", SCHEMA)
    body = _body(h._handle_tools_call({"name": "navig_host_info", "arguments": {"name": "h", "nope": 1}}))
    assert body["error"] == "invalid_arguments"
    assert body["field"] == "nope"


def test_dispatch_refuses_wrong_type_before_the_handler_runs() -> None:
    h = _bare_handler()
    _register(h, "navig_host_info", SCHEMA)
    body = _body(h._handle_tools_call({"name": "navig_host_info", "arguments": {"name": 12345}}))
    assert body["error"] == "invalid_arguments"
    assert body["field"] == "name"
    assert "must be string" in body["detail"]


def test_dispatch_runs_the_handler_for_valid_arguments() -> None:
    h = _bare_handler()
    _register(h, "navig_host_info", SCHEMA, result={"name": "web-1"})
    resp = h._handle_tools_call({"name": "navig_host_info", "arguments": {"name": "web-1"}})
    assert resp.get("isError") is not True
    assert "web-1" in resp["content"][0]["text"]


def test_dispatch_keeps_the_human_unknown_tool_prefix() -> None:
    body = _body(_bare_handler()._handle_tools_call({"name": "missing_tool", "arguments": {}}))
    assert body["error"] == "unknown_tool"
    assert "Unknown tool: missing_tool" in body["detail"]


def test_dispatch_still_reports_tool_exceptions() -> None:
    h = _bare_handler()
    _register(h, "explode", None)
    h._tool_handlers["explode"] = lambda _s, _a: 1 / 0
    body = _body(h._handle_tools_call({"name": "explode", "arguments": {}}))
    assert body["error"] == "tool_error"
    assert "Tool error:" in body["detail"]


# ── structured content ───────────────────────────────────────────────────────


def test_declared_output_schema_returns_structured_content_for_dicts() -> None:
    h = _bare_handler()
    _register(h, "obj_tool", None, result={"ok": True})
    h.tools["obj_tool"]["outputSchema"] = {"type": "object", "additionalProperties": True}
    resp = h._handle_tools_call({"name": "obj_tool", "arguments": {}})
    assert resp["structuredContent"] == {"ok": True}
    assert resp["content"][0]["type"] == "text"  # the text block stays


def test_declared_list_schema_wraps_items() -> None:
    h = _bare_handler()
    _register(h, "list_tool", None, result=[{"name": "a"}])
    h.tools["list_tool"]["outputSchema"] = {
        "type": "object",
        "properties": {"items": {"type": "array"}},
        "required": ["items"],
    }
    resp = h._handle_tools_call({"name": "list_tool", "arguments": {}})
    assert resp["structuredContent"] == {"items": [{"name": "a"}]}


def test_tool_without_output_schema_gets_no_structured_content() -> None:
    h = _bare_handler()
    _register(h, "plain_tool", None, result={"ok": True})
    resp = h._handle_tools_call({"name": "plain_tool", "arguments": {}})
    assert "structuredContent" not in resp
