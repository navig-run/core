"""Tests for navig.agent.speculative — Speculative Execution Engine (FC1)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import Counter
from unittest.mock import MagicMock, patch

import pytest

from navig.agent.speculative import (
    READ_ONLY_TOOLS,
    CachedResult,
    Prediction,
    PredictionEngine,
    SpeculativeCache,
    SpeculativeExecutor,
    ToolCallRecord,
    get_speculative_executor,
    get_speculative_runtime_snapshot,
    reset_speculative_executor,
)

# ─────────────────────────────────────────────────────────────
# ToolCallRecord
# ─────────────────────────────────────────────────────────────


class TestToolCallRecord:
    def test_fields(self):
        rec = ToolCallRecord(tool="read_file", args={"path": "/a"}, timestamp=1.0)
        assert rec.tool == "read_file"
        assert rec.args == {"path": "/a"}
        assert rec.timestamp == 1.0


# ─────────────────────────────────────────────────────────────
# Prediction
# ─────────────────────────────────────────────────────────────


class TestPrediction:
    def test_fields(self):
        p = Prediction(tool="grep_search", args={"q": "def"}, confidence=0.8)
        assert p.tool == "grep_search"
        assert p.confidence == 0.8


# ─────────────────────────────────────────────────────────────
# READ_ONLY_TOOLS classification
# ─────────────────────────────────────────────────────────────


class TestReadOnlyTools:
    def test_core_read_tools_included(self):
        for tool in ("read_file", "grep_search", "list_dir", "get_errors", "file_search"):
            assert tool in READ_ONLY_TOOLS, f"{tool} should be read-only"

    def test_write_tools_excluded(self):
        for tool in ("bash_exec", "write_file", "create_file", "replace_string_in_file"):
            assert tool not in READ_ONLY_TOOLS, f"{tool} must NOT be read-only"

    def test_navig_read_tools_included(self):
        for tool in ("navig_file_show", "navig_db_list", "navig_host_show"):
            assert tool in READ_ONLY_TOOLS

    def test_lsp_tools_included(self):
        for tool in ("lsp_diagnostics", "lsp_definition", "lsp_references", "lsp_symbols"):
            assert tool in READ_ONLY_TOOLS


# ─────────────────────────────────────────────────────────────
# PredictionEngine
# ─────────────────────────────────────────────────────────────


class TestPredictionEngine:
    def test_no_predictions_below_min_history(self):
        """Need at least 3 records before predicting."""
        eng = PredictionEngine()
        eng.record("read_file", {"path": "/a"})
        eng.record("grep_search", {"query": "x"})
        assert eng.predict("grep_search") == []

    def test_bigram_prediction_basic(self):
        """After read_file → grep_search repeated 3×, predict grep_search after read_file."""
        eng = PredictionEngine()
        for _ in range(3):
            eng.record("read_file", {"path": "/a"})
            eng.record("grep_search", {"query": "x"})

        preds = eng.predict("read_file")
        assert len(preds) >= 1
        assert preds[0].tool == "grep_search"
        assert preds[0].confidence > 0.3

    def test_write_tools_never_predicted(self):
        """Even if write tool follows frequently, it's filtered out."""
        eng = PredictionEngine()
        for _ in range(5):
            eng.record("read_file", {"path": "/a"})
            eng.record("bash_exec", {"command": "ls"})  # write tool

        preds = eng.predict("read_file")
        tool_names = {p.tool for p in preds}
        assert "bash_exec" not in tool_names

    def test_arg_prediction_uses_most_recent(self):
        """Predicted args should match the most recent invocation."""
        eng = PredictionEngine()
        eng.record("read_file", {"path": "/old"})
        eng.record("grep_search", {"query": "old_pattern"})
        eng.record("read_file", {"path": "/new"})
        eng.record("grep_search", {"query": "new_pattern"})
        eng.record("read_file", {"path": "/newer"})
        eng.record("grep_search", {"query": "newest"})

        preds = eng.predict("read_file")
        assert len(preds) >= 1
        assert preds[0].args["query"] == "newest"

    def test_max_predictions_capped(self):
        """No more than MAX_PREDICTIONS returned."""
        eng = PredictionEngine()
        eng.MAX_PREDICTIONS = 2
        # Create patterns: read_file → grep_search, read_file → list_dir
        for _ in range(4):
            eng.record("read_file", {"path": "/a"})
            eng.record("grep_search", {"query": "x"})
        for _ in range(4):
            eng.record("read_file", {"path": "/a"})
            eng.record("list_dir", {"path": "/b"})

        preds = eng.predict("read_file")
        assert len(preds) <= 2

    def test_low_confidence_filtered(self):
        """Predictions below MIN_CONFIDENCE are excluded."""
        eng = PredictionEngine()
        eng.MIN_CONFIDENCE = 0.5
        # read_file → grep_search 2/10, read_file → list_dir 8/10
        for _ in range(2):
            eng.record("read_file", {"path": "/a"})
            eng.record("grep_search", {"query": "x"})
        for _ in range(8):
            eng.record("read_file", {"path": "/a"})
            eng.record("list_dir", {"path": "/b"})

        preds = eng.predict("read_file")
        # grep_search has 2/10 = 0.2 < 0.5, should be excluded
        tools = {p.tool for p in preds}
        assert "grep_search" not in tools
        assert "list_dir" in tools

    def test_clear_resets_state(self):
        eng = PredictionEngine()
        for _ in range(5):
            eng.record("read_file", {"path": "/a"})
            eng.record("grep_search", {"query": "x"})
        assert eng.history_len > 0
        eng.clear()
        assert eng.history_len == 0
        assert eng.predict("read_file") == []

    def test_history_len_property(self):
        eng = PredictionEngine()
        assert eng.history_len == 0
        eng.record("read_file", {})
        assert eng.history_len == 1

    def test_unknown_tool_no_predictions(self):
        eng = PredictionEngine()
        for _ in range(3):
            eng.record("read_file", {"path": "/a"})
            eng.record("grep_search", {"query": "x"})
        assert eng.predict("totally_unknown_tool") == []

    def test_predict_args_returns_none_for_unseen_tool(self):
        eng = PredictionEngine()
        assert eng._predict_args("never_called") is None

    def test_env_overrides_prediction_knobs(self, monkeypatch):
        monkeypatch.setenv("NAVIG_SPEC_MAX_HISTORY", "40")
        monkeypatch.setenv("NAVIG_SPEC_MIN_CONFIDENCE", "0.7")
        monkeypatch.setenv("NAVIG_SPEC_MAX_PREDICTIONS", "4")

        eng = PredictionEngine()
        assert eng.MAX_HISTORY == 40
        assert eng.MIN_CONFIDENCE == pytest.approx(0.7)
        assert eng.MAX_PREDICTIONS == 4
        assert eng._history.maxlen == 40

    def test_invalid_env_prediction_knobs_fall_back_to_defaults(self, monkeypatch):
        monkeypatch.setenv("NAVIG_SPEC_MAX_HISTORY", "abc")
        monkeypatch.setenv("NAVIG_SPEC_MIN_CONFIDENCE", "1.5")
        monkeypatch.setenv("NAVIG_SPEC_MAX_PREDICTIONS", "0")

        eng = PredictionEngine()
        assert eng.MAX_HISTORY == 20
        assert eng.MIN_CONFIDENCE == pytest.approx(0.3)
        assert eng.MAX_PREDICTIONS == 2


