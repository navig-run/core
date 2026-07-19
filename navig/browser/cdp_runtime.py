"""
cdp_runtime — a single persistent event loop for all CDP work.

Why this exists: Playwright objects are bound to the event loop they were created
on and cannot be driven from a different loop. The MCP dispatcher calls tool
handlers *synchronously* (from inside the gateway's event loop), and we want CDP
sessions to persist across calls (see :mod:`navig.browser.session_manager`). So we
run every CDP coroutine on ONE long-lived background loop and block the caller
until it completes.

Both surfaces use it:
  * CLI (`navig cdp …`) — a short-lived process, submits one action and exits.
  * MCP (`cdp_*` tools) — the long-lived daemon; the persistent loop keeps the
    attached `CDPBridge` sessions alive between tool calls.

``run(coro)`` is safe to call from any thread (with or without a running loop of
its own) because the coroutine executes on the dedicated CDP loop, not the caller's.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Coroutine, TypeVar

_T = TypeVar("_T")

_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None
_lock = threading.Lock()

# Default per-action timeout (seconds). Launch/attach can be slow; page actions fast.
DEFAULT_TIMEOUT_S = 120.0


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """Return the CDP loop, starting its background thread on first use."""
    global _loop, _thread
    if _loop is not None:
        return _loop
    with _lock:
        if _loop is not None:
            return _loop
        loop = asyncio.new_event_loop()
        thread = threading.Thread(
            target=loop.run_forever, name="navig-cdp-loop", daemon=True
        )
        thread.start()
        _loop, _thread = loop, thread
        return loop


def run(coro: Coroutine[Any, Any, _T], timeout: float = DEFAULT_TIMEOUT_S) -> _T:
    """Run *coro* on the dedicated CDP loop and block until it returns."""
    loop = _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout)
