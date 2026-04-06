"""Messaging layer — multi-transport routing, adapters, and delivery tracking.

This package provides:
- :class:`ChannelAdapter` protocol for per-network adapters
- :class:`AdapterRegistryManager` for adapter lifecycle
- :class:`RoutingEngine` for deterministic 5-step message routing
- :class:`DeliveryTracker` for compliance-grade audit logging
- Legacy :class:`IMessagingProvider` for gateway boot (backward-compat)
"""

from .adapter import (
    ChannelAdapter,
    ComplianceMode,
    Contact,
    DeliveryReceipt,
    DeliveryStatus,
    IdentityMode,
    InboundEvent,
    ResolvedTarget,
    Route,
    RoutingDecision,
    Thread,
)
from .adapter_registry import AdapterRegistryManager, get_adapter_registry
from .provider import IMessagingProvider
from .registry import (
    create_channel_for_provider,
    get_active_provider_name,
    is_provider_enabled,
    supported_provider_names,
)

__all__ = [
    # Protocol & types
    "ChannelAdapter",
    "ComplianceMode",
    "Contact",
    "DeliveryReceipt",
    "DeliveryStatus",
    "IdentityMode",
    "InboundEvent",
    "ResolvedTarget",
    "Route",
    "RoutingDecision",
    "Thread",
    # Registry
    "AdapterRegistryManager",
    "get_adapter_registry",
    # Legacy provider
    "IMessagingProvider",
    "create_channel_for_provider",
    "get_active_provider_name",
    "is_provider_enabled",
    "supported_provider_names",
]
