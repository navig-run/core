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

from navig.notify.delivery import all_channels_failed
from navig.notify.producers import spawn

logger = logging.getLogger("navig.notify")

_WINDOW_S = 300.0
_MAX_PER_WINDOW = 5
_DEDUPE_COOLDOWN_S = 600.0


class _Throttle:
    """Rate-limit + per-key dedupe. Pure/testable (clock injected).

    ``allow()`` is a **reservation**, not a record of delivery: it is consulted
    before the send, because the whole point is to avoid a storm. If the send then
    reaches nobody, :meth:`rollback` returns the budget so the next occurrence is
    not silently swallowed for a full cooldown.

    Denials are counted rather than discarded — a reporter that quietly drops
    hundreds of records looks exactly like a system with only a handful of errors.
    :meth:`drain_suppressed` hands the count to the next notification that does go
    out.
    """

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
        self._suppressed = 0

    def allow(self, key: str, now: float) -> bool:
        last = self._recent.get(key)
        if last is not None and now - last < self.cooldown_s:
            self._suppressed += 1
            return False
        while self._times and now - self._times[0] > self.window_s:
            self._times.popleft()
        if len(self._times) >= self.max_per_window:
            self._suppressed += 1
            return False
        self._times.append(now)
        self._recent[key] = now
        if len(self._recent) > 256:  # bound memory
            oldest = min(self._recent, key=self._recent.__getitem__)
            self._recent.pop(oldest, None)
        return True

    def rollback(self, key: str) -> None:
        """Undo the reservation :meth:`allow` granted for *key* — nothing was delivered.

        Without this, an alert that reached zero channels still burned both the
        per-key cooldown and a slot in the rate-limit window, so the next identical
        occurrence was suppressed for up to ``cooldown_s`` — losing the event twice
        over. Safe to call when no reservation is outstanding.
        """
        ts = self._recent.pop(key, None)
        if ts is None:
            return
        try:
            self._times.remove(ts)
        except ValueError:  # already aged out of the window
            pass

    def drain_suppressed(self) -> int:
        """Number of denials since the last drain, resetting the counter."""
        n, self._suppressed = self._suppressed, 0
        return n


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
            # Say what the rate limit ate. During a storm only _MAX_PER_WINDOW
            # notifications go out per window; reporting 5 errors and silently
            # dropping 500 reads as "a few errors happened".
            suppressed = self._throttle.drain_suppressed()
            if suppressed:
                body += f"\n\n(+{suppressed} further error(s) suppressed by the rate limit)"
            # Hop onto the loop thread; the handler may fire from any thread.
            self._loop.call_soon_threadsafe(self._schedule, key, title, body)
        except Exception:  # a logging handler must NEVER raise
            pass

    def _schedule(self, key: str, title: str, body: str) -> None:
        # Runs in the loop thread → there is a running loop. spawn() keeps a
        # strong ref so the self-error push can't be GC'd before it runs.
        spawn(self._send(key, title, body))

    async def _send(self, key: str, title: str, body: str) -> None:
        try:
            from navig.notify import dispatch

            outcome = await dispatch(
                "self_error", title, body, priority="high", data={"source": "navig"}
            )
            if all_channels_failed(outcome):
                # Nobody was told, so give the budget back: otherwise this error's
                # cooldown is burnt and the next identical one is suppressed too.
                self._throttle.rollback(key)
        except Exception:
            # Deliberately NO rollback here. dispatch() is contracted not to raise
            # (a down channel returns {ok: False}), so reaching this means a bug in
            # the dispatch path itself — and rolling back would strip the rate limit
            # from an error that fails every single time, turning one fault into a
            # storm. Keep the reservation; the throttle stays intact.
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