# ─────────────────────────────────────────────────────────────
# CachedResult
# ─────────────────────────────────────────────────────────────


class TestCachedResult:
    def test_not_expired_within_ttl(self):
        cr = CachedResult(
            tool="read_file",
            args_hash="abc",
            result="data",
            created_at=time.time(),
            ttl=60.0,
        )
        assert not cr.expired

    def test_expired_after_ttl(self):
        cr = CachedResult(
            tool="read_file",
            args_hash="abc",
            result="data",
            created_at=time.time() - 120,
            ttl=60.0,
        )
        assert cr.expired


# ─────────────────────────────────────────────────────────────
# SpeculativeCache
# ─────────────────────────────────────────────────────────────


class TestSpeculativeCache:
    def test_put_and_get(self):
        cache = SpeculativeCache()
        cache.put("read_file", {"path": "/a"}, "file contents")
        result = cache.get("read_file", {"path": "/a"})
        assert result == "file contents"

    def test_miss_returns_none(self):
        cache = SpeculativeCache()
        assert cache.get("read_file", {"path": "/z"}) is None

    def test_expired_entry_returns_none(self):
        cache = SpeculativeCache()
        cache.put("read_file", {"path": "/a"}, "data", ttl=0.001)
        time.sleep(0.01)
        assert cache.get("read_file", {"path": "/a"}) is None

    def test_hit_miss_counters(self):
        cache = SpeculativeCache()
        cache.put("read_file", {"path": "/a"}, "data")
        cache.get("read_file", {"path": "/a"})  # hit
        cache.get("read_file", {"path": "/b"})  # miss
        assert cache._hits == 1
        assert cache._misses == 1
        assert cache.hit_rate == pytest.approx(0.5)

    def test_hit_rate_zero_when_empty(self):
        cache = SpeculativeCache()
        assert cache.hit_rate == 0.0

    def test_stats_dict(self):
        cache = SpeculativeCache()
        cache.put("read_file", {"path": "/a"}, "data")
        cache.get("read_file", {"path": "/a"})
        stats = cache.stats
        assert "hits" in stats
        assert "misses" in stats
        assert "hit_rate" in stats
        assert "entries" in stats
        assert stats["entries"] == 1

    def test_eviction_when_over_limit(self):
        cache = SpeculativeCache(max_entries=3)
        for i in range(5):
            cache.put("read_file", {"path": f"/{i}"}, f"data_{i}")
        assert cache.size <= 3

    def test_invalidate(self):
        cache = SpeculativeCache()
        cache.put("read_file", {"path": "/a"}, "data")
        assert cache.invalidate("read_file", {"path": "/a"})
        assert cache.get("read_file", {"path": "/a"}) is None

    def test_invalidate_missing(self):
        cache = SpeculativeCache()
        assert not cache.invalidate("nope", {})

    def test_clear(self):
        cache = SpeculativeCache()
        cache.put("read_file", {"path": "/a"}, "data")
        cache.clear()
        assert cache.size == 0

    def test_key_deterministic(self):
        """Same tool + args → same key."""
        k1 = SpeculativeCache._key("read_file", {"path": "/a", "lines": 10})
        k2 = SpeculativeCache._key("read_file", {"lines": 10, "path": "/a"})
        assert k1 == k2  # sort_keys ensures order-independence


