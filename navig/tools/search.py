"""
SearchTool — DuckDuckGo HTML scraper.  Zero API key required.

Scrapes DDG's /html endpoint and returns top-5 organic results.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)

_DDG_URL = "https://html.duckduckgo.com/html/"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}


class SearchTool(BaseTool):
    name = "search"
    description = "Search the web via DuckDuckGo. Returns top-5 results with title, URL, snippet."
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
            import httpx
        except ImportError:
            return ToolResult(name=self.name, success=False, error="httpx not installed")

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(15.0),
                follow_redirects=True,
                headers=_HEADERS,
            ) as client:
                resp = await client.post(_DDG_URL, data={"q": query, "b": "", "kl": ""})
                html = resp.text

            await self._emit(on_status, "Parsing results…", f"HTTP {resp.status_code}", 65)

            results = _parse_ddg_html(html)[:5]

            await self._emit(
                on_status,
                "Results ready",
                f"{len(results)} results found",
                90,
            )

            return ToolResult(
                name=self.name,
                success=True,
                output={"query": query, "results": results},
            )

        except httpx.TimeoutException:
            return ToolResult(name=self.name, success=False, error="search timed out (15s)")
        except Exception as exc:
            return ToolResult(name=self.name, success=False, error=str(exc))


def _parse_ddg_html(html: str) -> list[dict[str, str]]:
    """Extract title, url, snippet from DDG HTML results page."""
    results: list[dict[str, str]] = []

    # Match result blocks
    blocks = re.findall(
        r'class="result__body">(.*?)</div>\s*</div>',
        html,
        re.DOTALL,
    )
    if not blocks:
        # Try alternate DDG format
        blocks = re.findall(r'<div class="links_main[^>]*>(.*?)</div>', html, re.DOTALL)

    for block in blocks[:8]:
        # Title
        title_m = re.search(r'class="result__a"[^>]*>(.*?)</a>', block, re.DOTALL)
        title = _strip_tags(title_m.group(1)) if title_m else ""

        # URL
        url_m = re.search(r'href="([^"]+)"', block)
        url = url_m.group(1) if url_m else ""
        if url.startswith("//duckduckgo.com/l/?uddg="):
            # Decode DDG redirect
            from urllib.parse import unquote

            target_m = re.search(r"uddg=([^&]+)", url)
            if target_m:
                url = unquote(target_m.group(1))

        # Snippet
        snippet_m = re.search(r'class="result__snippet"[^>]*>(.*?)</span>', block, re.DOTALL)
        snippet = _strip_tags(snippet_m.group(1)) if snippet_m else ""

        if title or url:
            results.append({"title": title.strip(), "url": url, "snippet": snippet.strip()})

    return results


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()
