"""MCP protocol message handling."""

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MCPMethod(str, Enum):
    """MCP JSON-RPC methods."""

    INITIALIZE = "initialize"
    INITIALIZED = "notifications/initialized"
    TOOLS_LIST = "tools/list"
    TOOLS_CALL = "tools/call"
    RESOURCES_LIST = "resources/list"
    RESOURCES_READ = "resources/read"
    RESOURCES_TEMPLATES_LIST = "resources/templates/list"
    PROMPTS_LIST = "prompts/list"
    PROMPTS_GET = "prompts/get"
    PING = "ping"
    CANCELLED = "notifications/cancelled"


@dataclass
class JSONRPCRequest:
    """JSON-RPC 2.0 request."""

    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: str | int | None = None
    jsonrpc: str = "2.0"

    def to_json(self) -> str:
        """Serialize to JSON string."""
        data = {
            "jsonrpc": self.jsonrpc,
            "method": self.method,
        }
        if self.params:
            data["params"] = self.params
        if self.id is not None:
            data["id"] = self.id
        return json.dumps(data)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        data = {
            "jsonrpc": self.jsonrpc,
            "method": self.method,
        }
        if self.params:
            data["params"] = self.params
        if self.id is not None:
            data["id"] = self.id
        return data


@dataclass
class JSONRPCResponse:
    """JSON-RPC 2.0 response."""

    id: str | int | None
    result: Any | None = None
    error: dict[str, Any] | None = None
    jsonrpc: str = "2.0"

    @classmethod
    def from_json(cls, data: str) -> "JSONRPCResponse":
        """Parse from JSON string."""
        parsed = json.loads(data)
        return cls(
            id=parsed.get("id"),
            result=parsed.get("result"),
            error=parsed.get("error"),
            jsonrpc=parsed.get("jsonrpc", "2.0"),
        )

    @classmethod
    def from_dict(cls, data: dict) -> "JSONRPCResponse":
        """Parse from dictionary."""
        return cls(
            id=data.get("id"),
            result=data.get("result"),
            error=data.get("error"),
            jsonrpc=data.get("jsonrpc", "2.0"),
        )

    @property
    def is_error(self) -> bool:
        """Check if response is an error."""
        return self.error is not None

    def get_error_message(self) -> str:
        """Get error message if present."""
        if self.error:
            return self.error.get("message", str(self.error))
        return ""


@dataclass
class MCPTool:
    """Tool definition from MCP server."""

    name: str
    description: str
    input_schema: dict[str, Any]
    server_id: str  # Which server provides this tool

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "server_id": self.server_id,
        }

    @classmethod
    def from_dict(cls, data: dict, server_id: str = "") -> "MCPTool":
        """Create from dictionary."""
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            input_schema=data.get("inputSchema", {}),
            server_id=server_id,
        )


@dataclass
class MCPResource:
    """Resource definition from MCP server."""

    uri: str
    name: str
    description: str | None = None
    mime_type: str | None = None
    server_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "uri": self.uri,
            "name": self.name,
            "description": self.description,
            "mimeType": self.mime_type,
            "server_id": self.server_id,
        }

    @classmethod
    def from_dict(cls, data: dict, server_id: str = "") -> "MCPResource":
        """Create from dictionary."""
        return cls(
            uri=data["uri"],
            name=data["name"],
            description=data.get("description"),
            mime_type=data.get("mimeType"),
            server_id=server_id,
        )


@dataclass
class MCPPrompt:
    """Prompt definition from MCP server."""

    name: str
    description: str | None = None
    arguments: list[dict[str, Any]] = field(default_factory=list)
    server_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "arguments": self.arguments,
            "server_id": self.server_id,
        }


@dataclass
class MCPCapabilities:
    """Server capabilities."""

    tools: bool = False
    resources: bool = False
    prompts: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "MCPCapabilities":
        """Parse capabilities from server response."""
        caps = data.get("capabilities", {})
        return cls(
            tools="tools" in caps,
            resources="resources" in caps,
            prompts="prompts" in caps,
        )
