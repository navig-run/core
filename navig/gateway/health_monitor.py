"""Gateway channel health monitor with auto-restart budgeting.

Runs as a background task within the gateway server.  Polls all registered
channels at a configurable interval, detects stale channels (connected but
delivering no events), and restarts them subject to a per-rolling-hour
restart budget to prevent flapping.

Configuration keys (config/defaults.yaml  gateway.health_monitor.*):
    check_interval_s       Poll interval in seconds.          Default: 300
    stale_threshold_s      No-event window before stale.       Default: 600
    startup_grace_s        Grace period after channel start.   Default: 90
    max_restarts_per_hour  Restart budget per rolling hour.    Default: 10
    cooldown_cycles        Poll cycles of calm before reset.   Default: 2

Usage (in GatewayServer.start()):
    self._health_monitor = ChannelHealthMonitor(
        channels=self.channels,
        restart_fn=self._restart_channel,
        **health_cfg,
    )
    self._spawn_background_task(self._health_monitor.run())
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level defaults — sourced from config/defaults.yaml gateway.health_monitor.*
# ---------------------------------------------------------------------------
_DEFAULT_CHECK_INTERVAL_S: int = 300       # poll every 5 min
_DEFAULT_STALE_THRESHOLD_S: int = 600      # stale after 10 min of silence
_DEFAULT_STARTUP_GRACE_S: int = 90         # new channel gets 90 s grace
_DEFAULT_MAX_RESTARTS_PER_HOUR: int = 10   # restart budget per rolling hour
_DEFAULT_COOLDOWN_CYCLES: int = 2          # wait 2 poll cycles after restart


@dataclass
class _ChannelState:
    """Per-channel bookkeeping for the health monitor."""

    # Timestamps of recent restarts (epoch seconds); used for rolling-hour budget
    restart_timestamps: deque[float] = field(default_factory=lambda: deque(maxlen=100))
    # Poll-cycle index at which this channel was last restarted (for cooldown)
    last_restart_cycle: int = -1


class ChannelHealthMonitor:
    """Background health monitor for gateway channels.

    Parameters
    ----------
    channels:
        Reference to the server's ``channels`` dict.  The monitor reads
        ``channel._running`` and ``channel._last_event_at`` (float, set by the
        channel on every received update).  Channels that lack
        ``_last_event_at`` are skipped (backwards-compatible).
    restart_fn:
        Async callable ``restart_fn(name: str) -> None`` invoked when a
        channel is judged stale and within the restart budget.
    check_interval_s:
        How often to poll channel health (seconds).
    stale_threshold_s:
        Duration of silence (seconds, measured from ``_last_event_at``) after
        which a *running* channel is considered stale.
    startup_grace_s:
        Time (seconds) to ignore a channel after its ``_last_event_at`` was
        first set (i.e. immediately after starting).
    max_restarts_per_hour:
        Maximum number of restarts allowed per channel per rolling 60-minute
        window.  Beyond this, only a CRITICAL-level log is emitted.
    cooldown_cycles:
        Number of clean check-cycles required after a restart before the
        channel is eligible for another restart.
    """

    def __init__(
        self,
        channels: dict[str, Any],
        restart_fn: Callable[[str], Awaitable[None]],
        *,
        check_interval_s: int = _DEFAULT_CHECK_INTERVAL_S,
        stale_threshold_s: int = _DEFAULT_STALE_THRESHOLD_S,
        startup_grace_s: int = _DEFAULT_STARTUP_GRACE_S,
        max_restarts_per_hour: int = _DEFAULT_MAX_RESTARTS_PER_HOUR,
        cooldown_cycles: int = _DEFAULT_COOLDOWN_CYCLES,
    ) -> None:
        self._channels = channels
        self._restart_fn = restart_fn
        self._check_interval_s = check_interval_s
        self._stale_threshold_s = stale_threshold_s
        self._startup_grace_s = startup_grace_s
        self._max_restarts_per_hour = max_restarts_per_hour
        self._cooldown_cycles = cooldown_cycles

        self._state: defaultdict[str, _ChannelState] = defaultdict(_ChannelState)
        self._cycle: int = 0
        self._running: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Main background loop.  Call via ``asyncio.create_task(monitor.run())``."""
        self._running = True
        _log.info(
            "ChannelHealthMonitor started (interval=%ds, stale=%ds, budget=%d/hr)",
            self._check_interval_s,
            self._stale_threshold_s,
            self._max_restarts_per_hour,
        )
        try:
            while self._running:
                await asyncio.sleep(self._check_interval_s)
                if not self._running:
                    break
                await self._check_all()
                self._cycle += 1
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False
            _log.debug("ChannelHealthMonitor stopped.")

    def stop(self) -> None:
        """Signal the monitor loop to exit on the next cycle."""
        self._running = False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _check_all(self) -> None:
        now = time.monotonic()
        epoch_now = time.time()

        for name, channel in list(self._channels.items()):
            try:
                await self._check_channel(name, channel, now, epoch_now)
            except Exception as exc:  # noqa: BLE001
                _log.debug("ChannelHealthMonitor: error checking %r: %r", name, exc)

    async def _check_channel(
        self,
        name: str,
        channel: Any,
        now: float,
        epoch_now: float,
    ) -> None:
        # Only monitor channels that have been stamped with _last_event_at
        last_event_at: float | None = getattr(channel, "_last_event_at", None)
        if last_event_at is None:
            return

        is_running: bool = bool(getattr(channel, "_running", False))

        # Skip channels that are not running (stopped intentionally)
        if not is_running:
            return

        # Startup grace: skip for a short window after the channel first saw events
        idle_s = now - last_event_at
        if idle_s < self._startup_grace_s:
            return

        # Not stale yet
        if idle_s < self._stale_threshold_s:
            return

        # ── STALE DETECTED ──────────────────────────────────────────────
        state = self._state[name]

        # Cooldown guard: don't restart if we just restarted recently
        cycles_since_restart = self._cycle - state.last_restart_cycle
        if state.last_restart_cycle >= 0 and cycles_since_restart < self._cooldown_cycles:
            _log.debug(
                "ChannelHealthMonitor: %r stale but in cooldown (%d/%d cycles)",
                name,
                cycles_since_restart,
                self._cooldown_cycles,
            )
            return

        # Rolling-hour restart budget
        one_hour_ago = epoch_now - 3600.0
        recent_restarts = [t for t in state.restart_timestamps if t > one_hour_ago]
        if len(recent_restarts) >= self._max_restarts_per_hour:
            _log.critical(
                "ChannelHealthMonitor: channel %r is STALE and has exhausted its "
                "restart budget (%d restarts in the last hour) — manual intervention required. "
                "Last event was %.0f s ago.",
                name,
                self._max_restarts_per_hour,
                idle_s,
            )
            return

        # ── RESTART ─────────────────────────────────────────────────────
        _log.warning(
            "ChannelHealthMonitor: channel %r stale (last event %.0f s ago) — restarting "
            "(attempt %d/%d this hour).",
            name,
            idle_s,
            len(recent_restarts) + 1,
            self._max_restarts_per_hour,
        )
        state.restart_timestamps.append(epoch_now)
        state.last_restart_cycle = self._cycle

        try:
            await self._restart_fn(name)
        except Exception as exc:  # noqa: BLE001
            _log.error(
                "ChannelHealthMonitor: restart of channel %r failed: %r", name, exc
            )
