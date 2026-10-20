"""Confirm-first sending: "send an SMS to Alex …" becomes a DRAFT, never a send.

The natural-language intent (``TelegramMessagingMixin._try_nl_messaging_intent``) reads a
free-text instruction with an LLM — recipient, channel, the words, a translation. An LLM's
reading of free text must never be the last step before a real SMS leaves the phone, so the
intent now stops at a draft card::

    📨 SMS to Alex
    «Salut frère»
    [✅ Send] [✖ Cancel]

Only the **Send** tap sends, and only when all of these hold:

* the tap comes from the same chat AND the same person who asked;
* the draft is younger than :data:`DRAFT_TTL_S`;
* the draft is still unclaimed — :func:`claim` pops it under a lock, so a double tap or
  a retried callback can never send it twice. (Drafts are per process: the callback is
  answered by the same channel that rendered the card.)

Drafts live in memory only: a daemon restart drops them, which is the safe direction
(the operator asks again; nothing is ever sent from a stale draft).
"""

from __future__ import annotations

import html
import secrets
import threading
import time
from dataclasses import dataclass

#: A draft older than this is refused — the operator re-asks.
DRAFT_TTL_S = 600.0
#: Callback prefixes. ``msg:`` is owned by the Messaging extension, so the extension
#: switch gates these buttons too.
SEND_PREFIX = "msg:sd:"
CANCEL_PREFIX = "msg:sx:"


@dataclass(frozen=True)
class Draft:
    id: str
    chat_id: int
    user_id: int
    contact: str
    channel: str  # "sms" · "whatsapp" · "telegram"
    message: str
    language: str | None
    created: float


#: English fallbacks — the canonical copy lives in navig/locales/{en,ru,fr}.json.
_EN = {
    "drafts.button.send": "✅ Send",
    "drafts.button.cancel": "✖ Cancel",
    "drafts.title": "{channel} to {contact}",
    "drafts.in_language": " (in {language})",
    "drafts.hint": "Nothing is sent until you tap Send.",
    "drafts.sent": "{channel} sent to {contact}:",
    "drafts.cancelled": "Not sent to {contact}.",
    "drafts.failed": "Couldn't send to {contact}: {error}",
    "drafts.toast.sent": "Sent",
    "drafts.toast.cancelled": "Cancelled",
    "drafts.toast.failed": "Not sent",
    "drafts.expired": "This draft expired or was already handled — ask again.",
    "drafts.not_yours": "Only the person who asked can send this.",
}


def _t(key: str, **fields: object) -> str:
    """The operator's language (navig/locales); English if the read fails. Never raises."""
    try:
        from navig.core import i18n

        text = i18n.t(key, **fields)
        if text and text != key:
            return text
    except Exception:  # noqa: BLE001 — a card must never fail on a locale read
        pass
    try:
        return _EN[key].format(**fields)
    except Exception:  # noqa: BLE001
        return key


_lock = threading.Lock()
_drafts: dict[str, Draft] = {}


def _prune(now: float) -> None:
    for key in [k for k, d in _drafts.items() if now - d.created > DRAFT_TTL_S]:
        _drafts.pop(key, None)


def create(
    *,
    chat_id: int,
    user_id: int,
    contact: str,
    channel: str,
    message: str,
    language: str | None = None,
    now: float | None = None,
) -> Draft:
    """Store a draft and return it. Nothing is sent."""
    ts = time.time() if now is None else now
    draft = Draft(
        id=secrets.token_hex(4),
        chat_id=int(chat_id),
        user_id=int(user_id),
        contact=contact,
        channel=channel,
        message=message,
        language=language,
        created=ts,
    )
    with _lock:
        _prune(ts)
        _drafts[draft.id] = draft
    return draft


def claim(draft_id: str, *, chat_id: int, user_id: int, now: float | None = None) -> tuple[Draft | None, str]:
    """Take a draft for sending or cancelling. Returns ``(draft, "")`` or ``(None, reason)``.

    A draft is consumed by the first valid claim, so it can be sent at most once.
    A claim from another chat or person does NOT consume it — the owner can still act.
    """
    ts = time.time() if now is None else now
    with _lock:
        _prune(ts)
        draft = _drafts.get(draft_id)
        if draft is None:
            return None, _t("drafts.expired")
        if draft.chat_id != int(chat_id) or draft.user_id != int(user_id):
            return None, _t("drafts.not_yours")
        _drafts.pop(draft_id, None)
        return draft, ""


def card(draft: Draft) -> tuple[str, list[list[dict]]]:
    """The draft card: who, how, the exact words — and the two buttons."""
    tail = html.escape(_t("drafts.in_language", language=draft.language)) if draft.language else ""
    title = _t("drafts.title", channel=draft.channel.upper(), contact=draft.contact)
    text = (
        f"📨 <b>{html.escape(title)}</b>{tail}\n"
        f"«{html.escape(draft.message)}»\n\n"
        f"<i>{html.escape(_t('drafts.hint'))}</i>"
    )
    keyboard = [[
        {"text": _t("drafts.button.send"), "callback_data": f"{SEND_PREFIX}{draft.id}"},
        {"text": _t("drafts.button.cancel"), "callback_data": f"{CANCEL_PREFIX}{draft.id}"},
    ]]
    return text, keyboard


async def handle_callback(channel, cb_data: str, chat_id: int, message_id: int, user_id: int) -> str:
    """Route a draft button. Returns the toast text. Never raises."""
    if cb_data.startswith(SEND_PREFIX):
        action, draft_id = "send", cb_data[len(SEND_PREFIX):]
    elif cb_data.startswith(CANCEL_PREFIX):
        action, draft_id = "cancel", cb_data[len(CANCEL_PREFIX):]
    else:
        return ""

    draft, reason = claim(draft_id, chat_id=chat_id, user_id=user_id)
    if draft is None:
        return reason

    if action == "cancel":
        cancelled = _t("drafts.cancelled", contact=draft.contact)
        await _settle(channel, chat_id, message_id, "✖ " + html.escape(cancelled))
        return _t("drafts.toast.cancelled")

    try:
        receipt = await channel._messaging_dispatch(draft.contact, draft.message, network=draft.channel)
    except Exception as exc:  # noqa: BLE001 — report, never crash the callback
        failed = _t("drafts.failed", contact=draft.contact, error=str(exc))
        await _settle(channel, chat_id, message_id, "❌ " + html.escape(failed))
        return _t("drafts.toast.failed")
    if getattr(receipt, "ok", False):
        sent = _t("drafts.sent", channel=draft.channel.upper(), contact=draft.contact)
        await _settle(channel, chat_id, message_id, f"✅ {html.escape(sent)}\n«{html.escape(draft.message)}»")
        return _t("drafts.toast.sent")
    error = str(getattr(receipt, "error", "") or "failed")
    failed = _t("drafts.failed", contact=draft.contact, error=error)
    await _settle(channel, chat_id, message_id, "❌ " + html.escape(failed))
    return _t("drafts.toast.failed")


async def _settle(channel, chat_id: int, message_id: int, text: str) -> None:
    """Replace the draft card (and its buttons) with the outcome."""
    try:
        await channel._api_call(
            "editMessageText",
            {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"},
        )
    except Exception:  # noqa: BLE001 — the toast still tells the operator
        pass


def reset_for_tests() -> None:
    with _lock:
        _drafts.clear()
