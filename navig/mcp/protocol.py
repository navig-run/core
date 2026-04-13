"""MCP protocol message types."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MCPMethod(str, Enum):
    """MCP JSON-RPC method identifiers."""

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
    """JSON-RPC 2.0 request message."""

    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: str | int | None = None
    jsonrpc: str = "2.0"

    def to_json(self) -> str:
        """Serialise to a JSON string."""
        data: dict[str, Any] = {"jsonrpc": self.jsonrpc, "method": self.method}
        if self.params:
            data["params"] = self.params
        if self.id is not None:
            data["id"] = self.id
        return json.dumps(data)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        data: dict[str, Any] = {"jsonrpc": self.jsonrpc, "method": self.method}
        if self.params:
            data["params"] = self.params
        if self.id is not None:
            data["id"] = self.id
        return data


@dataclass
class JSONRPCResponse:
    """JSON-RPC 2.0 response message."""

    id: str | int | None
    result: Any = None
    error: dict[str, Any] | None = None
    jsonrpc: str = "2.0"

    @classmethod
    def from_json(cls, data: str) -> JSONRPCResponse:
        """Parse from a JSON string."""
        parsed = json.loads(data)
        return cls(
            id=parsed.get("id"),
            result=parsed.get("result"),
            error=parsed.get("error"),
            jsonrpc=parsed.get("jsonrpc", "2.0"),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JSONRPCResponse:
        """Parse from a plain dictionary."""
        return cls(
            id=data.get("id"),
            result=data.get("result"),
            error=data.get("error"),
            jsonrpc=data.get("jsonrpc", "2.0"),
        )

    @property
    def is_error(self) -> bool:
        return self.error is not None

    def get_error_message(self) -> str:
        """Return the human-readable error message, or an empty string."""
        if self.error:
            return self.error.get("message", str(self.error))
        return ""


@dataclass
class MCPTool:
    """Tool definition received from an MCP server."""

    name: str
    description: str
    input_schema: dict[str, Any]
    server_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "server_id": self.server_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], server_id: str = "") -> MCPTool:
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            input_schema=data.get("inputSchema", {}),
            server_id=server_id,
        )


@dataclass
class MCPResource:
    """Resource definition received from an MCP server."""

    uri: str
    name: str
    description: str | None = None
    mime_type: str | None = None
    server_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "name": self.name,
            "description": self.description,
            "mimeType": self.mime_type,
            "server_id": self.server_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], server_id: str = "") -> MCPResource:
        return cls(
            uri=data["uri"],
            name=data["name"],
            description=data.get("description"),
            mime_type=data.get("mimeType"),
            server_id=server_id,
        )


@dataclass
class MCPPrompt:
    """Prompt definition received from an MCP server."""

    name: str
    description: str | None = None
    arguments: list[dict[str, Any]] = field(default_factory=list)
    server_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "arguments": self.arguments,
            "server_id": self.server_id,
        }


@dataclass
class MCPCapabilities:
    """Server capability flags parsed from an ``initialize`` response."""

    tools: bool = False
    resources: bool = False
    prompts: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MCPCapabilities:
        caps = data.get("capabilities", {})
        return cls(
            tools="tools" in caps,
            resources="resources" in caps,
            prompts="prompts" in caps,
        )
