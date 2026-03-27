from unittest.mock import MagicMock, patch

import pytest

from navig.mcp_server import MCPProtocolHandler, start_mcp_server


class MockToolHandler:
    def __init__(self, tools, handlers):
        self.tools = tools
        self._tool_handlers = handlers


def mock_register_all_tools(handler):
    handler.tools["dummy_tool"] = {"name": "dummy_tool", "description": "dummy"}
    handler._tool_handlers["dummy_tool"] = lambda self, args: {"status": "ok"}
    handler.tools["navig_list_hosts"] = {"name": "navig_list_hosts"}
    handler._tool_handlers["navig_list_hosts"] = lambda self, args: ["host1"]


@pytest.fixture
def mcp_handler():
    with patch(
        "navig.mcp.tools.register_all_tools", side_effect=mock_register_all_tools
    ):
        handler = MCPProtocolHandler()
        return handler


def test_handler_init(mcp_handler):
    assert "dummy_tool" in mcp_handler.tools
    assert "navig://config/hosts" in mcp_handler.resources


def test_handle_initialize(mcp_handler):
    res = mcp_handler._handle_initialize({})
    assert "protocolVersion" in res
    assert "capabilities" in res


def test_handle_ping(mcp_handler):
    assert mcp_handler._handle_ping({}) == {}


def test_handle_tools_list(mcp_handler):
    res = mcp_handler._handle_tools_list({})
    assert isinstance(res["tools"], list)
    assert len(res["tools"]) > 0


def test_handle_tools_call_success(mcp_handler):
    res = mcp_handler._handle_tools_call({"name": "dummy_tool", "arguments": {}})
    assert res.get("isError") is not True
    assert "status" in res["content"][0]["text"]


def test_handle_tools_call_unknown(mcp_handler):
    res = mcp_handler._handle_tools_call({"name": "unknown_tool"})
    assert res.get("isError") is True
    assert "Unknown tool" in res["content"][0]["text"]


def test_handle_tools_call_error(mcp_handler):
    mcp_handler._tool_handlers["error_tool"] = lambda self, args: 1 / 0
    mcp_handler.tools["error_tool"] = {}
    res = mcp_handler._handle_tools_call({"name": "error_tool"})
    assert res.get("isError") is True


def test_handle_resources_list(mcp_handler):
    res = mcp_handler._handle_resources_list({})
    assert "resources" in res
    assert len(res["resources"]) > 0


def test_handle_resources_read_success(mcp_handler):
    res = mcp_handler._handle_resources_read({"uri": "navig://config/hosts"})
    assert "contents" in res
    assert '[\n  "host1"\n]' in res["contents"][0]["text"]

    # Test a few other resources to satisfy branch coverage
    mcp_handler._tool_handlers["navig_list_apps"] = lambda *a: ["app1"]
    res2 = mcp_handler._handle_resources_read({"uri": "navig://config/apps"})
    assert "app1" in res2["contents"][0]["text"]

    mcp_handler._tool_handlers["navig_wiki_list"] = lambda *a: [
        {"title": "t", "path": "p"}
    ]
    res3 = mcp_handler._handle_resources_read({"uri": "navig://wiki"})
    assert "# Wiki" in res3["contents"][0]["text"]

    mcp_handler._tool_handlers["navig_get_context"] = lambda *a: {"c": 1}
    res4 = mcp_handler._handle_resources_read({"uri": "navig://context"})
    assert "c" in res4["contents"][0]["text"]

    mcp_handler._tool_handlers["navig_agent_status_get"] = lambda *a: {"s": 2}
    res5 = mcp_handler._handle_resources_read({"uri": "navig://agent/status"})
    assert "s" in res5["contents"][0]["text"]

    mcp_handler._tool_handlers["navig_agent_goal_list"] = lambda *a: {"g": 3}
    res6 = mcp_handler._handle_resources_read({"uri": "navig://agent/goals"})
    assert "g" in res6["contents"][0]["text"]

    mcp_handler._tool_handlers["navig_agent_remediation_list"] = lambda *a: {"r": 4}
    res7 = mcp_handler._handle_resources_read({"uri": "navig://agent/remediation"})
    assert "r" in res7["contents"][0]["text"]

    mcp_handler._tool_handlers["navig_agent_learning_run"] = lambda *a: {"l": 5}
    res8 = mcp_handler._handle_resources_read({"uri": "navig://agent/learning"})
    assert "l" in res8["contents"][0]["text"]

    mcp_handler._tool_handlers["navig_agent_service_status"] = lambda *a: {"ss": 6}
    res9 = mcp_handler._handle_resources_read({"uri": "navig://agent/service"})
    assert "ss" in res9["contents"][0]["text"]


def test_handle_resources_read_unknown(mcp_handler):
    res = mcp_handler._handle_resources_read({"uri": "navig://unknown"})
    assert res.get("isError") is True


def test_handle_message(mcp_handler):
    # Valid
    res = mcp_handler.handle_message({"method": "ping", "id": 1})
    assert res["id"] == 1
    assert "result" in res

    # Unknown method
    res = mcp_handler.handle_message({"method": "unknown_method", "id": 2})
    assert res["id"] == 2
    assert "error" in res

    # Expection
    mcp_handler._handlers["error_method"] = lambda p: 1 / 0
    res = mcp_handler.handle_message({"method": "error_method", "id": 3})
    assert res["id"] == 3
    assert "error" in res


def test_start_mcp_server():
    with patch("navig.mcp_server.MCPProtocolHandler") as mock_class:
        mock_instance = MagicMock()
        mock_class.return_value = mock_instance

        start_mcp_server("stdio")
        mock_instance.run_stdio.assert_called_once()

    with patch("navig.mcp_server._run_websocket_server") as mock_ws:
        start_mcp_server("websocket", port=3005, token="abc")
        mock_ws.assert_called_once()

    with pytest.raises(ValueError):
        start_mcp_server("unknown")


# ---------------------------------------------------------------------------
# Test Memory Handlers
# ---------------------------------------------------------------------------
from navig.mcp_server import (
    memory_forget,
    memory_remember,
    memory_retrieve,
    memory_stats,
)


@pytest.mark.asyncio
@patch("navig.mcp_server._memory_store")
async def test_memory_handlers(mock_store):
    store_instance = MagicMock()
    mock_store.return_value = store_instance

    # Retrieve
    with patch("navig.memory.fact_retriever.FactRetriever") as rc:
        ret_inst = MagicMock()
        rc.return_value = ret_inst
        fact_mock = MagicMock()
        fact_mock.model_dump.return_value = {"id": "1"}
        ret_inst.retrieve.return_value = [fact_mock]

        res = await memory_retrieve("q")
        assert res["facts"][0]["id"] == "1"

    # Remember
    with patch("navig.memory.fact_extractor.FactExtractor") as ec:
        ext_inst = MagicMock()
        ec.return_value = ext_inst
        from unittest.mock import AsyncMock

        ext_inst.extract_and_store = AsyncMock(return_value=1)

        res = await memory_remember("hello")
        assert res["added"] == 1

    # Forget
    store_instance.soft_delete.return_value = True
    res = await memory_forget("1")
    assert res["deleted"] is True

    # Stats
    store_instance.stats.return_value = {"total": 5}
    res = await memory_stats()
    assert res["total"] == 5


from navig.mcp_server import generate_claude_mcp_config, generate_vscode_mcp_config


def test_generate_mcp_configs():
    assert "mcpServers" in generate_vscode_mcp_config()
    assert "mcpServers" in generate_claude_mcp_config()
