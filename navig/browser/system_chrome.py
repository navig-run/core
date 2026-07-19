"""Real-Chrome login/automation engine — a launched *system* Chrome driven over CDP.

Why this exists: NAVIG's other engines are all **automation** frameworks — Patchright/Playwright
launch with ``--enable-automation`` (so ``navigator.webdriver === true``) and Camoufox can be
unstable on heavy pages. Some sites hard-block automated logins (TikTok's login "maximum attempts"
limiter) while still accepting a **real Chrome that a human drives**. A Chrome launched with only a
debug port — *no* ``--enable-automation`` — reports ``navigator.webdriver === false`` (it looks
like a normal browser), and NAVIG drives it over CDP (``connect_over_cdp``), exactly like
``navig cdp``. Live-verified: such a Chrome loads TikTok's login page with 0 region failures.

Isolation & safety:
- Uses its OWN ``--user-data-dir`` — never the user's real Chrome profile.
- Kills ONLY the process it launched (its ``Popen`` handle) — **never** a name-based ``chrome.exe``
  sweep, which would close the user's real browser.

Trade-off vs the stealth tiers: it's a genuine Chrome (webdriver=false) but is NOT
fingerprint-patched, so it suits a **human-driven** login rather than fully-headless scraping.
Selected via ``router.get_browser(engine="chrome")``.
"""

from __future__ import annotations

import socket
import subprocess
import time
from pathlib import Path

from navig.debug_logger import get_debug_logger

logger = get_debug_logger()

__all__ = ["SystemChromeController", "SystemChromeUnavailable",
           "default_browser_profile", "capture_existing_cookies"]

_DEFAULT_PROFILE = "~/.navig/browser/profiles/system-chrome"
# Chromium browsers that accept --remote-debugging-port, in preference order.
_CHROME_APPS = ("chrome", "edge", "brave", "chromium")


class SystemChromeUnavailable(RuntimeError):
    """No system Chrome/Edge/Brave binary was found, or its debug port never came up."""


