"""MCP tool bundle: NAVIG Harbor Bay — browse + acquire marketplace items.

Exposes the marketplace to any MCP client (Claude Code / Cursor / VS Code
Copilot / Claude Desktop): ``navig_bay_list`` browses installable NAVIG spaces,
skills, personas, blocks, plugins, and more (with live unlock state for the
current license); ``navig_bay_acquire`` installs the safe ones and tells the
caller how to acquire the rest.

Reuses the daemon's Bay engine (``gateway/deck/routes/catalog``) — ONE
implementation behind both the ``/api/deck/bay`` HTTP endpoint and these tools.
Safety: locked (paid) items are gated server-side and never installed; blocks
are never auto-executed (``action: "apply"`` hands off to the block flow, which
requires inputs/approvals).
"""

from __future__ import annotations

from typing import Any


def register(server: Any) -> None:
    """Register Bay tools (schemas + handlers) on the MCP server."""
    server.tools.update(
        {
            "navig_bay_list": {
                "name": "navig_bay_list",
                "description": (
                    "Browse the NAVIG Harbor Bay marketplace — installable spaces, skills, "
                    "personas, blocks, plugins, and more. Returns each item's slug, name, kind, "
                    "one-line tagline, whether it's unlocked for the current license, and whether "
                    "it's already installed. Filter with 'kind' (space/skill/persona/block/plugin/…) "
                    "or 'surface' (cli/deck/os/…)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "description": "Filter by item kind"},
                        "surface": {"type": "string", "description": "Filter by surface"},
                    },
                    "required": [],
                },
            },
            "navig_bay_acquire": {
                "name": "navig_bay_acquire",
                "description": (
                    "Acquire a Bay item by slug (call navig_bay_list first for the slug). Installs "
                    "github-backed items (space/skill/formation/prompt/webapp) and plugins; a persona "
                    "returns action 'activate', a block returns 'apply' (blocks execute with inputs/"
                    "approvals — never auto-run), a lens returns 'manual'. Paid/locked items return "
                    "action 'locked' with the Harbor tier required — a locked item is never installed."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "slug": {"type": "string", "description": "The item's slug (from navig_bay_list)"},
                    },
                    "required": ["slug"],
                },
            },
        }
    )
    server._tool_handlers.update(
        {
            "navig_bay_list": _tool_bay_list,
            "navig_bay_acquire": _tool_bay_acquire,
        }
    )


def _tool_bay_list(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    from navig.gateway.deck.routes.catalog import gather_bay_items

    data = gather_bay_items(kind=args.get("kind") or None, surface=args.get("surface") or None)
    # Lean projection — the fields an agent needs to decide + acquire.
    items = [
        {
            "slug": i.get("slug"),
            "name": i.get("name"),
            "kind": i.get("kind"),
            "tagline": i.get("tagline") or i.get("description") or "",
            "unlocked": i.get("unlocked"),
            "installed": i.get("installed", False),
        }
        for i in data.get("items", [])
    ]
    return {
        "items": items,
        "count": data.get("count", len(items)),
        "license_tier": data.get("license_tier"),
    }


def _tool_bay_acquire(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    from navig.gateway.deck.routes.catalog import _bay_item, acquire_bay_item

    slug = str(args.get("slug") or "").strip()
    if not slug:
        return {"ok": False, "error": "'slug' is required"}
    if _bay_item(slug) is None:
        return {"ok": False, "error": f"'{slug}' is not in the Bay — run navig_bay_list to see available items"}

    result = acquire_bay_item(slug)  # derives kind/install from the catalog + gates server-side
    action = result.get("action")
    if action == "locked":
        return {
            "ok": False,
            "locked": True,
            "slug": slug,
            "tier_required": result.get("tier_required"),
            "message": "This item is paid/locked — unlock it with a NAVIG Harbor subscription or a one-time purchase.",
        }
    if action == "error":
        return {"ok": False, "error": result.get("error")}
    return {"ok": True, **result}
