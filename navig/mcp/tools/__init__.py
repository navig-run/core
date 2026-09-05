import sys
from typing import Any

from navig.debug_logger import get_debug_logger
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

logger = get_debug_logger()


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
        if not hasattr(bundle, "register"):
            continue
        # Guard each bundle: if one bundle's register() raises (a platform/import
        # edge in desktop/cdp/windows, a bad manifest), every bundle AFTER it must
        # still register. Ordering 'connectors' last was a partial mitigation; this
        # protects them all.
        try:
            bundle.register(server)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "MCP tool bundle %s failed to register: %s",
                getattr(bundle, "__name__", bundle),
                exc,
            )
