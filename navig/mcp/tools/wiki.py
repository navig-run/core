from typing import Any


def register(server: Any) -> None:
    """Register wiki manipulation tools."""
    server.tools.update(
        {
            "navig_search_wiki": {
                "name": "navig_search_wiki",
                "description": "Search the NAVIG wiki knowledge base for relevant documentation, guides, and notes.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "limit": {
                            "type": "integer",
                            "description": "Maximum results to return",
                            "default": 10,
                        },
                    },
                    "required": ["query"],
                },
            },
            "navig_list_wiki_pages": {
                "name": "navig_list_wiki_pages",
                "description": "List all pages in the NAVIG wiki knowledge base.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "folder": {
                            "type": "string",
                            "description": "Filter by folder (knowledge, technical, hub, external)",
                        }
                    },
                    "required": [],
                },
            },
            "navig_read_wiki_page": {
                "name": "navig_read_wiki_page",
                "description": "Read the content of a specific wiki page.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Wiki page path (e.g., 'knowledge/concepts/overview')",
                        }
                    },
                    "required": ["path"],
                },
            },
        }
    )

    server._tool_handlers.update(
        {
            "navig_search_wiki": _tool_search_wiki,
            "navig_list_wiki_pages": _tool_list_wiki_pages,
            "navig_read_wiki_page": _tool_read_wiki_page,
        }
    )


def _tool_search_wiki(server: Any, args: dict[str, Any]) -> list[dict[str, Any]]:
    """Search wiki pages."""
    from navig.commands.wiki import get_wiki_path, search_wiki

    wiki_path = get_wiki_path(server._config)
    if not wiki_path.exists():
        return [{"error": "Wiki not initialized"}]

    query = args.get("query", "")
    limit = args.get("limit", 10)

    results = search_wiki(wiki_path, query)
    return results[:limit]


def _tool_list_wiki_pages(server: Any, args: dict[str, Any]) -> list[dict[str, Any]]:
    """List wiki pages."""
    from navig.commands.wiki import get_wiki_path, list_wiki_pages

    wiki_path = get_wiki_path(server._config)
    if not wiki_path.exists():
        return [{"error": "Wiki not initialized"}]

    folder = args.get("folder")
    pages = list_wiki_pages(wiki_path, folder)

    return [
        {
            "path": p["path"],
            "title": p["title"],
            "folder": p["folder"],
            "modified": (
                p["modified"].isoformat()
                if hasattr(p["modified"], "isoformat")
                else str(p["modified"])
            ),
        }
        for p in pages
    ]


def _tool_read_wiki_page(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Read a wiki page."""
    from navig.commands.wiki import get_wiki_path, resolve_wiki_link

    wiki_path = get_wiki_path(server._config)
    if not wiki_path.exists():
        return {"error": "Wiki not initialized"}

    page_path = args.get("path", "")
    resolved = resolve_wiki_link(wiki_path, page_path)

    if not resolved:
        return {"error": f"Page not found: {page_path}"}

    content = resolved.read_text(encoding="utf-8")
    return {"path": str(resolved.relative_to(wiki_path)), "content": content}