def _wait_for_port(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


class SystemChromeController:
    """Launch a real system Chrome (isolated profile + debug port, no automation flags) and drive
    it over CDP. Same ``start``/``stop``/``.page``/``.context`` surface as the other controllers."""

    def __init__(self, *, headless: bool = False, proxy: str | None = None,
                 user_data_dir: str | None = None, app: str | None = None,
                 port: int | None = None):
        self.headless = headless
        self.proxy = proxy
        self.user_data_dir = user_data_dir or _DEFAULT_PROFILE
        self.app = app  # explicit app id, else auto-detect the first available
        self.port = port
        self.engine_name = "chrome"
        self._proc: subprocess.Popen | None = None
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    @property
    def is_running(self) -> bool:
        return self._page is not None

    @property
    def page(self):
        return self._page

    @property
    def context(self):
        return self._context

    def _resolve_exe(self) -> tuple[str | None, str | None]:
        from navig.browser import targets as t  # noqa: PLC0415

        for app in ((self.app,) if self.app else _CHROME_APPS):
            exe = t.resolve_executable(app)
            if exe:
                return app, exe
        return None, None

    def _build_args(self, exe: str, port: int) -> list[str]:
        from navig.browser.targets import CHROMIUM_QUIET_ARGS  # noqa: PLC0415

        args = [exe, f"--remote-debugging-port={port}",
                f"--user-data-dir={Path(self.user_data_dir).expanduser()}", *CHROMIUM_QUIET_ARGS]
        if self.headless:
            args.append("--headless=new")
        if self.proxy:
            try:
                from navig.browser.proxy import ProxySpec  # noqa: PLC0415

                args.append(f"--proxy-server={ProxySpec.from_url(self.proxy).server}")
            except Exception:  # noqa: BLE001
                args.append(f"--proxy-server={self.proxy}")
        return args

    async def start(self):
        if self._page is not None:
            logger.warning("SystemChromeController already started")
            return
        from navig.browser import targets as t  # noqa: PLC0415

        app, exe = self._resolve_exe()
        if not exe:
            raise SystemChromeUnavailable(
                "no system Chrome/Edge/Brave found to launch — install one, or use "
                "`--engine firefox`.")
        self.engine_name = app or "chrome"
        port = self.port or t.find_free_port() or 9222
        Path(self.user_data_dir).expanduser().mkdir(parents=True, exist_ok=True)
        args = self._build_args(exe, port)
        logger.info("[system_chrome] launching %s on CDP port %d", self.engine_name, port)
        self._proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not _wait_for_port(port):
            self._terminate_proc()
            raise SystemChromeUnavailable(
                f"{self.engine_name} did not open its debug port {port} in time.")
        try:
            from playwright.async_api import async_playwright  # noqa: PLC0415

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{port}")
        except Exception as exc:  # noqa: BLE001
            await self._safe_stop_playwright()
            self._terminate_proc()
            raise SystemChromeUnavailable(
                f"couldn't attach to {self.engine_name} over CDP: {exc}") from exc
        ctx = (self._browser.contexts[0] if self._browser.contexts
               else await self._browser.new_context())
        self._context = ctx
        self._page = ctx.pages[0] if ctx.pages else await ctx.new_page()

    async def stop(self):
        # We OWN this browser (we launched it), so close it, then hard-kill only OUR PID.
        try:
            if self._browser is not None:
                await self._browser.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("[system_chrome] browser close: %s", exc)
        await self._safe_stop_playwright()
        self._terminate_proc()
        self._browser = self._context = self._page = None

    async def _safe_stop_playwright(self):
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception as exc:  # noqa: BLE001
                logger.debug("[system_chrome] playwright stop: %s", exc)
            self._playwright = None

    def _terminate_proc(self) -> None:
        # Kill ONLY the process we launched — never a name-based sweep (would close the user's Chrome).
        if self._proc is not None:
            try:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    self._proc.kill()
            except Exception as exc:  # noqa: BLE001
                logger.debug("[system_chrome] process teardown: %s", exc)
            finally:
                self._proc = None


# ── capture an EXISTING logged-in session (no re-login) ────────────────────────────────
#
# The user is already logged in in their real browser. Chrome 127+ encrypts cookies with
# App-Bound Encryption (ABE), and Chrome 136+ refuses --remote-debugging-port on the *default*
# profile — so we can't read the live profile. BUT ABE's key is bound to the Windows user +
# the chrome.exe binary, NOT the profile PATH. So we COPY a minimal slice of the profile to a
# throwaway dir, launch the REAL chrome.exe against the copy over CDP, and read its OWN decrypted
# cookies (Playwright's context.cookies() == CDP Network.getAllCookies). No decryption on our
# side, no admin, no AV-flagged injection. The copy is deleted immediately; only the target host's
# cookies leave the function. Never touches the user's live browser or profile.

# The per-user "User Data" root for each Chromium browser on Windows.
def _browser_userdata_roots(app: str) -> list[Path]:
    import os

    la = Path(os.environ.get("LOCALAPPDATA", "")) if os.environ.get("LOCALAPPDATA") else None
    if la is None:
        return []
    table = {
        "chrome": [la / "Google/Chrome/User Data"],
        "edge": [la / "Microsoft/Edge/User Data"],
        "brave": [la / "BraveSoftware/Brave-Browser/User Data"],
        "chromium": [la / "Chromium/User Data"],
    }
    return table.get(app, [])


def default_browser_profile(app: str) -> Path | None:
    """The user's existing 'User Data' dir for *app* (chrome/edge/brave), or None if absent."""
    for root in _browser_userdata_roots(app):
        if root.exists():
            return root
    return None


# Only the files Chrome needs to launch + decrypt cookies — NOT History/Login Data/etc. Keeps
# the copy tiny and avoids sweeping up unrelated private data. NOTE: copy the WAL/SHM sidecars
# (recent cookies live there in WAL mode) but NOT the rollback `-journal` — a stale journal makes
# SQLite roll the DB back on open and drop cookies.
_MIN_PROFILE_FILES = (
    "Local State",                       # holds the ABE-wrapped cookie key
    "Default/Network/Cookies",           # the cookie DB
    "Default/Network/Cookies-wal",       # write-ahead log — recent cookies (if present)
    "Default/Network/Cookies-shm",       # WAL shared-memory index
    "Default/Preferences",
    "First Run",
)


def _copy_locked(src: Path, dst: Path) -> None:
    """Byte-copy a file another process may have open. Python opens with FILE_SHARE_READ on
    Windows, so a file Chrome holds open for its own use (SQLite share mode) can still be read."""
    with open(src, "rb") as r, open(dst, "wb") as w:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            w.write(chunk)


def _copy_min_profile(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for rel in _MIN_PROFILE_FILES:
        f = src / rel
        if f.exists() and f.is_file():
            out = dst / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            try:
                _copy_locked(f, out)
            except Exception as exc:  # noqa: BLE001 — a locked/absent optional file is fine
                logger.debug("[system_chrome] copy %s skipped: %s", rel, exc)


async def capture_existing_cookies(app: str, host_substr: str,
                                   headless: bool = True) -> list[dict]:
    """Capture the user's EXISTING cookies for *host_substr* from *app*'s logged-in profile, by
    copying a minimal profile slice and letting the real browser decrypt its own cookies over CDP.

    Returns Playwright-shaped cookies for the host (may be empty if not logged in, or if this
    machine hits the known ABE clone-decrypt failure). Never raises; deletes the temp copy.
    """
    import shutil
    import tempfile

    src = default_browser_profile(app)
    if src is None:
        logger.info("[system_chrome] no %s profile found to capture from", app)
        return []
    tmp = Path(tempfile.mkdtemp(prefix="navig_capture_"))
    try:
        _copy_min_profile(src, tmp)
        ctrl = SystemChromeController(app=app, headless=headless, user_data_dir=str(tmp))
        try:
            await ctrl.start()
        except SystemChromeUnavailable as exc:
            logger.info("[system_chrome] capture launch failed: %s", exc)
            return []
        try:
            cookies = await ctrl.context.cookies()
        finally:
            await ctrl.stop()
        host = (host_substr or "").lower()
        return [c for c in cookies if host in (c.get("domain") or "").lower()]
    except Exception as exc:  # noqa: BLE001 — capture is best-effort; caller falls back
        logger.info("[system_chrome] capture failed: %s", exc)
        return []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
