"""
Connector Engine — Base Connector ABC

Every external-service connector **must** subclass ``BaseConnector`` and
implement the five abstract methods: ``search``, ``fetch``, ``act``,
``health_check``, and ``disconnect``.

Pattern follows:
    navig/adapters/os/base.py   → ABC + dataclass contract
    navig/providers/registry.py → ProviderManifest metadata
    navig/gateway/channels/registry.py → ChannelId enum + ChannelMeta
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from navig.connectors.types import (
    Action,
    ActionResult,
    ConnectorDomain,
    ConnectorStatus,
    HealthStatus,
    Resource,
)

logger = logging.getLogger("navig.connectors")


# ---------------------------------------------------------------------------
# Connector Manifest (static metadata, like ProviderManifest)
# ---------------------------------------------------------------------------


@dataclass
class ConnectorManifest:
    """
    Declarative metadata for a connector.

    Every connector class must expose a class-level ``manifest`` attribute
    that describes its capabilities, auth requirements, and domain.
    """

    id: str  # e.g. "gmail", "google_calendar"
    display_name: str  # e.g. "Gmail", "Google Calendar"
    description: str
    domain: ConnectorDomain
    icon: str = "🔗"  # emoji for CLI/Telegram surfaces
    oauth_scopes: list[str] = field(default_factory=list)
    oauth_provider: str = ""  # key into OAUTH_PROVIDERS registry
    requires_oauth: bool = True
    # Capabilities this connector supports (search / fetch / act)
    can_search: bool = True
    can_fetch: bool = True
    can_act: bool = True


# ---------------------------------------------------------------------------
# Base Connector ABC
# ---------------------------------------------------------------------------


class BaseConnector(ABC):
    """
    Abstract base class for all NAVIG connectors.

    Subclasses must:
    1. Define a ``manifest`` class attribute of type ``ConnectorManifest``
    2. Implement ``search``, ``fetch``, ``act``, ``health_check``, ``disconnect``
    3. Optionally override ``connect`` for custom auth bootstrap

    The connector engine handles OAuth token management via
    ``ConnectorAuthManager`` — connectors receive an access token
    through ``_get_access_token()`` rather than managing auth themselves.
    """

    manifest: ConnectorManifest  # must be set by subclass

    def __init__(self) -> None:
        self._status: ConnectorStatus = ConnectorStatus.DISCONNECTED
        self._access_token: str | None = None
        self._metadata: dict[str, Any] = {}

    # -- Properties --------------------------------------------------------

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def domain(self) -> ConnectorDomain:
        return self.manifest.domain

    @property
    def status(self) -> ConnectorStatus:
        return self._status

    @status.setter
    def status(self, value: ConnectorStatus) -> None:
        old = self._status
        self._status = value
        if old != value:
            logger.info(
                "Connector %s status: %s → %s", self.id, old.value, value.value
            )

    # -- Token helper (set by ConnectorAuthManager) ------------------------

    def set_access_token(self, token: str) -> None:
        """Inject a valid access token (called by ConnectorAuthManager)."""
        self._access_token = token

    def _get_access_token(self) -> str:
        """
        Retrieve the current access token.

        Raises ``RuntimeError`` if no token is set so connectors fail
        loudly rather than sending unauthenticated requests.
        """
        if not self._access_token:
            raise RuntimeError(
                f"Connector '{self.id}' has no access token. "
                "Call ConnectorAuthManager.authenticate() first."
            )
        return self._access_token

    # -- Abstract interface ------------------------------------------------

    @abstractmethod
    async def search(self, query: str) -> list[Resource]:
        """
        Search this connector for resources matching *query*.

        Returns a list of ``Resource`` instances normalised to the
        universal schema.
        """

    @abstractmethod
    async def fetch(self, resource_id: str) -> Resource:
        """Fetch a single resource by its native ID."""

    @abstractmethod
    async def act(self, action: Action) -> ActionResult:
        """Execute a write action (reply, create, update, delete, …)."""

    @abstractmethod
    async def health_check(self) -> HealthStatus:
        """
        Probe the upstream API and report latency + availability.

        Must be safe to call repeatedly (idempotent, read-only).
        """

    async def connect(self) -> None:
        """
        Called after token injection to perform connector-specific setup.

        Default implementation simply marks status as CONNECTED.
        Override if the connector needs to fetch user profile, validate
        scopes, or cache metadata.
        """
        self.status = ConnectorStatus.CONNECTED

    async def disconnect(self) -> None:
        """
        Tear down connector state and release resources.

        Default marks status as DISCONNECTED and clears token.
        """
        self._access_token = None
        self.status = ConnectorStatus.DISCONNECTED

    # -- Convenience -------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} id={self.id!r} "
            f"status={self._status.value!r}>"
        )
