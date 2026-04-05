"""Tests for the MCP ↔ Connector bridge adapter (navig/mcp/tools/connectors.py).

All tests are fully isolated:
- A fresh ConnectorRegistry is used (never the global singleton).
- No real connector instances are created (manifest accessed from class only).
- No network calls, no file I/O, no vault access.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.connectors.base import BaseConnector, ConnectorManifest
from navig.connectors.errors import ConnectorNotFoundError
from navig.connectors.registry import ConnectorRegistry
from navig.connectors.types import (
    Action,
    ActionResult,
    ActionType,
    ConnectorDomain,
    Resource,
    ResourceType,
)
from navig.mcp.tools.connectors import handle_connector_call, list_connector_tools, register

# ---------------------------------------------------------------------------
# Helpers — lightweight fake connectors (no real I/O)
# ---------------------------------------------------------------------------


def _make_manifest(
    cid: str = "testconn",
    can_search: bool = True,
    can_fetch: bool = True,
    can_act: bool = True,
) -> ConnectorManifest:
    return ConnectorManifest(
        id=cid,
        display_name=f"TestConn {cid}",
        description=f"A test connector for {cid}.",
        domain=ConnectorDomain.DATA,
        icon="🧪",
        requires_oauth=False,
        can_search=can_search,
        can_fetch=can_fetch,
        can_act=can_act,
    )


def _make_connector_class(
    cid: str = "testconn",
    *,
    can_search: bool = True,
    can_fetch: bool = True,
    can_act: bool = True,
) -> type[BaseConnector]:
    """Dynamically build a minimal BaseConnector subclass."""

    class _Conn(BaseConnector):
        manifest = _make_manifest(cid, can_search=can_search, can_fetch=can_fetch, can_act=can_act)

        async def search(self, query: str, **kw: Any):  # type: ignore[override]
            return []

        async def fetch(self, resource_id: str, **kw: Any):  # type: ignore[override]
            return None

        async def act(self, action: Action):  # type: ignore[override]
            return ActionResult(success=False, error="stub")

        async def health_check(self):
            from navig.connectors.types import HealthStatus

            return HealthStatus(ok=True, latency_ms=0.0)

    _Conn.__name__ = f"Conn_{cid}"
    _Conn.__qualname__ = _Conn.__name__
    return _Conn


def _fresh_registry(*connector_classes: type) -> ConnectorRegistry:
    """Return a fresh, isolated ConnectorRegistry (not the global singleton)."""
    reg = object.__new__(ConnectorRegistry)
    reg._classes = {}
    reg._instances = {}
    for cls in connector_classes:
        reg.register(cls)
    return reg


def _make_resource(cid: str = "testconn") -> Resource:
    return Resource(
        id=f"{cid}:res:1",
        source=cid,
        title="Test result",
        preview="A preview",
        resource_type=ResourceType.GENERIC,
    )


# ---------------------------------------------------------------------------
# list_connector_tools
# ---------------------------------------------------------------------------


def test_list_tools_empty_registry() -> None:
    reg = _fresh_registry()
    tools = list_connector_tools(registry=reg)
    assert tools == []


def test_list_tools_search_only() -> None:
    """A search-only connector produces exactly one tool."""
    Cls = _make_connector_class("alpha", can_search=True, can_fetch=False, can_act=False)
    reg = _fresh_registry(Cls)
    tools = list_connector_tools(registry=reg)
    assert len(tools) == 1
    t = tools[0]
    assert t["name"] == "connector.alpha.search"
    assert "alpha" in t["description"].lower() or "TestConn" in t["description"]
    schema = t["inputSchema"]
    assert schema["type"] == "object"
    assert "query" in schema["properties"]
    assert schema["required"] == ["query"]


def test_list_tools_all_capabilities() -> None:
    """A full-capability connector produces three tools."""
    Cls = _make_connector_class("full", can_search=True, can_fetch=True, can_act=True)
    reg = _fresh_registry(Cls)
    tools = list_connector_tools(registry=reg)
    names = [t["name"] for t in tools]
    assert "connector.full.search" in names
    assert "connector.full.fetch" in names
    assert "connector.full.act" in names
    assert len(names) == 3


def test_list_tools_multiple_connectors() -> None:
    """Two connectors with different caps → correct total tool count."""
    C1 = _make_connector_class("c1", can_search=True, can_fetch=False, can_act=False)
    C2 = _make_connector_class("c2", can_search=True, can_fetch=True, can_act=True)
    reg = _fresh_registry(C1, C2)
    tools = list_connector_tools(registry=reg)
    assert len(tools) == 4  # 1 + 3
    names = {t["name"] for t in tools}
    assert "connector.c1.search" in names
    assert "connector.c2.search" in names
    assert "connector.c2.fetch" in names
    assert "connector.c2.act" in names


def test_bad_manifest_connector_is_skipped() -> None:
    """A connector whose manifest property raises is warned + skipped; others load fine."""

    class _BadConn(BaseConnector):
        @property  # type: ignore[override]
        def manifest(self):  # type: ignore[override]
            raise RuntimeError("manifest exploded")

        async def search(self, query, **kw):
            return []

        async def fetch(self, rid, **kw):
            return None

        async def act(self, action):
            return ActionResult(success=False, error="stub")

        async def health_check(self):
            from navig.connectors.types import HealthStatus

            return HealthStatus(ok=True, latency_ms=0.0)

    GoodCls = _make_connector_class("good", can_search=True, can_fetch=False, can_act=False)
    reg = _fresh_registry(GoodCls)
    # Inject bad class bypassing register() (which checks manifest.id)
    reg._classes["bad"] = _BadConn

    with patch("navig.mcp.tools.connectors.logger") as mock_logger:
        tools = list_connector_tools(registry=reg)

    names = [t["name"] for t in tools]
    assert "connector.good.search" in names  # good connector still present
    assert not any("bad" in n for n in names)  # bad connector skipped
    # warning called with the bad connector id
    assert mock_logger.warning.called, "logger.warning should have been called"
    # warning uses %-format: args[1] is the connector id
    warn_args = mock_logger.warning.call_args[0]
    assert any("bad" in str(a) for a in warn_args)


# ---------------------------------------------------------------------------
# handle_connector_call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_search_routes_correctly() -> None:
    expected = [_make_resource("s1")]
    Cls = _make_connector_class("s1", can_search=True, can_fetch=False, can_act=False)
    reg = _fresh_registry(Cls)
    inst = reg.get("s1")
    inst.search = AsyncMock(return_value=expected)  # type: ignore[method-assign]

    result = await handle_connector_call("connector.s1.search", {"query": "hello"}, registry=reg)

    inst.search.assert_called_once_with("hello", limit=5)
    assert result == {"results": [expected[0].to_dict()]}


@pytest.mark.asyncio
async def test_handle_search_honours_limit() -> None:
    Cls = _make_connector_class("slim", can_search=True, can_fetch=False, can_act=False)
    reg = _fresh_registry(Cls)
    inst = reg.get("slim")
    inst.search = AsyncMock(return_value=[])  # type: ignore[method-assign]

    await handle_connector_call("connector.slim.search", {"query": "x", "limit": 3}, registry=reg)
    inst.search.assert_called_once_with("x", limit=3)


@pytest.mark.asyncio
async def test_handle_fetch_routes_correctly() -> None:
    expected = _make_resource("f1")
    Cls = _make_connector_class("f1", can_search=False, can_fetch=True, can_act=False)
    reg = _fresh_registry(Cls)
    inst = reg.get("f1")
    inst.fetch = AsyncMock(return_value=expected)  # type: ignore[method-assign]

    result = await handle_connector_call(
        "connector.f1.fetch", {"resource_id": "abc123"}, registry=reg
    )

    inst.fetch.assert_called_once_with("abc123")
    assert result == {"resource": expected.to_dict()}


@pytest.mark.asyncio
async def test_handle_fetch_none_resource() -> None:
    """fetch() returning None → {"resource": None}."""
    Cls = _make_connector_class("fnone", can_search=False, can_fetch=True, can_act=False)
    reg = _fresh_registry(Cls)
    inst = reg.get("fnone")
    inst.fetch = AsyncMock(return_value=None)  # type: ignore[method-assign]

    result = await handle_connector_call(
        "connector.fnone.fetch", {"resource_id": "x"}, registry=reg
    )
    assert result == {"resource": None}


@pytest.mark.asyncio
async def test_handle_act_routes_correctly() -> None:
    action_result = ActionResult(success=True)
    Cls = _make_connector_class("a1", can_search=False, can_fetch=False, can_act=True)
    reg = _fresh_registry(Cls)
    inst = reg.get("a1")
    inst.act = AsyncMock(return_value=action_result)  # type: ignore[method-assign]

    result = await handle_connector_call(
        "connector.a1.act",
        {"action_type": "archive", "resource_id": "msg:42", "params": {"label": "done"}},
        registry=reg,
    )

    call_args = inst.act.call_args[0][0]
    assert isinstance(call_args, Action)
    assert call_args.action_type == ActionType.ARCHIVE
    assert call_args.resource_id == "msg:42"
    assert call_args.params == {"label": "done"}
    assert result == action_result.to_dict()


@pytest.mark.asyncio
async def test_handle_act_invalid_action_type_raises() -> None:
    Cls = _make_connector_class("bad_act", can_search=False, can_fetch=False, can_act=True)
    reg = _fresh_registry(Cls)

    with pytest.raises(ValueError, match="Unknown action_type"):
        await handle_connector_call(
            "connector.bad_act.act", {"action_type": "explode"}, registry=reg
        )


@pytest.mark.asyncio
async def test_handle_unknown_connector_raises() -> None:
    """ConnectorNotFoundError propagates when connector id is not in registry."""
    reg = _fresh_registry()
    with pytest.raises(ConnectorNotFoundError):
        await handle_connector_call("connector.nope.search", {"query": "x"}, registry=reg)


@pytest.mark.asyncio
async def test_handle_malformed_tool_name_raises() -> None:
    reg = _fresh_registry()
    with pytest.raises(ValueError, match="Invalid connector tool name"):
        await handle_connector_call("not_a_connector_name", {}, registry=reg)


@pytest.mark.asyncio
async def test_handle_unknown_operation_raises() -> None:
    Cls = _make_connector_class("op_test")
    reg = _fresh_registry(Cls)
    with pytest.raises(ValueError, match="Unsupported connector operation"):
        await handle_connector_call("connector.op_test.explode", {}, registry=reg)


# ---------------------------------------------------------------------------
# register()
# ---------------------------------------------------------------------------


def test_register_updates_server_tools() -> None:
    """register(server) pushes connector tool schemas into server.tools."""
    Cls = _make_connector_class("reg1", can_search=True, can_fetch=True, can_act=False)
    reg = _fresh_registry(Cls)
    server = MagicMock()
    server.tools = {}

    with patch("navig.mcp.tools.connectors.get_connector_registry", return_value=reg):
        register(server)

    assert "connector.reg1.search" in server.tools
    assert "connector.reg1.fetch" in server.tools
    assert "connector.reg1.act" not in server.tools  # can_act=False


def test_register_creates_tools_attr_if_missing() -> None:
    """register() tolerates a server with no .tools attribute."""
    reg = _fresh_registry()  # empty
    server = MagicMock(spec=[])  # no .tools

    with patch("navig.mcp.tools.connectors.get_connector_registry", return_value=reg):
        register(server)  # must not raise


def test_register_populates_tool_handlers() -> None:
    """register() must populate server._tool_handlers so tools are actually callable."""
    Cls = _make_connector_class("h1", can_search=True, can_fetch=True, can_act=True)
    reg = _fresh_registry(Cls)
    server = MagicMock()
    server.tools = {}
    server._tool_handlers = {}

    with patch("navig.mcp.tools.connectors.get_connector_registry", return_value=reg):
        register(server)

    # Per-connector handlers present
    assert "connector.h1.search" in server._tool_handlers
    assert "connector.h1.fetch" in server._tool_handlers
    assert "connector.h1.act" in server._tool_handlers
    # Meta-tool handlers present
    assert "connector.list" in server._tool_handlers
    assert "connector.health" in server._tool_handlers
    # All handlers are callable
    for name, fn in server._tool_handlers.items():
        if name.startswith("connector."):
            assert callable(fn), f"Handler for {name!r} is not callable"


def test_register_meta_tools_in_server_tools() -> None:
    """connector.list and connector.health appear in server.tools regardless of connectors."""
    reg = _fresh_registry()  # empty — no connectors
    server = MagicMock()
    server.tools = {}
    server._tool_handlers = {}

    with patch("navig.mcp.tools.connectors.get_connector_registry", return_value=reg):
        register(server)

    assert "connector.list" in server.tools
    assert "connector.health" in server.tools
    assert "connector.list" in server._tool_handlers
    assert "connector.health" in server._tool_handlers


def test_tool_connector_list_returns_all() -> None:
    """_tool_connector_list returns all connector summaries with capability flags."""
    from navig.mcp.tools.connectors import _tool_connector_list

    C1 = _make_connector_class("l1", can_search=True, can_fetch=False, can_act=False)
    C2 = _make_connector_class("l2", can_search=True, can_fetch=True, can_act=True)
    reg = _fresh_registry(C1, C2)

    with patch("navig.mcp.tools.connectors.get_connector_registry", return_value=reg):
        result = _tool_connector_list(None, {})

    assert "connectors" in result
    ids = {c["id"] for c in result["connectors"]}
    assert {"l1", "l2"} == ids

    l1_row = next(c for c in result["connectors"] if c["id"] == "l1")
    assert l1_row["can_search"] is True
    assert l1_row["can_fetch"] is False

    l2_row = next(c for c in result["connectors"] if c["id"] == "l2")
    assert l2_row["can_act"] is True


@pytest.mark.asyncio
async def test_tool_connector_health_returns_health_dict() -> None:
    """_tool_connector_health instantiates the connector and calls health_check()."""
    from navig.connectors.types import HealthStatus
    from navig.mcp.tools.connectors import _tool_connector_health

    Cls = _make_connector_class("hc1")
    reg = _fresh_registry(Cls)
    inst = reg.get("hc1")
    inst.health_check = AsyncMock(return_value=HealthStatus(ok=True, latency_ms=12.5))  # type: ignore[method-assign]

    with patch("navig.mcp.tools.connectors.get_connector_registry", return_value=reg):
        result = _tool_connector_health(None, {"connector_id": "hc1"})

    assert result.get("ok") is True
    assert "latency_ms" in result


def test_tool_connector_health_missing_id() -> None:
    """Missing connector_id → error dict."""
    from navig.mcp.tools.connectors import _tool_connector_health

    result = _tool_connector_health(None, {})
    assert result.get("isError") is True
    assert "connector_id" in result["error"]


def test_tool_connector_health_unknown_connector() -> None:
    """Unknown connector id → error dict (not an unhandled exception)."""
    from navig.mcp.tools.connectors import _tool_connector_health

    reg = _fresh_registry()  # empty
    with patch("navig.mcp.tools.connectors.get_connector_registry", return_value=reg):
        result = _tool_connector_health(None, {"connector_id": "ghost"})

    assert result.get("isError") is True


def test_sync_handler_dispatches_via_thread_pool() -> None:
    """_make_sync_connector_handler returns a callable that runs handle_connector_call."""
    from navig.mcp.tools.connectors import _make_sync_connector_handler

    expected = {"results": []}
    with patch(
        "navig.mcp.tools.connectors.handle_connector_call",
        new=AsyncMock(return_value=expected),
    ) as mock_hcc:
        handler = _make_sync_connector_handler("connector.mock.search")
        result = handler(None, {"query": "test"})

    mock_hcc.assert_called_once_with("connector.mock.search", {"query": "test"})
    assert result == expected


def test_register_all_tools_preserves_existing_bundles() -> None:
    """register_all_tools adds connector tools without clobbering memory/wiki."""
    from navig.mcp.tools import register_all_tools

    server = MagicMock()
    server.tools = {}
    server._tool_handlers = {}

    Cls = _make_connector_class("brdg", can_search=True, can_fetch=False, can_act=False)
    fresh_reg = _fresh_registry(Cls)

    with patch("navig.mcp.tools.connectors.get_connector_registry", return_value=fresh_reg):
        register_all_tools(server)

    # Connector tool present
    assert "connector.brdg.search" in server.tools
    # Built-in bundles also registered (memory / wiki add to server.tools)
    tool_names = set(server.tools.keys())
    assert any("memory" in n or "wiki" in n or "navig" in n for n in tool_names), (
        f"Expected memory/wiki/runtime tools in server.tools; got: {sorted(tool_names)}"
    )
