"""Deliver a :class:`~navig.telegram.card_doc.Card` to Telegram, rich or classic.

:mod:`navig.telegram.card_doc` builds one structure and projects it twice. This
module is the one place that decides which projection leaves the process, so no
card family has to re-implement the decision (and get it subtly wrong).

The rule is deliberately boring:

* a channel that advertises ``send_rich_message`` and has not been observed
  refusing rich messages gets the rich body, with the classic render attached as
  ``fallback_html`` — if the rich call fails inside the channel, Telegram already
  receives a body it can parse instead of rich-only tags it will reject;
* every other channel gets the classic ``sendMessage`` body with the same
  inline-keyboard rows attached.

Why the fallback body must travel with the rich one. ``sendRichMessage`` is
recent enough that a Bot API server can answer "unknown method" for it. The
channel then retries with ``sendMessage`` — and if that retry reuses the *rich*
HTML, Telegram rejects the whole message with a parse error (``<h1>``, ``<ul>``,
``<table>`` are not classic entities), so the card never arrives at all. Passing
the classic projection along turns a lost card into a layout downgrade.
"""

from __future__ import annotations

import hashlib
from typing import Any

from navig.telegram.card_doc import Card

__all__ = ["edit_card", "rich_available", "send_card"]


def rich_available(channel: Any) -> bool:
    """Whether *channel* should be asked for a rich message first.

    Three independent gates, each one a fact the channel keeps: the method
    exists, the operator has not switched rich off, and no earlier send has
    already been refused by the server (``_rich_supported is False`` is the
    channel's learned flag — asking again would cost a round trip per card).
    """
    if not hasattr(channel, "send_rich_message"):
        return False
    enabled = getattr(channel, "_rich_messages_enabled", None)
    if callable(enabled):
        try:
            if not enabled():
                return False
        except Exception:  # noqa: BLE001 — a config read must never block a card
            pass
    return getattr(channel, "_rich_supported", None) is not False


async def send_card(
    channel: Any,
    chat_id: int,
    card: Card,
    *,
    keyboard: list[list[dict[str, Any]]] | None = None,
    reply_to_message_id: int | None = None,
    business_connection_id: str | None = None,
) -> dict | None:
    """Send *card* to *chat_id*; return the sent message, or ``None``.

    *keyboard* overrides the card's own button rows (a view whose buttons carry
    state the card body does not, e.g. the todo list). Both projections embed
    that keyboard, so the actions survive a downgrade.
    """
    rows = keyboard if keyboard is not None else card.keyboard()
    if rich_available(channel):
        sent = await channel.send_rich_message(
            chat_id,
            html=card.rich_html(),
            fallback_html=card.classic_html(),
            keyboard=rows,
            reply_to_message_id=reply_to_message_id,
            business_connection_id=business_connection_id,
        )
        if sent:
            return sent
    send_message = getattr(channel, "send_message", None)
    if send_message is None:
        return None
    kwargs: dict[str, Any] = {
        "parse_mode": "HTML",
        "keyboard": rows,
        "reply_to_message_id": reply_to_message_id,
    }
    if business_connection_id:
        kwargs["business_connection_id"] = business_connection_id
    return await send_message(chat_id, card.classic_html(), **kwargs)


def _can_edit_rich(channel: Any) -> bool:
    """Whether a card may be *edited* as a rich message.

    Deliberately not :func:`rich_available`: editing needs ``edit_rich_message``, not
    ``send_rich_message``, and a channel can support one without the other. A press
    that downgrades the card to plain text is worse than a slightly different gate.
    """
    if not hasattr(channel, "edit_rich_message"):
        return False
    enabled = getattr(channel, "_rich_messages_enabled", None)
    if callable(enabled):
        try:
            if not enabled():
                return False
        except Exception:  # noqa: BLE001
            pass
    return getattr(channel, "_rich_supported", None) is not False


