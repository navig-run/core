"""BrowserFetchTool — Two-stage URL fetcher with auto JS-gate detection.

Stage 1: httpx (fast, no overhead, works for >90% of URLs)
Stage 2: Playwright via BrowserController (auto-triggered for JS-gated pages)

Replaces WebFetchTool in the pipeline registry.

Detection heuristic for JS-gated pages:
  • Body text < 200 chars after HTML strip
  • React/Next/Vue/Angular root ``<div id="root|app|__nuxt">`` with no text children
  • HTML contains ``<script`` references to JS bundles but empty visible content
  • ``<noscript>`` tag with "enable JavaScript" text
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, Optional

from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)

_MAX_CHARS = 8_000
_HTTPX_TIMEOUT = 15.0
_BROWSER_TIMEOUT_MS = 20_000

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# JS-gate heuristics
# ---------------------------------------------------------------------------

_JS_GATE_PATTERNS = [
    # noscript "enable JavaScript"
    re.compile(r"<noscript[^>]*>.*?(enable|javascript|required)", re.DOTALL | re.IGNORECASE),
    # empty SPA root divs  (id = root | app | __nuxt | __next | main-content)
    re.compile(
        r'<div\s+id=["\'](?:root|app|__nuxt|__next|main[-_]content)["\'][^>]*>\s*</div>',
        re.IGNORECASE,
    ),
    # typical CRA/Vite JS bundle reference
    re.compile(r'<script[^>]+src=["\'][^"\']+(?:main|bundle|app)\.[a-z0-9]{8,}\.js["\']'),
    # Next.js data script tag
    re.compile(r'id=["\']__NEXT_DATA__["\']'),
    # Angular / Ember attributes
    re.compile(r'<app-root\b|<ember-app\b', re.IGNORECASE),
]


def _is_js_gated(html: str) -> bool:
    """Return True if the page likely needs JavaScript to render meaningful content."""
    # Quick text-content check
    bare = re.sub(r"<[^>]+>", "", html)
    bare = re.sub(r"\s+", " ", bare).strip()
    if len(bare) < 250:
        return True

    for pattern in _JS_GATE_PATTERNS:
        if pattern.search(html):
            return True

    return False


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def _extract_text(html: str) -> str:
    """Best-effort HTML → plain text. Prefers trafilatura when available."""
    try:
        import trafilatura
        text = trafilatura.extract(html, include_comments=False, include_tables=True)
        if text:
            return text.strip()
    except Exception:
        pass

    text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Stage 2: Playwright via BrowserController
# ---------------------------------------------------------------------------

async def _browser_fetch(url: str, on_status: Optional[StatusCallback]) -> tuple[str, str]:
    """Use navig BrowserController to render the page.  Returns (html, method_used)."""
    if on_status:
        await on_status("Launching browser…", "Playwright headless", 55)

    try:
        from navig.browser.controller import BrowserController, BrowserConfig  # lazy
    except ImportError:
        raise RuntimeError("navig.browser not available (playwright not installed?)")

    config = BrowserConfig(
        headless=True,
        timeout_ms=_BROWSER_TIMEOUT_MS,
    )
    browser = BrowserController(config=config)
    try:
        await browser.start()
        await browser._page.goto(url, wait_until="networkidle")
        html = await browser._page.content()
        return html, "playwright"
    except Exception as exc:
        raise RuntimeError(f"Playwright failed: {exc}") from exc
    finally:
        await browser.stop()


# ---------------------------------------------------------------------------
# Main tool
# ---------------------------------------------------------------------------

class BrowserFetchTool(BaseTool):
    """Fetch a URL — httpx first, Playwright upgrade if JS-gated."""

    name = "browser_fetch"
    description = (
        "Fetch a URL and return the main text content. "
        "Auto-upgrades to headless browser for JavaScript-rendered pages."
    )

    async def run(
        self,
        args: Dict[str, Any],
        on_status: Optional[StatusCallback] = None,
    ) -> ToolResult:
        url: str = args.get("url", "").strip()
        if not url:
            return ToolResult(name=self.name, success=False, error="url arg required")

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        await self._emit(on_status, "Connecting…", url[:70], 15)

        try:
            import httpx
        except ImportError:
            return ToolResult(name=self.name, success=False, error="httpx not installed")

        method_used = "httpx"
        html = ""
        elapsed_ms = 0.0

        # Stage 1 — httpx
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(_HTTPX_TIMEOUT),
                headers={"User-Agent": _USER_AGENT},
            ) as client:
                t0 = time.monotonic()
                resp = await client.get(url)
                elapsed_ms = (time.monotonic() - t0) * 1000
                status_code = resp.status_code
                html = resp.text
                final_url = str(resp.url)

        except httpx.TimeoutException:
            return ToolResult(name=self.name, success=False, error="request timed out (15s)")
        except httpx.ConnectError as exc:
            return ToolResult(name=self.name, success=False, error=f"connection failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(name=self.name, success=False, error=str(exc))

        await self._emit(
            on_status, "Analysing page…", f"HTTP {status_code} · {len(html):,} bytes", 40
        )

        # Stage 2 — upgrade to Playwright if JS-gated
        if _is_js_gated(html):
            await self._emit(on_status, "JS-gated — launching browser…", url[:70], 50)
            try:
                t0 = time.monotonic()
                html, method_used = await _browser_fetch(url, on_status)
                elapsed_ms += (time.monotonic() - t0) * 1000
                final_url = url
                logger.debug("browser_fetch: upgraded to Playwright for %s", url)
            except RuntimeError as exc:
                logger.warning("browser_fetch: Playwright fallback failed for %s: %s", url, exc)
                # Continue with whatever httpx returned — partial content is better than nothing

        await self._emit(on_status, "Extracting text…", f"{len(html):,} bytes · {method_used}", 80)

        text = _extract_text(html)[:_MAX_CHARS]

        return ToolResult(
            name=self.name,
            success=True,
            output={
                "url": final_url,
                "content": text,
                "method": method_used,
                "elapsed_ms": round(elapsed_ms, 1),
                "chars": len(text),
            },
        )
