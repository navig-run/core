"""Email delivery for notifications — reuses the existing Gmail OAuth connector
(`gmail.send` scope). No separate SMTP adapter; email works once the user
connects Gmail in Settings → Connectors.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("navig.notify")


async def send_email(to: str, subject: str, body: str) -> tuple[bool, str]:
    """Send an email via the Gmail connector. Returns (ok, detail)."""
    if not to:
        return False, "no recipient configured"
    try:
        from navig.connectors.bootstrap import ensure_connectors_loaded
        from navig.connectors.registry import get_connector_registry
        from navig.connectors.auth_manager import ConnectorAuthManager
        from navig.connectors.types import Action, ActionType

        ensure_connectors_loaded()
        auth = ConnectorAuthManager()
        if not auth.is_connected("gmail"):
            return False, "Gmail not connected (Settings → Connectors)"

        obj = get_connector_registry().get("gmail")
        connector = obj() if isinstance(obj, type) else obj
        if connector is None:
            return False, "Gmail connector unavailable"

        if not await auth.inject_token(connector):
            return False, "Gmail token unavailable (reconnect)"

        result = await connector.act(
            Action(action_type=ActionType.SEND, params={"to": to, "subject": subject, "body": body})
        )
        if getattr(result, "success", False):
            return True, "sent"
        return False, getattr(result, "error", None) or "send failed"
    except Exception as exc:  # noqa: BLE001 — email must never crash a dispatch
        logger.debug("notify email send failed: %s", exc)
        return False, str(exc)
