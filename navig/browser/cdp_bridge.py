"""
CDP Bridge — Attach to an existing Chrome / NaviBrowser via CDP WebSocket.

Instead of spawning a new browser process, CDPBridge connects to a
running Chrome/Chromium instance that was started with:

    chrome --remote-debugging-port=9222

or the NaviBrowser Go binary with the --cdp-port flag.

This exposes the same interface as BrowserController so cortex.py is fully agnostic.

Usage:
    driver = CDPBridge(debug_port=9222)
    await driver.start()       # attaches — does NOT launch a new browser
    await driver.navigate("https://example.com")
    a11y, refs = await driver.get_a11y_snapshot_with_refs()
    await driver.stop()        # disconnects — does NOT close the browser
"""

from __future__ import annotations

from navig.browser.controller import BrowserConfig, BrowserController
from navig.debug_logger import get_debug_logger

logger = get_debug_logger()


class CDPBridge(BrowserController):
    """
    Attach-mode browser controller.

    Inherits all methods from BrowserController (a11y, fill_fast, safe_click, etc.)
    but connects to an *existing* Chrome process via CDP instead of launching one.
    """

    def __init__(self, debug_port: int = 9222, tab_index: int = 0):
        """
        Args:
            debug_port: Chrome remote debugging port (default 9222).
            tab_index:  Which open tab to attach to (0 = first/most recently focused).
        """
        # Pass a dummy config — most settings are irrelevant in attach mode
        super().__init__(BrowserConfig(headless=False))
        self.debug_port = debug_port
        self.tab_index = tab_index
        self._cdp_endpoint = f"http://localhost:{debug_port}"

    def _connection_hint(self) -> str:
        """Human hint shown when the CDP connection fails (overridden by CloudBridge)."""
        return (
            f"Make sure Chrome is running with --remote-debugging-port={self.debug_port}."
        )

    def _display_endpoint(self) -> str:
        """Loggable endpoint — CloudBridge overrides this to strip its auth token."""
        return self._cdp_endpoint

    async def start(self):
        """Attach to an existing browser via CDP (no new process launched)."""
        if self._browser:
            logger.warning("[CDPBridge] Already attached")
            return

        from navig.browser.controller import _get_playwright

        async_playwright = _get_playwright()
        self._playwright = await async_playwright().start()

        logger.info("[CDPBridge] Connecting to %s ...", self._display_endpoint())
        try:
            self._browser = await self._playwright.chromium.connect_over_cdp(self._cdp_endpoint)
        except Exception as exc:
            await self._playwright.stop()
            self._playwright = None
            raise RuntimeError(
                f"[CDPBridge] Could not connect to Chrome at {self._display_endpoint()}. "
                f"{self._connection_hint()} "
                f"Error: {exc}"
            ) from exc

        # Attach across ALL contexts. When the caller didn't ask for a specific
        # tab (tab_index 0), prefer a real page over an about:blank/new-tab page
        # so screenshots and actions land on something meaningful by default.
        contexts = self._browser.contexts
        all_pages = [p for ctx in contexts for p in ctx.pages]
        if all_pages:
            if self.tab_index:
                idx = min(self.tab_index, len(all_pages) - 1)
                self._page = all_pages[idx]
            else:
                real = next((p for p in all_pages if p.url and p.url != "about:blank"), None)
                self._page = real or all_pages[0]
                idx = all_pages.index(self._page)
            self._context = self._page.context
            logger.info("[CDPBridge] Attached to tab %d: %s", idx, self._page.url)
        elif contexts:
            self._context = contexts[0]
            self._page = await self._context.new_page()
            logger.info("[CDPBridge] No open tabs — created new page")
        else:
            # No existing context — create one (unusual but safe fallback)
            self._context = await self._browser.new_context()
            self._page = await self._context.new_page()
            logger.info("[CDPBridge] No existing context — created new context+page")

        self._page.set_default_timeout(self.config.timeout_ms)
        logger.info("[CDPBridge] Ready. Current URL: %s", self._page.url)

    async def stop(self):
        """Disconnect from the browser WITHOUT closing it.

        For connect_over_cdp sessions, calling browser.close() would terminate
        the remote Chrome process. We only stop the Playwright wrapper here,
        which closes the CDP WebSocket connection and leaves Chrome running.
        """
        if self._playwright:
            # Nullify page/context refs before stopping to prevent finalizer double-close
            self._page = None
            self._context = None
            self._browser = None
            try:
                await self._playwright.stop()  # closes WS only; does NOT kill remote Chrome
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
            self._playwright = None
            logger.info("[CDPBridge] Disconnected (browser still running)")

    def _all_pages(self) -> list:
        """Every open page across ALL browser contexts (not just the attached one).

        connect_over_cdp can surface multiple contexts; enumerating only the
        attached context hid tabs. This flattens them in a stable order.
        """
        if self._browser is not None:
            pages: list = []
            for ctx in self._browser.contexts:
                pages.extend(ctx.pages)
            return pages
        if self._context is not None:
            return list(self._context.pages)
        return []

    async def list_tabs(self) -> list[dict]:
        """List all open tabs across every browser context.

        Returns list of {index, url, title} dicts.
        """
        result = []
        for i, page in enumerate(self._all_pages()):
            try:
                title = await page.title()
            except Exception:
                title = ""
            result.append({"index": i, "url": page.url, "title": title})
        return result

    async def switch_tab(self, index: int) -> bool:
        """Switch the active page to a different tab by index (across contexts)."""
        pages = self._all_pages()
        if index < 0 or index >= len(pages):
            logger.warning(
                "[CDPBridge] Tab index %d out of range (have %d tabs)",
                index,
                len(pages),
            )
            return False
        self._page = pages[index]
        # Keep the active context in sync with the selected page.
        self._context = self._page.context
        await self._page.bring_to_front()
        logger.info("[CDPBridge] Switched to tab %d: %s", index, self._page.url)
        return True

    async def switch_to(self, *, index: int | None = None, url: str | None = None) -> dict:
        """Make a specific open tab the active target, by index or URL substring.

        Returns {"ok": bool, "url": str, "index": int} describing the new active
        page. Matching by *url* picks the first page whose URL contains the
        substring (case-insensitive).
        """
        pages = self._all_pages()
        if not pages:
            return {"ok": False, "error": "no open tabs"}
        target_idx: int | None = None
        if index is not None:
            if 0 <= index < len(pages):
                target_idx = index
        elif url:
            needle = url.lower()
            for i, page in enumerate(pages):
                if needle in (page.url or "").lower():
                    target_idx = i
                    break
        if target_idx is None:
            return {"ok": False, "error": "no tab matched the given index/url"}
        self._page = pages[target_idx]
        self._context = self._page.context
        try:
            await self._page.bring_to_front()
        except Exception:  # noqa: BLE001
            pass  # non-fatal (e.g. background page)
        return {"ok": True, "index": target_idx, "url": self._page.url}


def auto_detect_cdp_port() -> int | None:
    """Try to find a running Chrome/NaviBrowser with a CDP port open.

    Checks common ports: 9222, 9223, 9229.
    Returns the first responding port, or None.
    """
    import socket

    for port in (9222, 9223, 9229):
        try:
            with socket.create_connection(("localhost", port), timeout=0.5):
                logger.info("[CDPBridge] Auto-detected CDP port: %d", port)
                return port
        except OSError:
            continue
    return None
