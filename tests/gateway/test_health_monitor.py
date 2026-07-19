"""Tests for navig.gateway.health_monitor — ChannelHealthMonitor."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from navig.gateway.health_monitor import ChannelHealthMonitor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_channel(
    *,
    running: bool = True,
    last_event_offset_s: float = 0.0,
    use_webhook: bool = False,
) -> MagicMock:
    """Return a mock channel with _running, _last_event_at and _use_webhook set.

    ``_use_webhook`` MUST be set explicitly — a bare MagicMock auto-creates a
    truthy attribute, which the monitor now reads to skip webhook channels.
    """
    ch = MagicMock()
    ch._running = running
    ch._last_event_at = time.monotonic() - last_event_offset_s
    ch._use_webhook = use_webhook
    return ch


def _make_monitor(channels, restart_fn=None, **kwargs) -> ChannelHealthMonitor:
    if restart_fn is None:
        restart_fn = AsyncMock()
    defaults = dict(
        check_interval_s=0,
        stale_threshold_s=60,
        startup_grace_s=10,
        max_restarts_per_hour=5,
        cooldown_cycles=1,
    )
    defaults.update(kwargs)
    return ChannelHealthMonitor(channels=channels, restart_fn=restart_fn, **defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_healthy_channel_not_restarted():
    restart = AsyncMock()
    channel = _make_channel(last_event_offset_s=10)  # only 10 s stale, threshold 60
    m = _make_monitor({"tg": channel}, restart)
    await m._check_all()
    restart.assert_not_called()


@pytest.mark.asyncio
async def test_stale_channel_restarted():
    restart = AsyncMock()
    channel = _make_channel(last_event_offset_s=120)  # 120 s > threshold 60
    m = _make_monitor({"tg": channel}, restart)
    await m._check_all()
    restart.assert_called_once_with("tg")


@pytest.mark.asyncio
async def test_stopped_channel_not_restarted():
    restart = AsyncMock()
    channel = _make_channel(running=False, last_event_offset_s=300)
    m = _make_monitor({"tg": channel}, restart)
    await m._check_all()
    restart.assert_not_called()


@pytest.mark.asyncio
async def test_channel_without_last_event_at_skipped():
    restart = AsyncMock()
    channel = MagicMock()
    channel._running = True
    # No _last_event_at attribute
    del channel._last_event_at
    m = _make_monitor({"tg": channel}, restart)
    await m._check_all()
    restart.assert_not_called()


@pytest.mark.asyncio
async def test_webhook_channel_never_restarted_on_silence():
    """A quiet webhook-mode channel must NOT be restarted.

    Regression: a webhook bot has no poll loop to refresh _last_event_at, so a
    quiet bot looks perpetually "stale" and was restarted every ~10 min, firing
    a boot greeting each time. Webhook channels are now skipped entirely.
    """
    restart = AsyncMock()
    # Way past the stale threshold, but it's a webhook channel → must be skipped.
    channel = _make_channel(last_event_offset_s=100_000, use_webhook=True)
    m = _make_monitor({"tg": channel}, restart)
    await m._check_all()
    restart.assert_not_called()


@pytest.mark.asyncio
async def test_startup_grace_skips_check():
    restart = AsyncMock()
    # Channel has an event only 5 s ago; grace is 10 s
    channel = _make_channel(last_event_offset_s=5)
    m = _make_monitor({"tg": channel}, restart, startup_grace_s=10, stale_threshold_s=1)
    await m._check_all()
    restart.assert_not_called()


@pytest.mark.asyncio
async def test_cooldown_prevents_immediate_second_restart():
    restart = AsyncMock()
    channel = _make_channel(last_event_offset_s=200)
    m = _make_monitor({"tg": channel}, restart, cooldown_cycles=2)

    # First check → restart
    await m._check_all()
    assert restart.call_count == 1
    m._cycle += 1  # simulate one more cycle

    # Second check immediately after (cycle diff == 1, cooldown needs 2)
    await m._check_all()
    assert restart.call_count == 1  # still 1 — cooldown active


@pytest.mark.asyncio
async def test_restart_allowed_after_cooldown():
    restart = AsyncMock()
    channel = _make_channel(last_event_offset_s=200)
    m = _make_monitor({"tg": channel}, restart, cooldown_cycles=1)

    await m._check_all()
    assert restart.call_count == 1
    m._cycle += 2  # advance past cooldown

    await m._check_all()
    assert restart.call_count == 2


@pytest.mark.asyncio
async def test_budget_exhaustion_prevents_restart():
    restart = AsyncMock()
    channel = _make_channel(last_event_offset_s=200)
    m = _make_monitor({"tg": channel}, restart, max_restarts_per_hour=2, cooldown_cycles=0)

    # Pre-fill the budget with two recent restarts (within the last hour)
    now = time.time()
    state = m._state["tg"]
    state.restart_timestamps.append(now - 100)
    state.restart_timestamps.append(now - 50)

    await m._check_all()

    # Budget exhausted → restart must NOT be called
    restart.assert_not_called()


@pytest.mark.asyncio
async def test_restart_fn_exception_does_not_propagate():
    async def _bad_restart(name):
        raise RuntimeError("restart exploded")

    channel = _make_channel(last_event_offset_s=200)
    m = _make_monitor({"tg": channel}, _bad_restart)
    # Should not raise
    await m._check_all()


@pytest.mark.asyncio
async def test_stop_exits_run_loop():
    m = ChannelHealthMonitor(
        channels={},
        restart_fn=AsyncMock(),
        check_interval_s=10,
    )

    async def _stop_after_start():
        await asyncio.sleep(0.05)
        m.stop()

    await asyncio.gather(
        asyncio.create_task(m.run()),
        asyncio.create_task(_stop_after_start()),
    )
    assert not m._running


@pytest.mark.asyncio
async def test_multiple_channels_independent():
    """Restarting one stale channel does not affect a healthy neighbour."""
    restart = AsyncMock()
    healthy = _make_channel(last_event_offset_s=5)
    stale = _make_channel(last_event_offset_s=200)
    m = _make_monitor({"healthy": healthy, "stale": stale}, restart)
    await m._check_all()
    restart.assert_called_once_with("stale")
