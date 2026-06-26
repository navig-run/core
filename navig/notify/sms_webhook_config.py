"""Auto-configure Twilio's inbound-SMS webhook to point at this daemon.

The problem: Twilio needs a PUBLIC URL to POST incoming SMS to, but the daemon
runs on a local PC. navig already exposes itself publicly via the cloud tunnel
(``*.trycloudflare.com``) or a user domain (``cloud.public_url``) — the same URL
the Deck uses to reach the daemon. We resolve that and PATCH the Twilio
Messaging Service's ``InboundRequestUrl`` to ``<public>/sms/webhook``.

Because quick-tunnel URLs rotate on restart, the notify scheduler re-runs
``auto_configure`` periodically, so Twilio always points at the current URL.
Set ``cloud.public_url`` to a stable domain to avoid rotation entirely.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("navig.notify")


def resolve_public_base(gateway) -> str | None:
    """The daemon's current public base URL (no trailing slash), or None."""
    # 1) Stable user domain (direct mode) wins.
    try:
        from navig.core import Config

        pub = (Config().get("cloud.public_url") or "").strip().rstrip("/")
        if pub:
            return pub
    except Exception:
        pass
    # 2) Live cloudflared tunnel URL.
    cm = getattr(gateway, "cloud_manager", None) if gateway is not None else None
    if cm is not None:
        try:
            snap = cm.snapshot()
            url = (snap.get("public_url") or snap.get("tunnel_url") or "").strip().rstrip("/")
            if url.startswith("http"):
                return url
        except Exception:
            pass
    return None


async def configure_twilio_inbound(base_url: str) -> tuple[bool, str]:
    """PATCH the Twilio Messaging Service inbound webhook → ``base_url/sms/webhook``.
    Returns (ok, webhook_url_or_error)."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return False, "no public base url"
    webhook = f"{base}/sms/webhook"

    try:
        from navig.vault import get_vault

        v = get_vault()
        sid = v.get_secret("twilio_account_sid").reveal()
        token = v.get_secret("twilio_auth_token").reveal()
        mgs = v.get_secret("twilio_messaging_service_sid").reveal()
    except Exception as exc:
        return False, f"twilio creds unavailable: {exc}"
    if not (sid and token and mgs):
        return False, "missing twilio account_sid / auth_token / messaging_service_sid"

    import httpx

    url = f"https://messaging.twilio.com/v1/Services/{mgs}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            resp = await c.post(
                url,
                auth=(sid, token),
                data={"InboundRequestUrl": webhook, "InboundMethod": "POST"},
            )
        if resp.status_code in (200, 201):
            return True, webhook
        return False, f"twilio HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


async def auto_configure(gateway, *, force: bool = False) -> dict:
    """Resolve the public URL and (re)point Twilio's inbound webhook at it.
    No-op when the URL is unchanged (unless ``force``)."""
    from navig.notify import prefs

    base = resolve_public_base(gateway)
    if not base:
        return {"ok": False, "reason": "no_public_url",
                "hint": "Enable cloud (Settings → Cloud) or set cloud.public_url to a stable domain."}
    webhook = f"{base}/sms/webhook"
    if not force and prefs.get_raw("sms_webhook_url") == webhook:
        return {"ok": True, "unchanged": True, "url": webhook, "base": base}

    ok, detail = await configure_twilio_inbound(base)
    if ok:
        prefs.set_setting("sms_webhook_url", detail)
        prefs.set_setting("sms_webhook_base", base)
        logger.info("Twilio inbound webhook set → %s", detail)
        return {"ok": True, "url": detail, "base": base}
    return {"ok": False, "error": detail, "base": base}
