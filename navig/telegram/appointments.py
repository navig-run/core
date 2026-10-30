"""An appointment typed in chat becomes a real task with real reminders.

Before this, "Ce rendez-vous a lieu le lundi 26 octobre 2026 à 10h30 …" reached the
LLM, which answered "Rendez-vous noté" — and nothing was noted. The chat agent was
never offered the task tools, so the reply described a save that did not happen.
That is worse than no reply at all: the operator stops writing the date down
anywhere else.

So a message that names a calendar date AND a clock time in the future is saved
here, before any model sees it, onto the same todo board `/todo` and `navig todo`
use, with reminders 3 days, 1 day and 1 hour before (plus at the time itself — that
one is always scheduled, see `pim.reminders.planned_fires`). The reply states what
was saved and offers the existing `td:` buttons to change the reminders or undo.

The detector is deliberately strict (`pim.dates.find_when`): a false positive costs
one tap on Undo; a vague one ("monday at 10") would cost the operator's trust in
every confirmation.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

#: The leads a chat-captured appointment starts with. Every token is in
#: `pim.dates.LEAD_TIMES`, so the buttons below can toggle each of them.
DEFAULT_LEADS: tuple[str, ...] = ("3d", "1d", "1h")

#: Kept for callers that imported it. Appointments are no longer a category — they are
#: their own kind (`board.KIND_APPOINTMENT`) and live in `/agenda`.
CATEGORY = "rendezvous"

_MIN_WORDS = 4
_MAX_TITLE_CHARS = 60
# "(1)" footnote markers pasted from an email.
_FOOTNOTE_RE = re.compile(r"\(\d{1,2}\)")


#: Verbs that mean "ping me at that moment" — a one-off `/remindme`, not an
#: appointment with leads. Longest first, so "remind me" wins over "remind".
REMIND_VERBS: tuple[str, ...] = (
    "set a reminder", "set reminder", "remind me",
    "напомните мне", "напомни мне", "напомните", "напомни",
    "rappelle-moi", "rappelle moi", "rappelez-moi",
    "erinnere mich", "recuérdame", "提醒我", "알려줘",
)

_VERB_RE = re.compile(
    "|".join(rf"(?<!\w){re.escape(v)}(?!\w)" for v in REMIND_VERBS), re.IGNORECASE
)


def strip_remind_verb(text: str) -> str | None:
    """The request with its remind verb removed, or None when there is no verb.

    "Rappelle-moi lundi prochain à 9h d'appeler Sarah" → "lundi prochain à 9h d'appeler
    Sarah". Everything else is kept as typed: the date reader decides what is a date.
    """
    if not _VERB_RE.search(text or ""):
        return None
    rest = _VERB_RE.sub(" ", text, count=1)
    rest = re.sub(
        r"^[\s,:;.!-]*(?:please|stp|s'il te plaît|пожалуйста)?[\s,:;.!-]*", "", rest, flags=re.I
    )
    return re.sub(r"\s+", " ", rest).strip(" ,.!?")


def _t(key: str, **fields: Any) -> str:
    from navig.telegram.todo_actions import _t as todo_t  # noqa: PLC0415

    return todo_t(key, **fields)


def detect(text: str, now: datetime) -> tuple[datetime, str] | None:
    """``(when, title)`` when ``text`` reads as an appointment, else None. Pure."""
    hit = classify(text, now)
    return None if hit is None else (hit[0], hit[1])


def classify(text: str, now: datetime) -> tuple[datetime, str, bool] | None:
    """``(when, title, firm)`` for an appointment, else None. Pure.

    ``firm`` decides between SAVING and ASKING. A calendar date ("26 octobre 2026")
    or an appointment word ("rdv", "встреча", "meeting") is firm — that message is an
    appointment. A relative day alone is not: "on se voit demain à 10h" is as often
    small talk as a plan, so it is offered with a Save button instead of written
    silently onto the list.

    A message with a remind verb ("rappelle-moi …") is not an appointment at all:
    it asked for ONE ping at that moment, which is `/remindme`'s job.
    """
    from navig.pim.dates import find_when, names_a_calendar_date  # noqa: PLC0415

    body = (text or "").strip()
    if not body or body.startswith("/") or body.endswith("?"):
        return None
    if strip_remind_verb(body) is not None:
        return None
    if _CHANGE_RE.search(body):
        # "Cancel my appointment on …" / "Move it from X to Y" asks to CHANGE one —
        # capturing it would add a todo named "Cancel …" or save the obsolete time.
        return None
    words = body.split()
    if len(words) < _MIN_WORDS:
        return None
    found = find_when(body, now)
    if found is None:
        return None
    when, start, end = found
    if find_when(" ".join(words[:start] + words[end:]), now) is not None:
        return None  # two moments: which one is meant is the agent's call, not ours
    title = _title(words[:start] + words[end:])
    if not title:
        from navig.telegram.reminder_card import shorten  # noqa: PLC0415

        title = shorten(body, _MAX_TITLE_CHARS)
    firm = names_a_calendar_date(words[start:end]) or bool(_APPOINTMENT_RE.search(body))
    return when, title, firm


#: Words that make a message an appointment even with a relative day ("rdv demain
#: à 10h"). Matched as word STARTS, so "встреч" covers "встреча"/"встречу".
_APPOINTMENT_RE = re.compile(
    r"(?<!\w)(rdv|rendez-vous|r[ée]union|visio|entretien|m[ée]decin|dentiste|docteur|"
    r"appointment|meeting|interview|doctor|dentist|call with|"
    r"встреч|созвон|врач|стоматолог|при[её]м|собеседован|запис[ьа]н|к врачу)",
    re.IGNORECASE,
)


#: A request to cancel or move an appointment, not a new one. Matched as word STARTS.
_CHANGE_RE = re.compile(
    r"(?<!\w)(cancel|call off|reschedul|postpon|move (?:my|the|our|it)|annul|d[ée]plac|"
    r"report(?:e|er|ez|é)(?!\w)|отмен|перен[её]с|перенос)",
    re.IGNORECASE,
)


def _title(words: list[str]) -> str:
    """A short subject for the task — the full message is kept in its notes.

    The whole pasted message used to become the title (up to 120 chars), so every
    reminder for it repeated the motive, the address and a file path in one line.
    Cut at the first dash / colon / sentence boundary, else at a word boundary.
    """
    from navig.telegram.reminder_card import split_headline  # noqa: PLC0415

    text = _FOOTNOTE_RE.sub(" ", " ".join(words))
    text = re.sub(r"\s+", " ", text).strip(" ,;:-–—.")
    head, _rest = split_headline(text, _MAX_TITLE_CHARS)
    return head.strip(" ,;:-–—.") if not head.endswith("…") else head


def save(
    text: str,
    when: datetime,
    title: str,
    *,
    user_id: int,
    chat_id: int,
    source: str,
    now: datetime,
    proposed: bool = False,
) -> dict[str, Any] | None:
    """Create the APPOINTMENT and schedule its reminders. Returns it, or None when
    this ``source`` was already captured (a re-delivered Telegram update).

    Goes through `pim.service` — the one write path — so it is stored as its own
    kind (never a todo in a "rendezvous" category) and its pings are scheduled the
    same way the OS and the Mini App schedule theirs. A PROPOSED one ("ai") schedules
    nothing until it is accepted.

    Raises on a store failure: the caller must not claim "saved" for a row that does
    not exist.
    """
    from navig.pim import service  # noqa: PLC0415

    return service.create_appointment(
        title,
        when,
        # The whole message, untouched: the title is a summary we derived, and a
        # derivation is never allowed to be the only copy of what the operator sent.
        notes=text,
        leads=DEFAULT_LEADS,
        origin="ai" if proposed else "chat",
        origin_ref=source,
        created_by=int(user_id) if user_id else None,
    )


def build_reply(todo: dict[str, Any], now: datetime) -> tuple[str, dict[str, Any]]:
    """The confirmation card: what was saved, when, which reminders — and buttons
    that reuse the `/todo` callbacks, so toggling and undo need no new handler."""
    from navig.pim.clock import from_utc_iso, to_local  # noqa: PLC0415
    from navig.pim.dates import describe_lead, format_local  # noqa: PLC0415
    from navig.telegram.todo_actions import extension_banner  # noqa: PLC0415

    tid = todo["id"]
    title = html.escape(str(todo.get("title") or ""))
    due = to_local(from_utc_iso(todo.get("due_at")))
    when = format_local(due, now) if due else ""
    active = [t for t in DEFAULT_LEADS if t in (todo.get("remind_before") or [])]

    if todo.get("origin") == "ai":
        # A SUGGESTION: nothing is scheduled until Save is tapped. The buttons are the
        # list's own keep/no — keep turns it into an ordinary task and schedules its
        # reminders; no deletes it and records the dismissal.
        ask = _t("pim.appt.ask", title=title, when=when) or (
            f"📅 <b>Save this?</b> {title}\n🕐 {when}"
        )
        leads = ", ".join(describe_lead(t) for t in active)
        hint = _t("pim.appt.ask_hint", leads=leads) or f"🔔 Reminders once saved: {leads}"
        return extension_banner() + f"{ask}\n<i>{hint}</i>", {
            "inline_keyboard": [[
                {"text": f"✓ {_t('pim.appt.save') or 'Save'}", "callback_data": f"ag:ok:{tid}"},
                {"text": f"✕ {_t('pim.appt.dismiss') or 'No'}", "callback_data": f"ag:no:{tid}"},
            ]]
        }

    saved = _t("pim.appt.saved", title=title, when=when) or f"📅 <b>Saved</b>: {title}\n🕐 {when}"
    if active:
        leads = ", ".join(describe_lead(t) for t in active)
        line = _t("pim.appt.leads", leads=leads) or f"🔔 Reminders: {leads} + at the time"
    else:
        line = _t("pim.appt.no_leads") or "🔔 Reminder at the time"
    body = extension_banner() + f"{saved}\n{line}"

    keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": ("🔔 " if token in active else "🔕 ")
                    + describe_lead(token).replace(" before", ""),
                    "callback_data": f"ag:r:{tid}:{token}",
                }
                for token in DEFAULT_LEADS
            ],
            [
                {"text": f"↩ {_t('pim.appt.undo') or 'Undo'}", "callback_data": f"ag:x:{tid}"},
                {"text": f"📅 {_t('pim.agenda.nav') or 'Agenda'}", "callback_data": "ag:v"},
            ],
        ]
    }
    return body, keyboard


async def maybe_capture(
    channel: Any,
    *,
    chat_id: int,
    user_id: int,
    message_id: int | None,
    text: str,
    is_group: bool,
) -> bool:
    """Save + confirm when ``text`` is an appointment. True when it handled the message.

    Never raises: on any failure the message falls through to the ordinary chat path,
    which is the pre-existing behaviour — never a swallowed message.
    """
    if is_group:
        return False  # the board is the operator's own list, not a group's
    try:
        from navig.telegram.todo_actions import extension_is_off  # noqa: PLC0415

        if extension_is_off():
            return False

        from navig.pim.clock import local_now  # noqa: PLC0415

        now = local_now()
        hit = classify(text, now)
        if hit is None:
            return False
        when, title, firm = hit
        source = f"tg:{chat_id}:{message_id}" if message_id else f"tg:{chat_id}:{text[:80]}"
        todo = save(
            text, when, title,
            user_id=user_id, chat_id=chat_id, source=source, now=now, proposed=not firm,
        )
        if todo is None:
            return True  # already captured from this very message — say nothing twice
    except Exception:  # noqa: BLE001 — fall through to chat rather than drop the message
        logger.exception("appointment capture failed; falling back to chat")
        return False

    # From here the task EXISTS. Falling through to the chat path now would let the
    # model answer as if nothing had happened, so a failed card degrades to plain text.
    try:
        body, keyboard = build_reply(todo, now)
        await channel._api_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": body,
                "parse_mode": "HTML",
                "reply_markup": keyboard,
                **({"reply_to_message_id": message_id} if message_id else {}),
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception("appointment %s saved but the card failed", todo.get("id"))
        try:
            await channel._api_call(
                "sendMessage",
                {"chat_id": chat_id, "text": f"📅 {todo.get('title')} — /todo"},
            )
        except Exception:  # noqa: BLE001
            logger.exception("appointment %s: plain confirmation failed too", todo.get("id"))
    return True
