"""Shared outbound-send flow: resolve a target to an adapter and deliver.

The single seam behind both `navig dispatch send` (the CLI —
`navig.commands.dispatch.dispatch_send`) and the deck
`POST /api/deck/messages/send` route, so routing resolution and delivery
tracking behave identically no matter which surface sends the message.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from navig.messaging.adapter import DeliveryReceipt, RoutingDecision


class AdapterUnavailableError(RuntimeError):
    """The routing decision resolved to an adapter that isn't registered/enabled."""

    def __init__(self, adapter_name: str):
        self.adapter_name = adapter_name
        super().__init__(f"Adapter '{adapter_name}' is not available.")


async def route_and_send(
    target: str, message: str, *, network: str | None = None
) -> "tuple[RoutingDecision, DeliveryReceipt]":
    """Resolve *target* → adapter, send *message*, and record the delivery.

    Args:
        target: contact alias (``@alice``) or ``network:address`` (``sms:+1234``).
        message: the text to send.
        network: force a network (``sms``/``whatsapp``/``discord``/``telegram``);
            ``None`` lets the routing engine choose.

    Returns:
        ``(decision, receipt)`` — the resolved :class:`RoutingDecision` and the
        adapter's :class:`DeliveryReceipt`.

    Raises:
        navig.messaging.routing.NoRouteError: *target* cannot be routed.
        AdapterUnavailableError: the resolved adapter isn't registered/enabled.
    """
    from navig.messaging.adapter_registry import get_adapter_registry
    from navig.messaging.delivery import get_delivery_tracker
    from navig.messaging.routing import RoutingEngine
    from navig.store.contacts import get_contact_store
    from navig.store.threads import get_thread_store

    registry = get_adapter_registry()
    engine = RoutingEngine(get_contact_store(), get_thread_store(), registry)
    decision = engine.resolve(target, network=network)  # may raise NoRouteError

    adapter = registry.get(decision.adapter_name)
    if adapter is None:
        raise AdapterUnavailableError(decision.adapter_name)

    tracker = get_delivery_tracker()
    delivery_id = tracker.record_send(
        adapter=decision.adapter_name,
        target=decision.resolved_target.address,
        contact_alias=decision.resolved_target.display_hint or None,
        compliance=decision.compliance_mode,
    )
    thread = await adapter.get_or_create_thread(
        f"{decision.adapter_name}:{decision.resolved_target.address}"
    )
    receipt = await adapter.send_message(thread.remote_conversation_id, message)
    tracker.apply_receipt(delivery_id, receipt)
    return decision, receipt
