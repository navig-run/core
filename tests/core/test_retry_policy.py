"""Tests for navig.retry_policy — BackoffPolicy and helpers."""

from __future__ import annotations

import asyncio
import time

import pytest

from navig.retry_policy import (
    API_RATE_LIMIT,
    CHANNEL_RESTART,
    TELEGRAM_POLLING,
    TUNNEL_RECONNECT,
    BackoffPolicy,
    backoff_sleep,
    backoff_sleep_sync,
)


class TestBackoffPolicy:
    def test_delay_ms_attempt_zero_returns_initial(self):
        p = BackoffPolicy(initial_ms=1_000, max_ms=60_000, factor=2.0, jitter=0.0)
        # With jitter=0.0, spread is 0 so result is exactly initial
        assert p.delay_ms(0) == pytest.approx(1_000.0)

    def test_delay_ms_doubles_each_attempt(self):
        p = BackoffPolicy(initial_ms=1_000, max_ms=1_000_000, factor=2.0, jitter=0.0)
        assert p.delay_ms(0) == pytest.approx(1_000.0)
        assert p.delay_ms(1) == pytest.approx(2_000.0)
        assert p.delay_ms(2) == pytest.approx(4_000.0)
        assert p.delay_ms(3) == pytest.approx(8_000.0)

    def test_delay_ms_capped_at_max(self):
        p = BackoffPolicy(initial_ms=1_000, max_ms=5_000, factor=2.0, jitter=0.0)
        assert p.delay_ms(10) == pytest.approx(5_000.0)

    def test_delay_s_is_ms_divided_by_1000(self):
        p = BackoffPolicy(initial_ms=2_000, max_ms=60_000, factor=2.0, jitter=0.0)
        assert p.delay_s(1) == pytest.approx(p.delay_ms(1) / 1_000.0)

    def test_jitter_produces_values_within_spread(self):
        p = BackoffPolicy(initial_ms=1_000, max_ms=60_000, factor=2.0, jitter=0.1)
        for _ in range(200):
            d = p.delay_ms(0)
            assert 900.0 <= d <= 1_100.0, f"jitter out of range: {d}"

    def test_frozen_dataclass_immutable(self):
        p = BackoffPolicy()
        with pytest.raises((AttributeError, TypeError)):
            p.initial_ms = 999  # type: ignore[misc]

    def test_predefined_tunnel_policy(self):
        assert TUNNEL_RECONNECT.initial_ms == 2_000
        assert TUNNEL_RECONNECT.max_ms == 120_000

    def test_predefined_telegram_policy(self):
        assert TELEGRAM_POLLING.initial_ms == 5_000
        assert TELEGRAM_POLLING.max_ms == 300_000

    def test_predefined_api_rate_limit_policy(self):
        assert API_RATE_LIMIT.initial_ms == 1_000

    def test_predefined_channel_restart_policy(self):
        assert CHANNEL_RESTART.initial_ms == 5_000


class TestBackoffSleep:
    def test_attempt_zero_does_not_sleep(self):
        """backoff_sleep on attempt 0 should return immediately."""
        async def _run():
            t0 = time.monotonic()
            await backoff_sleep(BackoffPolicy(initial_ms=10_000), 0)
            return time.monotonic() - t0

        elapsed = asyncio.run(_run())
        assert elapsed < 0.1, f"sleep(attempt=0) took {elapsed:.3f}s — should be instant"

    def test_attempt_zero_sync_does_not_sleep(self):
        t0 = time.monotonic()
        backoff_sleep_sync(BackoffPolicy(initial_ms=10_000), 0)
        assert time.monotonic() - t0 < 0.1

    def test_async_sleep_waits_at_least_initial(self):
        p = BackoffPolicy(initial_ms=50, max_ms=1_000, factor=2.0, jitter=0.0)

        async def _run():
            t0 = time.monotonic()
            await backoff_sleep(p, 1)
            return time.monotonic() - t0

        elapsed = asyncio.run(_run())
        assert elapsed >= 0.04, f"sleep too short: {elapsed:.3f}s"

    def test_sync_sleep_waits_at_least_initial(self):
        p = BackoffPolicy(initial_ms=50, max_ms=500, factor=2.0, jitter=0.0)
        t0 = time.monotonic()
        backoff_sleep_sync(p, 1)
        assert time.monotonic() - t0 >= 0.04
