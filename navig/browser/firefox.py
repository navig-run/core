"""Firefox browser engine — a **non-CDP** stealth tier (Juggler protocol, not CDP).

Why Firefox exists alongside the Chromium tiers: some anti-bot stacks fingerprint the
**CDP/Chromium automation surface itself**. Patchright, despite its stealth patches, still
drives Chromium over CDP, so it still carries that class of artifact — which is exactly what
TikTok's web-login *region* check flags (the region call dies with ``net::ERR_FAILED`` in the
automated Chromium browser). Playwright's Firefox is driven over Firefox's **Juggler**
protocol, so those CDP artifacts don't exist and the whole detection class is sidestepped.

Two engines behind one controller:
- ``engine="firefox"`` (default) — **Playwright Firefox**: reliable, cross-platform, and
  navig-managed (the binary is auto-provisioned on first use, the same way Chromium is).
- ``engine="camoufox"`` — **Camoufox**: a C++-patched Firefox with an engine-level coherent
  fingerprint + geoip. Opt-in (``pip install camoufox && python -m camoufox fetch``); raises a
  clear error if its binary isn't present.

The interface mirrors ``StealthController`` (``start`` / ``stop`` / ``.page`` / ``.context``) so
it drops into the same call sites (login flows, session capture). Session state round-trips
through NAVIG's vault via the standard Playwright ``storage_state``, so a session captured in
Firefox restores into any other engine. Selected via ``router.get_browser(engine="firefox")``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from navig.debug_logger import get_debug_logger

logger = get_debug_logger()

__all__ = ["FirefoxController", "FirefoxUnavailable", "camoufox_available",
           "camoufox_binary_available", "ensure_camoufox", "ensure_playwright_firefox",
           "best_login_engine"]

_DEFAULT_PROFILE = "~/.navig/browser/profiles/firefox"


class FirefoxUnavailable(RuntimeError):
    """No usable Firefox engine (Playwright Firefox couldn't launch / Camoufox absent)."""


def camoufox_available() -> bool:
    """True if the Camoufox (C++-stealth Firefox) package is importable."""
    try:
        import camoufox.async_api  # noqa: F401, PLC0415

        return True
    except Exception:  # noqa: BLE001
        return False


def ensure_playwright_firefox() -> None:
    """Provision the Playwright Firefox binary on demand (navig-managed, like Chromium).

    Idempotent and fast when already installed. Best-effort — a failure here surfaces later
    as a clear ``FirefoxUnavailable`` from the launch attempt.
    """
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "firefox"],
            check=False, capture_output=True, timeout=600,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[firefox] auto-install skipped: %s", exc)


def camoufox_binary_available() -> bool:
    """True if Camoufox's lib AND its fetched binary are both present (ready to launch)."""
    try:
        from camoufox.pkgman import installed_verstr  # noqa: PLC0415

        return bool(installed_verstr())
    except Exception:  # noqa: BLE001 — lib missing or binary not fetched
        return False


def ensure_camoufox() -> bool:
    """Provision the Camoufox binary on demand (navig-managed). Returns True if usable after.

    Only attempts a fetch when the Camoufox package is installed; the ~150MB binary download is
    idempotent and skipped when already present.
    """
    if not camoufox_available():
        return False
    if camoufox_binary_available():
        return True
    logger.info("[firefox] fetching the Camoufox stealth binary (one-time ~150MB) …")
    try:
        subprocess.run([sys.executable, "-m", "camoufox", "fetch"],
                       check=False, capture_output=True, timeout=900)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[firefox] camoufox fetch skipped: %s", exc)
    return camoufox_binary_available()


def best_login_engine() -> str:
    """The stealthiest Firefox engine available: ``camoufox`` if its package is installed
    (it hides ``navigator.webdriver`` at the C++ level — plain Playwright Firefox cannot),
    otherwise plain ``firefox``. The Camoufox binary is fetched on first use."""
    return "camoufox" if camoufox_available() else "firefox"


def _to_pw_proxy(proxy: str | None) -> dict | None:
    if not proxy:
        return None
    try:
        from navig.browser.proxy import ProxySpec  # noqa: PLC0415

        return ProxySpec.from_url(proxy).to_playwright()
    except Exception:  # noqa: BLE001 — never let proxy parsing block launch
        return {"server": proxy}


