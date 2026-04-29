"""
Tests for navig/retry_policy.py — BackoffPolicy and helpers.
Batch 93.
"""
from __future__ import annotations

import asyncio
import math

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


# ---------------------------------------------------------------------------
# BackoffPolicy dataclass — defaults
# ---------------------------------------------------------------------------

class TestBackoffPolicyDefaults:
    def test_default_initial_ms(self):
        p = BackoffPolicy()
        assert p.initial_ms == 5_000

    def test_default_max_ms(self):
        p = BackoffPolicy()
        assert p.max_ms == 300_000

    def test_default_factor(self):
        p = BackoffPolicy()
        assert p.factor == 2.0

    def test_default_jitter(self):
        p = BackoffPolicy()
        assert p.jitter == 0.1

    def test_is_frozen(self):
        p = BackoffPolicy()
        with pytest.raises((AttributeError, TypeError)):
            p.initial_ms = 999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# BackoffPolicy — delay_ms
# ---------------------------------------------------------------------------

class TestDelayMs:
    def test_attempt_zero_returns_initial(self):
        p = BackoffPolicy(initial_ms=1000, max_ms=60_000, factor=2.0, jitter=0.0)
        assert p.delay_ms(0) == 1000.0

    def test_attempt_one_doubles(self):
        p = BackoffPolicy(initial_ms=1000, max_ms=60_000, factor=2.0, jitter=0.0)
        assert p.delay_ms(1) == 2000.0

    def test_attempt_two_quadruples(self):
        p = BackoffPolicy(initial_ms=1000, max_ms=60_000, factor=2.0, jitter=0.0)
        assert p.delay_ms(2) == 4000.0

    def test_delay_capped_at_max_ms(self):
        p = BackoffPolicy(initial_ms=1000, max_ms=2000, factor=2.0, jitter=0.0)
        # attempt=5 would be 32_000 without cap
        assert p.delay_ms(5) == 2000.0

    def test_jitter_adds_spread(self):
        p = BackoffPolicy(initial_ms=1000, max_ms=60_000, factor=1.0, jitter=0.2)
        # With 20% jitter, delay should be in [800, 1200]
        delays = [p.delay_ms(0) for _ in range(50)]
        assert any(d != 1000.0 for d in delays)
        assert all(800.0 <= d <= 1200.0 for d in delays)

    def test_jitter_zero_returns_exact(self):
        p = BackoffPolicy(initial_ms=1000, max_ms=60_000, factor=2.0, jitter=0.0)
        # No jitter — always exact
        assert all(p.delay_ms(0) == 1000.0 for _ in range(10))

    def test_returns_float(self):
        p = BackoffPolicy(initial_ms=1000, max_ms=60_000, factor=2.0, jitter=0.0)
        assert isinstance(p.delay_ms(0), float)

    def test_large_attempt_still_capped(self):
        p = BackoffPolicy(initial_ms=100, max_ms=500, factor=2.0, jitter=0.0)
        assert p.delay_ms(100) == 500.0

    def test_custom_factor(self):
        p = BackoffPolicy(initial_ms=1000, max_ms=60_000, factor=3.0, jitter=0.0)
        assert p.delay_ms(2) == 9000.0  # 1000 * 3^2 = 9000


# ---------------------------------------------------------------------------
# BackoffPolicy — delay_s
# ---------------------------------------------------------------------------

class TestDelayS:
    def test_delay_s_is_delay_ms_divided_by_1000(self):
        p = BackoffPolicy(initial_ms=2000, max_ms=60_000, factor=2.0, jitter=0.0)
        assert math.isclose(p.delay_s(0), p.delay_ms(0) / 1000.0)

    def test_delay_s_attempt_zero(self):
        p = BackoffPolicy(initial_ms=2000, max_ms=60_000, factor=2.0, jitter=0.0)
        assert p.delay_s(0) == 2.0

    def test_delay_s_capped_in_seconds(self):
        p = BackoffPolicy(initial_ms=1000, max_ms=5000, factor=2.0, jitter=0.0)
        assert p.delay_s(10) == 5.0

    def test_delay_s_returns_float(self):
        p = BackoffPolicy(initial_ms=1000, max_ms=60_000, factor=2.0, jitter=0.0)
        assert isinstance(p.delay_s(0), float)


# ---------------------------------------------------------------------------
# Pre-defined canonical policies
# ---------------------------------------------------------------------------

