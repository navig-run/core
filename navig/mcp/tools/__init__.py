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
    dock,
    filesystem,
    inventory,
    memory,
    perplexity,
    recall,
    runtime,
    spaces,
    system,
    wiki,
)

logger = get_debug_logger()


def bundle_name(bundle: Any) -> str:
    """``navig.mcp.tools.recall`` → ``recall`` — the name ``--only`` selects by."""
    return str(getattr(bundle, "__name__", bundle)).rsplit(".", 1)[-1]


def all_bundles() -> list[Any]:
    """Every bundle this platform can actually serve, in registration order.

    ``desktop`` is Windows-only and is registered as such. It used to be unconditional, so
    a ``navig mcp serve`` in a Linux container (the Glama/hosted case) advertised 25
    Windows UI-automation tools whose every call raised
    ``ValueError("desktop tools are Windows only")`` — ``_desktop_client()`` in
    ``desktop.py`` is the guard, and it only runs AFTER the client has been told the tool
    exists. A capability list a client cannot use is worse than a shorter one: agents plan
    against it, and a reviewer reads it as a promise.
    """
    bundles: list[Any] = [inventory, wiki, system, agent, bay, blocks, runtime, memory, recall,
                          filesystem, spaces, cdp, dock, perplexity]
    if sys.platform == "win32":
        bundles.append(desktop)
        from navig.mcp.tools import windows  # noqa: PLC0415

        bundles.append(windows)
        # The context-menu manager is a Windows-only capability; registering it here
        # (rather than inside `windows`) keeps that module's surface unchanged for the
        # tools that already ship there. Imported defensively on purpose: main currently
        # names this module without shipping it, and an unconditional import raised
        # ImportError out of `all_bundles()` — taking EVERY MCP tool down with it, on the
        # one platform where the guard runs. A bundle that is not in this tree is skipped;
        # `register_all_tools` already guards each bundle's own `register()` for the same
        # reason.
        try:
            from navig.mcp.tools import contextmenu  # noqa: PLC0415
        except ImportError:  # pragma: no cover - exercised by a tree without the module
            contextmenu = None
        if contextmenu is not None:
            bundles.append(contextmenu)
    # connectors is last: a bad connector manifest cannot block other tools
    bundles.append(connectors)
    return bundles


def register_all_tools(server: Any, only: list[str] | tuple[str, ...] | None = None) -> None:
    """Register the MCP tool bundles — all of them, or just the ones named in *only*.

    ``only`` is what ``navig mcp serve --only recall`` passes: a narrow server whose
    ``tools/list`` shows nothing but those bundles. An unknown name raises ``ValueError``
    (a typo must not silently serve zero tools).
    """
    if not hasattr(server, "_tool_handlers"):
        server._tool_handlers = {}
    if not hasattr(server, "_tool_safety"):
        server._tool_safety = {}

    bundles = all_bundles()
    if only:
        wanted = {n.strip() for n in only if n and n.strip()}
        known = {bundle_name(b) for b in bundles}
        unknown = sorted(wanted - known)
        if unknown:
            raise ValueError(f"unknown MCP tool bundle(s): {', '.join(unknown)} (available: {', '.join(sorted(known))})")
        bundles = [b for b in bundles if bundle_name(b) in wanted]

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

    # Titles, safety annotations and schema strictness are applied in ONE place, after
    # every bundle has registered — including the connector tools generated at runtime.
    # Doing it per bundle would have meant editing all sixteen and keeping them in sync.
    from navig.mcp.annotations import annotate_tools

    annotate_tools(server.tools, getattr(server, "_tool_safety", None))
