from typing import Dict, Any, Callable
from navig.mcp.tools import inventory, wiki, system, agent, runtime, memory, desktop

def register_all_tools(server: Any) -> None:
    """Register all extracted MCP tool bundles."""
    if not hasattr(server, "_tool_handlers"):
        server._tool_handlers = {}
        
    for bundle in [inventory, wiki, system, agent, runtime, memory, desktop]:
        if hasattr(bundle, "register"):
            bundle.register(server)
