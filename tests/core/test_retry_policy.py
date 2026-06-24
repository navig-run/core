"""Tests for navig.retry_policy — BackoffPolicy, helpers, canonical policies."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# BackoffPolicy.delay_ms
# ---------------------------------------------------------------------------


class TestBackoffPolicyDelayMs:
    def test_attempt_0_near_initial(self):
        from navig.retry_policy import BackoffPolicy

        policy = BackoffPolicy(initial_ms=1_000, max_ms=60_000, factor=2.0, jitter=0.0)
        # jitter=0 → exact value
        assert policy.delay_ms(0) == pytest.approx(1_000.0)

    def test_grows_exponentially(self):
        from navig.retry_policy import BackoffPolicy

        policy = BackoffPolicy(initial_ms=1_000, max_ms=100_000, factor=2.0, jitter=0.0)
        assert policy.delay_ms(1) == pytest.approx(2_000.0)
        assert policy.delay_ms(2) == pytest.approx(4_000.0)
        assert policy.delay_ms(3) == pytest.approx(8_000.0)

    def test_caps_at_max(self):
        from navig.retry_policy import BackoffPolicy

        policy = BackoffPolicy(initial_ms=1_000, max_ms=5_000, factor=4.0, jitter=0.0)
        # attempt 4 → 1000 * 4^4 = 256_000, should be capped at 5_000
        assert policy.delay_ms(4) == pytest.approx(5_000.0)

    def test_jitter_within_range(self):
        from navig.retry_policy import BackoffPolicy

        policy = BackoffPolicy(initial_ms=1_000, max_ms=60_000, factor=2.0, jitter=0.1)
        raw = 1_000.0
        spread = raw * 0.1
        for _ in range(20):
            d = policy.delay_ms(0)
            assert raw - spread <= d <= raw + spread

    def test_jitter_zero_is_deterministic(self):
        from navig.retry_policy import BackoffPolicy

        policy = BackoffPolicy(initial_ms=500, max_ms=10_000, factor=2.0, jitter=0.0)
        vals = {policy.delay_ms(2) for _ in range(10)}
        assert len(vals) == 1  # all identical


# ---------------------------------------------------------------------------
# BackoffPolicy.delay_s
# ---------------------------------------------------------------------------


class TestBackoffPolicyDelayS:
    def test_is_ms_divided_by_1000(self):
        from navig.retry_policy import BackoffPolicy

        policy = BackoffPolicy(initial_ms=2_000, max_ms=60_000, factor=2.0, jitter=0.0)
        assert policy.delay_s(0) == pytest.approx(2.0)
        assert policy.delay_s(1) == pytest.approx(4.0)

    def test_frozen_dataclass(self):
        from navig.retry_policy import BackoffPolicy

        policy = BackoffPolicy()
        with pytest.raises((AttributeError, TypeError)):
            policy.initial_ms = 999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Canonical policies
# ---------------------------------------------------------------------------


class TestCanonicalPolicies:
    def test_tunnel_reconnect_values(self):
        from navig.retry_policy import TUNNEL_RECONNECT

        assert TUNNEL_RECONNECT.initial_ms == 2_000
        assert TUNNEL_RECONNECT.max_ms == 120_000
        assert TUNNEL_RECONNECT.factor == 2.0

    def test_telegram_polling_values(self):
        from navig.retry_policy import TELEGRAM_POLLING

        assert TELEGRAM_POLLING.initial_ms == 5_000
        assert TELEGRAM_POLLING.max_ms == 300_000

    def test_api_rate_limit_values(self):
        from navig.retry_policy import API_RATE_LIMIT

        assert API_RATE_LIMIT.initial_ms == 1_000
        assert API_RATE_LIMIT.max_ms == 64_000

    def test_channel_restart_values(self):
        from navig.retry_policy import CHANNEL_RESTART

        assert CHANNEL_RESTART.initial_ms == 5_000
        assert CHANNEL_RESTART.max_ms == 300_000


# ---------------------------------------------------------------------------
# backoff_sleep (async)
# ---------------------------------------------------------------------------


class TestBackoffSleep:
    @pytest.mark.asyncio
    async def test_attempt_0_skips_sleep(self):
        from navig.retry_policy import BackoffPolicy, backoff_sleep

        policy = BackoffPolicy(initial_ms=1_000, max_ms=10_000, factor=2.0, jitter=0.0)
        with patch("asyncio.sleep") as mock_sleep:
            await backoff_sleep(policy, 0)
        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_negative_attempt_skips_sleep(self):
        from navig.retry_policy import BackoffPolicy, backoff_sleep

        policy = BackoffPolicy()
        with patch("asyncio.sleep") as mock_sleep:
            await backoff_sleep(policy, -1)
        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_positive_attempt_sleeps(self):
        from navig.retry_policy import BackoffPolicy, backoff_sleep

        policy = BackoffPolicy(initial_ms=2_000, max_ms=60_000, factor=2.0, jitter=0.0)
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await backoff_sleep(policy, 1)
        mock_sleep.assert_called_once()
        sleep_secs = mock_sleep.call_args[0][0]
        assert sleep_secs == pytest.approx(4.0)  # 2s * 2^1 = 4s


# ---------------------------------------------------------------------------
# backoff_sleep_sync
# ---------------------------------------------------------------------------


class TestBackoffSleepSync:
    def test_attempt_0_skips_sleep(self):
        from navig.retry_policy import BackoffPolicy, backoff_sleep_sync

        policy = BackoffPolicy()
        with patch("time.sleep") as mock_sleep:
            backoff_sleep_sync(policy, 0)
        mock_sleep.assert_not_called()

    def test_positive_attempt_sleeps(self):
        from navig.retry_policy import BackoffPolicy, backoff_sleep_sync

        policy = BackoffPolicy(initial_ms=1_000, max_ms=60_000, factor=2.0, jitter=0.0)
        with patch("time.sleep") as mock_sleep:
            backoff_sleep_sync(policy, 2)
        mock_sleep.assert_called_once()
        sleep_secs = mock_sleep.call_args[0][0]
        assert sleep_secs == pytest.approx(4.0)  # 1000ms * 2^2 = 4000ms = 4s


# ---------------------------------------------------------------------------
# BackoffPolicy defaults (merged from root)
# ---------------------------------------------------------------------------

class TestBackoffPolicyDefaults:
    def test_default_initial_ms(self):
        from navig.retry_policy import BackoffPolicy
        p = BackoffPolicy()
        assert p.initial_ms == 5_000

    def test_default_max_ms(self):
        from navig.retry_policy import BackoffPolicy
        p = BackoffPolicy()
        assert p.max_ms == 300_000

    def test_default_factor(self):
        from navig.retry_policy import BackoffPolicy
        p = BackoffPolicy()
        assert p.factor == 2.0

    def test_default_jitter(self):
        from navig.retry_policy import BackoffPolicy
        p = BackoffPolicy()
        assert p.jitter == 0.1

    def test_is_frozen(self):
        import pytest
        from navig.retry_policy import BackoffPolicy
        p = BackoffPolicy()
        with pytest.raises((AttributeError, TypeError)):
            p.initial_ms = 999


# ---------------------------------------------------------------------------
# delay_ms (merged from root)
# ---------------------------------------------------------------------------

class TestDelayMs:
    def test_attempt_zero_returns_initial(self):
        from navig.retry_policy import BackoffPolicy
        p = BackoffPolicy(initial_ms=1000, max_ms=60_000, factor=2.0, jitter=0.0)
        assert p.delay_ms(0) == 1000.0

    def test_attempt_one_doubles(self):
        from navig.retry_policy import BackoffPolicy
        p = BackoffPolicy(initial_ms=1000, max_ms=60_000, factor=2.0, jitter=0.0)
        assert p.delay_ms(1) == 2000.0

    def test_attempt_two_quadruples(self):
        from navig.retry_policy import BackoffPolicy
        p = BackoffPolicy(initial_ms=1000, max_ms=60_000, factor=2.0, jitter=0.0)
        assert p.delay_ms(2) == 4000.0

    def test_delay_capped_at_max_ms(self):
        from navig.retry_policy import BackoffPolicy
        p = BackoffPolicy(initial_ms=1000, max_ms=2000, factor=2.0, jitter=0.0)
        assert p.delay_ms(5) == 2000.0

    def test_jitter_adds_spread(self):
        from navig.retry_policy import BackoffPolicy
        p = BackoffPolicy(initial_ms=1000, max_ms=60_000, factor=1.0, jitter=0.2)
        delays = [p.delay_ms(0) for _ in range(50)]
        assert any(d != 1000.0 for d in delays)
        assert all(800.0 <= d <= 1200.0 for d in delays)

    def test_jitter_zero_returns_exact(self):
        from navig.retry_policy import BackoffPolicy
        p = BackoffPolicy(initial_ms=1000, max_ms=60_000, factor=2.0, jitter=0.0)
        assert all(p.delay_ms(0) == 1000.0 for _ in range(10))

    def test_returns_float(self):
        from navig.retry_policy import BackoffPolicy
        p = BackoffPolicy(initial_ms=1000, max_ms=60_000, factor=2.0, jitter=0.0)
        assert isinstance(p.delay_ms(0), float)

    def test_large_attempt_still_capped(self):
        from navig.retry_policy import BackoffPolicy
        p = BackoffPolicy(initial_ms=100, max_ms=500, factor=2.0, jitter=0.0)
        assert p.delay_ms(100) == 500.0

    def test_custom_factor(self):
        from navig.retry_policy import BackoffPolicy
        p = BackoffPolicy(initial_ms=1000, max_ms=60_000, factor=3.0, jitter=0.0)
        assert p.delay_ms(2) == 9000.0


# ---------------------------------------------------------------------------
# delay_s (merged from root)
# ---------------------------------------------------------------------------

class TestDelayS:
    def test_delay_s_is_delay_ms_divided_by_1000(self):
        import math
        from navig.retry_policy import BackoffPolicy
        p = BackoffPolicy(initial_ms=2000, max_ms=60_000, factor=2.0, jitter=0.0)
        assert math.isclose(p.delay_s(0), p.delay_ms(0) / 1000.0)

    def test_delay_s_attempt_zero(self):
        from navig.retry_policy import BackoffPolicy
        p = BackoffPolicy(initial_ms=2000, max_ms=60_000, factor=2.0, jitter=0.0)
        assert p.delay_s(0) == 2.0

    def test_delay_s_capped_in_seconds(self):
        from navig.retry_policy import BackoffPolicy
        p = BackoffPolicy(initial_ms=1000, max_ms=5000, factor=2.0, jitter=0.0)
        assert p.delay_s(10) == 5.0

    def test_delay_s_returns_float(self):
        from navig.retry_policy import BackoffPolicy
        p = BackoffPolicy(initial_ms=1000, max_ms=60_000, factor=2.0, jitter=0.0)
        assert isinstance(p.delay_s(0), float)
