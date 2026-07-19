import sys
from typing import Any

from navig.mcp.tools import (
    agent,
    bay,
    blocks,
    cdp,
    connectors,
    desktop,
    filesystem,
    inventory,
    memory,
    runtime,
    system,
    wiki,
)


def register_all_tools(server: Any) -> None:
    """Register all extracted MCP tool bundles."""
    if not hasattr(server, "_tool_handlers"):
        server._tool_handlers = {}
    if not hasattr(server, "_tool_safety"):
        server._tool_safety = {}

    bundles: list[Any] = [inventory, wiki, system, agent, bay, blocks, runtime, memory, desktop, filesystem, cdp]
    if sys.platform == "win32":
        from navig.mcp.tools import windows  # noqa: PLC0415

        bundles.append(windows)
    # connectors is last: a bad connector manifest cannot block other tools
    bundles.append(connectors)

    for bundle in bundles:
        if hasattr(bundle, "register"):
            bundle.register(server)
