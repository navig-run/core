"""Tests for navig.connectors.circuit_breaker."""
from __future__ import annotations

import time

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


# ── HALF_OPEN admits exactly ONE probe ────────────────────────
#
# Regression: allow_request() returned True for *every* caller while HALF_OPEN
# despite its own "allow one probe request" contract, so the moment the recovery
# timeout elapsed a recovering upstream took the full concurrent herd -- the exact
# stampede a circuit breaker exists to prevent. Measured pre-fix: 50 of 50 callers
# admitted, and _total_trips reported 51 for a single outage because re-entering
# OPEN while already OPEN counted another trip.


class TestHalfOpenProbeIsExclusive:
    def _half_open(self, recovery: float = 30.0) -> CircuitBreaker:
        cb = _cb(threshold=1, recovery=recovery)
        cb.record_failure()
        assert cb._state is CircuitState.OPEN
        # Move past the recovery window without sleeping for it.
        cb._last_failure_time -= recovery + 1
        assert cb.state is CircuitState.HALF_OPEN
        return cb

    def test_only_one_of_many_callers_is_admitted(self):
        cb = self._half_open()
        admitted = [cb.allow_request() for _ in range(50)]
        assert admitted.count(True) == 1, (
            f"HALF_OPEN admitted {admitted.count(True)} callers -- it must admit "
            "exactly one probe, not stampede the recovering upstream"
        )
        assert admitted[0] is True, "the first caller should get the probe"

    def test_probe_slot_is_released_by_success(self):
        cb = self._half_open()
        assert cb.allow_request() is True
        cb.record_success()
        assert cb.state is CircuitState.CLOSED
        assert cb.allow_request() is True  # CLOSED admits everyone again

    def test_probe_slot_is_released_by_failure(self):
        cb = self._half_open(recovery=30.0)
        assert cb.allow_request() is True
        cb.record_failure()
        assert cb.state is CircuitState.OPEN
        assert cb.allow_request() is False  # back to OPEN, nothing gets through
        # ...and a fresh recovery window hands out a new probe.
        cb._last_failure_time -= 31.0
        assert cb.allow_request() is True

    def test_abandoned_probe_cannot_wedge_the_breaker_shut(self):
        """A probe that never reports back must not lock the connector out forever.

        The wrapper in connectors/base.py records the outcome from an
        ``except Exception`` block, but ``asyncio.CancelledError`` derives from
        ``BaseException`` -- a cancelled task never records. A boolean reservation
        would stay set forever; the timestamp reservation expires.
        """
        cb = self._half_open(recovery=1.0)
        assert cb.allow_request() is True  # probe taken...
        assert cb.allow_request() is False  # ...and held
        # The probe never calls record_success/record_failure (task cancelled).
        cb._probe_started_at -= 1.5  # recovery_timeout elapses
        assert cb.allow_request() is True, (
            "an unreported probe must be treated as abandoned -- otherwise a single "
            "cancelled task locks the connector out permanently"
        )

    def test_reset_releases_the_probe_slot(self):
        cb = self._half_open()
        assert cb.allow_request() is True
        cb.reset()
        assert cb._probe_started_at is None
        assert cb.allow_request() is True


class TestTripCountingIsPerOutage:
    def test_concurrent_failures_during_one_outage_count_one_trip(self):
        cb = _cb(threshold=3, recovery=30.0)
        for _ in range(3):
            cb.record_failure()
        assert cb._state is CircuitState.OPEN
        trips_after_open = cb._total_trips

        # 50 calls that were already in flight when the breaker tripped now fail.
        for _ in range(50):
            cb.record_failure()

        assert cb._total_trips == trips_after_open == 1, (
            f"one outage reported {cb._total_trips} trips -- re-entering OPEN while "
            "already OPEN must not count as a new trip"
        )

    def test_transition_to_same_state_is_a_noop(self):
        cb = _cb(threshold=1)
        cb.record_failure()
        assert cb._state is CircuitState.OPEN
        before = cb._total_trips
        cb._transition(CircuitState.OPEN)
        assert cb._total_trips == before

    def test_distinct_outages_still_count_separately(self):
        cb = _cb(threshold=1, recovery=30.0)
        cb.record_failure()          # outage 1
        cb.record_success()          # recovered
        cb.record_failure()          # outage 2
        assert cb._total_trips == 2


class TestProbeDiagnostics:
    def test_to_dict_reports_probe_in_flight(self):
        cb = _cb(threshold=1, recovery=30.0)
        assert cb.to_dict()["probe_in_flight"] is False
        cb.record_failure()
        cb._last_failure_time -= 31.0
        assert cb.allow_request() is True
        assert cb.to_dict()["probe_in_flight"] is True
        cb.record_success()
        assert cb.to_dict()["probe_in_flight"] is False
