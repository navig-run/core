"""Self-error reporter — surface the daemon's own ERROR/CRITICAL logs as
``self_error`` notifications, so your brain tells you when it breaks (deck + the
channels you enabled) instead of you having to tail logs.

A ``logging.Handler`` attached to the ``navig`` logger does the work. Three
safeguards keep it from being a nuisance:

  * **recursion guard** — records from ``navig.notify*`` (the dispatch path
    itself) are skipped, so a delivery error can't trigger another notification.
  * **rate limit** — at most ``_MAX_PER_WINDOW`` notifications per ``_WINDOW_S``.
  * **dedupe** — the same (logger, message) won't re-fire within ``_DEDUPE_COOLDOWN_S``.

Opt-in: the gateway installs it only when ``monitors.self_errors.enabled`` is set.
The rate-limit/dedupe logic is pure (``_Throttle``) and unit-tested.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

logger = logging.getLogger("navig.notify")

_WINDOW_S = 300.0
_MAX_PER_WINDOW = 5
_DEDUPE_COOLDOWN_S = 600.0


class _Throttle:
    """Rate-limit + per-key dedupe. Pure/testable (clock injected)."""

    def __init__(
        self,
        *,
        window_s: float = _WINDOW_S,
        max_per_window: int = _MAX_PER_WINDOW,
        cooldown_s: float = _DEDUPE_COOLDOWN_S,
    ) -> None:
        self.window_s = window_s
        self.max_per_window = max_per_window
        self.cooldown_s = cooldown_s
        self._times: deque[float] = deque()
        self._recent: dict[str, float] = {}

    def allow(self, key: str, now: float) -> bool:
        last = self._recent.get(key)
        if last is not None and now - last < self.cooldown_s:
            return False
        while self._times and now - self._times[0] > self.window_s:
            self._times.popleft()
        if len(self._times) >= self.max_per_window:
            return False
        self._times.append(now)
        self._recent[key] = now
        if len(self._recent) > 256:  # bound memory
            oldest = min(self._recent, key=self._recent.__getitem__)
            self._recent.pop(oldest, None)
        return True


class NotifyErrorHandler(logging.Handler):
    def __init__(self, loop: asyncio.AbstractEventLoop, *, level: int = logging.ERROR) -> None:
        super().__init__(level=level)
        self._loop = loop
        self._throttle = _Throttle()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.name.startswith("navig.notify"):  # recursion guard
                return
            if getattr(record, "_no_notify", False):
                return
            msg = record.getMessage()
            key = f"{record.name}:{msg[:80]}"
            if not self._throttle.allow(key, time.time()):
                return
            title = (msg.splitlines()[0][:100] if msg else record.levelname)
            body = f"{record.name} · {record.levelname}"
            if record.exc_info:
                import traceback

                tb = "".join(traceback.format_exception(*record.exc_info))
                body += "\n\n" + tb[-600:]
            # Hop onto the loop thread; the handler may fire from any thread.
            self._loop.call_soon_threadsafe(self._schedule, title, body)
        except Exception:  # a logging handler must NEVER raise
            pass

    def _schedule(self, title: str, body: str) -> None:
        # Runs in the loop thread → there is a running loop.
        asyncio.create_task(self._send(title, body))

    async def _send(self, title: str, body: str) -> None:
        try:
            from navig.notify import dispatch

            await dispatch("self_error", title, body, priority="high", data={"source": "navig"})
        except Exception:
            logger.debug("self-error notify failed", exc_info=True)


_handler: NotifyErrorHandler | None = None


def install_self_error_reporter() -> NotifyErrorHandler:
    """Attach the handler to the ``navig`` logger. Idempotent. Call from the loop."""
    global _handler
    if _handler is not None:
        return _handler
    loop = asyncio.get_running_loop()
    handler = NotifyErrorHandler(loop)
    logging.getLogger("navig").addHandler(handler)
    _handler = handler
    return handler


def uninstall_self_error_reporter() -> None:
    global _handler
    if _handler is not None:
        logging.getLogger("navig").removeHandler(_handler)
        _handler = None
