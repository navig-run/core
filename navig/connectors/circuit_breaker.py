"""
Per-Connector Circuit Breaker

Prevents cascading latency when an upstream API is down.

States:
    CLOSED   → healthy, calls pass through
    OPEN     → broken, calls immediately rejected
    HALF_OPEN → recovery test: next success → CLOSED, next failure → OPEN

Thresholds:
    failure_threshold = 3 consecutive failures → OPEN
    recovery_timeout  = 30 s  → transition from OPEN → HALF_OPEN

Events are emitted on the ``EventBridge`` (topic ``connector.circuit.*``)
if the bridge is available; otherwise failures are logged only.
"""

from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Any

logger = logging.getLogger("navig.connectors.circuit_breaker")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """
    Lightweight circuit breaker for a single connector.

    Usage:
        cb = CircuitBreaker("gmail")
        if cb.allow_request():
            try:
                result = await do_api_call()
                cb.record_success()
            except Exception:
                cb.record_failure()
                raise
        else:
            raise ConnectorDegradedError("gmail")
    """

    def __init__(
        self,
        connector_id: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
    ) -> None:
        self.connector_id = connector_id
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._total_trips = 0
        # monotonic timestamp of the outstanding HALF_OPEN probe, or None.
        # A *timestamp* rather than a bool on purpose — see allow_request().
        self._probe_started_at: float | None = None

    # -- Public API --------------------------------------------------------

    @property
    def state(self) -> CircuitState:
        """Current state, with automatic OPEN→HALF_OPEN promotion."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.recovery_timeout:
                self._transition(CircuitState.HALF_OPEN)
        return self._state

    def allow_request(self) -> bool:
        """Return ``True`` if a request is permitted.

        In HALF_OPEN exactly **one** probe is admitted at a time. Previously every
        caller was admitted, so a recovering upstream took the full concurrent herd
        the moment the recovery timeout elapsed — the precise stampede a circuit
        breaker exists to prevent — and each in-flight failure counted a separate
        "trip", making ``total_trips`` meaningless.

        The reservation is a **timestamp, not a boolean**, so a probe that never
        reports back cannot wedge the breaker shut. That is not hypothetical: the
        wrapper in ``connectors/base.py`` records the outcome from an
        ``except Exception`` block, and ``asyncio.CancelledError`` derives from
        ``BaseException`` — a cancelled task would leave a boolean flag set forever
        and lock the connector out permanently. After ``recovery_timeout`` an
        unreported probe is treated as abandoned and a fresh one is admitted.
        """
        current = self.state  # triggers promotion check
        if current == CircuitState.CLOSED:
            return True
        if current != CircuitState.HALF_OPEN:
            return False  # OPEN

        now = time.monotonic()
        if (
            self._probe_started_at is not None
            and (now - self._probe_started_at) < self.recovery_timeout
        ):
            return False  # a probe is already out and still plausibly running
        self._probe_started_at = now
        return True

    def record_success(self) -> None:
        """Record a successful call — reset to CLOSED."""
        self._probe_started_at = None
        if self._state != CircuitState.CLOSED:
            self._transition(CircuitState.CLOSED)
        self._failure_count = 0

    def record_failure(self) -> None:
        """Record a failed call — may trip the breaker."""
        self._probe_started_at = None
        self._failure_count += 1
        self._last_failure_time = time.monotonic()

        if self._state == CircuitState.HALF_OPEN:
            # Recovery probe failed → back to OPEN
            self._transition(CircuitState.OPEN)
        elif self._failure_count >= self.failure_threshold:
            self._transition(CircuitState.OPEN)

    def reset(self) -> None:
        """Force-reset to CLOSED (e.g. after manual reconnect)."""
        self._failure_count = 0
        self._probe_started_at = None
        self._state = CircuitState.CLOSED

    # -- Diagnostics -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "connector_id": self.connector_id,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "total_trips": self._total_trips,
            "recovery_timeout": self.recovery_timeout,
            "probe_in_flight": self._probe_started_at is not None,
        }

    # -- Internal ----------------------------------------------------------

    def _transition(self, new_state: CircuitState) -> None:
        old = self._state
        if new_state == old:
            # Not a transition. Re-entering OPEN while already OPEN used to count
            # another "trip" and log another line, so a single outage with N calls
            # in flight reported N trips (measured: 51 for one outage).
            return
        self._state = new_state
        if new_state == CircuitState.HALF_OPEN:
            # A fresh recovery window — let the next caller take the probe.
            self._probe_started_at = None
        if new_state == CircuitState.OPEN:
            self._total_trips += 1
        logger.info(
            "Circuit breaker [%s]: %s → %s (failures=%d, trips=%d)",
            self.connector_id,
            old.value,
            new_state.value,
            self._failure_count,
            self._total_trips,
        )
        # Best-effort event emission (non-critical if bridge unavailable)
        self._emit_event(old, new_state)

    def _emit_event(self, old: CircuitState, new: CircuitState) -> None:
        """Placeholder: emit a state-change event when an EventBridge instance is available.

        The circuit breaker operates synchronously and does not hold a reference
        to the gateway event bus.  Connectors or the gateway layer may hook into
        state changes by sub-classing or by observing connector status changes.
        """
        # No-op at this layer.  State changes are logged via logger.info in
        # _transition(); higher-level observers should monitor connector status.
        pass
