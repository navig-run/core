"""SearchTool — unified web intelligence search via navig.tools.web.web_search."""

from __future__ import annotations

import logging
from typing import Any

from navig.tools.registry import BaseTool, StatusCallback, ToolResult
from navig.tools.web import web_search

logger = logging.getLogger(__name__)

class SearchTool(BaseTool):
    name = "search"
    description = "Search the web via NAVIG unified provider routing (Firecrawl-first fallback)."
    parameters = [
        {
            "name": "query",
            "type": "string",
            "description": "Search query containing keywords",
            "required": True,
        }
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        query: str = args.get("query", "")
        if not query:
            return ToolResult(name=self.name, success=False, error="query arg required")

        await self._emit(on_status, "Querying…", query[:60], 20)

        try:
            result = web_search(query=query, count=5, provider="auto", use_cache=True)

            if not result.success:
                return ToolResult(name=self.name, success=False, error=result.error or "search failed")

            await self._emit(on_status, "Parsing results…", result.provider, 65)

            results = [
                {"title": r.title, "url": r.url, "snippet": r.snippet}
                for r in result.results[:5]
            ]

            await self._emit(
                on_status,
                "Results ready",
                f"{len(results)} results found",
                90,
            )

            return ToolResult(
                name=self.name,
                success=True,
                output={"query": query, "provider": result.provider, "results": results},
            )

        except Exception as exc:
            return ToolResult(name=self.name, success=False, error=str(exc))
