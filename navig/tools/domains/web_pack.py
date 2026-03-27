"""
Web Tool Pack - web_search, web_fetch, docs_search.

Wraps navig.tools.web functions for the ToolRouter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from navig.tools.router import ToolRegistry


def register_tools(registry: "ToolRegistry") -> None:
    from navig.tools.router import SafetyLevel, ToolDomain, ToolMeta

    registry.register(
        ToolMeta(
            name="web_search",
            domain=ToolDomain.WEB,
            description="Search the web using Brave or DuckDuckGo.",
            safety=SafetyLevel.SAFE,
            module_path="navig.tools.web",
            handler_name="web_search",
            parameters_schema={
                "query": {
                    "type": "string",
                    "required": True,
                    "description": "Search query",
                },
                "count": {
                    "type": "integer",
                    "default": 5,
                    "description": "Number of results (1-10)",
                },
                "provider": {
                    "type": "string",
                    "default": "auto",
                    "description": "brave|duckduckgo|auto",
                },
            },
            tags=["search", "research", "web"],
        )
    )

    registry.register(
        ToolMeta(
            name="web_fetch",
            domain=ToolDomain.WEB,
            description="Fetch a URL and extract readable content.",
            safety=SafetyLevel.SAFE,
            module_path="navig.tools.web",
            handler_name="web_fetch",
            parameters_schema={
                "url": {
                    "type": "string",
                    "required": True,
                    "description": "URL to fetch",
                },
                "extract_mode": {
                    "type": "string",
                    "default": "markdown",
                    "description": "markdown|text",
                },
                "max_chars": {
                    "type": "integer",
                    "default": 50000,
                    "description": "Max chars to return",
                },
            },
            tags=["fetch", "browse", "web"],
        )
    )

    registry.register(
        ToolMeta(
            name="docs_search",
            domain=ToolDomain.WEB,
            description="Search NAVIG local documentation.",
            safety=SafetyLevel.SAFE,
            module_path="navig.tools.web",
            handler_name="search_docs",
            parameters_schema={
                "query": {
                    "type": "string",
                    "required": True,
                    "description": "Search query",
                },
                "max_results": {
                    "type": "integer",
                    "default": 5,
                    "description": "Max results",
                },
            },
            tags=["docs", "help", "documentation"],
        )
    )
