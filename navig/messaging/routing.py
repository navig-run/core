"""
RoutingEngine — Deterministic 5-step resolution for outbound messages.

Resolution order:
  1. Explicit ``network:address`` → direct route
  2. ``@alias`` + explicit ``network`` → contact's matching route
  3. ``@alias`` default_network → contact's default network route
  4. ``@alias`` first route by priority → highest-priority available route
  5. Fallback chain → contact's fallback list

Raises :class:`AmbiguityError` when no deterministic route can be resolved.

Every route resolution is logged for compliance when ``compliance_mode``
is ``ComplianceMode.OFFICIAL``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from navig.messaging.adapter import (
    Contact,
    ResolvedTarget,
    Route,
    RoutingDecision,
)

if TYPE_CHECKING:
    from navig.messaging.adapter_registry import AdapterRegistryManager
    from navig.store.contacts import ContactStore
    from navig.store.threads import ThreadStore

logger = logging.getLogger(__name__)


class AmbiguityError(ValueError):
    """Routing could not determine a single target."""

    def __init__(self, alias: str, candidates: list[str] | None = None):
        self.alias = alias
        self.candidates = candidates or []
        cands = ", ".join(self.candidates) or "none"
        super().__init__(
            f"Ambiguous route for @{alias}: candidates=[{cands}]. "
            "Specify network explicitly (e.g. /send @alice whatsapp hello)."
        )


class NoRouteError(ValueError):
    """No route found for the given target."""

    def __init__(self, target: str, reason: str = ""):
        self.target = target
        super().__init__(f"No route for {target!r}. {reason}".strip())


class RoutingEngine:
    """
    Deterministic message routing with compliance logging.

    Usage::

        engine = RoutingEngine(contact_store, thread_store, adapter_registry)
        decision = engine.resolve("@alice", network="whatsapp")
        # decision.adapter_name == "whatsapp"
        # decision.resolved_target.address == "+33612345678"
    """

    def __init__(
        self,
        contact_store: ContactStore,
        thread_store: ThreadStore,
        adapter_registry: AdapterRegistryManager,
    ):
        self._contacts = contact_store
        self._threads = thread_store
        self._adapters = adapter_registry

    def resolve(
        self,
        target: str,
        *,
        network: str | None = None,
    ) -> RoutingDecision:
        """
        5-step deterministic route resolution.

        Parameters
        ----------
        target
            Either ``"network:address"`` (direct) or ``"@alias"``/``"alias"``.
        network
            Optional explicit network override when targeting an alias.

        Returns
        -------
        RoutingDecision
            The resolved adapter, target, and compliance classification.
        """
        target = target.strip()

        # ── Step 1: Explicit network:address ──────────────────
        if ":" in target and not target.startswith("@"):
            net, _, addr = target.partition(":")
            net = net.strip().lower()
            addr = addr.strip()
            return self._build_decision(net, addr)

        # ── Steps 2–5: Alias resolution ───────────────────────
        alias = target.lstrip("@").strip()
        contact = self._contacts.resolve_alias(alias)
        if contact is None:
            raise NoRouteError(alias, "Contact not found.")

        # Step 2: alias + explicit network
        if network:
            route = self._find_route(contact, network)
            if route:
                return self._build_decision(route.network, route.address, contact=contact)
            raise NoRouteError(f"@{alias}", f"No {network!r} route configured.")

        # Step 3: alias → default_network
        if contact.default_network:
            route = self._find_route(contact, contact.default_network)
            if route:
                return self._build_decision(route.network, route.address, contact=contact)

        # Step 4: first route by priority
        available = self._available_routes(contact)
        if len(available) == 1:
            r = available[0]
            return self._build_decision(r.network, r.address, contact=contact)
        if len(available) > 1:
            raise AmbiguityError(
                alias,
                [f"{r.network}:{r.address}" for r in available],
            )

        # Step 5: fallback chain
        for fb_str in contact.fallbacks:
            if ":" not in fb_str:
                continue
            net, _, addr = fb_str.partition(":")
            net = net.strip().lower()
            addr = addr.strip()
            if self._adapters.is_available(net):
                return self._build_decision(net, addr, contact=contact)

        raise NoRouteError(f"@{alias}", "No available routes or fallbacks.")

    # ── Internal helpers ──────────────────────────────────────

    def _find_route(self, contact: Contact, network: str) -> Route | None:
        """Find the first route matching ``network`` (case-insensitive)."""
        network_lower = network.lower()
        for route in contact.routes:
            if route.network.lower() == network_lower:
                return route
        return None

    def _available_routes(self, contact: Contact) -> list[Route]:
        """Filter contact routes to those with an available adapter."""
        return [r for r in contact.routes if self._adapters.is_available(r.network)]

    def _build_decision(
        self,
        network: str,
        address: str,
        *,
        contact: Contact | None = None,
    ) -> RoutingDecision:
        """Build a :class:`RoutingDecision` and log compliance."""
        adapter_name = network.lower()
        compliance = self._adapters.get_compliance(adapter_name)

        decision = RoutingDecision(
            adapter_name=adapter_name,
            resolved_target=ResolvedTarget(
                adapter=adapter_name,
                address=address,
                display_hint=contact.display_name if contact else "",
            ),
            compliance_mode=compliance,
        )

        self._log_routing(decision, contact)
        return decision

    def _log_routing(
        self,
        decision: RoutingDecision,
        contact: Contact | None = None,
    ) -> None:
        """Compliance log every routing decision."""
        alias = contact.alias if contact else "(direct)"
        logger.info(
            "route_resolved | target=%s | adapter=%s | compliance=%s | address=%s",
            alias,
            decision.adapter_name,
            decision.compliance_mode.value,
            decision.resolved_target.address,
        )