# ─────────────────────────────────────────────────────────────
# SpeculativeExecutor
# ─────────────────────────────────────────────────────────────


class TestSpeculativeExecutor:
    def test_cache_hit(self):
        """When result is cached, dispatch_fn is NOT called."""
        dispatch = MagicMock(return_value="fresh_data")
        exec_ = SpeculativeExecutor(dispatch)
        # Warm the cache manually.
        exec_.cache.put("read_file", {"path": "/a"}, "cached_data")
        # Record enough history so prediction.record() works.
        exec_.prediction.record("grep_search", {"q": "x"})
        exec_.prediction.record("read_file", {"path": "/a"})
        exec_.prediction.record("grep_search", {"q": "x"})

        result = exec_.execute("read_file", {"path": "/a"})
        assert result == "cached_data"
        dispatch.assert_not_called()

    def test_cache_miss_calls_dispatch(self):
        dispatch = MagicMock(return_value="live_data")
        exec_ = SpeculativeExecutor(dispatch, config={"enabled": False})
        result = exec_.execute("read_file", {"path": "/a"})
        assert result == "live_data"
        dispatch.assert_called_once_with("read_file", {"path": "/a"})

    def test_records_every_call(self):
        dispatch = MagicMock(return_value="data")
        exec_ = SpeculativeExecutor(dispatch, config={"enabled": False})
        exec_.execute("read_file", {"path": "/a"})
        exec_.execute("grep_search", {"query": "x"})
        assert exec_.prediction.history_len == 2

    def test_disabled_does_not_speculate(self):
        dispatch = MagicMock(return_value="data")
        exec_ = SpeculativeExecutor(dispatch, config={"enabled": False})
        assert not exec_._should_speculate()

    def test_should_speculate_with_low_samples(self):
        """Speculation enabled even without much data (< 5 min data points)."""
        dispatch = MagicMock(return_value="data")
        exec_ = SpeculativeExecutor(dispatch, config={"enabled": True})
        assert exec_._should_speculate()

    def test_auto_disable_on_low_hit_rate(self):
        """Once many misses accumulate, speculation should stop."""
        dispatch = MagicMock(return_value="data")
        exec_ = SpeculativeExecutor(dispatch, config={"enabled": True, "min_hit_rate": 0.5})
        # Simulate many misses.
        exec_.cache._hits = 0
        exec_.cache._misses = 20
        assert not exec_._should_speculate()

    def test_stats_property(self):
        dispatch = MagicMock(return_value="data")
        exec_ = SpeculativeExecutor(dispatch)
        stats = exec_.stats
        assert "enabled" in stats
        assert "cache" in stats
        assert "predictions_tracked" in stats
        assert "active_speculations" in stats
        assert "speculating" in stats

    def test_enabled_property(self):
        exec_ = SpeculativeExecutor(lambda t, a: "", config={"enabled": True})
        assert exec_.enabled
        exec2 = SpeculativeExecutor(lambda t, a: "", config={"enabled": False})
        assert not exec2.enabled

    def test_env_overrides_executor_and_cache_tuning(self, monkeypatch):
        monkeypatch.setenv("NAVIG_SPEC_MIN_HIT_RATE", "0.55")
        monkeypatch.setenv("NAVIG_SPEC_TIMEOUT_SEC", "3.5")
        monkeypatch.setenv("NAVIG_SPEC_CACHE_MAX_ENTRIES", "77")
        monkeypatch.setenv("NAVIG_SPEC_CACHE_TTL_SEC", "222")

        exec_ = SpeculativeExecutor(
            lambda t, a: "ok",
            config={"min_hit_rate": 0.9, "cache_max_entries": 11, "cache_ttl": 20},
        )
        assert exec_._min_hit_rate == pytest.approx(0.55)
        assert exec_.SPECULATION_TIMEOUT == pytest.approx(3.5)
        assert exec_.cache._max_entries == 77
        assert exec_.cache.DEFAULT_TTL == pytest.approx(222.0)


