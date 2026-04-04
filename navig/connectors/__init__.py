"""
NAVIG Connector Engine

Universal connector interface for external service integrations.
Gmail, Google Calendar, and future connectors all implement the
``BaseConnector`` ABC and register in the ``ConnectorRegistry``.

Usage:
    from navig.connectors import get_connector_registry

    registry = get_connector_registry()
    gmail = registry.get("gmail")
    results = await gmail.search("meeting notes")

Architecture:
    BaseConnector ABC  ──────────────────────────────────────
        ↑ implements                                        │
    GmailConnector / GoogleCalendarConnector / …            │
        ↓ registers into                                    │
    ConnectorRegistry (singleton)                           │
        ↓ auth delegated to                                 │
    ConnectorAuthManager (wraps vault + OAuth PKCE)         │
        ↓ resilience via                                    │
    CircuitBreaker (per-connector, 3-strike → degraded)     │
"""

from __future__ import annotations

from navig.connectors.registry import ConnectorRegistry, get_connector_registry
from navig.connectors.types import (
    Action,
    ActionResult,
    ConnectorDomain,
    ConnectorStatus,
    HealthStatus,
    Resource,
    ResourceType,
)

__all__ = [
    "Action",
    "ActionResult",
    "ConnectorDomain",
    "ConnectorRegistry",
    "ConnectorStatus",
    "HealthStatus",
    "Resource",
    "ResourceType",
    "get_connector_registry",
]
