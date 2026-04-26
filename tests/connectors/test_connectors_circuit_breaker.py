"""Tests for navig.connectors.circuit_breaker."""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from navig.connectors.circuit_breaker import CircuitBreaker, CircuitState


# ── factory ──────────────────────────────────────────────────


def _cb(threshold: int = 3, recovery: float = 30.0) -> CircuitBreaker:
    return CircuitBreaker("test-connector", failure_threshold=threshold, recovery_timeout=recovery)


# ── initial state ─────────────────────────────────────────────


class TestInitialState:
    def test_starts_closed(self):
        assert _cb().state == CircuitState.CLOSED

    def test_connector_id_stored(self):
        cb = CircuitBreaker("myconn")
        assert cb.connector_id == "myconn"

    def test_failure_threshold_stored(self):
        cb = CircuitBreaker("x", failure_threshold=5)
        assert cb.failure_threshold == 5

    def test_recovery_timeout_stored(self):
        cb = CircuitBreaker("x", recovery_timeout=60.0)
        assert cb.recovery_timeout == 60.0

    def test_default_threshold_is_3(self):
        assert _cb().failure_threshold == 3

    def test_default_recovery_is_30(self):
        assert _cb().recovery_timeout == 30.0

    def test_allow_request_initially_true(self):
        assert _cb().allow_request() is True


# ── CLOSED state ──────────────────────────────────────────────


class TestClosedState:
    def test_single_failure_stays_closed(self):
        cb = _cb(threshold=3)
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED

    def test_two_failures_stay_closed(self):
        cb = _cb(threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED

    def test_threshold_failures_opens(self):
        cb = _cb(threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_success_resets_failure_count(self):
        cb = _cb(threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        # Only 2 failures since reset, still CLOSED
        assert cb.state == CircuitState.CLOSED

    def test_allow_request_true_when_closed(self):
        cb = _cb()
        assert cb.allow_request() is True


# ── OPEN state ────────────────────────────────────────────────


class TestOpenState:
    def _open_cb(self) -> CircuitBreaker:
        cb = _cb(threshold=1)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        return cb

    def test_allow_request_false_when_open(self):
        cb = self._open_cb()
        assert cb.allow_request() is False

    def test_total_trips_increments_on_open(self):
        cb = _cb(threshold=1)
        cb.record_failure()
        assert cb._total_trips == 1

    def test_multiple_trips_count(self):
        cb = _cb(threshold=1)
        cb.record_failure()
        cb.record_success()  # reset
        cb.record_failure()
        assert cb._total_trips == 2

    def test_transitions_to_half_open_after_timeout(self):
        cb = _cb(threshold=1, recovery=0.01)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        time.sleep(0.02)
        assert cb.state == CircuitState.HALF_OPEN

    def test_stays_open_before_timeout(self):
        cb = _cb(threshold=1, recovery=999.0)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN


# ── HALF_OPEN state ───────────────────────────────────────────


class TestHalfOpenState:
    def _half_open_cb(self) -> CircuitBreaker:
        cb = _cb(threshold=1, recovery=0.01)
        cb.record_failure()
        time.sleep(0.02)
        _ = cb.state  # trigger promotion
        return cb

    def test_allow_request_true_when_half_open(self):
        cb = self._half_open_cb()
        assert cb.allow_request() is True

    def test_success_in_half_open_closes(self):
        cb = self._half_open_cb()
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_failure_in_half_open_reopens(self):
        cb = self._half_open_cb()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_trips_increment_on_half_open_failure(self):
        cb = self._half_open_cb()
        trips_before = cb._total_trips
        cb.record_failure()
        assert cb._total_trips == trips_before + 1


# ── reset ─────────────────────────────────────────────────────


class TestReset:
    def test_reset_from_open(self):
        cb = _cb(threshold=1)
        cb.record_failure()
        cb.reset()
        assert cb.state == CircuitState.CLOSED

    def test_reset_clears_failure_count(self):
        cb = _cb(threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.reset()
        assert cb._failure_count == 0

    def test_allow_request_true_after_reset(self):
        cb = _cb(threshold=1)
        cb.record_failure()
        cb.reset()
        assert cb.allow_request() is True


# ── to_dict ───────────────────────────────────────────────────


class TestToDict:
    def test_contains_connector_id(self):
        cb = CircuitBreaker("my-api")
        d = cb.to_dict()
        assert d["connector_id"] == "my-api"

    def test_state_is_string(self):
        cb = _cb()
        d = cb.to_dict()
        assert isinstance(d["state"], str)

    def test_state_value_closed(self):
        d = _cb().to_dict()
        assert d["state"] == "closed"

    def test_contains_failure_count(self):
        cb = _cb()
        cb.record_failure()
        d = cb.to_dict()
        assert d["failure_count"] == 1

    def test_contains_total_trips(self):
        cb = _cb(threshold=1)
        cb.record_failure()
        d = cb.to_dict()
        assert d["total_trips"] == 1

    def test_contains_recovery_timeout(self):
        cb = CircuitBreaker("x", recovery_timeout=45.0)
        d = cb.to_dict()
        assert d["recovery_timeout"] == 45.0
