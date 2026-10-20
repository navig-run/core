"""The compact routed surface dispatches only through its explicit table.

Every assertion here is about *routing*, not business behaviour: the action must resolve
to the registered original handler, its arguments must be validated by the original
schema, and the safety level used for approval must be the original operation's — never
the router's aggregate level.
"""

from __future__ import annotations

from typing import Any

import pytest

from navig.mcp.routing import (
    ROUTER_SPECS,
    dispatch,
    gate_target,
    router_error,
)
from navig.mcp_server import MCPProtocolHandler


@pytest.fixture()
def handler() -> MCPProtocolHandler:
    return MCPProtocolHandler(profile="default")


def _fake(server: MCPProtocolHandler, original: str, result: Any = None) -> dict[str, Any]:
    calls: dict[str, Any] = {}

    def fake(_server: Any, args: dict[str, Any]) -> Any:
        calls["args"] = args
        return result if result is not None else {"ok": True, "args": args}

    server._tool_handlers[original] = fake
    return calls


def test_every_action_resolves_to_a_registered_handler(handler: MCPProtocolHandler) -> None:
    for spec in ROUTER_SPECS.values():
        for action in spec.actions:
            if action.connector:
                continue  # providers are environment-dependent and checked below
            assert action.original in handler._tool_handlers, (spec.name, action)


def test_read_routers_contain_only_read_only_originals(handler: MCPProtocolHandler) -> None:
    for spec in ROUTER_SPECS.values():
        if spec.kind != "read":
            continue
        for action in spec.actions:
            if action.connector:
                continue
            assert action.original
            annotations = handler.tools[action.original]["annotations"]
            assert annotations.get("readOnlyHint") is True, (spec.name, action)
            assert annotations.get("destructiveHint") is False, (spec.name, action)


def test_dispatch_calls_the_original_handler_without_the_router_arguments(
    handler: MCPProtocolHandler,
) -> None:
    calls = _fake(handler, "navig_list_hosts")
    result = dispatch(
        handler, ROUTER_SPECS["navig_fleet"], {"action": "list_hosts", "filter": "web"}
    )
    assert result["ok"] is True
    assert calls["args"] == {"filter": "web"}


def test_action_is_not_leaked_to_the_original(handler: MCPProtocolHandler) -> None:
    calls = _fake(handler, "memory_key_facts_stats")
    dispatch(handler, ROUTER_SPECS["navig_memory"], {"action": "stats"})
    assert calls["args"] == {}


def test_unknown_action_is_a_structured_error(handler: MCPProtocolHandler) -> None:
    result = dispatch(handler, ROUTER_SPECS["navig_fleet"], {"action": "delete_everything"})
    assert result["isError"] is True
    assert result["error"] == "unknown_action"
    assert "list_hosts" in result["valid_actions"]


def test_non_string_action_is_an_error(handler: MCPProtocolHandler) -> None:
    result = dispatch(handler, ROUTER_SPECS["navig_fleet"], {"action": 3})
    assert result["isError"] is True
    assert result["error"] == "unknown_action"


def test_missing_required_argument_is_rejected_by_the_original_schema(
    handler: MCPProtocolHandler,
) -> None:
    result = dispatch(handler, ROUTER_SPECS["navig_fleet"], {"action": "host_info"})
    assert result["isError"] is True
    assert result["error"] == "invalid_arguments"
    assert result["field"] == "name"


def test_extra_argument_is_rejected_by_the_original_schema(handler: MCPProtocolHandler) -> None:
    result = dispatch(
        handler,
        ROUTER_SPECS["navig_fleet"],
        {"action": "list_hosts", "name": "not-a-list-arg"},
    )
    assert result["isError"] is True
    assert result["error"] == "invalid_arguments"


def test_unknown_provider_is_refused_with_the_supported_list(handler: MCPProtocolHandler) -> None:
    result = dispatch(
        handler,
        ROUTER_SPECS["navig_connectors"],
        {"action": "fetch", "provider": "../../etc", "resource_id": "x"},
    )
    assert result["isError"] is True
    assert result["error"] == "unsupported_action"


def test_provider_without_the_capability_is_refused(handler: MCPProtocolHandler) -> None:
    # ``bank`` is a fetch-only connector in this tree.
    result = dispatch(
        handler,
        ROUTER_SPECS["navig_connectors"],
        {"action": "search", "provider": "bank", "query": "x"},
    )
    assert result["isError"] is True
    assert result["error"] == "unsupported_action"
    assert "bank" not in result["supported_providers"]
    assert result["supported_providers"]  # the error names the providers that DO search


