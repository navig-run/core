"""
WebFetchTool — Async HTTP page fetcher with markdown/text extraction.

Falls back to existing navig.tools.web helpers if trafilatura is available,
otherwise returns raw text with basic HTML stripping.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from navig.tools.registry import BaseTool, StatusCallback, ToolResult

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
            import httpx
        except ImportError:
            return ToolResult(name=self.name, success=False, error="httpx not installed")

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(15.0),
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"
                    )
                },
            ) as client:
                t0 = time.monotonic()
                resp = await client.get(url)
                status_code = resp.status_code
                raw_html = resp.text
                elapsed_ms = (time.monotonic() - t0) * 1000

            await self._emit(
                on_status,
                "Reading response…",
                f"{len(raw_html):,} bytes · HTTP {status_code}",
                55,
            )

            text = _extract_text(raw_html)
            text = text[:_MAX_CHARS]

            await self._emit(on_status, "Parsing content…", f"{len(text):,} chars extracted", 80)

            return ToolResult(
                name=self.name,
                success=True,
                output={
                    "url": str(resp.url),
                    "status_code": status_code,
                    "content": text,
                    "elapsed_ms": round(elapsed_ms, 1),
                },
            )

        except httpx.TimeoutException:
            return ToolResult(name=self.name, success=False, error="request timed out (15s)")
        except httpx.ConnectError as exc:
            return ToolResult(name=self.name, success=False, error=f"connection failed: {exc}")
        except Exception as exc:
            return ToolResult(name=self.name, success=False, error=str(exc))


def _extract_text(html: str) -> str:
    """Best-effort HTML → plain text.  Prefers trafilatura when available."""
    # Try trafilatura first for clean extraction
    try:
        import trafilatura

        text = trafilatura.extract(html, include_comments=False, include_tables=True)
        if text:
            return text.strip()
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    # Use html.parser for tag stripping (text extraction only — not a security sanitizer)
    import html as _html
    from html.parser import HTMLParser as _HTMLParser

    _SKIP_TAGS = frozenset({"script", "style", "noscript"})

    class _TextExtractor(_HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self._parts: list[str] = []
            self._skip = 0

        def handle_starttag(self, tag: str, attrs: list) -> None:
            if tag.lower() in _SKIP_TAGS:
                self._skip += 1

        def handle_endtag(self, tag: str) -> None:
            if tag.lower() in _SKIP_TAGS:
                self._skip = max(0, self._skip - 1)

        def handle_data(self, data: str) -> None:
            if not self._skip:
                self._parts.append(data)

    _extractor = _TextExtractor()
    try:
        _extractor.feed(html)
        text = " ".join(_extractor._parts)
    except Exception:  # noqa: BLE001
        text = _html.unescape(re.sub(r"<[^>]+>", " ", html))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
