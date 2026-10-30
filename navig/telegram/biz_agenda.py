"""Propose an appointment the OWNER wrote in a Business chat — in their own DM.

"See you Monday 26 Oct at 10:30" typed to a client is a plan the operator just made,
and the Planner should know about it. But a Business chat is someone else's
conversation too, so this is deliberately narrow:

* only the OWNER's own messages — a contact's text is never captured;
* only PROPOSED (origin ``ai``) — it pings nothing until accepted;
* the proposal goes to the owner's DM (Accept / Dismiss via ``ag:``), never into the
  chat;
* opt-in: ``telegram.business.agenda_capture`` (default off).

The dedup key is the message (``tgb:<chat>:<message>``), so an edited or re-delivered
update never proposes twice, and a dismissed one never comes back.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def enabled() -> bool:
    try:
        from navig.config import get_config_manager  # noqa: PLC0415
        from navig.core.coerce import coerce_bool  # noqa: PLC0415

        return coerce_bool(
            get_config_manager().get("telegram.business.agenda_capture", False), default=False
        )
    except Exception:  # noqa: BLE001
        return False


def _peer(msg: dict[str, Any]) -> str:
    chat = msg.get("chat") or {}
    name = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x)
    return name or chat.get("username") or ""


async def maybe_propose(channel: Any, msg: dict[str, Any], *, owner_id: int) -> bool:
    """True when a proposal was sent. Never raises."""
    if not enabled():
        return False
    from navig.pim import service  # noqa: PLC0415
    from navig.pim.clock import local_now  # noqa: PLC0415
    from navig.telegram import agenda_actions, appointments  # noqa: PLC0415

    text = (msg.get("text") or "").strip()
    hit = appointments.detect(text, local_now())
    if hit is None:
        return False
    when, title = hit
    chat_id = (msg.get("chat") or {}).get("id")
    peer = _peer(msg)
    try:
        item = service.create_appointment(
            title,
            when,
            with_whom=peer or None,
            notes=text,
            leads=appointments.DEFAULT_LEADS,
            origin="ai",
            origin_ref=f"tgb:{chat_id}:{msg.get('message_id')}",
        )
    except service.PlannerError:
        return False
    if item is None:
        return False  # already proposed (or dismissed) for this very message
    rendered = agenda_actions.build_detail(item["id"])
    if rendered is None:
        return False
    body, keyboard = rendered
    try:
        sent = await channel._api_call(
            "sendMessage",
            {"chat_id": owner_id, "text": body, "parse_mode": "HTML", "reply_markup": keyboard},
        )
    except Exception:  # noqa: BLE001 — treated like a refusal: roll back below
        logger.debug("agenda proposal send failed", exc_info=True)
        sent = None
    if not sent:
        # `_api_call` refuses with None. The proposal would sit unseen, and its
        # origin_ref would turn every redelivery into a "duplicate" — roll it back
        # WITHOUT a dismissal so the next delivery can offer it again.
        try:
            service.delete(item["id"], dismiss=False)
        except Exception:  # noqa: BLE001
            logger.warning("agenda proposal rollback failed for %s", item.get("id"), exc_info=True)
        return False
    return True