class TestCanonicalPolicies:
    def test_tunnel_reconnect_is_backoff_policy(self):
        assert isinstance(TUNNEL_RECONNECT, BackoffPolicy)

    def test_tunnel_reconnect_initial_ms(self):
        assert TUNNEL_RECONNECT.initial_ms == 2_000

    def test_tunnel_reconnect_max_ms(self):
        assert TUNNEL_RECONNECT.max_ms == 120_000

    def test_telegram_polling_is_backoff_policy(self):
        assert isinstance(TELEGRAM_POLLING, BackoffPolicy)

    def test_telegram_polling_initial_ms(self):
        assert TELEGRAM_POLLING.initial_ms == 5_000

    def test_telegram_polling_max_ms(self):
        assert TELEGRAM_POLLING.max_ms == 300_000

    def test_api_rate_limit_is_backoff_policy(self):
        assert isinstance(API_RATE_LIMIT, BackoffPolicy)

    def test_api_rate_limit_initial_ms(self):
        assert API_RATE_LIMIT.initial_ms == 1_000

    def test_api_rate_limit_max_ms(self):
        assert API_RATE_LIMIT.max_ms == 64_000

    def test_channel_restart_is_backoff_policy(self):
        assert isinstance(CHANNEL_RESTART, BackoffPolicy)

    def test_channel_restart_initial_ms(self):
        assert CHANNEL_RESTART.initial_ms == 5_000

    def test_channel_restart_max_ms(self):
        assert CHANNEL_RESTART.max_ms == 300_000

    def test_all_use_factor_2(self):
        for p in [TUNNEL_RECONNECT, TELEGRAM_POLLING, API_RATE_LIMIT, CHANNEL_RESTART]:
            assert p.factor == 2.0

    def test_all_have_positive_jitter(self):
        for p in [TUNNEL_RECONNECT, TELEGRAM_POLLING, API_RATE_LIMIT, CHANNEL_RESTART]:
            assert p.jitter > 0


# ---------------------------------------------------------------------------
# backoff_sleep (async)
# ---------------------------------------------------------------------------

class TestBackoffSleep:
    async def test_attempt_zero_skips_sleep(self, monkeypatch):
        slept = []
        async def fake_sleep(secs):
            slept.append(secs)
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        p = BackoffPolicy(initial_ms=1000, max_ms=60_000, factor=2.0, jitter=0.0)
        await backoff_sleep(p, 0)
        assert slept == []

    async def test_negative_attempt_skips_sleep(self, monkeypatch):
        slept = []
        async def fake_sleep(secs):
            slept.append(secs)
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        p = BackoffPolicy(initial_ms=1000, max_ms=60_000, factor=2.0, jitter=0.0)
        await backoff_sleep(p, -1)
        assert slept == []

    async def test_attempt_one_sleeps(self, monkeypatch):
        slept = []
        async def fake_sleep(secs):
            slept.append(secs)
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        p = BackoffPolicy(initial_ms=2000, max_ms=60_000, factor=2.0, jitter=0.0)
        await backoff_sleep(p, 1)
        assert len(slept) == 1
        assert slept[0] == pytest.approx(4.0)  # 2000ms * 2^1 / 1000 = 4.0s

    async def test_sleep_duration_positive(self, monkeypatch):
        slept = []
        async def fake_sleep(secs):
            slept.append(secs)
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        p = BackoffPolicy(initial_ms=500, max_ms=60_000, factor=2.0, jitter=0.0)
        await backoff_sleep(p, 2)
        assert slept[0] > 0


# ---------------------------------------------------------------------------
# backoff_sleep_sync
# ---------------------------------------------------------------------------

class TestBackoffSleepSync:
    def test_attempt_zero_skips(self, monkeypatch):
        slept = []
        monkeypatch.setattr("navig.retry_policy.time.sleep", lambda s: slept.append(s))
        p = BackoffPolicy(initial_ms=1000, max_ms=60_000, factor=2.0, jitter=0.0)
        backoff_sleep_sync(p, 0)
        assert slept == []

    def test_negative_attempt_skips(self, monkeypatch):
        slept = []
        monkeypatch.setattr("navig.retry_policy.time.sleep", lambda s: slept.append(s))
        p = BackoffPolicy(initial_ms=1000, max_ms=60_000, factor=2.0, jitter=0.0)
        backoff_sleep_sync(p, -5)
        assert slept == []

    def test_attempt_one_sleeps(self, monkeypatch):
        slept = []
        monkeypatch.setattr("navig.retry_policy.time.sleep", lambda s: slept.append(s))
        p = BackoffPolicy(initial_ms=2000, max_ms=60_000, factor=2.0, jitter=0.0)
        backoff_sleep_sync(p, 1)
        assert len(slept) == 1
        assert slept[0] == pytest.approx(4.0)

    def test_sleep_called_once_per_call(self, monkeypatch):
        slept = []
        monkeypatch.setattr("navig.retry_policy.time.sleep", lambda s: slept.append(s))
        p = BackoffPolicy(initial_ms=100, max_ms=60_000, factor=2.0, jitter=0.0)
        backoff_sleep_sync(p, 2)
        assert len(slept) == 1
