from typing import Any

from navig.mcp.tools import agent, connectors, desktop, inventory, memory, runtime, system, wiki


def register_all_tools(server: Any) -> None:
    """Register all extracted MCP tool bundles."""
    if not hasattr(server, "_tool_handlers"):
        server._tool_handlers = {}

    # connectors is last: a bad connector manifest cannot block memory/wiki/runtime
    for bundle in [inventory, wiki, system, agent, runtime, memory, desktop, connectors]:
        if hasattr(bundle, "register"):
            bundle.register(server)
