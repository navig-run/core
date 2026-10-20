"""Link providers — systems that know something about a project.

Core cannot import harbor (paid, private) or navig-msstore (a plugin), and must
not need them. So the dependency points the other way: a plugin registers a
provider here at load time, and the project hub shows whatever providers
answer. With none installed the hub simply has no Links tab.

A provider is ``fn(project_path: str, space_id: str) -> dict | None``. It returns
a card (``{"title", "summary", "metrics": [...], "href"?, ...}``) when it has
something linked to that folder, else ``None``. It must be cheap (it runs for
every card in the list) — cache inside the provider if it needs I/O.

Contract: :func:`collect_links` never raises. A provider that throws is logged
and its card omitted; one broken plugin must not take the project hub down.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["LinkProvider", "clear_link_providers", "collect_links", "link_provider_names", "register_link_provider"]

LinkProvider = Callable[[str, str], "dict[str, Any] | None"]

_providers: dict[str, LinkProvider] = {}


def register_link_provider(name: str, fn: LinkProvider) -> None:
    """Register (or replace) the provider called *name* — idempotent on reload."""
    if not name or not callable(fn):
        raise ValueError("a link provider needs a name and a callable")
    _providers[name] = fn


def link_provider_names() -> list[str]:
    return sorted(_providers)


def clear_link_providers() -> None:
    """Tests only."""
    _providers.clear()


def collect_links(project_path: str, space_id: str) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for name in sorted(_providers):
        try:
            card = _providers[name](project_path, space_id)
        except Exception:  # noqa: BLE001 — a plugin's bug must not break the hub
            logger.warning("project link provider %r failed for %s", name, project_path, exc_info=True)
            continue
        if isinstance(card, dict):
            cards.append({"provider": name, **card})
    return cards
