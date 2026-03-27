"""
Browser tier router — auto-selects the right browser controller for the task.

Tier 1 (Fast):    BrowserController — vanilla Playwright, no overhead.
Tier 2 (Stealth): StealthController — Patchright, best for hardened sites.
Tier 3 (CDP):     CDPBridge         — attach to existing Chrome/NaviBrowser.

Usage:
    # Simple
    browser = get_browser(stealth=False)

    # Attach to existing Chrome on port 9222
    browser = get_browser(cdp_port=9222)

    # Auto-retry with stealth on failure
    browser = await get_browser_auto(url)
"""

from navig.browser.controller import BrowserConfig, BrowserController
from navig.browser.stealth import StealthConfig, StealthController
from navig.debug_logger import get_debug_logger

logger = get_debug_logger()


def get_browser(
    stealth: bool = False,
    cdp_port: int | None = None,
    browser_config: BrowserConfig | None = None,
    stealth_config: StealthConfig | None = None,
) -> BrowserController | StealthController:
    """
    Return the appropriate browser controller for the task.

    Args:
        stealth:        Force Patchright stealth mode (Cloudflare, login flows, etc.)
        cdp_port:       Attach to an existing Chrome/NaviBrowser at this CDP port.
                        If None, launches a fresh browser instance.
        browser_config: Config for Tier-1 BrowserController (ignored when stealth/cdp)
        stealth_config: Config for Tier-2 StealthController (ignored when stealth=False)

    Returns:
        BrowserController, StealthController, or CDPBridge depending on flags.
    """
    if cdp_port is not None:
        from navig.browser.cdp_bridge import CDPBridge

        logger.info("[BrowserRouter] CDP attach tier selected (port=%d)", cdp_port)
        return CDPBridge(debug_port=cdp_port)

    if stealth:
        logger.info("[BrowserRouter] Stealth tier selected")
        return StealthController(stealth_config)

    logger.info("[BrowserRouter] Fast tier selected")
    return BrowserController(browser_config)


async def get_browser_auto(
    url: str,
    browser_config: BrowserConfig | None = None,
    stealth_config: StealthConfig | None = None,
) -> BrowserController | StealthController:
    """
    Try Tier-1 navigation; if blocked (403/429/timeout), switch to Tier-2.

    The returned controller is already started and has navigated to `url`.
    Caller is responsible for calling .stop() when done.

    Example:
        browser = await get_browser_auto("https://example.com")
        text = await browser.get_text()
        await browser.stop()
    """
    tier1 = BrowserController(browser_config)
    try:
        await tier1.start()
        result = await tier1.navigate(url)
        status = result.get("status")
        # Treat bot-wall responses as failures → escalate to stealth
        if status is not None and status in (403, 429, 503):
            raise RuntimeError(f"Bot-wall detected: HTTP {status}")
        logger.info("[BrowserRouter] Tier-1 succeeded for %s (HTTP %s)", url, status)
        return tier1
    except Exception as e:
        logger.warning("[BrowserRouter] Tier-1 failed (%s), escalating to stealth tier", e)
        try:
            await tier1.stop()
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

    tier2 = StealthController(stealth_config)
    await tier2.start()
    await tier2.navigate(url)
    logger.info("[BrowserRouter] Stealth tier succeeded for %s", url)
    return tier2


# ── Convenience aliases ────────────────────────────────────────────────────────


def fast_browser(config: BrowserConfig | None = None) -> BrowserController:
    """Return a Tier-1 BrowserController (vanilla Playwright)."""
    return BrowserController(config)


def stealth_browser(config: StealthConfig | None = None) -> StealthController:
    """Return a Tier-2 StealthController (Patchright)."""
    return StealthController(config)


def cdp_browser(port: int = 9222) -> "CDPBridge":
    """Return a Tier-3 CDPBridge (attach to existing Chrome/NaviBrowser)."""
    from navig.browser.cdp_bridge import CDPBridge

    return CDPBridge(debug_port=port)
