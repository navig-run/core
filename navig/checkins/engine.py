"""The check-in engine — scheduled asks, taps, typed answers and the gateway tick.

**Who calls what**

* the scheduler runs ``NAVIG_CHECKIN:<id>:<chat>[:<slot>[:nudge]]`` jobs through
  :func:`run_command` — IN PROCESS, no CLI cold start (the cold start is what used
  to overrun the weigh-in's 60 s timeout and get it asked twice);
* the callback router hands every ``ci:`` tap to :func:`handle_callback`;
* the message chain hands a reply to an open check-in question to
  :func:`consume_reply` (only a reply to that EXACT message id is ever consumed);
* the reminder poller calls :func:`tick` (due snoozes, expired prompts).

**Safety rules (pills), each pinned by a test in ``tests/checkins``**

1. Nothing is ever recorded ``taken`` without the operator's tap or typed time.
   Expiry, chat tidy and a scheduler timeout can only ever produce ``missed``.
2. A second "taken" for the same dose asks first (the double-dose guard).
3. One missed-dose nudge, ``missed_after`` minutes after the dose — never repeated.
4. An open pill prompt is never deleted by chat tidy (:func:`state.protected_messages`).
5. No dose advice anywhere: the dose is the operator's own text, shown verbatim.

**Dedup.** Every scheduled ask is marked BEFORE the send and unmarked only when
the send definitely failed — so a run the scheduler kills at its timeout after
Telegram accepted the message still blocks the retry five minutes later.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any

from navig.checkins import cadence, cards, records, state
from navig.checkins import defs as d

logger = logging.getLogger(__name__)

COMMAND_PREFIX = "NAVIG_CHECKIN:"
NUDGE = "nudge"
WEEKLY = "weekly"
#: An unanswered pill prompt this long after its slot is closed as ``missed``.
EXPIRE_AFTER = timedelta(hours=24)
_TICK_EVERY_S = 30.0
_last_tick = 0.0

Send = Callable[[str, dict[str, Any]], Awaitable[Any]]


class SendFailed(RuntimeError):
    """Telegram did not accept the message — the scheduler should retry."""


# ── seams ─────────────────────────────────────────────────────────────────────


def _tz() -> tzinfo:
    return cadence.local_tz()


def _now(tz: tzinfo | None = None) -> datetime:
    """The aware local "now" — one seam, so tests pin the clock."""
    return datetime.now(tz or _tz())


def channel_sender(channel: Any) -> Send:
    """Send through the live gateway channel (``_api_call`` → Message or None)."""

    async def _send(method: str, payload: dict[str, Any]) -> Any:
        return await channel._api_call(method, payload)  # noqa: SLF001

    return _send


def http_sender() -> Send:
    """Send through the Bot API directly — the scheduler outside the gateway."""

    async def _send(method: str, payload: dict[str, Any]) -> Any:
        from navig.telegram.api import _api_call, _load_telegram_token  # noqa: PLC0415

        data = await asyncio.to_thread(_api_call, _load_telegram_token(), method, payload)
        return (data or {}).get("result")

    return _send


def _gateway_sender(gateway: Any) -> Send:
    channel = None
    try:
        channel = (getattr(gateway, "channels", None) or {}).get("telegram")
    except Exception:  # noqa: BLE001
        channel = None
    return channel_sender(channel) if channel is not None else http_sender()


async def _send_message(send: Send, chat: int, text: str, markup: Any = None) -> int:
    """Send and return the message id; raise :class:`SendFailed` when it did not land."""
    from navig.telegram import chat_tidy  # noqa: PLC0415

    payload: dict[str, Any] = {"chat_id": chat, "text": text, "parse_mode": "HTML"}
    if markup is not None:
        payload["reply_markup"] = markup
    with chat_tidy.routine("checkin"):
        result = await send("sendMessage", payload)
    mid = state.message_id_of(result)
    if mid is None:
        raise SendFailed(f"Telegram did not accept the check-in for chat {chat}")
    return mid


async def _edit(send: Send, chat: int, mid: int, text: str, markup: Any = None) -> bool:
    """Rewrite a card in place. Never raises — the answer is already on disk."""
    try:
        result = await send(
            "editMessageText",
            {
                "chat_id": chat,
                "message_id": mid,
                "text": text,
                "parse_mode": "HTML",
                "reply_markup": markup if markup is not None else {"inline_keyboard": []},
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("check-in card %s could not be edited: %s", mid, exc)
        return False
    return result is not None


async def _delete(send: Send, chat: int, mid: int) -> bool:
    try:
        return (await send("deleteMessage", {"chat_id": chat, "message_id": mid})) is not None
    except Exception:  # noqa: BLE001
        return False


def _extension_on(item: d.CheckinItem) -> bool:
    try:
        from navig.gateway.channels.telegram_extensions import is_enabled  # noqa: PLC0415

        return is_enabled(item.definition.extension)
    except Exception:  # noqa: BLE001 — fail open, like the gate itself
        return True


def _local_hhmm(iso: str | None, tz: tzinfo) -> str:
    when = records.parse_iso(iso)
    return when.astimezone(tz).strftime("%H:%M") if when else "?"


# ── the scheduled command ─────────────────────────────────────────────────────


def build_command(
    item_id: str, chat: int, slot: str | None = None, action: str | None = None
) -> str:
    parts = [item_id, str(int(chat))]
    if slot:
        parts.append(slot)
        if action:
            parts.append(action)
    return COMMAND_PREFIX + ":".join(parts)


def parse_command(command: str) -> tuple[str, int, str | None, str | None]:
    """``NAVIG_CHECKIN:<id>:<chat>[:<slot>[:nudge]]`` → ``(id, chat, slot, action)``."""
    body = command.strip()
    if not body.startswith(COMMAND_PREFIX):
        raise ValueError("not a check-in command")
    parts = body[len(COMMAND_PREFIX) :].split(":")
    if len(parts) < 2 or not parts[0]:
        raise ValueError(f"malformed check-in command: {command!r}")
    item_id = parts[0]
    chat = int(parts[1])
    slot = parts[2] if len(parts) > 2 and parts[2] else None
    action = parts[3] if len(parts) > 3 and parts[3] else None
    return item_id, chat, slot, action


async def run_command(
    command: str,
    *,
    gateway: Any = None,
    send: Send | None = None,
    now: datetime | None = None,
) -> str:
    """Run one scheduled check-in. Returns the run-log line; raises to ask for a retry."""
    item_id, chat, slot, action = parse_command(command)
    return await run_scheduled(
        item_id, chat, slot, action, send=send or _gateway_sender(gateway), now=now
    )


async def run_scheduled(
    item_id: str,
    chat: int,
    slot: str | None,
    action: str | None,
    *,
    send: Send,
    now: datetime | None = None,
) -> str:
    item = d.get_item(item_id)
    if item is None:
        # Not an error to retry: the check-in was removed and its job lingered.
        return f"skipped: no check-in {item_id!r} (removed — `navig checkin list`)"
    if not _extension_on(item):
        ext = item.definition.extension.capitalize()
        return f"skipped: the {ext} extension is off (/extensions to turn it on)"
    if item.template == "weigh":
        if slot == WEEKLY:
            return await _run_weekly(chat, send=send, now=now)
        return await _run_weigh(chat, send=send, now=now)
    if not item.enabled:
        return f"skipped: {item.id} is paused (navig checkin resume {item.id})"
    tz = _tz()
    now = (now or _now(tz)).astimezone(tz)
    if item.template == "pills":
        if not slot:
            return "skipped: a pill check-in needs a slot"
        if action == NUDGE:
            return await _run_pill_nudge(item, chat, slot, send=send, now=now, tz=tz)
        return await _run_pill_ask(item, chat, slot, send=send, now=now, tz=tz)
    if item.template == "water":
        return await _run_water(item, chat, slot, send=send, now=now, tz=tz)
    return f"skipped: unknown template {item.template!r}"


# ── weigh-in: the behaviour `navig body checkin --send` always had ────────────


def weigh_path(chat: int):
    """The metrics.csv the weigh-in for *chat* writes into.

    ``checkins.weigh.space`` (set by the migration from the old job's ``--space``)
    wins; then the path the chat was last pinned to; then normal resolution.
    """
    from navig.spaces import body_metrics as bm  # noqa: PLC0415

    try:
        from navig.config import get_config_manager  # noqa: PLC0415

        space = get_config_manager().get(f"{d.CONFIG_KEY}.{d.WEIGH_ID}.space", default=None)
    except Exception:  # noqa: BLE001
        space = None
    if space:
        return bm.metrics_path(str(space))
    return bm.resolve_target(chat)


async def _run_weigh(chat: int, *, send: Send, now: datetime | None) -> str:
    from navig.spaces import body_metrics as bm  # noqa: PLC0415
    from navig.spaces import weigh_cadence as wc  # noqa: PLC0415
    from navig.telegram import body_actions  # noqa: PLC0415

    local = (now or _now()).astimezone(_tz())
    day_d = local.date()
    when = day_d.isoformat()
    path = weigh_path(chat)
    try:
        last = bm.last_weighed(path, on_or_before=day_d)
    except bm.MetricsReadError:
        last = None  # unreadable record: ask rather than go silent
    reason = wc.skip_reason(
        wc.load_settings(),
        day=day_d,
        now=local.replace(tzinfo=None),
        last_weighed=last,
        asked_on=bm.weigh_asked_on(chat),
    )
    if reason is not None:
        return f"skipped: {reason[1]}"
    text, markup = body_actions.build_weigh_prompt(path, when)
    bm.mark_weigh_asked(chat, when)  # BEFORE the send — see the module docstring
    try:
        mid = await _send_message(send, chat, text, markup)
    except Exception:
        bm.unmark_weigh_asked(chat, when)
        raise
    bm.remember_target(chat, path, message_id=mid, day=when)
    bm.set_prompt(chat, "weigh", when, mid)
    return f"weigh-in sent to chat {chat}"


async def _run_weekly(chat: int, *, send: Send, now: datetime | None) -> str:
    from navig.spaces import body_metrics as bm  # noqa: PLC0415
    from navig.telegram import body_actions  # noqa: PLC0415

    local = (now or _now()).astimezone(_tz())
    when = local.date().isoformat()
    if state.asked_on(chat, d.WEIGH_ID, WEEKLY) == when:
        return f"skipped: the weekly card already went out for {when}"
    path = weigh_path(chat)
    text, markup = body_actions.build_weekly_card(path, when)
    state.mark_asked(chat, d.WEIGH_ID, WEEKLY, when)
    try:
        mid = await _send_message(send, chat, text, markup)
    except Exception:
        state.unmark_asked(chat, d.WEIGH_ID, WEEKLY, when)
        raise
    bm.remember_target(chat, path, message_id=mid, day=when)
    return f"weekly card sent to chat {chat}"


# ── pills ─────────────────────────────────────────────────────────────────────


def _rows(chat: int) -> list[dict[str, Any]]:
    try:
        return records.read(records.records_path(chat))
    except records.RecordsReadError as exc:
        logger.warning("check-in record unreadable: %s", exc)
        raise


async def _run_pill_ask(
    item: d.CheckinItem, chat: int, slot: str, *, send: Send, now: datetime, tz: tzinfo
) -> str:
    day = cadence.occurrence_day(now, slot, tz).isoformat()
    if records.dose_status(_rows(chat), item.id, day, slot) is not None:
        return f"skipped: {item.id} {cards.fmt_slot(slot)} already answered for {day}"
    if state.asked_on(chat, item.id, slot) == day:
        return f"skipped: {item.id} {cards.fmt_slot(slot)} already asked for {day}"
    if cadence.is_late(now, date.fromisoformat(day), slot, tz, item.missed_after):
        # A daemon that comes back hours late must not ask "did you take your 08:00
        # dose?" at 15:00 as if it were due now — that is how a dose gets doubled.
        return (
            f"skipped: too late for the {cards.fmt_slot(slot)} dose (past {item.missed_after} min)"
        )
    text, markup = cards.pill_card(item, day, slot)
    state.mark_asked(chat, item.id, slot, day)
    try:
        mid = await _send_message(send, chat, text, markup)
    except Exception:
        state.unmark_asked(chat, item.id, slot, day)
        raise
    state.add_prompt(chat, mid, item=item.id, day=day, slot=slot, kind="card")
    return f"pill check-in {item.id} {cards.fmt_slot(slot)} sent to chat {chat}"


async def _run_pill_nudge(
    item: d.CheckinItem, chat: int, slot: str, *, send: Send, now: datetime, tz: tzinfo
) -> str:
    day = cadence.occurrence_day(
        now, slot, tz, lag=timedelta(minutes=item.missed_after)
    ).isoformat()
    if records.dose_status(_rows(chat), item.id, day, slot) is not None:
        return f"skipped: {item.id} {cards.fmt_slot(slot)} answered — no nudge"
    if state.asked_on(chat, item.id, slot) != day:
        return f"skipped: {item.id} {cards.fmt_slot(slot)} was never asked on {day} — no nudge"
    if not state.claim_nudge(chat, item.id, slot, day):
        return f"skipped: {item.id} {cards.fmt_slot(slot)} already nudged — never twice"
    text, markup = cards.pill_card(item, day, slot, nudge=True)
    try:
        mid = await _send_message(send, chat, text, markup)
    except Exception:
        state.release_nudge(chat, item.id, slot, day)
        raise
    state.add_prompt(chat, mid, item=item.id, day=day, slot=slot, kind="card")
    return f"missed-dose nudge for {item.id} {cards.fmt_slot(slot)} sent to chat {chat}"


async def _record_dose(
    send: Send,
    chat: int,
    item: d.CheckinItem,
    day: str,
    slot: str | None,
    status: str,
    *,
    at: datetime | None,
    source: str,
    tz: tzinfo,
    keep_mid: int | None = None,
) -> str:
    """Append one dose answer, close every open prompt about it, settle the cards."""
    rec: dict[str, Any] = {
        "date": day,
        "item": item.id,
        "kind": d.KIND_DOSE,
        "slot": slot,
        "status": status,
        "source": source,
        "name": item.name,
        "dose": item.dose,
    }
    if at is not None:
        rec["at"] = records.utc_iso(at)
    records.append(records.records_path(chat), rec)
    state.cancel_snoozes(chat, item.id, slot, day)
    at_txt = at.astimezone(tz).strftime("%H:%M") if at else ""
    settled = cards.pill_settled(item, slot, status, at=at_txt, day=day)
    for mid, kind in state.drop_prompts_for(chat, item.id, day, slot):
        if mid == keep_mid:
            continue
        # A "time" question is a force_reply: only deleting it disarms the box.
        if kind == "time" or not await _edit(send, chat, mid, settled):
            await _delete(send, chat, mid)
    return settled


# ── water ─────────────────────────────────────────────────────────────────────


async def _run_water(
    item: d.CheckinItem, chat: int, slot: str | None, *, send: Send, now: datetime, tz: tzinfo
) -> str:
    day = now.date().isoformat()
    total = records.count_total(_rows(chat), item.id, day)
    if total >= item.target:
        return f"skipped: water target reached ({total}/{item.target})"
    if state.asked_on(chat, item.id, slot) == day:
        return f"skipped: water {cards.fmt_slot(slot)} already sent for {day}"
    text, markup = cards.water_card(item, day, total, reminder=True)
    state.mark_asked(chat, item.id, slot, day)
    try:
        mid = await _send_message(send, chat, text, markup)
    except Exception:
        state.unmark_asked(chat, item.id, slot, day)
        raise
    state.set_water_card(chat, item.id, mid, day)
    return f"water reminder sent to chat {chat} ({total}/{item.target})"


# ── taps ──────────────────────────────────────────────────────────────────────


async def handle_callback(
    channel: Any,
    data: str,
    chat: int,
    message_id: int,
    user_id: int = 0,
    *,
    send: Send | None = None,
    now: datetime | None = None,
) -> str:
    """Apply one ``ci:`` tap and return the toast. The write lands before the toast."""
    send = send or channel_sender(channel)
    try:
        item_id, action, day, slot = cards.parse_cb(data)
    except ValueError:
        return "?"
    item = d.get_item(item_id)
    if item is None:
        await _edit(send, chat, message_id, cards._esc(cards.t("checkins.toast.gone")))
        return cards.t("checkins.toast.gone")
    tz = _tz()
    now = (now or _now(tz)).astimezone(tz)
    if item.template == "pills":
        return await _pill_tap(send, chat, message_id, item, action, day, slot, now=now, tz=tz)
    if item.template == "water":
        return await _water_tap(send, chat, message_id, item, action, day, now=now)
    return "?"


async def _pill_tap(
    send: Send,
    chat: int,
    mid: int,
    item: d.CheckinItem,
    action: str,
    day: str,
    slot: str | None,
    *,
    now: datetime,
    tz: tzinfo,
) -> str:
    rows = _rows(chat)
    taken = records.taken(rows, item.id, day, slot)
    status = records.dose_status(rows, item.id, day, slot)

    async def _take(offset: int, *, confirmed: bool) -> str:
        if taken and not confirmed:
            # The double-dose guard: never record a second dose on one tap.
            first = _local_hhmm(taken[0].get("at") or taken[0].get("ts"), tz)
            text, markup = cards.pill_double(item, day, slot, first_at=first, offset=offset)
            await _edit(send, chat, mid, text, markup)
            return cards.t("checkins.toast.confirm")
        at = cadence.shift(now, -timedelta(minutes=offset))
        settled = await _record_dose(
            send, chat, item, day, slot, records.TAKEN, at=at, source="tap", tz=tz, keep_mid=mid
        )
        await _edit(send, chat, mid, settled)
        return cards.t("checkins.toast.saved")

    if action == "t":
        return await _take(0, confirmed=False)
    if action.startswith("e") and action[1:].isdigit():
        return await _take(min(int(action[1:]), 24 * 60), confirmed=False)
    if action.startswith("y") and action[1:].isdigit():
        return await _take(min(int(action[1:]), 24 * 60), confirmed=True)
    if action == "n":
        first = _local_hhmm(taken[0].get("at") if taken else None, tz)
        await _edit(send, chat, mid, cards.pill_settled(item, slot, records.TAKEN, at=first))
        state.drop_prompt(chat, mid)
        return cards.t("checkins.toast.kept")
    if action == "s":
        if status == records.TAKEN:
            first = _local_hhmm(taken[0].get("at"), tz)
            await _edit(send, chat, mid, cards.pill_settled(item, slot, records.TAKEN, at=first))
            state.drop_prompt(chat, mid)
            return cards.t("checkins.toast.already_taken")
        settled = await _record_dose(
            send, chat, item, day, slot, records.SKIPPED, at=now, source="tap", tz=tz, keep_mid=mid
        )
        await _edit(send, chat, mid, settled)
        return cards.t("checkins.toast.saved")
    if action == "z":
        if status in (records.TAKEN, records.SKIPPED):
            return cards.t("checkins.toast.answered")
        due = cadence.shift(now, timedelta(minutes=cards.SNOOZE_MINUTES))
        state.add_snooze(chat, item.id, slot, day, due.timestamp())
        state.drop_prompt(chat, mid)
        text = cards.pill_settled(item, slot, "snoozed", at=due.strftime("%H:%M"))
        await _edit(send, chat, mid, text)
        return cards.t("checkins.toast.snoozed", minutes=cards.SNOOZE_MINUTES)
    if action == "e":
        text, markup = cards.pill_earlier(item, day, slot)
        await _edit(send, chat, mid, text, markup)
        return ""
    if action == "b":
        text, markup = cards.pill_card(item, day, slot)
        await _edit(send, chat, mid, text, markup)
        return ""
    if action == "eo":
        prompt = cards._esc(cards.t("checkins.pills.time_prompt", name=item.label()))
        try:
            pmid = await _send_message(send, chat, prompt, {"force_reply": True, "selective": True})
        except SendFailed:
            return "⚠️"
        state.add_prompt(chat, pmid, item=item.id, day=day, slot=slot, kind="time")
        return ""
    return "?"


async def _water_tap(
    send: Send, chat: int, mid: int, item: d.CheckinItem, action: str, day: str, *, now: datetime
) -> str:
    path = records.records_path(chat)
    total = records.count_total(_rows(chat), item.id, day)
    delta = {"a1": 1, "a2": 2, "l1": 1}.get(action)
    if action == "u":
        delta = -1 if total > 0 else 0
    if delta is None:
        return "?"
    if delta:
        records.append(
            path,
            {
                "date": day,
                "item": item.id,
                "kind": d.KIND_COUNT,
                "status": records.ADD,
                "value": delta,
                "source": "tap",
            },
        )
    total = max(0, total + delta)
    if action == "l1":
        # From the /checkins card: redraw that card, not a water card.
        text, markup = list_card(chat, now=now)
        await _edit(send, chat, mid, text, markup)
    else:
        text, markup = cards.water_card(item, day, total)
        await _edit(send, chat, mid, text, markup)  # in place — one card a day, not a stream
        state.set_water_card(chat, item.id, mid, day)
    return cards.t("checkins.toast.water", total=total, target=item.target)


# ── typed replies ─────────────────────────────────────────────────────────────


async def consume_reply(
    channel: Any,
    chat: int,
    reply_to: int | None,
    text: str,
    *,
    send: Send | None = None,
    now: datetime | None = None,
) -> str | None:
    """Answer an open check-in question; ``None`` means "not ours, pass it on".

    Only a reply to the exact message id of an OPEN prompt is consumed — so a time
    typed for any other reason stays an ordinary message.
    """
    prompt = state.get_prompt(chat, reply_to)
    if prompt is None or reply_to is None:
        return None
    item = d.get_item(str(prompt.get("item") or ""))
    if item is None:
        state.drop_prompt(chat, reply_to)
        return cards.t("checkins.toast.gone")
    if not _extension_on(item):
        return None
    send = send or channel_sender(channel)
    tz = _tz()
    now = (now or _now(tz)).astimezone(tz)
    if prompt.get("kind") != "time":
        return None  # a card is answered with its buttons
    body = (text or "").strip()
    if body.lower() in {"skip", "cancel", "-", "нет", "отмена", "annuler"}:
        state.drop_prompt(chat, reply_to)
        await _delete(send, chat, reply_to)
        return cards.t("checkins.pills.time_cancelled")
    try:
        hhmm = d.parse_time(body)
    except ValueError:
        return cards.t("checkins.pills.unreadable_time")  # prompt stays open
    at = datetime.combine(now.date(), cadence.parse_slot(hhmm)).replace(tzinfo=tz)
    at = at.astimezone(UTC).astimezone(tz)
    if not cadence.before_or_at(at, now):
        # "23:40" typed at 00:20 means last night: the same wall time a day back.
        at = datetime.combine(now.date() - timedelta(days=1), cadence.parse_slot(hhmm))
        at = at.replace(tzinfo=tz).astimezone(UTC).astimezone(tz)
    day = str(prompt.get("day"))
    slot = prompt.get("slot")
    taken = records.taken(_rows(chat), item.id, day, slot)
    if taken:
        state.drop_prompt(chat, reply_to)
        await _delete(send, chat, reply_to)
        first = _local_hhmm(taken[0].get("at"), tz)
        offset = max(0, int((now.astimezone(UTC) - at.astimezone(UTC)).total_seconds() // 60))
        dtext, dmarkup = cards.pill_double(item, day, slot, first_at=first, offset=offset)
        try:
            await _send_message(send, chat, dtext, dmarkup)
        except SendFailed:
            pass
        return cards.t("checkins.toast.confirm")
    settled = await _record_dose(
        send, chat, item, day, slot, records.TAKEN, at=at, source="reply", tz=tz
    )
    await _delete(send, chat, reply_to)  # disarm the force_reply box
    return settled


# ── the tick: snoozes and expiry ──────────────────────────────────────────────


async def tick(
    channel: Any = None,
    *,
    send: Send | None = None,
    now: datetime | None = None,
    force: bool = False,
) -> dict[str, int]:
    """Re-send due snoozes and close expired prompts. Cheap; throttled in-process."""
    global _last_tick
    mono = time.monotonic()
    if not force and _last_tick and mono - _last_tick < _TICK_EVERY_S:
        return {"snoozed": 0, "expired": 0}
    _last_tick = mono
    send = send or channel_sender(channel)
    tz = _tz()
    now = (now or _now(tz)).astimezone(tz)
    resent = 0
    for snooze in state.take_due_snoozes(now.timestamp()):
        item = d.get_item(str(snooze.get("item") or ""))
        chat = int(snooze.get("chat") or 0)
        day, slot = str(snooze.get("day")), snooze.get("slot")
        if item is None or not item.enabled or not _extension_on(item) or not chat:
            continue
        if records.dose_status(_rows(chat), item.id, day, slot) is not None:
            continue
        text, markup = cards.pill_card(item, day, slot)
        try:
            mid = await _send_message(send, chat, text, markup)
        except Exception as exc:  # noqa: BLE001
            logger.warning("snoozed check-in %s could not be re-sent: %s", item.id, exc)
            state.add_snooze(chat, item.id, slot, day, now.timestamp() + 60)
            continue
        state.add_prompt(chat, mid, item=item.id, day=day, slot=slot, kind="card")
        resent += 1
    expired = await expire_stale(send=send, now=now)
    return {"snoozed": resent, "expired": expired}


async def expire_stale(*, send: Send | None, now: datetime) -> int:
    """Close pill prompts left open past :data:`EXPIRE_AFTER` — as ``missed``, never ``taken``."""
    tz = _tz()
    closed = 0
    for chat, mid, entry in state.open_prompts():
        item = d.get_item(str(entry.get("item") or ""))
        day, slot = entry.get("day"), entry.get("slot")
        if item is None:
            state.drop_prompt(chat, mid)
            continue
        if item.template != "pills" or not day or not slot:
            continue
        try:
            due = cadence.slot_instant(date.fromisoformat(str(day)), str(slot), tz)
        except ValueError:
            state.drop_prompt(chat, mid)
            continue
        if not cadence.before_or_at(cadence.shift(due, EXPIRE_AFTER), now):
            continue
        if records.dose_status(_rows(chat), item.id, str(day), slot) is None:
            records.append(
                records.records_path(chat),
                {
                    "date": day,
                    "item": item.id,
                    "kind": d.KIND_DOSE,
                    "slot": slot,
                    "status": records.MISSED,
                    "source": "expired",
                    "name": item.name,
                    "dose": item.dose,
                },
            )
        state.drop_prompt(chat, mid)
        if send is not None:
            text = cards.pill_settled(item, slot, records.MISSED, day=str(day))
            if entry.get("kind") == "time" or not await _edit(send, chat, mid, text):
                await _delete(send, chat, mid)
        closed += 1
    return closed


# ── today, for every surface ──────────────────────────────────────────────────


def today_entries(chat: int | None = None, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Every check-in with today's state — the /checkins card, the CLI and the deck."""
    tz = _tz()
    now = (now or _now(tz)).astimezone(tz)
    day = now.date().isoformat()
    hhmm = now.strftime("%H%M")
    try:
        rows = records.read(records.records_path(chat))
        unavailable = None
    except records.RecordsReadError as exc:
        rows, unavailable = [], str(exc)
    out: list[dict[str, Any]] = []
    for item in d.load_items():
        entry = item.as_dict()
        if item.template != "pills":
            entry["label"] = cards.t(item.definition.title_key)
        if item.template == "weigh":
            entry.update(_weigh_today(chat, day))
        elif item.template == "pills":
            slots = []
            for s in item.slots():
                st = records.dose_status(rows, item.id, day, s)
                slots.append({"slot": cards.fmt_slot(s), "status": st, "due": s <= hhmm})
            entry["today"] = slots
            entry["done"] = records.done_today(rows, item, day, now_hhmm=hhmm)
            entry["adherence"] = records.adherence(
                rows,
                item.id,
                item.slots(),
                today=now.date(),
                days=7,
                since=item.created_date(),
                now_local=now,
            )
            marks = {"taken": "✓", "skipped": "⏭", "missed": "✗"}
            entry["state"] = " · ".join(
                f"{x['slot']} {marks.get(x['status'] or '', '—')}" for x in slots
            )
        elif item.template == "water":
            total = records.count_total(rows, item.id, day)
            entry.update(total=total, done=total >= item.target, state=f"{total}/{item.target}")
        entry["unavailable"] = unavailable
        out.append(entry)
    return out


def _weigh_today(chat: int | None, day: str) -> dict[str, Any]:
    from navig.spaces import body_metrics as bm  # noqa: PLC0415

    try:
        path = weigh_path(chat) if chat else bm.default_path()
        last = bm.latest(path)
    except Exception:  # noqa: BLE001
        return {"done": False, "state": "?", "weight_kg": None}
    if last and last[0].isoformat() == day:
        return {"done": True, "state": f"{last[1]:g} kg", "weight_kg": last[1]}
    return {"done": False, "state": "—", "weight_kg": None}


def list_card(chat: int, *, now: datetime | None = None) -> tuple[str, Any]:
    tz = _tz()
    now = (now or _now(tz)).astimezone(tz)
    return cards.list_card(today_entries(chat, now=now), now.date().isoformat())
