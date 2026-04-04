"""
Connector Registry — Singleton connector catalogue.

Follows the ``ChannelRegistry`` pattern from
``navig/gateway/channels/registry.py``: thread-safe singleton,
register-by-class, lazy instantiation, status tracking.

Usage:
    from navig.connectors.registry import get_connector_registry

    registry = get_connector_registry()
    registry.register(GmailConnector)
    gmail = registry.get("gmail")
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from navig.connectors.base import BaseConnector
from navig.connectors.errors import ConnectorNotFoundError
from navig.connectors.types import ConnectorDomain, ConnectorStatus

logger = logging.getLogger("navig.connectors.registry")


class ConnectorRegistry:
    """
    Thread-safe singleton registry of all known connectors.

    Connectors are registered by *class*; instances are created lazily
    on first ``get()`` call.
    """

    _instance: ConnectorRegistry | None = None
    _lock = threading.Lock()

    def __new__(cls) -> ConnectorRegistry:
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._classes: dict[str, type[BaseConnector]] = {}
                cls._instance._instances: dict[str, BaseConnector] = {}
            return cls._instance

    # -- Registration ------------------------------------------------------

    def register(self, connector_cls: type[BaseConnector]) -> None:
        """
        Register a connector class.

        The class must have a ``manifest`` attribute; its ``manifest.id``
        becomes the registry key.
        """
        manifest = getattr(connector_cls, "manifest", None)
        if manifest is None:
            raise ValueError(
                f"{connector_cls.__name__} is missing a 'manifest' class attribute"
            )
        cid = manifest.id
        if cid in self._classes:
            logger.debug("Connector %r re-registered (overwrite)", cid)
        self._classes[cid] = connector_cls
        logger.debug("Registered connector: %s (%s)", cid, connector_cls.__name__)

    # -- Retrieval ---------------------------------------------------------

    def get(self, connector_id: str) -> BaseConnector:
        """
        Return the singleton instance for *connector_id*.

        Creates the instance on first call (lazy init).

        Raises:
            ConnectorNotFoundError: If *connector_id* is not registered.
        """
        if connector_id not in self._classes:
            raise ConnectorNotFoundError(connector_id)

        if connector_id not in self._instances:
            cls = self._classes[connector_id]
            self._instances[connector_id] = cls()
            logger.debug("Instantiated connector: %s", connector_id)

        return self._instances[connector_id]

    def has(self, connector_id: str) -> bool:
        """Check whether a connector is registered."""
        return connector_id in self._classes

    # -- Listing -----------------------------------------------------------

    def list_all(self) -> list[dict[str, Any]]:
        """Return summary dicts for all registered connectors."""
        out: list[dict[str, Any]] = []
        for cid, cls in sorted(self._classes.items()):
            m = cls.manifest
            inst = self._instances.get(cid)
            status = inst.status.value if inst else ConnectorStatus.DISCONNECTED.value
            out.append(
                {
                    "id": m.id,
                    "display_name": m.display_name,
                    "domain": m.domain.value,
                    "icon": m.icon,
                    "status": status,
                }
            )
        return out

    def list_by_domain(self, domain: ConnectorDomain) -> list[dict[str, Any]]:
        """Return connectors belonging to *domain*."""
        return [c for c in self.list_all() if c["domain"] == domain.value]

    def list_connected(self) -> list[BaseConnector]:
        """Return instances that are currently CONNECTED or DEGRADED."""
        return [
            inst
            for inst in self._instances.values()
            if inst.status
            in (ConnectorStatus.CONNECTED, ConnectorStatus.DEGRADED)
        ]

    # -- Lifecycle ---------------------------------------------------------

    def reset(self) -> None:
        """Clear all registrations (for testing)."""
        self._classes.clear()
        self._instances.clear()


# ---------------------------------------------------------------------------
# Module-level accessor (like get_channel_registry)
# ---------------------------------------------------------------------------


def get_connector_registry() -> ConnectorRegistry:
    """Return the global ``ConnectorRegistry`` singleton."""
    return ConnectorRegistry()
