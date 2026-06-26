"""Inbound SMS webhook: POST /sms/webhook  (alias /sms/inbound)

Twilio (and Vonage) POST inbound SMS here, form-encoded. We surface each message
through the notification router as an ``sms_inbound`` event, so it:
  - shows up in the deck (bell + Inbox + toast) — your in-app SMS view, and
  - forwards to every channel you enabled for incoming SMS (Telegram, email, …)
    in Settings → Notifications.
It also touches the SMS thread store so the Messages thread list reflects it.

Configure in the Twilio console:
  Messaging Service → Integration → "Send a webhook" / inbound request URL →
    https://<your-navig-tunnel>/sms/webhook   (HTTP POST)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import web
    from navig.gateway.server import NavigGateway  # noqa: F401

try:
    from aiohttp import web
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("aiohttp is required for gateway routes") from exc

logger = logging.getLogger("navig.notify")

_TWIML_OK = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def register(app: "web.Application", gateway: "NavigGateway") -> None:
    """Register the inbound SMS webhook (public — Twilio posts here)."""
    app.router.add_post("/sms/webhook", _handler)
    app.router.add_post("/sms/inbound", _handler)


def _account_sid_ok(form: dict) -> bool:
    """Light anti-spoof: if Twilio sent AccountSid, require it to match ours."""
    import hmac

    incoming = (form.get("AccountSid") or "").strip()
    if not incoming:
        return True  # nothing to check (Vonage / older payloads)
    try:
        from navig.vault import get_vault

        stored = (get_vault().get_secret("twilio_account_sid").reveal() or "").strip()
        return not stored or hmac.compare_digest(stored, incoming)
    except Exception:
        return True  # never block delivery on a vault read hiccup


def _signature_ok(request: "web.Request", form: dict) -> bool:
    """Verify Twilio's X-Twilio-Signature when ``sms.verify_signature`` is enabled (P-F).

    **Opt-in** (default off) so it never breaks intake until the operator has
    stored ``twilio_auth_token`` and confirmed the public webhook URL. When on,
    a missing token or signature fails closed.
    """
    try:
        from navig.config import get_config_manager

        raw = get_config_manager().get("sms.verify_signature")
        enabled = str(raw).strip().lower() in ("1", "true", "yes", "on")
    except Exception:  # noqa: BLE001
        enabled = False
    if not enabled:
        return True  # default: no enforcement

    sig = request.headers.get("X-Twilio-Signature", "")
    if not sig:
        logger.warning("inbound sms rejected: missing X-Twilio-Signature (verification on)")
        return False
    try:
        from navig.vault import get_vault

        token = (get_vault().get_secret("twilio_auth_token").reveal() or "").strip()
    except Exception:  # noqa: BLE001
        token = ""
    if not token:
        logger.warning("sms.verify_signature is on but no 'twilio_auth_token' in vault — rejecting")
        return False

    from navig.webhooks.signatures import verify_twilio_signature

    # Reconstruct the PUBLIC URL Twilio signed (behind cloudflared/lighthouse the
    # request is loopback; the public host arrives in forwarded headers).
    proto = request.headers.get("X-Forwarded-Proto") or request.scheme
    host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host") or request.host
    url = f"{proto}://{host}{request.path_qs}"
    params = {k: str(v) for k, v in form.items()}
    return verify_twilio_signature(url, params, sig, token)


async def _handler(request: "web.Request") -> "web.Response":
    try:
        form = dict(await request.post())
    except Exception:
        form = {}

    sender = str(form.get("From") or form.get("msisdn") or "").strip()
    body = str(form.get("Body") or form.get("text") or "").strip()

    if not (sender or body):
        return web.Response(text=_TWIML_OK, content_type="application/xml")
    if not _signature_ok(request, form):
        return web.Response(text=_TWIML_OK, content_type="application/xml")
    if not _account_sid_ok(form):
        logger.warning("inbound sms rejected: AccountSid mismatch")
        return web.Response(text=_TWIML_OK, content_type="application/xml")

    # Surface + forward via the notification router (deck + matrix-enabled channels).
    try:
        from navig.notify import dispatch as notify_dispatch

        await notify_dispatch(
            "sms_inbound",
            f"SMS from {sender or 'unknown'}",
            body,
            data={"from": sender, "channel": "sms"},
        )
    except Exception:
        logger.debug("inbound sms dispatch failed", exc_info=True)

    # Best-effort: record in the SMS thread store so Messages reflects it.
    try:
        from navig.messaging.adapter_registry import get_adapter_registry

        adapter = get_adapter_registry().get_unchecked("sms")
        if adapter is not None:
            event = await adapter.receive_webhook(form)
            await adapter.ingest_event(event)
    except Exception:
        logger.debug("inbound sms ingest failed", exc_info=True)

    # Empty TwiML = accept, no auto-reply.
    return web.Response(text=_TWIML_OK, content_type="application/xml")
