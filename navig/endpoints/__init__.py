"""Unified Endpoints — one queryable model of every controllable thing.

Public surface::

    from navig.endpoints import EndpointStore, Endpoint, HomeBridge

``EndpointStore.list()`` / ``.show()`` are the reads the NAVIG Reflex (B) builds
on; the store is a 5-second in-process projection of the shipped ``navig-home``
plugin's own CLI. There is no second inventory, no database and no provider
import in core.
"""

from __future__ import annotations

from navig.endpoints.client import DEFAULT_TIMEOUT, HomeBridge
from navig.endpoints.errors import (
    EndpointDirectiveError,
    EndpointError,
    EndpointNotFound,
    HomeUnavailable,
)
from navig.endpoints.model import (
    CAPABILITY_TYPES,
    DIRECTIVES,
    OUT_OF_SCOPE_CAPABILITY_TYPES,
    PROPERTY_TYPES,
    Capability,
    Endpoint,
    validate_endpoint,
)
from navig.endpoints.projection import project, unmapped_capabilities
from navig.endpoints.store import TTL_SECONDS, EndpointStore, SetResult

__all__ = [
    "CAPABILITY_TYPES",
    "DEFAULT_TIMEOUT",
    "DIRECTIVES",
    "OUT_OF_SCOPE_CAPABILITY_TYPES",
    "PROPERTY_TYPES",
    "TTL_SECONDS",
    "Capability",
    "Endpoint",
    "EndpointDirectiveError",
    "EndpointError",
    "EndpointNotFound",
    "EndpointStore",
    "HomeBridge",
    "HomeUnavailable",
    "SetResult",
    "project",
    "unmapped_capabilities",
    "validate_endpoint",
]
