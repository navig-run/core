"""Canonical Telegram update-type list + Lighthouse webhook-tenant derivation.

Two invariants live here, both of which failed silently in production:

**1. ONE allowed-updates list.** Telegram only sends the update types you ask for,
and ``allowed_updates`` is *sticky* — whatever the last ``setWebhook`` / ``getUpdates``
declared is what you keep getting. Business updates are NOT in Telegram's default
set, so any caller that forgets them turns the Business bot 100% deaf. This list is
therefore shared by every caller (the channel's long-poll, the channel's setWebhook,
and ``navig lighthouse`` deploy) instead of being retyped per site.

**2. The webhook tenant must track the live ``deck.api_key``.** In lighthouse mode the
webhook URL is ``<edge>/tg/<sha256(deck.api_key)>``. That hash is the Durable Object
the edge routes to, and the brain's uplink attaches to the DO for the *current* key.
``telegram.webhook_url`` is a stored string, so rotating ``deck.api_key`` orphans it:
Telegram keeps POSTing to the OLD DO, which has no socket, so it queues every update
and acks ``202 {"queued": true}`` — while the brain, attached to the NEW DO, happily
reports "uplink online". The bot goes completely deaf with every health signal green.
:func:`corrected_webhook_url` detects that split and returns the URL that fixes it.
"""

from __future__ import annotations

# Every update type NAVIG consumes. Keep in sync with the channel's _process_update.
ALLOWED_UPDATES: list[str] = [
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "callback_query",
    "inline_query",
    # Telegram Business (the owner's business-profile conversations). Not in
    # Telegram's default set — omit these and business_* updates never arrive.
    "business_connection",
    "business_message",
    "edited_business_message",
    "deleted_business_messages",
]


def webhook_url_for(edge_url: str, api_key: str) -> str:
    """The lighthouse webhook URL for *api_key*: ``<edge>/tg/<sha256(api_key)>``."""
    from navig.cloud import api_key_hash

    return f"{edge_url.rstrip('/')}/tg/{api_key_hash(api_key)}"


def webhook_tenant(url: str | None) -> str:
    """The tenant (DO id) segment of a ``/tg/<hash>`` webhook URL."""
    return (url or "").rstrip("/").rsplit("/", 1)[-1]


def corrected_webhook_url(webhook_url: str | None, cfg=None) -> str | None:
    """Return the URL that repairs a *stale-tenant* webhook, or ``None`` if fine.

    ``None`` means "leave it alone" — which covers every case we must not touch:
    not lighthouse mode, no stored URL, no ``deck.api_key``, a webhook pointing at
    a host that isn't our edge (a user's own domain / reverse proxy), or a URL whose
    tenant already matches the live key.
    """
    if not webhook_url:
        return None
    if cfg is None:
        from navig.core import Config

        cfg = Config()

    if str(cfg.get("cloud.mode", "") or "").lower() != "lighthouse":
        return None
    edge = str(cfg.get("cloud.lighthouse_url", "") or "").strip()
    api_key = str(cfg.get("deck.api_key", "") or "").strip()
    if not edge or not api_key:
        return None

    # Only ever rewrite a URL that is OUR edge's /tg/<hash> path. A custom webhook
    # host is the user's business, not ours.
    expected = webhook_url_for(edge, api_key)
    if not webhook_url.startswith(f"{edge.rstrip('/')}/tg/"):
        return None
    return None if webhook_url == expected else expected