# ─────────────────────────────────────────────────────────────
# SpeculativeExecutor — async speculation
# ─────────────────────────────────────────────────────────────


class TestSpeculativeAsync:
    @pytest.mark.asyncio
    async def test_speculative_run_caches_result(self):
        """_speculative_run puts result into cache on success."""

        def fake_dispatch(tool: str, args: dict) -> str:
            return f"result_for_{tool}"

        exec_ = SpeculativeExecutor(fake_dispatch)
        pred = Prediction(tool="read_file", args={"path": "/test"}, confidence=0.9)
        await exec_._speculative_run(pred)
        assert exec_.cache.get("read_file", {"path": "/test"}) == "result_for_read_file"

    @pytest.mark.asyncio
    async def test_speculative_run_timeout_handled(self):
        """Timeout during speculation is handled gracefully."""
        import asyncio

        def slow_dispatch(tool: str, args: dict) -> str:
            import time

            time.sleep(10)  # simulate very slow tool
            return "data"

        exec_ = SpeculativeExecutor(slow_dispatch)
        exec_.SPECULATION_TIMEOUT = 0.1  # very short
        pred = Prediction(tool="read_file", args={"path": "/slow"}, confidence=0.9)
        await exec_._speculative_run(pred)
        # Should NOT be cached due to timeout.
        assert exec_.cache.get("read_file", {"path": "/slow"}) is None

    @pytest.mark.asyncio
    async def test_speculative_run_error_handled(self):
        """Exception during speculation is swallowed."""

        def failing_dispatch(tool: str, args: dict) -> str:
            raise RuntimeError("boom")

        exec_ = SpeculativeExecutor(failing_dispatch)
        pred = Prediction(tool="read_file", args={"path": "/err"}, confidence=0.9)
        await exec_._speculative_run(pred)
        assert exec_.cache.get("read_file", {"path": "/err"}) is None

    @pytest.mark.asyncio
    async def test_cancel_speculations(self):
        """cancel_speculations clears all tasks."""

        def fake_dispatch(tool: str, args: dict) -> str:
            return "data"

        exec_ = SpeculativeExecutor(fake_dispatch)
        # Create a dummy task.
        loop = asyncio.get_running_loop()

        async def long_task():
            await asyncio.sleep(100)

        task = loop.create_task(long_task())
        exec_._speculation_tasks.append(task)
        await exec_.cancel_speculations()
        assert task.cancelled() or task.done()
        assert len(exec_._speculation_tasks) == 0


