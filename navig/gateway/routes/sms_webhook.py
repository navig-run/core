"""Inbound SMS webhook: POST /sms/webhook  (alias /sms/inbound)

Twilio (and Vonage) POST inbound SMS here, form-encoded. We surface each message
through the notification router as an ``sms_inbound`` event, so it:
  - shows up in the deck (bell + Inbox + toast) — your in-app SMS view, and
  - forwards to every channel you enabled for incoming SMS (Telegram, email, …)
    in Settings → Notifications.
It also touches the SMS thread store so the Messages thread list reflects it.

**Two-way (text NAVIG, get an answer):** a text from a number listed in
``sms.allowed_numbers`` is answered by the assistant, and the answer is texted back.
Both conditions are required, every time:

  * the sender is on the allowlist (deny by default — an SMS From number is only a
    claim, so the list is the operator saying "these numbers are me"), AND
  * the request carries a VALID Twilio signature, checked against the stored
    ``twilio_auth_token`` whether or not ``sms.verify_signature`` is on — a From
    number is trivially forged without it.

The assistant is reached with ``allow_cli=False``: a text starting with ``navig …``
goes to the assistant (whose tools ask for approval), never straight to the CLI.
The reply is sent after the webhook returns, because Twilio gives up on a webhook
after 15 s and an assistant turn can take longer.

Configure in the Twilio console:
  Messaging Service → Integration → "Send a webhook" / inbound request URL →
    https://<your-navig-tunnel>/sms/webhook   (HTTP POST)
  or, with Lighthouse, the edge URL ``navig lighthouse status`` prints
  (``<edge>/sms/<tenant>``).
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

#: One reply is at most this many SMS segments' worth of text. Every segment is billed,
#: and a runaway answer should not cost the operator dozens of messages.
_REPLY_CHUNK = 1500
_REPLY_MAX_CHUNKS = 3


def register(app: "web.Application", gateway: "NavigGateway") -> None:
    """Register the inbound SMS webhook (public — Twilio posts here)."""
    handler = _make_handler(gateway)
    app.router.add_post("/sms/webhook", handler)
    app.router.add_post("/sms/inbound", handler)


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
        from navig.core.coerce import coerce_bool

        # coerce_bool tolerates the string `navig config set sms.verify_signature true`
        # stores; default off so intake never breaks before the operator opts in.
        enabled = coerce_bool(get_config_manager().get("sms.verify_signature"), default=False)
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

    params = {k: str(v) for k, v in form.items()}
    return verify_twilio_signature(_signed_url(request), params, sig, token)


def _signed_url(request: "web.Request") -> str:
    """The PUBLIC URL Twilio signed — which is not the URL this request arrived on.

    * Behind cloudflared / a user domain the request is proxied, and the public host
      arrives in forwarded headers.
    * Behind Lighthouse the edge received ``<edge>/sms/<tenant>`` and the uplink
      replays it as a loopback POST to ``/sms/webhook``, with no forwarded host at
      all. Rebuilding from that request gave ``http://127.0.0.1:…/sms/webhook``, so
      every signature check failed there. The uplink marks those requests
      ``X-Navig-Via: lighthouse``; for them the signed URL is the tenant URL.
    """
    if request.headers.get("X-Navig-Via", "").lower() == "lighthouse":
        url = _lighthouse_sms_url()
        if url:
            return url
    proto = request.headers.get("X-Forwarded-Proto") or request.scheme
    host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host") or request.host
    return f"{proto}://{host}{request.path_qs}"


def _lighthouse_sms_url() -> str | None:
    """``<edge>/sms/<sha256(deck.api_key)>`` from FRESH config (the key can rotate)."""
    try:
        from navig.cloud.uplink import api_key_hash
        from navig.config import get_config_manager

        cm = get_config_manager()
        cm.refresh_global_config()
        edge = str(cm.get("cloud.lighthouse_url") or "").strip().rstrip("/")
        key = str(cm.get("deck.api_key") or "").strip()
    except Exception:  # noqa: BLE001
        return None
    return f"{edge}/sms/{api_key_hash(key)}" if edge and key else None


def _signature_valid(request: "web.Request", form: dict) -> bool:
    """A VALID Twilio signature, regardless of ``sms.verify_signature``.

    That toggle governs whether unsigned texts are accepted into the inbox. Letting a
    text drive the assistant is a different bar: it always needs proof the text came
    from Twilio, because the From number alone proves nothing.
    """
    sig = request.headers.get("X-Twilio-Signature", "")
    if not sig:
        return False
    try:
        from navig.vault import get_vault

        token = (get_vault().get_secret("twilio_auth_token").reveal() or "").strip()
    except Exception:  # noqa: BLE001
        return False
    if not token:
        return False
    from navig.webhooks.signatures import verify_twilio_signature

    params = {k: str(v) for k, v in form.items()}
    return verify_twilio_signature(_signed_url(request), params, sig, token)


def _normalize_number(raw: str) -> str:
    """``+1 (555) 010-0100`` → ``+15550100100``; comparison form for the allowlist."""
    raw = str(raw or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    return ("+" if raw.startswith("+") else "") + digits


def _allowed_numbers() -> set[str]:
    """``sms.allowed_numbers`` as normalized numbers. A list, or the comma-separated
    string ``navig config set sms.allowed_numbers "+1555…,+1555…"`` stores."""
    try:
        from navig.config import get_config_manager

        raw = get_config_manager().get("sms.allowed_numbers")
    except Exception:  # noqa: BLE001
        return set()
    items = raw if isinstance(raw, (list, tuple)) else str(raw or "").split(",")
    return {n for n in (_normalize_number(x) for x in items) if len(n) > 1}


def _reply_chunks(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    chunks = [text[i: i + _REPLY_CHUNK] for i in range(0, len(text), _REPLY_CHUNK)]
    if len(chunks) > _REPLY_MAX_CHUNKS:
        chunks = chunks[:_REPLY_MAX_CHUNKS]
        chunks[-1] = chunks[-1][: _REPLY_CHUNK - 30].rstrip() + " …(full answer on the deck)"
    return chunks


async def _answer(gateway: "NavigGateway", sender: str, body: str) -> None:
    """Run the assistant on the text and text the answer back. Never raises."""
    try:
        reply = await gateway.router.route_message(
            channel="sms",
            user_id=sender,
            message=body,
            metadata={"username": sender, "source": "sms"},
            allow_cli=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("sms agent turn failed for %s: %s", sender[-4:], exc)
        reply = "Sorry, I couldn't process that just now. Try again, or check the deck."

    try:
        from navig.messaging.adapter_registry import get_adapter_registry

        adapter = get_adapter_registry().get("sms")
        if adapter is None:
            logger.warning("sms reply dropped: no usable SMS adapter (adapters.sms)")
            return
        for chunk in _reply_chunks(str(reply or "")):
            receipt = await adapter.send_message(sender, chunk)
            if not getattr(receipt, "ok", False):
                logger.warning("sms reply failed: %s", getattr(receipt, "error", "unknown"))
                return
    except Exception:  # noqa: BLE001
        logger.warning("sms reply failed", exc_info=True)


def _make_handler(gateway: "NavigGateway"):
    async def handler(request: "web.Request") -> "web.Response":
        return await _handle(request, gateway)

    return handler


async def _handle(request: "web.Request", gateway: "NavigGateway | None") -> "web.Response":
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

    to_agent = False
    if gateway is not None and body and getattr(gateway, "router", None) is not None:
        if _normalize_number(sender) in _allowed_numbers():
            if _signature_valid(request, form):
                to_agent = True
            else:
                logger.warning(
                    "sms from an allowed number NOT answered: no valid Twilio signature "
                    "(store 'twilio_auth_token' in the vault and check the webhook URL)"
                )

    # Surface + forward via the notification router (deck + matrix-enabled channels).
    # A text the assistant is answering is the operator talking to NAVIG, not news to
    # forward back to them — so it is recorded in the thread store below but not pushed.
    if not to_agent:
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

    if to_agent:
        from navig.core.background import spawn

        spawn(_answer(gateway, sender, body), name="sms-reply")

    # Empty TwiML = accept now; any answer goes out separately via the REST API.
    return web.Response(text=_TWIML_OK, content_type="application/xml")