def test_connector_health_keeps_its_native_argument_name(handler: MCPProtocolHandler) -> None:
    calls = _fake(handler, "connector_health")
    dispatch(handler, ROUTER_SPECS["navig_connectors"], {"action": "health", "provider": "gmail"})
    assert calls["args"] == {"connector_id": "gmail"}


def test_gate_target_uses_the_original_safety(handler: MCPProtocolHandler) -> None:
    assert gate_target(handler, "navig_runtime_action", {"action": "mission_action"}) == (
        "navig_runtime_mission_action",
        "dangerous",
    )
    assert gate_target(handler, "navig_runtime_action", {"action": "create_mission"}) == (
        "navig_runtime_create_mission",
        "moderate",
    )
    assert gate_target(handler, "navig_memory", {"action": "retrieve"}) == (
        "memory_key_facts_retrieve",
        "safe",
    )
    assert gate_target(handler, "navig_fleet", {"action": "nope"}) is None


def test_gate_runs_once_for_the_router_and_names_the_original(
    handler: MCPProtocolHandler, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake(handler, "navig_runtime_mission_action", {"state": "running"})
    seen: list[dict[str, Any]] = []

    def fake_check(tool_name, safety, **kwargs):
        seen.append({"tool": tool_name, "safety": safety, **kwargs})
        from navig.tools.approval import ApprovalDecision

        return ApprovalDecision.APPROVED

    monkeypatch.setattr("navig.tools.approval.check_sync", fake_check)
    result = handler._execute_tool(
        "navig_runtime_action",
        {"action": "mission_action", "mission_id": "m1", "transition": "start"},
    )
    assert result == {"state": "running"}
    assert calls["args"] == {"mission_id": "m1", "action": "start"}
    assert len(seen) == 1
    assert seen[0]["tool"] == "navig_runtime_mission_action"
    assert seen[0]["safety"] == "dangerous"
    assert seen[0]["context"]["router"] == "navig_runtime_action"
    assert seen[0]["context"]["action"] == "mission_action"


def test_read_action_is_not_gated(
    handler: MCPProtocolHandler, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake(handler, "navig_list_hosts", {"hosts": []})
    monkeypatch.setattr(
        "navig.tools.approval.check_sync",
        lambda *a, **k: pytest.fail("a read action must not reach the approval gate"),
    )
    assert handler._execute_tool("navig_fleet", {"action": "list_hosts"}) == {"hosts": []}


def test_unknown_action_never_reaches_an_original(
    handler: MCPProtocolHandler, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "navig.tools.approval.check_sync",
        lambda *a, **k: pytest.fail("unknown action must not be gated or dispatched"),
    )
    result = handler._execute_tool("navig_fleet", {"action": "nope"})
    assert result["isError"] is True


def test_original_error_sentinel_is_preserved(handler: MCPProtocolHandler) -> None:
    _fake(handler, "recall_search", {"isError": True, "error": "backend down"})
    result = dispatch(handler, ROUTER_SPECS["navig_recall"], {"action": "search", "query": "x"})
    assert result["isError"] is True


def test_browser_user_router_runs_concurrently_like_dock(handler: MCPProtocolHandler) -> None:
    # dock_* opt into off-reader-thread execution; a router that fronts them must too.
    assert "navig_browser_user" in handler._concurrent_tools


def test_hidden_full_tool_is_not_callable_on_the_compact_surface(
    handler: MCPProtocolHandler,
) -> None:
    result = handler._handle_tools_call({"name": "navig_list_hosts", "arguments": {}})
    assert result.get("isError") is True
    assert "Unknown tool" in result["content"][0]["text"]


def test_router_error_shape() -> None:
    assert router_error("x", "y") == {"error": "x", "message": "y", "isError": True}


def test_colliding_original_argument_is_exposed_under_its_router_name(
    handler: MCPProtocolHandler,
) -> None:
    properties = handler.tools["navig_runtime_action"]["inputSchema"]["properties"]
    assert "transition" in properties, "mission_action's own `action` must not collide"
    assert "start" in (properties["transition"].get("description") or "")
    missing = dispatch(
        handler,
        ROUTER_SPECS["navig_runtime_action"],
        {"action": "mission_action", "mission_id": "m1"},
    )
    assert missing["isError"] is True
    assert missing["error"] == "invalid_arguments"
    assert missing["field"] == "transition"
