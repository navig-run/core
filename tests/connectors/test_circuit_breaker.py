"""Tests for navig.connectors.circuit_breaker — CircuitBreaker."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from navig.connectors.circuit_breaker import CircuitBreaker, CircuitState


class TestCircuitBreaker:
    def test_initial_state_closed(self):
        cb = CircuitBreaker("test")
        assert cb.state == CircuitState.CLOSED

    def test_stays_closed_under_threshold(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        assert cb.allow_request() is True

    def test_opens_at_threshold(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.allow_request() is False

    def test_success_resets_failures(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        # Should not open after one more failure
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_after_recovery_timeout(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.1)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # Wait for recovery
        time.sleep(0.15)
        assert cb.allow_request() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_success_closes(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.01)
        cb.record_failure()
        time.sleep(0.02)
        cb.allow_request()  # Promotes to HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.01)
        cb.record_failure()
        time.sleep(0.02)
        cb.allow_request()  # Promotes to HALF_OPEN
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_reset(self):
        cb = CircuitBreaker("test", failure_threshold=1)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.allow_request() is True


# ---------------------------------------------------------------------------
# Integration — circuit breaker is transparently wired into BaseConnector
# ---------------------------------------------------------------------------


class TestCircuitBreakerIntegration:
    """Confirm that BaseConnector.__init_subclass__ wires the circuit breaker."""

    def _make_connector(self, failure_threshold: int = 3):
        """Build a minimal concrete connector whose _search raises on demand."""
        from navig.connectors.base import BaseConnector, ConnectorManifest
        from navig.connectors.types import (
            Action,
            ActionResult,
            ConnectorDomain,
            HealthStatus,
            Resource,
        )

        class _Flaky(BaseConnector):
            manifest = ConnectorManifest(
                id="flaky_test",
                display_name="Flaky",
                description="Fails on demand",
                domain=ConnectorDomain.COMMUNICATION,
                icon="⚡",
            )
            should_fail: bool = False

            async def search(self, query: str, limit: int = 5):  # type: ignore[override]
                if self.should_fail:
                    raise RuntimeError("upstream down")
                return []

            async def fetch(self, resource_id: str):  # type: ignore[override]
                return None

            async def act(self, action: Action):  # type: ignore[override]
                return ActionResult(success=True)

            async def health_check(self):
                return HealthStatus(ok=True, latency_ms=0.0)

        connector = _Flaky()
        connector._circuit_breaker = CircuitBreaker(
            "flaky_test", failure_threshold=failure_threshold
        )
        return connector

    def test_circuit_breaker_initialised_on_instance(self):
        """Every connector instance must have a _circuit_breaker attribute."""
        conn = self._make_connector()
        assert hasattr(conn, "_circuit_breaker")
        assert isinstance(conn._circuit_breaker, CircuitBreaker)
        assert conn._circuit_breaker.connector_id == "flaky_test"

    def test_success_keeps_breaker_closed(self):
        conn = self._make_connector()
        asyncio.run(conn.search("ok"))
        assert conn._circuit_breaker.state == CircuitState.CLOSED

    def test_failures_open_circuit(self):
        """After enough consecutive failures, the breaker opens."""
        from navig.connectors.errors import ConnectorDegradedError

        conn = self._make_connector(failure_threshold=2)
        conn.should_fail = True
        for _ in range(2):
            with pytest.raises(RuntimeError):
                asyncio.run(conn.search("fail"))
        assert conn._circuit_breaker.state == CircuitState.OPEN

        # Next call should raise ConnectorDegradedError (not RuntimeError)
        with pytest.raises(ConnectorDegradedError):
            asyncio.run(conn.search("fail"))

    def test_connect_resets_circuit(self):
        # Calling connect() resets an open circuit breaker.
        conn = self._make_connector(failure_threshold=1)
        conn._circuit_breaker.record_failure()
        assert conn._circuit_breaker.state == CircuitState.OPEN
        asyncio.run(conn.connect())  # BaseConnector.connect() calls _circuit_breaker.reset()
        assert conn._circuit_breaker.state == CircuitState.CLOSED
