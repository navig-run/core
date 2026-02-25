"""
Stealth browser automation using Patchright — a drop-in Playwright replacement
that bypasses Cloudflare, Kasada, Akamai, Datadome and other bot detectors.

Use this tier for:
- Sites protected by Cloudflare / bot challenges
- Login flows on hardened sites
- Cookie-session scraping
- Anything where vanilla Playwright gets blocked

For simple/internal sites use navig.browser.controller (faster, no overhead).
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime

from navig.debug_logger import get_debug_logger

logger = get_debug_logger()

_patchright = None


def _get_patchright():
    """Lazy import of patchright. Falls back to vanilla playwright if not installed."""
    global _patchright
    if _patchright is None:
        try:
            from patchright.async_api import async_playwright as _pw
            _patchright = _pw
            logger.info("Stealth engine: patchright loaded")
        except ImportError:
            try:
                from playwright.async_api import async_playwright as _pw
                _patchright = _pw
                logger.warning(
                    "Patchright not installed — falling back to vanilla Playwright. "
                    "Install with: pip install patchright && patchright install chromium"
                )
            except ImportError:
                raise ImportError(
                    "Neither patchright nor playwright is installed. "
                    "Run: pip install patchright && patchright install chromium"
                )
    return _patchright


@dataclass
class StealthConfig:
    """Stealth browser configuration.

    Best practice (per Patchright docs): use persistent context + channel='chrome'
    + no_viewport=True. Do NOT set custom user_agent or extra headers.
    """
    headless: bool = False   # headless=False is harder to detect for most CAPTCHAs
    channel: str = "chrome"  # use installed Chrome, not Chromium build
    user_data_dir: str = "~/.navig/browser/profiles/stealth"
    timeout_ms: int = 30000
    screenshot_dir: str = "~/.navig/screenshots"
    proxy: Optional[str] = None
    allowed_domains: List[str] = field(default_factory=list)
    blocked_domains: List[str] = field(default_factory=list)

    @classmethod
    def from_config(cls, config: dict) -> 'StealthConfig':
        stealth_cfg = config.get('browser_stealth', config.get('browser', {}))
        return cls(
            headless=stealth_cfg.get('headless', False),
            channel=stealth_cfg.get('channel', 'chrome'),
            user_data_dir=stealth_cfg.get('user_data_dir', '~/.navig/browser/profiles/stealth'),
            timeout_ms=stealth_cfg.get('timeout_seconds', 30) * 1000,
            proxy=stealth_cfg.get('proxy'),
            allowed_domains=stealth_cfg.get('allowed_domains', []),
            blocked_domains=stealth_cfg.get('blocked_domains', []),
        )


class StealthController:
    """
    Patchright-powered stealth browser controller.

    Drop-in replacement for BrowserController for hardened targets.
    Uses persistent context (keeps cookies, sessions between runs).

    Example:
        controller = StealthController()
        await controller.start()
        result = await controller.navigate("https://bot.sannysoft.com")
        path = await controller.screenshot()
        await controller.stop()
    """

    def __init__(self, config: Optional[StealthConfig] = None):
        self.config = config or StealthConfig()
        self._playwright = None
        self._context = None   # persistent context (browser + cookies combined)
        self._page = None

        self._screenshot_dir = Path(self.config.screenshot_dir).expanduser()
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)

    @property
    def is_running(self) -> bool:
        return self._page is not None

    async def start(self):
        """Start a stealth browser session (persistent context)."""
        if self._context:
            logger.warning("StealthController already started")
            return

        logger.info("Starting stealth browser (patchright)...")
        async_playwright = _get_patchright()
        self._playwright = await async_playwright().start()

        user_data = Path(self.config.user_data_dir).expanduser()
        user_data.mkdir(parents=True, exist_ok=True)

        launch_kwargs: Dict[str, Any] = {
            "channel": self.config.channel,
            "headless": self.config.headless,
            "no_viewport": True,          # critical stealth setting
            # Do NOT add custom user_agent or extra_http_headers — detectable
        }

        if self.config.proxy:
            launch_kwargs["proxy"] = {"server": self.config.proxy}

        self._context = await self._playwright.chromium.launch_persistent_context(
            str(user_data),
            **launch_kwargs,
        )

        # Reuse existing page or open a new one
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        self._page.set_default_timeout(self.config.timeout_ms)

        logger.info("Stealth browser ready")

    async def stop(self):
        """Close stealth browser (cookies are persisted to disk)."""
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()

        self._page = None
        self._context = None
        self._playwright = None
        logger.info("Stealth browser stopped")

    async def _ensure_started(self):
        if not self._page:
            await self.start()

    def _check_domain(self, url: str) -> bool:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower()
        for blocked in self.config.blocked_domains:
            if blocked.lower().replace('*', '') in domain:
                return False
        if self.config.allowed_domains:
            return any(a.lower().replace('*', '') in domain for a in self.config.allowed_domains)
        return True

    # ── Core navigation ────────────────────────────────────────────────────────

    async def navigate(self, url: str, wait_until: str = "domcontentloaded") -> Dict[str, Any]:
        await self._ensure_started()
        if not self._check_domain(url):
            raise ValueError(f"Domain not allowed: {url}")
        response = await self._page.goto(url, wait_until=wait_until)
        return {
            "url": self._page.url,
            "title": await self._page.title(),
            "status": response.status if response else None,
        }

    async def fill(self, selector: str, value: str) -> bool:
        await self._ensure_started()
        await self._page.fill(selector, value)
        return True

    async def click(self, selector: str) -> bool:
        await self._ensure_started()
        await self._page.click(selector)
        return True

    async def type_text(self, selector: str, text: str, delay: int = 50) -> bool:
        await self._ensure_started()
        await self._page.type(selector, text, delay=delay)
        return True

    async def press(self, key: str) -> bool:
        await self._ensure_started()
        await self._page.keyboard.press(key)
        return True

    async def evaluate(self, script: str) -> Any:
        await self._ensure_started()
        return await self._page.evaluate(script)

    async def screenshot(self, name: Optional[str] = None, full_page: bool = False,
                         selector: Optional[str] = None) -> str:
        await self._ensure_started()
        if not name:
            name = f"stealth_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        if not name.endswith('.png'):
            name += '.png'
        path = self._screenshot_dir / name
        if selector:
            el = await self._page.query_selector(selector)
            if el:
                await el.screenshot(path=str(path))
            else:
                raise ValueError(f"Element not found: {selector}")
        else:
            await self._page.screenshot(path=str(path), full_page=full_page)
        logger.info(f"Stealth screenshot: {path}")
        return str(path)

    async def get_content(self) -> str:
        await self._ensure_started()
        return await self._page.content()

    async def get_text(self, selector: Optional[str] = None) -> str:
        await self._ensure_started()
        if selector:
            el = await self._page.query_selector(selector)
            return await el.text_content() or "" if el else ""
        return await self._page.text_content("body") or ""

    async def get_url(self) -> str:
        await self._ensure_started()
        return self._page.url

    async def get_title(self) -> str:
        await self._ensure_started()
        return await self._page.title()

    async def get_cookies(self) -> List[Dict[str, Any]]:
        await self._ensure_started()
        return await self._context.cookies()

    async def set_cookies(self, cookies: List[Dict[str, Any]]):
        await self._ensure_started()
        await self._context.add_cookies(cookies)

    async def wait_for_selector(self, selector: str,
                                timeout: Optional[int] = None,
                                state: str = "visible") -> bool:
        await self._ensure_started()
        try:
            await self._page.wait_for_selector(selector, timeout=timeout, state=state)
            return True
        except Exception:
            return False

    async def go_back(self) -> bool:
        await self._ensure_started()
        await self._page.go_back()
        return True

    async def reload(self) -> bool:
        await self._ensure_started()
        await self._page.reload()
        return True

    # ── Phase 1+2 Intelligence Methods (Cortex compatibility) ─────────────────

    async def screenshot_base64(self, quality: int = 60) -> str:
        """Return screenshot as base64 JPEG string."""
        import base64
        await self._ensure_started()
        data = await self._page.screenshot(type="jpeg", quality=quality)
        return base64.b64encode(data).decode("utf-8")

    async def wait_for_stable(self, timeout_ms: int = 3000) -> None:
        """Wait for network idle. Silently accepts timeout."""
        try:
            await self._page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:
            pass

    async def get_a11y_tree(self) -> str:
        """Return ARIA snapshot text (Playwright 1.46+ Locator API)."""
        if not self._page:
            return ""
        try:
            return await self._page.locator("body").aria_snapshot() or ""
        except Exception as exc:
            logger.debug("[A11y/Stealth] aria_snapshot failed: %s", exc)
            return ""

    async def get_a11y_snapshot_with_refs(self) -> tuple:
        """Annotate ARIA snapshot with numeric [ref] IDs."""
        raw = await self.get_a11y_tree()
        if not raw:
            return "", {}
        ref_map: dict = {}
        annotated_lines: list = []
        ref_id = 0
        import re as _re  # hoisted

        for line in raw.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("- "):
                rest = stripped[2:]
                m = _re.match(r'(\w[\w\s]*)\s*(?:"([^"]*)"|\[([^\]]*)\])?', rest)
                role = m.group(1).strip() if m else rest.split()[0] if rest.split() else ""
                name = (m.group(2) or m.group(3) or "").strip() if m else ""
                ref_map[ref_id] = {"role": role, "name": name, "raw_line": line}
                indent = line[: len(line) - len(stripped)]
                annotated_lines.append(f"{indent}- [{ref_id}] {rest}")
                ref_id += 1
            else:
                annotated_lines.append(line)
        return "\n".join(annotated_lines), ref_map

    async def get_interactive_elements_fast(self, limit: int = 50) -> list:
        """Single-JS-eval interactive element scan."""
        try:
            return await self._page.evaluate(f"""() => {{
                const SEL = 'a[href],button,input,textarea,select,[role="button"],[role="textbox"]';
                return Array.from(document.querySelectorAll(SEL))
                    .filter(el => el.offsetParent !== null).slice(0, {limit})
                    .map(el => {{
                        const r = el.getBoundingClientRect();
                        return {{tag: el.tagName.toLowerCase(),
                            role: el.getAttribute('role') || el.tagName.toLowerCase(),
                            name: el.getAttribute('aria-label') || el.getAttribute('placeholder') || (el.textContent||'').trim().slice(0,60),
                            x: Math.round(r.left+r.width/2), y: Math.round(r.top+r.height/2),
                            w: Math.round(r.width), h: Math.round(r.height)}};
                    }});
            }}""")
        except Exception:
            return []

    async def fill_fast(self, selector: str, text: str, timeout: int = 5000) -> dict:
        """JS value injection fill — faster than keyboard simulation."""
        try:
            locator = self._page.locator(selector)
            await locator.wait_for(state="visible", timeout=timeout)
            el = await locator.element_handle(timeout=timeout)
            await self._page.evaluate(
                "([el, val]) => { el.focus(); el.value=val; el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); }",
                [el, text],
            )
            return {"ok": True}
        except Exception as exc:
            try:
                await self._page.fill(selector, text, timeout=timeout)
                return {"ok": True}
            except Exception as exc2:
                return {"ok": False, "error": str(exc2)[:200]}

    async def safe_click(self, selector: str, timeout: int = 5000) -> dict:
        """Click with AI-readable structured error."""
        try:
            await self._page.click(selector, timeout=timeout)
            return {"ok": True}
        except Exception as exc:
            err = str(exc)
            return {"ok": False, "error": type(exc).__name__, "detail": err[:200],
                    "suggestion": "scroll into view" if "not visible" in err else "check selector"}

    async def safe_fill(self, selector: str, text: str, timeout: int = 5000) -> dict:
        """Fill with AI-readable structured error."""
        try:
            await self._page.fill(selector, text, timeout=timeout)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": type(exc).__name__, "detail": str(exc)[:200]}