def _looks_like_missing_binary(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "install" in msg or "executable doesn't exist" in msg or "playwright install" in msg


class FirefoxController:
    """A Firefox (Juggler, non-CDP) browser controller — Playwright Firefox or Camoufox.

    ``engine`` is ``"firefox"`` (Playwright Firefox, default) or ``"camoufox"`` (opt-in
    C++-stealth). Same ``start``/``stop``/``.page``/``.context`` surface as StealthController.
    """

    def __init__(self, *, engine: str = "firefox", headless: bool = True,
                 proxy: str | None = None, user_data_dir: str | None = None,
                 locale: str | None = None, timezone_id: str | None = None,
                 geolocation: dict | None = None):
        self.engine_name = "camoufox" if engine == "camoufox" else "firefox"
        self.headless = headless
        self.proxy = proxy
        self.user_data_dir = user_data_dir or _DEFAULT_PROFILE
        self.locale = locale
        self.timezone_id = timezone_id
        self.geolocation = geolocation
        self._playwright = None
        self._context = None
        self._page = None
        self._camoufox_cm = None

    # ── accessors (mirror StealthController) ──
    @property
    def is_running(self) -> bool:
        return self._page is not None

    @property
    def page(self):
        return self._page

    @property
    def context(self):
        return self._context

    async def start(self):
        if self._context is not None:
            logger.warning("FirefoxController already started")
            return
        if self.engine_name == "camoufox":
            await self._start_camoufox()
        else:
            await self._start_playwright_firefox()
        self._page = (
            self._context.pages[0] if self._context.pages else await self._context.new_page()
        )

    async def _start_playwright_firefox(self):
        try:
            from playwright.async_api import async_playwright  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            raise FirefoxUnavailable(
                "Playwright is required for the Firefox engine: `pip install playwright`."
            ) from exc

        self._playwright = await async_playwright().start()
        user_data = Path(self.user_data_dir).expanduser()
        user_data.mkdir(parents=True, exist_ok=True)

        kwargs: dict[str, Any] = {"headless": self.headless}
        pxy = _to_pw_proxy(self.proxy)
        if pxy:
            kwargs["proxy"] = pxy
        if self.locale:
            kwargs["locale"] = self.locale
        if self.timezone_id:
            kwargs["timezone_id"] = self.timezone_id
        if self.geolocation:
            kwargs["geolocation"] = self.geolocation
            kwargs["permissions"] = ["geolocation"]

        try:
            self._context = await self._playwright.firefox.launch_persistent_context(
                str(user_data), **kwargs)
        except Exception as exc:  # noqa: BLE001
            if _looks_like_missing_binary(exc):
                # Auto-provision the binary (navig-managed) and retry once.
                logger.info("[firefox] Firefox binary missing — provisioning via "
                            "`playwright install firefox` …")
                ensure_playwright_firefox()
                try:
                    self._context = await self._playwright.firefox.launch_persistent_context(
                        str(user_data), **kwargs)
                except Exception as exc2:  # noqa: BLE001
                    await self._safe_stop_playwright()
                    raise FirefoxUnavailable(
                        f"couldn't launch Playwright Firefox after install: {exc2}. Run "
                        "`python -m playwright install firefox` manually."
                    ) from exc2
            else:
                await self._safe_stop_playwright()
                raise FirefoxUnavailable(f"couldn't launch Playwright Firefox: {exc}") from exc
        self.engine_name = "firefox"

    async def _start_camoufox(self):
        try:
            from camoufox.async_api import AsyncCamoufox  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            raise FirefoxUnavailable(
                "the Camoufox engine isn't installed — `pip install camoufox[geoip] && "
                "python -m camoufox fetch` (or use engine=\"firefox\")."
            ) from exc

        if not ensure_camoufox():  # provision the binary on first use
            raise FirefoxUnavailable(
                "the Camoufox binary isn't provisioned — run `python -m camoufox fetch` "
                "(or use engine=\"firefox\")."
            )

        # geoip aligns timezone/locale/WebRTC to the exit IP — only meaningful with a proxy,
        # and it needs the camoufox[geoip] extra, so enable it ONLY when a proxy is set.
        opts: dict[str, Any] = {"headless": self.headless, "humanize": True}
        pxy = _to_pw_proxy(self.proxy)
        if pxy:
            opts["proxy"] = pxy
            opts["geoip"] = True
        if self.locale:
            opts["locale"] = self.locale
        try:
            self._camoufox_cm = AsyncCamoufox(**opts)
            obj = await self._camoufox_cm.__aenter__()
        except Exception as exc:  # noqa: BLE001
            self._camoufox_cm = None
            raise FirefoxUnavailable(
                f"couldn't start Camoufox: {exc}. Fetch its binary with "
                "`python -m camoufox fetch`, or use engine=\"firefox\"."
            ) from exc
        # AsyncCamoufox yields a Browser (has new_context) or a BrowserContext (no new_context).
        # no_viewport avoids Camoufox's Browser.setDefaultViewport protocol error.
        if hasattr(obj, "new_context"):
            self._context = await obj.new_context(no_viewport=True)
        else:
            self._context = obj
        self.engine_name = "camoufox"

    async def stop(self):
        try:
            if self._camoufox_cm is not None:
                await self._camoufox_cm.__aexit__(None, None, None)
            elif self._context is not None:
                await self._context.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("[firefox] context close: %s", exc)
        finally:
            await self._safe_stop_playwright()
            self._context = self._page = self._camoufox_cm = None

    async def _safe_stop_playwright(self):
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception as exc:  # noqa: BLE001
                logger.debug("[firefox] playwright stop: %s", exc)
            self._playwright = None
