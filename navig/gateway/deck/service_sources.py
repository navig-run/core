"""Plugins that contribute rows to ``GET /api/deck/services`` register here.

Core lists its own connectors and messaging channels; a plugin that owns more services
(navig-social's publishing networks) registers a source instead of core importing the
plugin. A source is a zero-argument callable returning a list of service dicts in the
shape ``routes/services.py`` documents; it runs in a worker thread, and one that raises
is reported in the response's ``sources`` map without failing the list.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

ServiceSource = Callable[[], list[dict[str, Any]]]

_SOURCES: dict[str, ServiceSource] = {}


def register_service_source(name: str, source: ServiceSource) -> None:
    """Add (or replace) the source called *name*. Idempotent across gateway restarts."""
    _SOURCES[name] = source


def unregister_service_source(name: str) -> None:
    _SOURCES.pop(name, None)


def service_sources() -> dict[str, ServiceSource]:
    return dict(_SOURCES)
