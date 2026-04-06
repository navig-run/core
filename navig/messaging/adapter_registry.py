"""
AdapterRegistryManager — Manages messaging adapters with compliance gating.

Wraps the gateway :class:`ChannelRegistry` to add:
- Adapter instance lifecycle (register / lookup / availability)
- Compliance mode tracking per adapter
- Experimental gating (disabled adapters refuse send)
- Config-driven enable/disable
"""

from __future__ import annotations

import logging
from typing import Any

from navig.messaging.adapter import ChannelAdapter, ComplianceMode

logger = logging.getLogger(__name__)


class AdapterRegistryManager:
    """
    Registry of :class:`ChannelAdapter` instances with compliance gating.

    Usage::

        registry = AdapterRegistryManager()
        registry.register(sms_adapter, compliance=ComplianceMode.OFFICIAL)
        registry.register(wa_web_adapter, compliance=ComplianceMode.EXPERIMENTAL)

        adapter = registry.get("sms")
        assert registry.is_available("sms")
        assert not registry.is_available("wa_web")  # experimental → disabled by default
    """

    def __init__(self) -> None:
        self._adapters: dict[str, ChannelAdapter] = {}
        self._compliance: dict[str, ComplianceMode] = {}
        self._enabled: dict[str, bool] = {}

    # ── Registration ──────────────────────────────────────────

    def register(
        self,
        adapter: ChannelAdapter,
        *,
        compliance: ComplianceMode | None = None,
        enabled: bool | None = None,
    ) -> None:
        """
        Register an adapter instance.

        Parameters
        ----------
        adapter
            Adapter conforming to :class:`ChannelAdapter`.
        compliance
            Override compliance mode.  Defaults to reading
            ``adapter.compliance``.
        enabled
            Override enabled state.  If ``None``, official adapters are
            enabled by default; experimental are disabled unless opted-in.
        """
        name = adapter.name.lower()
        comp = compliance or ComplianceMode(adapter.compliance)

        if enabled is None:
            enabled = comp != ComplianceMode.EXPERIMENTAL

        self._adapters[name] = adapter
        self._compliance[name] = comp
        self._enabled[name] = enabled

        logger.info(
            "adapter_registered | name=%s | compliance=%s | enabled=%s",
            name,
            comp.value,
            enabled,
        )

    def unregister(self, name: str) -> None:
        """Remove an adapter."""
        name = name.lower()
        self._adapters.pop(name, None)
        self._compliance.pop(name, None)
        self._enabled.pop(name, None)

    # ── Lookup ────────────────────────────────────────────────

    def get(self, name: str) -> ChannelAdapter | None:
        """Get adapter by name. Returns ``None`` if not found or disabled."""
        name = name.lower()
        if not self._enabled.get(name, False):
            return None
        return self._adapters.get(name)

    def get_unchecked(self, name: str) -> ChannelAdapter | None:
        """Get adapter regardless of enabled state."""
        return self._adapters.get(name.lower())

    def is_available(self, name: str) -> bool:
        """True if registered and enabled."""
        name = name.lower()
        return name in self._adapters and self._enabled.get(name, False)

    def is_registered(self, name: str) -> bool:
        """True if registered (regardless of enabled state)."""
        return name.lower() in self._adapters

    def get_compliance(self, name: str) -> ComplianceMode:
        """Return compliance mode for adapter (DISABLED if unknown)."""
        return self._compliance.get(name.lower(), ComplianceMode.DISABLED)

    # ── Enable / disable ──────────────────────────────────────

    def enable(self, name: str) -> bool:
        """Enable an adapter. Returns False if not registered."""
        name = name.lower()
        if name not in self._adapters:
            return False
        self._enabled[name] = True
        logger.info("adapter_enabled | name=%s", name)
        return True

    def disable(self, name: str) -> bool:
        """Disable an adapter. Returns False if not registered."""
        name = name.lower()
        if name not in self._adapters:
            return False
        self._enabled[name] = False
        logger.info("adapter_disabled | name=%s", name)
        return True

    # ── Bulk operations ───────────────────────────────────────

    def list_adapters(self) -> list[dict[str, Any]]:
        """Return summary of all registered adapters."""
        result = []
        for name in sorted(self._adapters):
            result.append(
                {
                    "name": name,
                    "compliance": self._compliance.get(name, ComplianceMode.DISABLED).value,
                    "enabled": self._enabled.get(name, False),
                    "identity_mode": self._adapters[name].identity_mode,
                    "capabilities": self._adapters[name].capabilities,
                }
            )
        return result

    def available_names(self) -> list[str]:
        """Names of all enabled adapters."""
        return sorted(n for n in self._adapters if self._enabled.get(n, False))

    def apply_config(self, config: dict[str, Any]) -> None:
        """
        Apply adapter configuration from NAVIG settings.

        Expected config structure::

            adapters:
              sms:
                enabled: true
              whatsapp_cloud:
                enabled: true
              wa_web:
                enabled: false
              discord:
                enabled: true
        """
        for name, settings in config.items():
            name = name.lower()
            if not isinstance(settings, dict):
                continue
            if "enabled" in settings:
                if settings["enabled"]:
                    self.enable(name)
                else:
                    self.disable(name)


# ── Singleton ─────────────────────────────────────────────────

_registry: AdapterRegistryManager | None = None


def get_adapter_registry() -> AdapterRegistryManager:
    """Return the global :class:`AdapterRegistryManager` singleton."""
    global _registry
    if _registry is None:
        _registry = AdapterRegistryManager()
    return _registry
