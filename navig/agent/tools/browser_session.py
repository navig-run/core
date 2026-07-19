"""Persistent browser worker for the agent ``browser_tool``.

Why a dedicated thread + loop
-----------------------------
Playwright objects are bound to the event loop that created them, but the agent
tool dispatcher (``AgentToolRegistry.execute`` → ``_run_tool_sync``) runs *every*
tool call in a **throwaway thread with a brand-new event loop**. A controller
stashed in a plain dict therefore cannot be reused on the next call.

So we host ONE long-lived daemon thread that owns a persistent asyncio loop;
all ``BrowserController`` sessions live on it and survive across tool calls.
Tools (running on their own ephemeral threads) marshal coroutines onto it via
:func:`run_on_browser_loop`. ``_SESSIONS`` is only ever touched *on* the browser
loop, so it needs no extra locking.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, TypeVar

logger = logging.getLogger(__name__)

# Auto-close a session after this many seconds idle (no orphan Chromium).
IDLE_TIMEOUT = 300.0
# Hard cap on concurrent browser sessions; open one more → close the LRU.
MAX_SESSIONS = 3
_GC_INTERVAL = 60.0
# Per-command wall clock; the whole tool call stays under the 120 s _run_tool_sync cap.
DEFAULT_CALL_TIMEOUT = 90.0

T = TypeVar("T")

_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None
_start_lock = threading.Lock()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """Return the shared browser loop, starting its daemon thread on first use."""
    global _loop, _thread
    with _start_lock:
        if _loop is not None and _loop.is_running():
            return _loop
        loop = asyncio.new_event_loop()

        def _run() -> None:
            asyncio.set_event_loop(loop)
            loop.call_soon(lambda: loop.create_task(_gc_task()))
            loop.run_forever()

        t = threading.Thread(target=_run, name="navig-browser-loop", daemon=True)
        t.start()
        _loop = loop
        _thread = t
        logger.info("[browser] persistent browser loop started")
        return loop


def run_on_browser_loop(coro: Awaitable[T], timeout: float = DEFAULT_CALL_TIMEOUT) -> T:
    """Run *coro* on the shared browser loop from any thread and block for the result."""
    loop = _ensure_loop()
    fut = asyncio.run_coroutine_threadsafe(coro, loop)  # type: ignore[arg-type]
    return fut.result(timeout=timeout)


# ── Session registry (browser-loop-only state) ──────────────────────────────


@dataclass
class BrowserSession:
    controller: Any  # BrowserController | StealthController
    ref_map: dict[int, dict] = field(default_factory=dict)
    created_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)


_SESSIONS: dict[str, BrowserSession] = {}


# session_key -> CDP endpoint of a *visible* browser (e.g. navig-os's WebView2
# pane) that navig-os has advertised. When set, the agent ATTACHES to that
# browser (via CDPBridge) instead of launching its own headless one — so the
# agent drives the exact pane the user sees ("one browser everywhere"). Set/read
# across threads; plain-dict ops are GIL-atomic and register is rare.
_DESKTOP_ENDPOINTS: dict[str, str] = {}


def _port_from_endpoint(cdp_url: str) -> int | None:
    """'http://127.0.0.1:9333' / 'http://127.0.0.1:9333/' -> 9333."""
    try:
        host_port = cdp_url.split("//", 1)[-1].split("/", 1)[0]
        return int(host_port.rsplit(":", 1)[-1])
    except (ValueError, IndexError):
        return None


async def _open_controller(stealth: bool, cdp_url: str | None = None) -> Any:
    """Open a controller for a session.

    * ``cdp_url`` set  → ATTACH to that running browser via CDPBridge (the visible
      desktop pane); does not launch a new process.
    * otherwise        → launch a fresh headless BrowserController (or stealth).
    Raises RuntimeError if navig.browser/Playwright is absent.
    """
    try:
        from navig.browser.controller import BrowserConfig, BrowserController
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise RuntimeError(
            "navig.browser is unavailable — install Playwright + Chromium "
            "(run: playwright install chromium)"
        ) from exc

    if cdp_url:
        port = _port_from_endpoint(cdp_url)
        if port is None:
            raise RuntimeError(f"invalid desktop CDP endpoint: {cdp_url!r}")
        from navig.browser.cdp_bridge import CDPBridge

        ctrl = CDPBridge(debug_port=port)
        await ctrl.start()  # attaches to the existing browser — no new window
        return ctrl

    if stealth:
        try:
            from navig.browser.stealth import StealthController

            ctrl = StealthController()
        except Exception:  # noqa: BLE001 - stealth optional; fall back to Tier-1
            ctrl = BrowserController(BrowserConfig(headless=True, timeout_ms=30_000))
    else:
        ctrl = BrowserController(BrowserConfig(headless=True, timeout_ms=30_000))
    await ctrl.start()
    return ctrl


async def get_or_open(key: str, stealth: bool = False) -> BrowserSession:
    """Return the live session for *key*, opening one (LRU-capped) if needed. On loop.

    If navig-os has registered a desktop CDP endpoint for *key*, attach to that
    visible pane instead of launching a headless browser.
    """
    sess = _SESSIONS.get(key)
    if sess is not None and getattr(sess.controller, "is_running", False):
        sess.last_used = time.monotonic()
        return sess

    _SESSIONS.pop(key, None)  # stale/closed → drop
    if len(_SESSIONS) >= MAX_SESSIONS:
        oldest = min(_SESSIONS.items(), key=lambda kv: kv[1].last_used)[0]
        await _close(oldest)

    cdp_url = _DESKTOP_ENDPOINTS.get(key)
    ctrl = await _open_controller(stealth, cdp_url=cdp_url)
    sess = BrowserSession(controller=ctrl)
    _SESSIONS[key] = sess
    logger.info(
        "[browser] opened session %r via %s (total=%d)",
        key, "desktop-pane CDP" if cdp_url else "headless", len(_SESSIONS),
    )
    return sess


async def _close(key: str) -> bool:
    sess = _SESSIONS.pop(key, None)
    if sess is None:
        return False
    try:
        await sess.controller.stop()
    except Exception as exc:  # noqa: BLE001 - teardown is best-effort
        logger.debug("[browser] error closing %r: %s", key, exc)
    logger.info("[browser] closed session %r (remaining=%d)", key, len(_SESSIONS))
    return True


async def _gc_task() -> None:
    """Idle-GC: close sessions unused for IDLE_TIMEOUT. Runs forever on the loop."""
    while True:
        try:
            await asyncio.sleep(_GC_INTERVAL)
            now = time.monotonic()
            stale = [k for k, s in _SESSIONS.items() if now - s.last_used > IDLE_TIMEOUT]
            for k in stale:
                await _close(k)
        except asyncio.CancelledError:  # pragma: no cover
            raise
        except Exception as exc:  # noqa: BLE001 - GC must never die
            logger.debug("[browser] GC error: %s", exc)


# ── Public sync helpers (safe to call from any thread) ──────────────────────


def close_session(key: str) -> bool:
    """Close a session by key (e.g. on chat /reset). Best-effort; never raises."""
    try:
        return run_on_browser_loop(_close(key), timeout=30.0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[browser] close_session(%r) failed: %s", key, exc)
        return False


def close_all() -> None:
    """Close every browser session (e.g. on agent-cache flush). Best-effort."""
    async def _all() -> None:
        for k in list(_SESSIONS.keys()):
            await _close(k)

    if _loop is None or not _loop.is_running():
        return  # loop never started → nothing to close
    try:
        run_on_browser_loop(_all(), timeout=30.0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[browser] close_all failed: %s", exc)


def register_desktop_endpoint(key: str, cdp_url: str) -> None:
    """navig-os advertises its visible pane's CDP endpoint for session *key*.

    The next ``get_or_open(key)`` will ATTACH to that browser. Any session already
    open for *key* (e.g. a headless one) is closed so it re-opens attached.
    """
    _DESKTOP_ENDPOINTS[key] = cdp_url
    if key in _SESSIONS:
        close_session(key)  # runs _close on the loop; next open re-attaches
    logger.info("[browser] desktop pane registered for %r → %s", key, cdp_url)


def clear_desktop_endpoint(key: str) -> None:
    """Drop the desktop pane for *key* (pane closed) and close the attached session."""
    _DESKTOP_ENDPOINTS.pop(key, None)
    if key in _SESSIONS:
        close_session(key)


def active_session_count() -> int:
    return len(_SESSIONS)