async def edit_card(
    channel: Any,
    chat_id: int,
    message_id: int,
    card: Card,
    *,
    keyboard: list[list[dict[str, Any]]] | None = None,
) -> bool:
    """Redraw *message_id* as *card*: rich first, classic second. Returns True if edited.

    This exists because a button press used to rebuild the card as the classic HTML and
    edit with ``editMessageText`` — so the first tap on a rich card silently threw away
    every tag that made it a card.
    """
    rows = keyboard if keyboard is not None else card.keyboard()
    if _can_edit_rich(channel):
        try:
            edited = await channel.edit_rich_message(
                chat_id,
                message_id,
                html=card.rich_html(),
                fallback_html=card.classic_html(),
                keyboard=rows,
            )
        except Exception:  # noqa: BLE001 — an older server falls back to plain text
            edited = None
        if edited:
            return True
    # The channel's own edit_message carries its payload conventions (business
    # connection, message splitting, "returned None means it did not land"), so a
    # classic edit goes through it whenever the channel has one. Only a channel
    # without that seam is driven through the raw API call.
    edit_message = getattr(channel, "edit_message", None)
    if edit_message is not None:
        try:
            edited = await edit_message(
                chat_id,
                message_id,
                card.classic_html(),
                parse_mode="HTML",
                keyboard=rows,
            )
        except Exception:  # noqa: BLE001 — the caller falls back to a fresh send
            return False
        return edited is not None
    api_call = getattr(channel, "_api_call", None)
    if api_call is None:
        return False
    await api_call(
        "editMessageText",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": card.classic_html(),
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": rows},
        },
    )
    return True


#: How many consecutive edits must fail before an anchor is treated as a dead message.
#: Two, not one: a single failure is usually a transient API error or a rate limit, and
#: reposting on it would recreate exactly the duplicate this machinery exists to stop.
_ANCHOR_FAIL_LIMIT = 2


def _fingerprint(card: Card, rows: list[list[dict[str, Any]]] | None) -> str:
    """What the message *says* — the card body plus the keyboard it carries.

    The keyboard is part of it on purpose: a card whose text is unchanged but whose buttons
    moved must still be edited.
    """
    payload = card.rich_html() + "\x00" + repr(rows if rows is not None else card.keyboard())
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


async def publish_card(
    channel: Any,
    chat_id: int,
    card: Card,
    *,
    surface: str,
    keyboard: list[list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Send *card* once per ``(surface, chat_id)`` and **edit it on every later run**.

    The defect this closes: scheduled surfaces reposted instead of editing (a campaign card
    at 22:00 and 22:28, the tasks digest at 22:03 and 22:29, the reminders sheet at 00:57 and
    00:58), because a run that builds a card had no memory of the message its last run sent.

    Three decisions worth knowing:

    * **Unchanged content costs nothing.** If the fingerprint matches the anchored message,
      nothing is sent and nothing is edited — so a nightly identical card no longer produces
      a "message is not modified" error, which used to be indistinguishable from a dead
      message and turned into a duplicate.
    * **A failed edit does not repost.** It increments a counter; only the second consecutive
      failure forgets the anchor and sends again, so a genuinely deleted message is still
      recovered while a transient error is not turned into a duplicate.
    * **The keyboard is part of the fingerprint**, so a card whose buttons changed is edited
      even if its text did not.

    Returns a dict with ``message_id``, ``edited`` and, when nothing was done, ``skipped``.
    """
    from navig.telegram import card_anchor  # noqa: PLC0415 — keeps the import graph flat

    rows = keyboard if keyboard is not None else card.keyboard()
    fingerprint = _fingerprint(card, rows)
    entry = card_anchor.get(surface, chat_id)

    if entry and entry.get("message_id"):
        message_id = int(entry["message_id"])
        if entry.get("sha") == fingerprint:
            return {
                "message_id": message_id,
                "edited": False,
                "skipped": True,
                "reason": "unchanged",
            }
        edited = await edit_card(channel, chat_id, message_id, card, keyboard=rows)
        if edited:
            card_anchor.set(surface, chat_id, message_id, sha=fingerprint, fails=0)
            return {"message_id": message_id, "edited": True, "skipped": False}
        fails = int(entry.get("fails") or 0) + 1
        if fails < _ANCHOR_FAIL_LIMIT:
            card_anchor.set(surface, chat_id, message_id, sha=str(entry.get("sha") or ""), fails=fails)
            return {
                "message_id": message_id,
                "edited": False,
                "skipped": True,
                "reason": f"edit failed ({fails}/{_ANCHOR_FAIL_LIMIT})",
            }
        # The anchored message is gone (deleted, or too old to edit): drop the anchor and
        # send a fresh one on this run, once.
        card_anchor.forget(surface, chat_id)

    sent = await send_card(channel, chat_id, card, keyboard=rows)
    message_id = 0
    if isinstance(sent, dict):
        raw = sent.get("message_id") or (sent.get("result") or {}).get("message_id")
        try:
            message_id = int(raw or 0)
        except (TypeError, ValueError):
            message_id = 0
    if message_id:
        card_anchor.set(surface, chat_id, message_id, sha=fingerprint, fails=0)
    return {"message_id": message_id, "edited": False, "skipped": False}
