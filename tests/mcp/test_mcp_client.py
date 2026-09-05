"""Unit tests for the MCP client module."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from navig.mcp.client import MCPClient, MCPClientConfig, _error_text_from_content
from navig.mcp.protocol import (
    JSONRPCRequest,
    JSONRPCResponse,
    MCPCapabilities,
    MCPMethod,
    MCPResource,
    MCPTool,
)
from navig.mcp.registry import MCPClientManager

pytestmark = pytest.mark.integration


class TestJSONRPCProtocol:
    """Tests for JSON-RPC protocol classes."""

    def test_request_creation(self):
        """Request should be created with proper fields."""
        req = JSONRPCRequest(method=MCPMethod.INITIALIZE, params={"a": 1}, id=1)

        assert req.id == 1
        assert req.jsonrpc == "2.0"
        assert req.method == MCPMethod.INITIALIZE

    def test_request_to_dict_with_id(self):
        """Request should serialize to dict with id."""
        req = JSONRPCRequest(
            method=MCPMethod.TOOLS_CALL,
            params={"name": "test", "arguments": {}},
            id=42,
        )

        data = req.to_dict()

        assert data["jsonrpc"] == "2.0"
        assert "params" in data
        assert data["id"] == 42

    def test_request_to_dict_without_id(self):
        """Request without id should not include id in dict."""
        req = JSONRPCRequest(
            method=MCPMethod.TOOLS_LIST,
        )

        data = req.to_dict()

        assert data["jsonrpc"] == "2.0"
        assert "id" not in data  # No id when None

    def test_response_from_dict_success(self):
        """Response should parse success result."""
        data = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"tools": []},
        }

        resp = JSONRPCResponse.from_dict(data)

        assert resp.id == 1
        assert resp.result == {"tools": []}
        assert resp.error is None

    def test_response_from_dict_error(self):
        """Response should parse error."""
        data = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32600, "message": "Invalid Request"},
        }

        resp = JSONRPCResponse.from_dict(data)

        assert resp.error is not None
        assert resp.error["code"] == -32600
        assert resp.result is None


class TestMCPTool:
    """Tests for MCPTool dataclass."""

    def test_tool_from_dict(self):
        """Tool should parse from server response."""
        data = {
            "name": "read_file",
            "description": "Read contents of a file",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
        }

        tool = MCPTool.from_dict(data)

        assert tool.name == "read_file"
        assert tool.description == "Read contents of a file"
        assert "path" in tool.input_schema.get("properties", {})


class TestMCPResource:
    """Tests for MCPResource dataclass."""

    def test_resource_from_dict(self):
        """Resource should parse from server response."""
        data = {
            "uri": "file:///path/to/resource",
            "name": "config.json",
            "mimeType": "application/json",
            "description": "Configuration file",
        }

        resource = MCPResource.from_dict(data)

        assert resource.uri == "file:///path/to/resource"
        assert resource.name == "config.json"
        assert resource.mime_type == "application/json"


class TestMCPCapabilities:
    """Tests for MCPCapabilities dataclass."""

    def test_capabilities_from_dict(self):
        """Capabilities should parse from initialize response."""
        data = {
            "capabilities": {
                "tools": {},
                "resources": {},
            }
        }

        caps = MCPCapabilities.from_dict(data)

        assert caps.tools == True
        assert caps.resources == True
        assert caps.prompts == False


class TestMCPClientConfig:
    """Tests for MCPClientConfig dataclass."""

    def test_config_defaults(self):
        """Config should have sensible defaults."""
        config = MCPClientConfig(id="test")

        assert config.id == "test"
        assert config.transport == "stdio"
        assert config.auto_connect == True

    def test_config_with_command(self):
        """Config should accept command for stdio transport."""
        config = MCPClientConfig(
            id="test",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem"],
        )

        assert config.command == "npx"
        assert len(config.args) == 2

    def test_config_with_url(self):
        """Config should accept URL for SSE transport."""
        config = MCPClientConfig(
            id="test",
            transport="sse",
            url="http://localhost:3000/sse",
        )

        assert config.url == "http://localhost:3000/sse"
        assert config.transport == "sse"

    def test_config_from_dict(self):
        """Config should be created from dictionary."""
        data = {
            "command": "python",
            "args": ["-m", "my_server"],
            "env": {"DEBUG": "1"},
        }

        config = MCPClientConfig.from_dict("my-server", data)

        assert config.id == "my-server"
        assert config.command == "python"
        assert config.args == ["-m", "my_server"]

    def test_config_from_dict_coerces_string_bools(self):
        """`navig config set mcp.clients.<id>.enabled false` stores the STRING
        "false" (truthy). from_dict must coerce enabled/auto_connect so a config-set
        toggle takes effect — the registry gates on the parsed `cfg.enabled`."""
        for off in ("false", "False", "0", "off", "no"):
            cfg = MCPClientConfig.from_dict("x", {"enabled": off, "auto_connect": off})
            assert cfg.enabled is False, off
            assert cfg.auto_connect is False, off
        for on in ("true", "True", "1", "on", "yes"):
            cfg = MCPClientConfig.from_dict("x", {"enabled": on, "auto_connect": on})
            assert cfg.enabled is True, on
            assert cfg.auto_connect is True, on
        # real bools pass through; missing keys keep the True defaults
        assert MCPClientConfig.from_dict("x", {"enabled": False}).enabled is False
        assert MCPClientConfig.from_dict("x", {}).enabled is True
        assert MCPClientConfig.from_dict("x", {}).auto_connect is True


class TestMCPClient:
    """Tests for MCPClient class."""

    @pytest.fixture
    def client(self):
        """Create test client."""
        config = MCPClientConfig(
            id="test-client",
            command="echo",
            args=["test"],
        )
        return MCPClient(config)

    def test_client_not_connected_initially(self, client):
        """Client should not be connected initially."""
        assert not client.is_connected

    def test_client_id(self, client):
        """Client should expose id from config."""
        assert client.config.id == "test-client"

    async def test_discover_tools_skips_malformed_and_keeps_valid(self, client):
        """One malformed tool (missing 'name') must be skipped, not abort discovery —
        otherwise connect() catches the raise and the WHOLE client fails to connect,
        losing every other tool the server offered."""
        client._send_request = AsyncMock(
            return_value=JSONRPCResponse(
                id=1,
                result={
                    "tools": [
                        {"description": "no name"},  # malformed → KeyError, skipped
                        {"name": "good", "inputSchema": {}},  # valid
                    ]
                },
            )
        )
        await client._discover_tools()  # must NOT raise
        assert "good" in client._tools
        assert len(client._tools) == 1

    async def test_discover_resources_skips_malformed_and_keeps_valid(self, client):
        client._send_request = AsyncMock(
            return_value=JSONRPCResponse(
                id=1,
                result={
                    "resources": [
                        {"name": "no uri"},  # malformed → KeyError, skipped
                        {"uri": "file:///a", "name": "good"},  # valid
                    ]
                },
            )
        )
        await client._discover_resources()
        assert "file:///a" in client._resources
        assert len(client._resources) == 1

    async def test_discover_prompts_skips_malformed_and_keeps_valid(self, client):
        client._send_request = AsyncMock(
            return_value=JSONRPCResponse(
                id=1,
                result={
                    "prompts": [
                        {"description": "no name"},  # malformed → KeyError, skipped
                        {"name": "good"},  # valid
                    ]
                },
            )
        )
        await client._discover_prompts()
        assert "good" in client._prompts
        assert len(client._prompts) == 1


class TestMCPClientManager:
    """Tests for MCPClientManager class."""

    @pytest.fixture
    def manager(self):
        """Create empty client manager."""
        return MCPClientManager()

    def test_initially_empty(self, manager):
        """Manager should start with no clients."""
        assert len(manager.clients) == 0
        assert len(manager.get_all_tools()) == 0

    async def test_add_client(self, manager):
        """Should add client config."""
        config = MCPClientConfig(
            id="test",
            command="echo",
            args=["test"],
        )

        await manager.add_client(config)

        assert "test" in manager.clients

    def test_find_tool_not_found(self, manager):
        """Should return None for unknown tool."""
        result = manager.find_tool("unknown_tool")
        assert result is None

    async def test_remove_client(self, manager):
        """Should remove client."""
        # Add a mock client
        mock_client = MagicMock()
        mock_client.disconnect = AsyncMock()
        manager._clients["test"] = mock_client

        await manager.remove_client("test")

        assert "test" not in manager.clients
        mock_client.disconnect.assert_called_once()

    async def test_stop(self, manager):
        """Should stop all clients."""
        mock1 = MagicMock()
        mock1.disconnect = AsyncMock()
        mock2 = MagicMock()
        mock2.disconnect = AsyncMock()

        manager._clients["client1"] = mock1
        manager._clients["client2"] = mock2

        await manager.stop()

        mock1.disconnect.assert_called_once()
        mock2.disconnect.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# ── call_tool must surface a remote tool-level failure (result.isError) ──────


def _connected_client() -> MCPClient:
    """A client that passes _assert_connected with one registered tool."""
    client = MCPClient(MCPClientConfig(id="t", command="echo", args=["x"]))
    transport = MagicMock()
    transport.is_connected = MagicMock(return_value=True)
    client._transport = transport
    client._initialized = True  # is_connected requires the handshake flag too
    client._tools = {"mytool": object()}
    return client


def test_error_text_from_content_extracts_text():
    assert (
        _error_text_from_content([{"type": "text", "text": "boom"}]) == "boom"
    )
    assert (
        _error_text_from_content(
            [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
        )
        == "a b"
    )
    assert _error_text_from_content("plain") == "plain"
    assert _error_text_from_content(None) == "remote tool reported an error"
    assert _error_text_from_content([]) == "remote tool reported an error"


async def test_call_tool_raises_on_result_iserror():
    """A tool-level failure (result.isError true, JSON-RPC OK) must RAISE, not be
    returned as a normal value — the phantom-success mirror of the server bug."""
    client = _connected_client()
    client._send_request = AsyncMock(
        return_value=JSONRPCResponse(
            id=1,
            result={
                "isError": True,
                "content": [{"type": "text", "text": "permission denied"}],
            },
        )
    )
    with pytest.raises(RuntimeError, match="permission denied"):
        await client.call_tool("mytool", {})


async def test_call_tool_success_unwraps_text():
    client = _connected_client()
    client._send_request = AsyncMock(
        return_value=JSONRPCResponse(
            id=1, result={"content": [{"type": "text", "text": "ok result"}]}
        )
    )
    assert await client.call_tool("mytool", {}) == "ok result"
