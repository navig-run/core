"""Publisher registry — mirrors ``navig.messaging.adapter_registry``."""

from __future__ import annotations

import logging
from typing import Any

from navig.social.base import SocialPublisher

logger = logging.getLogger(__name__)


class PublisherRegistry:
    """Holds the live :class:`SocialPublisher` instances keyed by network name."""

    def __init__(self) -> None:
        self._publishers: dict[str, SocialPublisher] = {}

    def register(self, publisher: SocialPublisher) -> None:
        self._publishers[publisher.name] = publisher
        logger.debug("social publisher registered: %s", publisher.name)

    def get(self, name: str) -> SocialPublisher | None:
        return self._publishers.get(name)

    def names(self) -> list[str]:
        return list(self._publishers.keys())

    def all(self) -> list[SocialPublisher]:
        return list(self._publishers.values())


_registry: PublisherRegistry | None = None


def get_publisher_registry() -> PublisherRegistry:
    """Return the global registry, auto-registering built-in publishers once."""
    global _registry
    if _registry is None:
        _registry = PublisherRegistry()
        try:
            from navig.social.publishers import BUILTIN_PUBLISHERS

            for cls in BUILTIN_PUBLISHERS:
                _registry.register(cls())
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not register built-in social publishers: %s", exc)
    return _registry


def reset_publisher_registry() -> None:
    """Test hook — drop the singleton so the next call rebuilds it."""
    global _registry
    _registry = None
