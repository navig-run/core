"""
Connector Engine — Error Hierarchy

All connector-specific exceptions derive from ``ConnectorError`` so callers
can catch the whole family with a single ``except ConnectorError``.

Policy (per exception-policy.instructions.md):
- Never silently swallow — always log at debug minimum.
- Use the narrowest type possible (e.g. ``ConnectorAuthError`` not bare
  ``ConnectorError``).
"""

from __future__ import annotations


class ConnectorError(Exception):
    """Base exception for all connector operations."""

    def __init__(self, connector_id: str, message: str) -> None:
        self.connector_id = connector_id
        super().__init__(f"[{connector_id}] {message}")


class ConnectorAuthError(ConnectorError):
    """Authentication or token-refresh failure."""


class ConnectorNotFoundError(ConnectorError):
    """Requested connector is not registered."""

    def __init__(self, connector_id: str, message: str | None = None) -> None:
        super().__init__(
            connector_id,
            message or f"Connector '{connector_id}' is not registered",
        )


class ConnectorDegradedError(ConnectorError):
    """Connector's circuit breaker is open — calls are rejected."""

    def __init__(self, connector_id: str, message: str | None = None) -> None:
        super().__init__(
            connector_id,
            message or "Connector is degraded (circuit breaker open)",
        )


class ConnectorAPIError(ConnectorError):
    """Upstream API returned a non-success response."""

    def __init__(self, connector_id: str, status_code: int, detail: str = "") -> None:
        self.status_code = status_code
        self.detail = detail
        msg = f"API error {status_code}"
        if detail:
            msg += f": {detail}"
        super().__init__(connector_id, msg)


class ConnectorRateLimitError(ConnectorAPIError):
    """Upstream API rate limit hit (HTTP 429)."""

    def __init__(self, connector_id: str, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        detail = f"retry after {retry_after}s" if retry_after else ""
        super().__init__(connector_id, 429, detail)