# ─────────────────────────────────────────────────────────────
# Launch speculation integration
# ─────────────────────────────────────────────────────────────


class TestLaunchSpeculation:
    @pytest.mark.asyncio
    async def test_launch_speculation_creates_tasks(self):
        """After enough history, _launch_speculation should create tasks."""

        def fake_dispatch(tool: str, args: dict) -> str:
            return f"result_{tool}"

        exec_ = SpeculativeExecutor(fake_dispatch)
        # Build a pattern: read_file → grep_search (repeated).
        for _ in range(4):
            exec_.prediction.record("read_file", {"path": "/a"})
            exec_.prediction.record("grep_search", {"query": "x"})

        # Now trigger speculation after a read_file invocation.
        exec_._launch_speculation("read_file")

        # Give tasks a chance to complete.
        await asyncio.sleep(0.2)
        # The predicted grep_search result should be cached.
        result = exec_.cache.get("grep_search", {"query": "x"})
        assert result is not None

    @pytest.mark.asyncio
    async def test_no_speculation_without_loop(self):
        """_launch_speculation is a no-op outside of an event loop."""

        # This test simply verifies no exception is raised.
        def fake_dispatch(tool: str, args: dict) -> str:
            return "data"

        exec_ = SpeculativeExecutor(fake_dispatch)
        for _ in range(4):
            exec_.prediction.record("read_file", {"path": "/a"})
            exec_.prediction.record("grep_search", {"query": "x"})

        # Within asyncio, _launch_speculation should work fine.
        exec_._launch_speculation("read_file")
        await asyncio.sleep(0.1)


# ─────────────────────────────────────────────────────────────
# get_speculative_executor / reset
# ─────────────────────────────────────────────────────────────


class TestGetSpeculativeExecutor:
    def setup_method(self):
        reset_speculative_executor()

    def teardown_method(self):
        reset_speculative_executor()

    def test_returns_executor_when_enabled(self):
        with patch("navig.config.get_config_manager") as mock_cm:
            mock_cm.return_value.global_config = {"agent": {"speculative": {"enabled": True}}}
            dispatch = MagicMock()
            exec_ = get_speculative_executor(dispatch)
            assert exec_ is not None
            assert exec_.enabled

    def test_returns_none_when_disabled(self):
        with patch("navig.config.get_config_manager") as mock_cm:
            mock_cm.return_value.global_config = {"agent": {"speculative": {"enabled": False}}}
            exec_ = get_speculative_executor(MagicMock())
            assert exec_ is None

    def test_returns_cached_singleton(self):
        with patch("navig.config.get_config_manager") as mock_cm:
            mock_cm.return_value.global_config = {"agent": {"speculative": {"enabled": True}}}
            dispatch = MagicMock()
            exec1 = get_speculative_executor(dispatch)
            exec2 = get_speculative_executor(dispatch)
            assert exec1 is exec2

    def test_reset_clears_singleton(self):
        with patch("navig.config.get_config_manager") as mock_cm:
            mock_cm.return_value.global_config = {"agent": {"speculative": {"enabled": True}}}
            dispatch = MagicMock()
            exec1 = get_speculative_executor(dispatch)
            reset_speculative_executor()
            exec2 = get_speculative_executor(dispatch)
            assert exec1 is not exec2

    def test_fallback_to_registry_dispatch(self):
        """When no dispatch_fn given, uses _AGENT_REGISTRY.dispatch."""
        with (
            patch("navig.config.get_config_manager") as mock_cm,
            patch("navig.agent.agent_tool_registry._AGENT_REGISTRY") as mock_reg,
        ):
            mock_cm.return_value.global_config = {"agent": {"speculative": {"enabled": True}}}
            mock_reg.dispatch = MagicMock()
            exec_ = get_speculative_executor()
            assert exec_ is not None
            assert exec_._dispatch_fn is mock_reg.dispatch

    def test_config_error_still_returns_executor(self):
        """ConfigManager import failure → default config → enabled."""
        with patch("navig.config.get_config_manager", side_effect=RuntimeError):
            dispatch = MagicMock()
            exec_ = get_speculative_executor(dispatch)
            assert exec_ is not None

    def test_custom_config_applied(self):
        with patch("navig.config.get_config_manager") as mock_cm:
            mock_cm.return_value.global_config = {
                "agent": {
                    "speculative": {
                        "enabled": True,
                        "cache_max_entries": 25,
                        "min_hit_rate": 0.3,
                        "cache_ttl": 120.0,
                    }
                }
            }
            dispatch = MagicMock()
            exec_ = get_speculative_executor(dispatch)
            assert exec_.cache._max_entries == 25
            assert exec_._min_hit_rate == 0.3


