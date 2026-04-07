"""
WebFetchTool — Async HTTP page fetcher with markdown/text extraction.

Falls back to existing navig.tools.web helpers if trafilatura is available,
otherwise returns raw text with basic HTML stripping.
"""

from __future__ import annotations

import logging
from typing import Any

from navig.tools.registry import BaseTool, StatusCallback, ToolResult
from navig.tools.web import web_fetch

logger = logging.getLogger(__name__)

_MAX_CHARS = 5_000


class WebFetchTool(BaseTool):
    name = "web_fetch"
    description = "Fetch a URL and return the main text content (max 5000 chars)."
    owner_only = False
    parameters = [
        {
            "name": "url",
            "type": "string",
            "description": "The target URL to fetch text from",
            "required": True,
        },
        {
            "name": "css_selector",
            "type": "string",
            "description": "Optional CSS selector to target specific content",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        url: str = args.get("url", "")
        if not url:
            return ToolResult(name=self.name, success=False, error="url arg required")

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        await self._emit(on_status, "Connecting…", url[:60], 20)

        try:
            result = web_fetch(
                url=url,
                extract_mode="markdown",
                max_chars=_MAX_CHARS,
                timeout_seconds=15,
                use_cache=True,
            )
            if not result.success:
                return ToolResult(name=self.name, success=False, error=result.error or "fetch failed")

            await self._emit(
                on_status,
                "Reading response…",
                f"HTTP {result.status_code or '-'}",
                55,
            )

            text = (result.text or "")[:_MAX_CHARS]

            await self._emit(on_status, "Parsing content…", f"{len(text):,} chars extracted", 80)

            return ToolResult(
                name=self.name,
                success=True,
                output={
                    "url": result.final_url or url,
                    "status_code": result.status_code,
                    "content": text,
                    "cached": result.cached,
                },
            )

        except Exception as exc:
            return ToolResult(name=self.name, success=False, error=str(exc))
