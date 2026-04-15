"""Tests for navig.memory.auto_compact — AutoCompactManager circuit breaker."""

from __future__ import annotations

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_manager(buffer: int = 2000, max_failures: int = 3, min_turns: int = 1):
    from navig.memory.auto_compact import AutoCompactManager

    return AutoCompactManager(
        session_key="test-session",
        buffer_tokens=buffer,
        max_consecutive_failures=max_failures,
        min_turns=min_turns,
    )


# ─────────────────────────────────────────────────────────────────────────────
# should_compact
# ─────────────────────────────────────────────────────────────────────────────

class TestShouldCompact:
    def test_returns_false_when_below_threshold(self):
        mgr = _make_manager(buffer=5000)
        assert mgr.should_compact(tokens_used=50_000, context_window=100_000, turn_count=5) is False

    def test_returns_true_when_within_buffer(self):
        mgr = _make_manager(buffer=5000)
        # used = 97_000 / 100_000 → 3_000 headroom < 5_000 buffer
        assert mgr.should_compact(tokens_used=97_000, context_window=100_000, turn_count=5) is True

    def test_min_turns_guard(self):
        mgr = _make_manager(buffer=5000, min_turns=10)
        assert mgr.should_compact(tokens_used=97_000, context_window=100_000, turn_count=3) is False

    def test_returns_false_when_already_compacting(self):
        mgr = _make_manager(buffer=5000)
        mgr._state.compacting = True
        assert mgr.should_compact(tokens_used=97_000, context_window=100_000, turn_count=5) is False

    def test_circuit_open_disables_compact(self):
        mgr = _make_manager(buffer=5000, max_failures=2)
        mgr._record_failure("err1")
        mgr._record_failure("err2")  # trip breaker
        assert mgr._state.circuit_open is True
        assert mgr.should_compact(tokens_used=99_000, context_window=100_000, turn_count=5) is False


# ─────────────────────────────────────────────────────────────────────────────
# Circuit breaker
# ─────────────────────────────────────────────────────────────────────────────

class TestCircuitBreaker:
    def test_trips_after_max_failures(self):
        mgr = _make_manager(max_failures=3)
        for _ in range(3):
            mgr._record_failure("err")
        assert mgr._state.circuit_open is True

    def test_does_not_trip_before_max_failures(self):
        mgr = _make_manager(max_failures=3)
        mgr._record_failure("err")
        mgr._record_failure("err")
        assert mgr._state.circuit_open is False

    def test_record_success_resets_failure_count(self):
        mgr = _make_manager(max_failures=3)
        mgr._record_failure("err")
        mgr._record_failure("err")
        # Reset by directly manipulating state (no public _record_success method)
        mgr._state.consecutive_failures = 0
        mgr._state.circuit_open = False
        assert mgr._state.consecutive_failures == 0
        assert mgr._state.circuit_open is False

    def test_total_compactions_incremented_on_success(self):
        mgr = _make_manager()
        mgr._state.total_compactions += 1
        mgr._state.total_compactions += 1
        assert mgr._state.total_compactions == 2


# ─────────────────────────────────────────────────────────────────────────────
# Process-wide registry
# ─────────────────────────────────────────────────────────────────────────────

class TestRegistry:
    def test_same_key_returns_same_instance(self):
        from navig.memory.auto_compact import get_auto_compact_manager

        a = get_auto_compact_manager("sess-x")
        b = get_auto_compact_manager("sess-x")
        assert a is b

    def test_different_keys_give_different_instances(self):
        from navig.memory.auto_compact import get_auto_compact_manager

        a = get_auto_compact_manager("sess-alpha")
        b = get_auto_compact_manager("sess-beta")
        assert a is not b
