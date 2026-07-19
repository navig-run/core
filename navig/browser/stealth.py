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

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from navig.browser.a11y import annotate_a11y_snapshot
from navig.debug_logger import get_debug_logger

logger = get_debug_logger()

_patchright = None
_engine_is_patchright = False


def _get_patchright(require: bool = False):
    """Lazy import of patchright.

    With ``require=True`` this fails LOUDLY instead of silently degrading to vanilla
    Playwright — vanilla is trivially detected, so a caller that asked for stealth should
    know it isn't getting it rather than get quietly unmasked.
    """
    global _patchright, _engine_is_patchright
    if _patchright is None:
        try:
            from patchright.async_api import async_playwright as _pw

            _patchright = _pw
            _engine_is_patchright = True
            logger.info("Stealth engine: patchright loaded")
        except ImportError:
            if require:
                raise ImportError(
                    "Stealth engine required but Patchright is not installed. "
                    "Run: pip install patchright && patchright install chromium "
                    "(or set browser_stealth.require_stealth = false to allow the "
                    "detectable vanilla-Playwright fallback)."
                ) from None
            try:
                from playwright.async_api import async_playwright as _pw

                _patchright = _pw
                _engine_is_patchright = False
                logger.warning(
                    "⚠ Patchright NOT installed — using DETECTABLE vanilla Playwright. "
                    "Stealth is degraded. Install: pip install patchright && "
                    "patchright install chromium"
                )
            except ImportError as _exc:
                raise ImportError(
                    "Neither patchright nor playwright is installed. "
                    "Run: pip install patchright && patchright install chromium"
                ) from _exc
    elif require and not _engine_is_patchright:
        raise ImportError(
            "Stealth engine required but only vanilla Playwright is available. "
            "Run: pip install patchright && patchright install chromium"
        )
    return _patchright


