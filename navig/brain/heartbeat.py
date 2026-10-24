"""Keep the Brain lease alive while this process is the owner.

A lease with no heartbeat expires. Expiry is not cosmetic: an expired record is
what permits a *manual* takeover, so a long-running Brain that never heartbeats
silently turns its own guarantee off after one TTL. Every always-on process that
holds the lease (gateway, daemon supervisor) therefore runs this loop.

The loop is deliberately boring:

* renew at ``ttl / 3`` so two consecutive failures still fit inside the TTL;
* a :class:`~navig.brain.lease.StaleEpoch` (another machine took over) stops the
  loop — it does not retry, because retrying would fight the new Brain;
* a few transient failures are tolerated; after ``max_failures`` consecutive
  ones the loop stops and says so, rather than pretending to be the owner.

It never decides ownership. It only proves liveness to the authority.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import Any

from navig.brain.lease import StaleEpoch

logger = logging.getLogger(__name__)

#: Used when the lease record cannot tell us its own TTL.
DEFAULT_TTL_S: float = 300.0

#: Never renew more often than this — a tight loop against the authority is rude
#: and pointless: the TTL is minutes, not milliseconds.
MIN_INTERVAL_S: float = 20.0

#: Consecutive failures tolerated before the loop gives up.
MAX_FAILURES: int = 3


def _coerce_positive(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def lease_ttl_s() -> float:
    """The TTL of the lease this process holds, or the default."""
    try:  # pragma: no cover - trivial read through two layers
        from navig.brain import authority  # noqa: PLC0415

        store = authority.lease_store(None)
        record = store.read()
        if record is not None:
            return _coerce_positive(getattr(record, "ttl", None), DEFAULT_TTL_S)
    except Exception:  # noqa: BLE001 - a status read must never break the loop setup
        pass
    return DEFAULT_TTL_S


def refresh_interval(ttl_s: float | None = None) -> float:
    """Renew at a third of the TTL, with a sane floor."""
    ttl = _coerce_positive(ttl_s, DEFAULT_TTL_S) if ttl_s is not None else lease_ttl_s()
    return max(MIN_INTERVAL_S, ttl / 3.0)


async def heartbeat_loop(
    *,
    interval: float | None = None,
    refresh: Callable[[], Any] | None = None,
    max_failures: int = MAX_FAILURES,
    stop: asyncio.Event | None = None,
) -> None:
    """Renew until the lease is gone, the epoch is stale, or we stop.

    ``refresh`` is injectable so the loop is testable without a network.
    """
    if refresh is None:
        from navig.brain import guard  # noqa: PLC0415

        refresh = guard.refresh
    period = _coerce_positive(interval, refresh_interval()) if interval is not None else refresh_interval()
    failures = 0
    while True:
        if stop is not None and stop.is_set():
            return
        await asyncio.sleep(period)
        if stop is not None and stop.is_set():
            return
        try:
            # The authority client is synchronous HTTP; never block the loop thread.
            await asyncio.to_thread(refresh)
        except StaleEpoch:
            logger.warning(
                "brain lease heartbeat stopped: another machine took the Brain over "
                "(stale epoch) — this process no longer owns it"
            )
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            failures += 1
            logger.warning(
                "brain lease heartbeat failed (%d/%d): %s", failures, max_failures, exc
            )
            if failures >= max_failures:
                logger.error(
                    "brain lease heartbeat gave up after %d consecutive failures; "
                    "the lease will expire and a takeover becomes possible",
                    failures,
                )
                return
            continue
        failures = 0


def start_thread(
    *,
    interval: float | None = None,
    refresh: Callable[[], Any] | None = None,
) -> threading.Thread:
    """Run :func:`heartbeat_loop` on a daemon thread.

    The daemon supervisor is a synchronous program; the gateway is an asyncio one.
    Both need the same renewal, so the loop is factored out and started on a thread
    here. The thread is a daemon: it must never keep a stopping process alive.
    """

    def _run() -> None:
        try:
            asyncio.run(heartbeat_loop(interval=interval, refresh=refresh))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a renewal thread must not crash the process
            logger.exception("brain lease heartbeat thread stopped unexpectedly")

    thread = threading.Thread(target=_run, name="brain-lease-heartbeat", daemon=True)
    thread.start()
    return thread

