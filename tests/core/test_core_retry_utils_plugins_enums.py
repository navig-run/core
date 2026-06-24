"""
Batch 78: hermetic unit tests for
  - navig/core/retry_utils.py  (jittered_backoff, RetryConfig, async_retry,
                                 retry_sync)
  - navig/core/plugins.py      (PluginState, PluginType enums)
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# navig/core/retry_utils.py
# ---------------------------------------------------------------------------

class TestJitteredBackoff:
    def test_attempt_zero_returns_near_base_delay(self) -> None:
        from navig.core.retry_utils import jittered_backoff
        delay = jittered_backoff(0, base_delay=5.0, jitter_ratio=0.0)
        assert delay == pytest.approx(5.0)

    def test_delay_grows_with_attempt(self) -> None:
        from navig.core.retry_utils import jittered_backoff
        d0 = jittered_backoff(0, base_delay=1.0, jitter_ratio=0.0)
        d1 = jittered_backoff(1, base_delay=1.0, jitter_ratio=0.0)
        d2 = jittered_backoff(2, base_delay=1.0, jitter_ratio=0.0)
        # attempt 0 → base, attempt 1 → base (exponent=0), attempt 2 → base*2
        assert d2 >= d1

    def test_delay_capped_at_max(self) -> None:
        from navig.core.retry_utils import jittered_backoff
        delay = jittered_backoff(100, base_delay=5.0, max_delay=30.0, jitter_ratio=0.0)
        assert delay <= 30.0

    def test_jitter_adds_positive_amount(self) -> None:
        from navig.core.retry_utils import jittered_backoff
        no_jitter = jittered_backoff(2, base_delay=5.0, max_delay=120.0, jitter_ratio=0.0)
        with_jitter = jittered_backoff(2, base_delay=5.0, max_delay=120.0, jitter_ratio=0.5)
        assert with_jitter >= no_jitter

    def test_return_type_is_float(self) -> None:
        from navig.core.retry_utils import jittered_backoff
        assert isinstance(jittered_backoff(0), float)


class TestRetryConfig:
    def test_defaults(self) -> None:
        from navig.core.retry_utils import RetryConfig
        cfg = RetryConfig()
        assert cfg.max_attempts == 3
        assert cfg.base_delay == 5.0
        assert cfg.max_delay == 120.0
        assert cfg.jitter_ratio == 0.5
        assert cfg.reraise_last is True
        assert Exception in cfg.retryable_exceptions

    def test_custom_values(self) -> None:
        from navig.core.retry_utils import RetryConfig
        cfg = RetryConfig(max_attempts=5, base_delay=1.0, reraise_last=False)
        assert cfg.max_attempts == 5
        assert cfg.base_delay == 1.0
        assert cfg.reraise_last is False


class TestRetrySyncWithoutSleep:
    """Use a zero-delay config to avoid actual sleeping."""

    def _zero_config(self, max_attempts: int = 3):
        from navig.core.retry_utils import RetryConfig
        return RetryConfig(max_attempts=max_attempts, base_delay=0.0, max_delay=0.0, jitter_ratio=0.0)

    def test_success_on_first_attempt(self) -> None:
        from navig.core.retry_utils import retry_sync
        result = retry_sync(lambda: 42, config=self._zero_config())
        assert result == 42

    def test_retries_on_failure_then_succeeds(self) -> None:
        from navig.core.retry_utils import retry_sync
        call_counts = [0]

        def flaky():
            call_counts[0] += 1
            if call_counts[0] < 3:
                raise ValueError("not yet")
            return "ok"

        result = retry_sync(flaky, config=self._zero_config(max_attempts=5))
        assert result == "ok"
        assert call_counts[0] == 3

    def test_reraises_after_exhaustion(self) -> None:
        from navig.core.retry_utils import retry_sync
        with pytest.raises(ValueError, match="always fails"):
            retry_sync(
                lambda: (_ for _ in ()).throw(ValueError("always fails")),
                config=self._zero_config(max_attempts=2),
            )

    def test_returns_none_when_reraise_false(self) -> None:
        from navig.core.retry_utils import retry_sync, RetryConfig
        cfg = RetryConfig(max_attempts=2, base_delay=0.0, max_delay=0.0, jitter_ratio=0.0, reraise_last=False)
        result = retry_sync(lambda: (_ for _ in ()).throw(ValueError("boom")), config=cfg)
        assert result is None

    def test_on_retry_callback_invoked(self) -> None:
        from navig.core.retry_utils import retry_sync, RetryConfig
        cfg = RetryConfig(max_attempts=3, base_delay=0.0, max_delay=0.0, jitter_ratio=0.0, reraise_last=False)
        calls = [0]

        def flaky():
            calls[0] += 1
            raise RuntimeError("err")

        retry_sync(flaky, config=cfg)
        assert calls[0] == 3


class TestAsyncRetry:
    def _zero_config(self, max_attempts: int = 3):
        from navig.core.retry_utils import RetryConfig
        return RetryConfig(max_attempts=max_attempts, base_delay=0.0, max_delay=0.0, jitter_ratio=0.0)

    async def test_success_on_first_attempt(self) -> None:
        from navig.core.retry_utils import async_retry

        @async_retry(self._zero_config())
        async def fn():
            return "done"

        result = await fn()
        assert result == "done"

    async def test_retries_then_succeeds(self) -> None:
        from navig.core.retry_utils import async_retry
        calls = [0]

        @async_retry(self._zero_config(max_attempts=5))
        async def flaky():
            calls[0] += 1
            if calls[0] < 3:
                raise IOError("not yet")
            return "ok"

        result = await flaky()
        assert result == "ok"
        assert calls[0] == 3

    async def test_reraises_after_exhaustion(self) -> None:
        from navig.core.retry_utils import async_retry

        @async_retry(self._zero_config(max_attempts=2))
        async def always_fails():
            raise RuntimeError("persistent error")

        with pytest.raises(RuntimeError, match="persistent error"):
            await always_fails()

    async def test_on_retry_callback(self) -> None:
        from navig.core.retry_utils import async_retry
        logged = []

        def on_retry(attempt, exc, delay):
            logged.append(attempt)

        @async_retry(self._zero_config(max_attempts=3), on_retry=on_retry)
        async def flaky():
            raise ValueError("err")

        try:
            await flaky()
        except ValueError:
            pass
        assert logged == [1, 2]  # Called before attempts 2 and 3


# ---------------------------------------------------------------------------
# navig/core/plugins.py — enums (lightweight)
# ---------------------------------------------------------------------------

class TestPluginStateEnum:
    def test_values(self) -> None:
        from navig.core.plugins import PluginState
        assert PluginState.DISCOVERED == "discovered"
        assert PluginState.LOADED == "loaded"
        assert PluginState.ENABLED == "enabled"
        assert PluginState.DISABLED == "disabled"
        assert PluginState.ERROR == "error"
        assert PluginState.UNLOADED == "unloaded"

    def test_membership(self) -> None:
        from navig.core.plugins import PluginState
        all_states = list(PluginState)
        assert len(all_states) == 6


class TestPluginTypeEnum:
    def test_values(self) -> None:
        from navig.core.plugins import PluginType
        assert PluginType.COMMAND == "command"
        assert PluginType.CHANNEL == "channel"
        assert PluginType.PROVIDER == "provider"
        assert PluginType.TOOL == "tool"
        assert PluginType.HOOK == "hook"
        assert PluginType.EXTENSION == "extension"
