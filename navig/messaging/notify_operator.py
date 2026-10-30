"""notify_operator — one call to DM the operator on Telegram from ANY process.

The notify router (``navig.notify``) fans out through channel objects that only
exist inside the running gateway; from a plain CLI or a cron subprocess its
Telegram branch reports "not configured". This helper is the process-agnostic
path the habit/body check-ins already use: resolve the operator's chat, call the
Bot API directly. Plugins (email, paperwork) share it so a notification never
depends on which process happens to run the command.

Text is sent as Telegram HTML by default — escape untrusted fragments with
:func:`escape_html` before interpolating them.
"""

from __future__ import annotations

import html
import logging
from typing import Any

logger = logging.getLogger("navig.messaging.notify_operator")

# Telegram rejects messages longer than this (Bot API sendMessage limit).
TELEGRAM_TEXT_LIMIT = 4096


def escape_html(text: str) -> str:
    """Escape ``& < >`` for Telegram's HTML parse mode (quotes are fine there)."""
    return html.escape(str(text or ""), quote=False)


def resolve_operator_chat_id() -> str | None:
    """The operator's Telegram chat id, or ``None`` when nothing is configured.

    Order: ``telegram.allowed_users[0]`` in the global config (the same rule
    the gateway uses for its boot message), then the vault/env owner UID.
    """
    try:
        from navig.config import get_config_manager

        cfg = get_config_manager().global_config or {}
        users = (cfg.get("telegram") or {}).get("allowed_users") or []
        if users:
            return str(users[0]).strip()
    except Exception:  # noqa: BLE001 — config unreadable → fall through to the UID
        pass
    try:
        from navig.messaging.secrets import resolve_telegram_uid

        uid = resolve_telegram_uid()
        return str(uid).strip() if uid else None
    except Exception:  # noqa: BLE001
        return None


def truncate_for_telegram(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    marker = "\n…"
    return text[: limit - len(marker)] + marker


def _send_rich(target: str, html_text: str, *, keyboard=None, silent: bool = False) -> bool:
    """``sendRichMessage`` straight through the Bot API — the card path.

    Returns False (never raises) so the caller can fall back to the plain body:
    a Bot API server without rich messages answers "unknown method", and the
    notice still has to arrive.
    """
    from navig.telegram.api import api_call, load_telegram_token

    payload: dict = {"chat_id": target, "rich_message": {"html": html_text}}
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    if silent:
        payload["disable_notification"] = True
    try:
        api_call(load_telegram_token(), "sendRichMessage", payload)
        return True
    except Exception as exc:  # noqa: BLE001 — see the fallback in notify_operator
        logger.debug("notify_operator: rich send refused (%s), falling back to text", exc)
        return False


def notify_operator(
    text: str,
    *,
    parse_mode: str = "HTML",
    chat_id: str | None = None,
    keyboard: list[list[dict]] | None = None,
    silent: bool = False,
    card: Any = None,
) -> bool:
    """Send *text* to the operator. Returns True only when the Bot API accepted it.

    ``silent`` sends with ``disable_notification`` — it arrives without a sound.

    ``card`` (a :class:`navig.telegram.card_doc.Card`, when the caller builds one)
    is sent as a rich message — headings, tables, in-bubble buttons — and falls
    back to the card's own classic projection, then to *text*, so a server
    without rich messages still delivers the notice.

    Never raises: a notification is a side effect of some other job, and that
    job's own result must not be masked by a Telegram hiccup. The failure is
    logged at WARNING so it is visible in the cron/gateway log.
    """
    target = (chat_id or resolve_operator_chat_id() or "").strip()
    if not target:
        logger.warning(
            "notify_operator: no Telegram chat configured (telegram.allowed_users / NAVIG_TELEGRAM_UID)"
        )
        return False
    if card is not None:
        try:
            rich = card.rich_html()
        except Exception as exc:  # noqa: BLE001 — a broken card must not swallow the notice
            logger.warning("notify_operator: card did not render (%s), sending text", exc)
            rich = ""
        if rich and _send_rich(target, rich, keyboard=keyboard, silent=silent):
            return True
        try:
            text, parse_mode = card.classic_html(), "HTML"
        except Exception:  # noqa: BLE001 — keep the caller's text
            pass
    if not text or not text.strip():
        return False
    try:
        from navig.telegram.api import telegram_send

        # Each option is passed only when it is set: a plain ringing send keeps the
        # exact call shape every existing caller (and test double) of telegram_send
        # expects.
        extra: dict = {"reply_markup": {"inline_keyboard": keyboard}} if keyboard else {}
        if silent:
            extra["disable_notification"] = True
        telegram_send(
            target=target,
            message=truncate_for_telegram(text),
            parse_mode=parse_mode,
            **extra,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — see docstring
        logger.warning("notify_operator: Telegram send failed: %s", exc)
        return False
