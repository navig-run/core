"""
CDPSessionManager — reuse live CDP attachments across CLI / MCP / agent calls.

Attaching over CDP (``connect_over_cdp``) costs a WebSocket handshake + Playwright
startup. If every ``navig cdp click`` / ``cdp_type`` re-attached, the agent would
crawl. This manager keeps one live :class:`~navig.browser.cdp_bridge.CDPBridge`
per debug port, hands it back on request, and evicts idle sessions.

Process-wide singleton (``get_session_manager()``). Async — the bridge is async.
Stopping a session only closes the CDP WebSocket; the remote app keeps running
(see ``CDPBridge.stop``).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from navig.browser.cdp_bridge import CDPBridge
from navig.debug_logger import get_debug_logger

logger = get_debug_logger()

# Evict a session after this many seconds with no use.
IDLE_TIMEOUT_S = 300.0


@dataclass
class _Session:
    bridge: CDPBridge
    last_used: float = field(default_factory=time.monotonic)


class CDPSessionManager:
    """Pool of live CDP attachments keyed by debug port."""

    def __init__(self) -> None:
        self._sessions: dict[int, _Session] = {}
        self._lock = asyncio.Lock()

    async def get(self, port: int = 9222, tab_index: int = 0) -> CDPBridge:
        """Return a live bridge attached to *port*, attaching on first use.

        If the existing session's attach has died, it is transparently replaced.
        """
        async with self._lock:
            sess = self._sessions.get(port)
            if sess is not None and sess.bridge._page is not None:
                sess.last_used = time.monotonic()
                return sess.bridge

            # (Re)attach.
            if sess is not None:
                await self._safe_stop(sess.bridge)
            bridge = CDPBridge(debug_port=port, tab_index=tab_index)
            await bridge.start()
            self._sessions[port] = _Session(bridge=bridge)
            logger.info("[cdp.session] Attached session on port %d", port)
            return bridge

    async def release(self, port: int) -> None:
        """Detach and drop the session for *port* (remote app keeps running)."""
        async with self._lock:
            sess = self._sessions.pop(port, None)
        if sess is not None:
            await self._safe_stop(sess.bridge)
            logger.info("[cdp.session] Released session on port %d", port)

    async def release_all(self) -> None:
        """Detach every session (called on daemon shutdown)."""
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for sess in sessions:
            await self._safe_stop(sess.bridge)

    def active_ports(self) -> list[int]:
        """Ports with a currently-held session."""
        return sorted(self._sessions.keys())

    async def sweep_idle(self, now: float | None = None) -> int:
        """Evict sessions idle longer than IDLE_TIMEOUT_S. Returns count evicted."""
        now = now if now is not None else time.monotonic()
        to_evict: list[int] = []
        async with self._lock:
            for port, sess in self._sessions.items():
                if now - sess.last_used > IDLE_TIMEOUT_S:
                    to_evict.append(port)
            evicting = [self._sessions.pop(p) for p in to_evict]
        for sess in evicting:
            await self._safe_stop(sess.bridge)
        if to_evict:
            logger.info("[cdp.session] Swept %d idle session(s): %s", len(to_evict), to_evict)
        return len(to_evict)

    @staticmethod
    async def _safe_stop(bridge: CDPBridge) -> None:
        try:
            await bridge.stop()
        except Exception as exc:  # noqa: BLE001
            logger.debug("[cdp.session] stop error (ignored): %s", exc)


_MANAGER: CDPSessionManager | None = None


def get_session_manager() -> CDPSessionManager:
    """Return the process-wide CDP session manager (created on first call)."""
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = CDPSessionManager()
    return _MANAGER
