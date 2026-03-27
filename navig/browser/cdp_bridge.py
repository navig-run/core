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

from typing import Optional

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

    async def start(self):
        """Attach to an existing browser via CDP (no new process launched)."""
        if self._browser:
            logger.warning("[CDPBridge] Already attached")
            return

        from navig.browser.controller import _get_playwright

        async_playwright = _get_playwright()
        self._playwright = await async_playwright().start()

        logger.info("[CDPBridge] Connecting to %s ...", self._cdp_endpoint)
        try:
            self._browser = await self._playwright.chromium.connect_over_cdp(
                self._cdp_endpoint
            )
        except Exception as exc:
            await self._playwright.stop()
            self._playwright = None
            raise RuntimeError(
                f"[CDPBridge] Could not connect to Chrome at {self._cdp_endpoint}. "
                f"Make sure Chrome is running with --remote-debugging-port={self.debug_port}. "
                f"Error: {exc}"
            ) from exc

        # Attach to existing context + page
        contexts = self._browser.contexts
        if contexts:
            self._context = contexts[0]
            pages = self._context.pages
            if pages:
                idx = min(self.tab_index, len(pages) - 1)
                self._page = pages[idx]
                logger.info("[CDPBridge] Attached to tab %d: %s", idx, self._page.url)
            else:
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

    async def list_tabs(self) -> list[dict]:
        """List all open tabs in the attached browser.

        Returns list of {index, url, title} dicts.
        """
        if not self._context:
            return []
        result = []
        for i, page in enumerate(self._context.pages):
            try:
                title = await page.title()
            except Exception:
                title = ""
            result.append({"index": i, "url": page.url, "title": title})
        return result

    async def switch_tab(self, index: int) -> bool:
        """Switch the active page to a different tab by index."""
        if not self._context:
            return False
        pages = self._context.pages
        if index < 0 or index >= len(pages):
            logger.warning(
                "[CDPBridge] Tab index %d out of range (have %d tabs)",
                index,
                len(pages),
            )
            return False
        self._page = pages[index]
        await self._page.bring_to_front()
        logger.info("[CDPBridge] Switched to tab %d: %s", index, self._page.url)
        return True


def auto_detect_cdp_port() -> Optional[int]:
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