@dataclass
class StealthConfig:
    """Stealth browser configuration.

    Best practice (per Patchright docs): use persistent context + channel='chrome'
    + no_viewport=True. Do NOT set custom user_agent or extra headers.
    """

    headless: bool = False  # headless=False is harder to detect for most CAPTCHAs
    channel: str = "chrome"  # use installed Chrome, not Chromium build
    user_data_dir: str = "~/.navig/browser/profiles/stealth"
    timeout_ms: int = 30000
    screenshot_dir: str = "~/.navig/screenshots"
    proxy: str | None = None
    allowed_domains: list[str] = field(default_factory=list)
    blocked_domains: list[str] = field(default_factory=list)
    # ── Stage 5 stealth-engine options ──
    require_stealth: bool = False    # fail loudly if Patchright missing (no vanilla fallback)
    fingerprint: bool = True         # apply coherence-safe context opts (locale/tz/geo)
    fingerprint_js: bool = False     # apply the JS shim layer (OFF on Patchright — it clashes)
    webrtc_protection: bool = True   # WebRTC IP-leak launch flags
    locale: str | None = None        # overrides fingerprint locale (derive from proxy geo)
    timezone_id: str | None = None
    geolocation: dict | None = None
    seed: str | None = None          # deterministic fingerprint (per-profile persona, Stage 7)
    window_size: tuple[int, int] | None = None  # force a window W,H (no_viewport → page matches
    #                                              it); use a wide desktop size (e.g. 1280×1000) when
    #                                              a site's responsive layout must render its desktop
    #                                              variant (TikTok's comment side-panel only exists
    #                                              ≥~1024px — narrower gives the immersive layout).
    window_position: tuple[int, int] | None = None  # place the OS window at X,Y — use a large
    #                                                  negative (e.g. -2400,-2400) to run a HEADFUL
    #                                                  browser offscreen: real enough to defeat
    #                                                  headless bot-detection, invisible to the user.
    mute_audio: bool = False  # --mute-audio — silence a headful/offscreen window (no page sound).

    @classmethod
    def from_config(cls, config: dict) -> "StealthConfig":
        stealth_cfg = config.get("browser_stealth", config.get("browser", {}))
        return cls(
            headless=stealth_cfg.get("headless", False),
            channel=stealth_cfg.get("channel", "chrome"),
            user_data_dir=stealth_cfg.get("user_data_dir", "~/.navig/browser/profiles/stealth"),
            timeout_ms=stealth_cfg.get("timeout_seconds", 30) * 1000,
            proxy=stealth_cfg.get("proxy"),
            allowed_domains=stealth_cfg.get("allowed_domains", []),
            blocked_domains=stealth_cfg.get("blocked_domains", []),
            require_stealth=stealth_cfg.get("require_stealth", False),
            fingerprint=stealth_cfg.get("fingerprint", True),
            fingerprint_js=stealth_cfg.get("fingerprint_js", False),
            webrtc_protection=stealth_cfg.get("webrtc_protection", True),
            locale=stealth_cfg.get("locale"),
            timezone_id=stealth_cfg.get("timezone_id"),
            geolocation=stealth_cfg.get("geolocation"),
            seed=stealth_cfg.get("seed"),
            window_size=(tuple(stealth_cfg["window_size"])  # type: ignore[arg-type]
                         if stealth_cfg.get("window_size") else None),
            window_position=(tuple(stealth_cfg["window_position"])  # type: ignore[arg-type]
                             if stealth_cfg.get("window_position") else None),
            mute_audio=stealth_cfg.get("mute_audio", False),
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

    def __init__(self, config: StealthConfig | None = None):
        self.config = config or StealthConfig()
        self._playwright = None
        self._context = None  # persistent context (browser + cookies combined)
        self._page = None

        self._screenshot_dir = Path(self.config.screenshot_dir).expanduser()
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)

    @property
    def is_running(self) -> bool:
        return self._page is not None

    @property
    def page(self):
        """The live Playwright/Patchright page (for advanced use: interception, routing)."""
        return self._page

    @property
    def context(self):
        """The persistent browser context (cookies + pages)."""
        return self._context

    async def start(self):
        """Start a stealth browser session (persistent context)."""
        if self._context:
            logger.warning("StealthController already started")
            return

        logger.info("Starting stealth browser (patchright)...")
        async_playwright = _get_patchright(require=self.config.require_stealth)
        self._playwright = await async_playwright().start()

        user_data = Path(self.config.user_data_dir).expanduser()
        user_data.mkdir(parents=True, exist_ok=True)

        launch_kwargs: dict[str, Any] = {
            "channel": self.config.channel,
            "headless": self.config.headless,
            "no_viewport": True,  # critical stealth setting
            # Do NOT add custom user_agent or extra_http_headers — detectable
        }

        if self.config.proxy:
            # Honour credentials embedded in the proxy URL (user:pass@host) — a bare
            # {"server": url} silently drops them and the proxy auth-fails.
            try:
                from navig.browser.proxy import ProxySpec  # noqa: PLC0415

                launch_kwargs["proxy"] = ProxySpec.from_url(self.config.proxy).to_playwright()
            except Exception:  # noqa: BLE001 — never let proxy parsing block launch
                launch_kwargs["proxy"] = {"server": self.config.proxy}

        # ── Stage 5: coherent fingerprint (context opts) + WebRTC leak flags ──
        self._fingerprint = None
        if self.config.fingerprint:
            self._fingerprint = self._build_fingerprint()
            launch_kwargs.update(self._fingerprint_context_opts(self._fingerprint))
        if self.config.webrtc_protection:
            from navig.browser.fingerprint import webrtc_launch_args  # noqa: PLC0415

            launch_kwargs.setdefault("args", [])
            launch_kwargs["args"] = list(launch_kwargs["args"]) + webrtc_launch_args()

        # Keep the automation Chrome quiet: no first-run/welcome tab, no default-browser nag,
        # no EU "choose a search engine" screen. Added explicitly because Patchright can strip
        # Playwright's own default first-run flags.
        from navig.browser.targets import CHROMIUM_QUIET_ARGS  # noqa: PLC0415

        launch_kwargs.setdefault("args", [])
        launch_kwargs["args"] = list(launch_kwargs["args"]) + list(CHROMIUM_QUIET_ARGS)

        # Optional fixed window size. With no_viewport=True the page matches the OS window, so a
        # site that only renders its desktop layout above a width breakpoint (e.g. TikTok's comment
        # side-panel) needs the window widened here rather than via a (stealth-detectable) viewport.
        if self.config.window_size:
            w, h = self.config.window_size
            launch_kwargs["args"] = [
                a for a in launch_kwargs["args"] if not str(a).startswith("--window-size=")
            ] + [f"--window-size={int(w)},{int(h)}"]
        # Offscreen window (headful-but-hidden) + audio mute. Together these let a HEADFUL browser
        # run invisibly: real enough to defeat headless bot-detection, never seen or heard.
        if self.config.window_position:
            x, y = self.config.window_position
            launch_kwargs["args"] = [
                a for a in launch_kwargs["args"] if not str(a).startswith("--window-position=")
            ] + [f"--window-position={int(x)},{int(y)}"]
        if self.config.mute_audio and "--mute-audio" not in launch_kwargs["args"]:
            launch_kwargs["args"] = list(launch_kwargs["args"]) + ["--mute-audio"]

        self._context = await self._playwright.chromium.launch_persistent_context(
            str(user_data),
            **launch_kwargs,
        )

        # The JS shim layer is opt-in and applied ONLY off Patchright (it would contradict
        # Patchright's engine-level patches and *create* an incoherence tell).
        if self._fingerprint is not None and self.config.fingerprint_js and not _engine_is_patchright:
            from navig.browser.fingerprint import to_init_script  # noqa: PLC0415

            try:
                await self._context.add_init_script(to_init_script(self._fingerprint))
            except Exception as exc:  # noqa: BLE001
                logger.debug("[stealth] fingerprint init-script skipped: %s", exc)

        # Reuse existing page or open a new one
        self._page = (
            self._context.pages[0] if self._context.pages else await self._context.new_page()
        )
        self._page.set_default_timeout(self.config.timeout_ms)

        logger.info("Stealth browser ready")

    def _build_fingerprint(self):
        """Generate the coherent fingerprint for this session (seeded → stable identity)."""
        from navig.browser.fingerprint import generate  # noqa: PLC0415

        return generate(
            seed=self.config.seed,
            locale=self.config.locale,
            timezone=self.config.timezone_id,
            geolocation=self.config.geolocation,
        )

    @staticmethod
    def _fingerprint_context_opts(fp) -> dict:
        from navig.browser.fingerprint import to_context_options  # noqa: PLC0415

        return to_context_options(fp)

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
            if blocked.lower().replace("*", "") in domain:
                return False
        if self.config.allowed_domains:
            return any(a.lower().replace("*", "") in domain for a in self.config.allowed_domains)
        return True

    # ── Core navigation ────────────────────────────────────────────────────────

    async def navigate(self, url: str, wait_until: str = "domcontentloaded") -> dict[str, Any]:
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

    async def screenshot(
        self,
        name: str | None = None,
        full_page: bool = False,
        selector: str | None = None,
    ) -> str:
        await self._ensure_started()
        if not name:
            name = f"stealth_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        if not name.endswith(".png"):
            name += ".png"
        path = self._screenshot_dir / name
        if selector:
            el = await self._page.query_selector(selector)
            if el:
                await el.screenshot(path=str(path))
            else:
                raise ValueError(f"Element not found: {selector}")
        else:
            await self._page.screenshot(path=str(path), full_page=full_page)
        logger.info("Stealth screenshot: %s", path)
        return str(path)

    async def get_content(self) -> str:
        await self._ensure_started()
        return await self._page.content()

    async def get_text(self, selector: str | None = None) -> str:
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

    async def get_cookies(self) -> list[dict[str, Any]]:
        await self._ensure_started()
        return await self._context.cookies()

    async def set_cookies(self, cookies: list[dict[str, Any]]):
        await self._ensure_started()
        await self._context.add_cookies(cookies)

    async def wait_for_selector(
        self, selector: str, timeout: int | None = None, state: str = "visible"
    ) -> bool:
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
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

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
        return annotate_a11y_snapshot(raw)

    async def get_interactive_elements_fast(self, limit: int = 50) -> list:
        """Single-JS-eval interactive element scan."""
        try:
            return await self._page.evaluate(
                f"""() => {{
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
            }}"""
            )
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
        except Exception:
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
            return {
                "ok": False,
                "error": type(exc).__name__,
                "detail": err[:200],
                "suggestion": ("scroll into view" if "not visible" in err else "check selector"),
            }

    async def safe_fill(self, selector: str, text: str, timeout: int = 5000) -> dict:
        """Fill with AI-readable structured error."""
        try:
            await self._page.fill(selector, text, timeout=timeout)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": type(exc).__name__, "detail": str(exc)[:200]}