class TestSpeculativeRuntimeSnapshot:
    def setup_method(self):
        reset_speculative_executor()

    def teardown_method(self):
        reset_speculative_executor()

    def test_snapshot_without_live_executor(self):
        snap = get_speculative_runtime_snapshot()
        assert "enabled" in snap
        assert snap["has_live_executor"] is False
        assert "effective" in snap
        assert snap["live"] is None

    def test_snapshot_with_live_executor(self):
        with patch("navig.config.get_config_manager") as mock_cm:
            mock_cm.return_value.global_config = {"agent": {"speculative": {"enabled": True}}}
            dispatch = MagicMock(return_value="ok")
            exec_ = get_speculative_executor(dispatch)
            assert exec_ is not None

        snap = get_speculative_runtime_snapshot()
        assert snap["has_live_executor"] is True
        assert isinstance(snap["live"], dict)
        assert "cache" in snap["live"]

    def test_snapshot_env_override_effective_values(self, monkeypatch):
        monkeypatch.setenv("NAVIG_SPEC_CACHE_TTL_SEC", "123")
        monkeypatch.setenv("NAVIG_SPEC_MAX_HISTORY", "44")
        monkeypatch.setenv("NAVIG_SPEC_TIMEOUT_SEC", "4.5")

        snap = get_speculative_runtime_snapshot()
        eff = snap["effective"]
        assert eff["cache_ttl_sec"] == pytest.approx(123.0)
        assert eff["max_history"] == 44
        assert eff["timeout_sec"] == pytest.approx(4.5)


# ─────────────────────────────────────────────────────────────
# End-to-end integration
# ─────────────────────────────────────────────────────────────


class TestEndToEnd:
    def test_full_predict_cache_hit_flow(self):
        """Simulate a real agent session with repeated tool patterns."""
        call_count = 0

        def counting_dispatch(tool: str, args: dict) -> str:
            nonlocal call_count
            call_count += 1
            return f"result:{tool}:{args}"

        exec_ = SpeculativeExecutor(counting_dispatch, config={"enabled": False})

        # Phase 1: Build up bigram data (no speculation, just recording).
        for _ in range(3):
            exec_.execute("read_file", {"path": "/src/main.py"})
            exec_.execute("grep_search", {"query": "def main"})

        assert call_count == 6
        assert exec_.prediction.history_len == 6

        # Phase 2: Pre-warm cache manually (simulating what speculation would do).
        exec_.cache.put("grep_search", {"query": "def main"}, "cached_grep")

        # Phase 3: Execute read_file → then grep_search should hit cache.
        exec_.execute("read_file", {"path": "/src/main.py"})
        result = exec_.execute("grep_search", {"query": "def main"})
        assert result == "cached_grep"
        assert call_count == 7  # only read_file was dispatched, grep was cached

    def test_write_tool_always_passes_through(self):
        """Write tools should always call dispatch, never cache."""
        dispatch = MagicMock(return_value="written")
        exec_ = SpeculativeExecutor(dispatch, config={"enabled": False})
        result = exec_.execute("bash_exec", {"command": "echo hi"})
        assert result == "written"
        dispatch.assert_called_once()
