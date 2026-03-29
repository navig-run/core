from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class IMessagingProvider(Protocol):
    """Contract for pluggable messaging providers."""

    @property
    def name(self) -> str:
        """Provider identifier (for example: ``telegram``)."""

    def is_enabled(self, raw_config: dict[str, Any]) -> bool:
        """Return whether this provider is enabled with the given config."""

    def create_channel(self, gateway: Any, provider_config: dict[str, Any]) -> Any | None:
        """Build and return a channel adapter instance (or ``None`` on failure)."""
