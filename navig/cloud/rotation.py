"""Every derivative of ``deck.api_key`` — and what rotating it breaks.

``deck.api_key`` is not just a bearer token: its **sha256 is the Lighthouse tenant**
(the Durable Object the edge routes inbound traffic to), and its raw value is embedded
in the Mini App entry link. So a rotation silently moves the bot's mailbox, and every
*stored* derivative left behind keeps addressing a tenant nothing is attached to.

The gateway rotates the key **on its own** (mints one when missing; force-upgrades one
under 16 chars — ``gateway/deck/__init__.py``), and ``navig cloud key --rotate`` does
too. This module is the single inventory of what that breaks:

===============================  ==========================  ====================
derivative                       stored where                on rotation
===============================  ==========================  ====================
``/tg/<hash>``   webhook         config + Telegram           bot goes 100% deaf
``/connect?key=`` Mini App btn   **on Telegram**             deck can't reach brain
``/ingest/<hash>/<src>``         **on the user's website**   events silently dropped
``/sms/<hash>``                  **in the Twilio console**   inbound SMS dead
broker registration              api.navig.run               re-sent on gateway start
===============================  ==========================  ====================

The first two we own and can repair automatically (:func:`repoint_owned`). The next two
live in systems we cannot reach, so the only honest thing is to hand the operator the
new URLs and say so plainly (:func:`manual_repoint_urls`) — never let them believe a
rotation was clean when their website is quietly dropping events.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _cfg(cfg=None):
    if cfg is not None:
        return cfg
    from navig.core import Config

    return Config()


def _truthy(v) -> bool:
    """Tolerant bool for the ``navig config set`` raw-string gotcha — delegates to the
    canonical :func:`navig.core.coerce.coerce_bool` (one shared truth table)."""
    from navig.core.coerce import coerce_bool

    return coerce_bool(v, default=False)


def _lighthouse(cfg) -> tuple[str, str]:
    """(edge_url, api_key) — both empty strings when lighthouse isn't in play."""
    if str(cfg.get("cloud.mode", "") or "").lower() != "lighthouse":
        return "", ""
    edge = str(cfg.get("cloud.lighthouse_url", "") or "").strip().rstrip("/")
    key = str(cfg.get("deck.api_key", "") or "").strip()
    return (edge, key) if edge and key else ("", "")


# ── things we own: repair them ───────────────────────────────────────────────


def repoint_owned(cfg=None) -> tuple[list[str], list[str]]:
    """Re-point every derivative WE control at the current ``deck.api_key``.

    Returns ``(fixed, failed)`` as human-readable labels. Best-effort throughout: a
    rotation must never hard-fail because Telegram is briefly unreachable — the
    gateway also self-heals the webhook tenant on its next start.
    """
    cfg = _cfg(cfg)
    fixed: list[str] = []
    failed: list[str] = []

    edge, _key = _lighthouse(cfg)
    if not edge:
        return fixed, failed  # not lighthouse — nothing of ours is tenant-bound

    # 1. The Telegram webhook — without this the bot receives NOTHING.
    try:
        from navig.commands.lighthouse import configure_telegram_webhook

        if configure_telegram_webhook(edge):
            fixed.append("Telegram webhook")
    except Exception as exc:  # noqa: BLE001
        logger.debug("webhook repoint failed", exc_info=True)
        failed.append(f"Telegram webhook ({exc})")

    # 2. The Mini App menu button — it embeds the RAW key, so a rotation leaves the
    #    deck seeding a dead key into localStorage (blank screen / 401).
    try:
        if repoint_menu_button(cfg):
            fixed.append("Mini App menu button")
    except Exception as exc:  # noqa: BLE001
        logger.debug("menu button repoint failed", exc_info=True)
        failed.append(f"Mini App menu button ({exc})")

    return fixed, failed


def menu_button_url(cfg=None) -> str:
    """The Mini App entry URL for the CURRENT key, or "" when no deck URL is known.

    Built from *cfg* only — no hidden reads of global config, so a caller (and a test)
    gets exactly the URL for the config it passed. Format is pinned to
    ``miniapp._connect_url`` by test_menu_button_url_matches_miniapp_format.
    """
    cfg = _cfg(cfg)
    from urllib.parse import quote

    base = str(cfg.get("deck.public_url", "") or "").strip().rstrip("/")
    if not base:
        base = str(cfg.get("cloud.public_url", "") or "").strip().rstrip("/")
    key = str(cfg.get("deck.api_key", "") or "").strip()
    if not base or not key:
        return ""
    return f"{base}/connect?key={quote(key, safe='')}"


def repoint_menu_button(cfg=None) -> bool:
    """Re-register the bot's Mini App button with the current key. False when N/A.

    A no-op (returns False) when there is no bot token or no deck URL — a user who
    never deployed the deck has no button to fix.
    """
    cfg = _cfg(cfg)
    url = menu_button_url(cfg)
    if not url:
        return False

    from navig.commands.miniapp import _bot_token, _tg_call

    token = _bot_token()
    if not token:
        return False

    result = _tg_call(
        token,
        "setChatMenuButton",
        {"menu_button": {"type": "web_app", "text": "NAVIG Deck", "web_app": {"url": url}}},
    )
    return bool(result.get("ok"))


# ── things we do NOT own: tell the operator, precisely ───────────────────────


def manual_repoint_urls(cfg=None) -> list[tuple[str, str]]:
    """``[(where_it_lives, new_url)]`` the operator must update by hand.

    These URLs were pasted into systems we cannot reach — a website's event snippet,
    a Twilio console. Rotation orphans them silently: the edge keeps accepting the
    POST and queues it for a tenant that will never connect, so nothing errors and
    the data is simply gone. Empty list when nothing of the sort is configured.
    """
    cfg = _cfg(cfg)
    edge, key = _lighthouse(cfg)
    if not edge:
        return []

    from navig.cloud import api_key_hash

    tenant = api_key_hash(key)
    out: list[tuple[str, str]] = []

    # Signals: the ingest URL lives in the USER'S WEBSITE.
    try:
        from navig.notify import signals

        for row in signals.list_sources() or []:
            name = (row.get("name") or "").strip()
            if name:
                out.append((f"website ingest '{name}'", f"{edge}/ingest/{tenant}/{name}"))
    except Exception:  # noqa: BLE001 — signals may never have been configured
        logger.debug("signals sources unavailable", exc_info=True)

    # SMS: the inbound webhook lives in the Twilio (or equivalent) console.
    try:
        sms = cfg.get("adapters.sms")
        enabled = sms.get("enabled") if isinstance(sms, dict) else cfg.get("sms.enabled")
        if _truthy(enabled):
            out.append(("SMS provider console", f"{edge}/sms/{tenant}"))
    except Exception:  # noqa: BLE001
        logger.debug("sms config unreadable", exc_info=True)

    return out
