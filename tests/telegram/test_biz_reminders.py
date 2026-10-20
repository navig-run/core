"""A reminder set inside a business chat comes back INTO that chat.

Reported 2026-09-30 with a screenshot: the other person typed "Напомнить через 16
часов про схему" and "!напомнить", the owner typed "remember in 15 hours schema" —
nothing happened. No business-chat command understood a reminder at all.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

import navig.telegram.biz_commands as bc
import navig.telegram.biz_reminders as r


class _Chan:
    def __init__(self, refuse: tuple[str, ...] = ()) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.refuse = set(refuse)
        self.send_message = AsyncMock()

    async def _api_call(self, method, data=None):
        await asyncio.sleep(0)  # a network call yields — two processes can overlap
        self.calls.append((method, data or {}))
        return None if method in self.refuse else {"message_id": 500}

    def of(self, method):
        return [d for m, d in self.calls if m == method]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """Every test gets its own reminder file and claim dir — never the real ones."""
    store = tmp_path / "business_reminders.json"
    monkeypatch.setattr(r, "_path", lambda: store)
    import navig.platform.paths as paths

    monkeypatch.setattr(paths, "cache_dir", lambda: tmp_path / "cache")
    yield store
    for task in list(r._ARMED.values()):
        task.cancel()
    r._ARMED.clear()


def _msg(text, *, sender=555, mid=70):
    return {
        "chat": {"id": 555, "first_name": "Sam"},
        "message_id": mid,
        "business_connection_id": "bc1",
        "from": {"id": sender},
        "text": text,
    }


# ── what was typed in the real chat ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "seconds", "about"),
    [
        ("in 15 hours schema", 15 * 3600, "schema"),
        ("через 16 часов про схему", 16 * 3600, "про схему"),
        ("dans 2 heures le devis", 7200, "le devis"),
        ("call the bank in 2 hours", 7200, "call the bank"),
        ("позвонить через час", 3600, "позвонить"),
        ("через 2 часа и 30 минут купить хлеб", 9000, "купить хлеб"),
        ("2h buy milk", 7200, "buy milk"),
        ("через полчаса чай", 1800, "чай"),
        ("me in 10 min pizza", 600, "pizza"),
    ],
)
def test_the_phrasings_people_use(text, seconds, about):
    assert r.parse(text) == (seconds, about)


def test_a_number_inside_the_note_is_not_a_time():
    assert r.parse("buy 5 m of cable") is None


async def test_their_russian_reminder_is_set_in_russian_and_points_back():
    ch = _Chan()
    assert await bc.dispatch(
        ch, _msg("Напомнить через 16 часов про схему"), is_owner=False, owner_id=777
    )
    posted = ch.of("sendMessage")[-1]
    assert posted["text"].startswith("⏰ Напомню через 16 ч — про схему")
    assert posted["business_connection_id"] == "bc1"
    [saved] = r.pending(555)
    assert saved["about"] == "про схему" and saved["reply_to"] == 70
    assert ch.of("deleteBusinessMessages") == [], "their message stays"


async def test_the_bang_spelling_works():
    ch = _Chan()
    assert await bc.dispatch(ch, _msg("!напомнить"), is_owner=False, owner_id=777)
    assert "напомни через" in ch.of("sendMessage")[-1]["text"], "usage, in Russian"


async def test_the_owners_english_reminder_keeps_its_case_and_removes_the_trigger():
    ch = _Chan()
    assert await bc.dispatch(
        ch, _msg("remember in 15 hours Schema v2", sender=777), is_owner=True, owner_id=777
    )
    assert ch.of("deleteBusinessMessages")[0]["message_ids"] == [70]
    assert ch.of("sendMessage")[-1]["text"].startswith("⏰ I'll remind you in 15 h — Schema v2")
    assert r.pending(555)[0]["reply_to"] is None, "the trigger is gone; nothing to point at"


# ── delivery ─────────────────────────────────────────────────────────────────


def _due_item(**over):
    item = {
        "id": "abc",
        "chat_id": 555,
        "bcid": "bc1",
        "owner_id": 777,
        "due": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        "about": "про схему",
        "lang": "ru",
        "reply_to": 70,
        "chat_title": "Sam",
    }
    item.update(over)
    return item


async def test_a_due_reminder_is_posted_into_the_chat_once():
    item = _due_item()
    r._save([item])
    ch = _Chan()
    # Both supervisor processes reach it at the same moment — neither has removed
    # it yet, so only the cross-process claim can stop the second post.
    await asyncio.gather(r._deliver(ch, item, 777), r._deliver(ch, item, 777))
    [sent] = ch.of("sendMessage")
    assert sent["text"] == "⏰ Напоминание — про схему"
    assert sent["business_connection_id"] == "bc1"
    assert sent["reply_parameters"]["message_id"] == 70
    assert r.pending(555) == []


async def test_a_refused_post_reaches_the_owner_instead():
    """Business bots may only write into chats with recent activity."""
    item = _due_item()
    r._save([item])
    ch = _Chan(refuse=("sendMessage",))
    await r._deliver(ch, item, 777)
    note = ch.send_message.await_args
    assert note.args[0] == 777 and "про схему" in note.args[1]
    assert r.pending(555) == []


async def test_a_cancelled_reminder_is_not_delivered():
    ch = _Chan()
    await r._deliver(ch, _due_item(), 777)  # never saved == cancelled
    assert ch.of("sendMessage") == []


async def test_a_long_missed_reminder_is_not_dropped_into_the_chat():
    old = _due_item(due=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat())
    r._save([old])
    ch = _Chan()
    await r._deliver(ch, old, 777)
    assert ch.of("sendMessage") == []
    assert "missed" in ch.send_message.await_args.args[1]


async def test_it_survives_a_restart_and_fires():
    """Saved before it is confirmed, re-armed on start, then delivered."""
    soon = _due_item(due=(datetime.now(timezone.utc) + timedelta(seconds=0.2)).isoformat())
    r._save([soon])
    ch = _Chan()
    assert await r.resume_pending(ch) == 1
    await asyncio.wait_for(asyncio.gather(*r._ARMED.values()), 5)
    assert ch.of("sendMessage")[0]["text"] == "⏰ Напоминание — про схему"


def test_an_unreadable_file_is_kept_aside_not_wiped(_isolated):
    _isolated.write_text("{not json", encoding="utf-8")
    assert r._load() == []
    assert _isolated.with_suffix(".json.corrupt").read_text(encoding="utf-8") == "{not json"


# ── list / cancel / limits ───────────────────────────────────────────────────


async def test_list_and_owner_only_cancel():
    ch = _Chan()
    await bc.dispatch(ch, _msg("remind in 2 hours call"), is_owner=False, owner_id=777)
    await bc.dispatch(ch, _msg("remind list"), is_owner=False, owner_id=777)
    assert "call" in ch.of("sendMessage")[-1]["text"]
    await bc.dispatch(ch, _msg("remind cancel"), is_owner=False, owner_id=777)
    assert len(r.pending(555)) == 1, "the other person cannot clear the owner's list"
    await bc.dispatch(ch, _msg("remind cancel", sender=777), is_owner=True, owner_id=777)
    assert r.pending(555) == []


async def test_more_than_a_year_is_refused_but_ninety_days_is_fine():
    """ "remind me in 90 days schema public" was asked twice and refused at 30."""
    ch = _Chan()
    await bc.dispatch(ch, _msg("remind in 400 days x"), is_owner=False, owner_id=777)
    assert "year" in ch.of("sendMessage")[-1]["text"]
    assert r.pending(555) == []
    await bc.dispatch(ch, _msg("remind in 90 days schema public"), is_owner=False, owner_id=777)
    assert len(r.pending(555)) == 1


def test_the_store_is_plain_json(_isolated):
    r._save([_due_item()])
    assert json.loads(_isolated.read_text(encoding="utf-8"))[0]["id"] == "abc"


# ── a day and a clock time, in Russian and French too ────────────────────────
#
# Asked 2026-10-01: "must work in Russian and French too". Durations did; "завтра
# в 10", "demain à 9h" and the polite / hyphenated command words did not.

_NOW = datetime(2026, 10, 1, 14, 0).astimezone()


@pytest.mark.parametrize(
    ("text", "due", "about"),
    [
        ("завтра в 10 про схему", (2, 10, 0), "про схему"),
        ("в 18:30 позвонить маме", (1, 18, 30), "позвонить маме"),
        ("сегодня в 7 вечера ужин", (1, 19, 0), "ужин"),
        ("послезавтра оплатить", (3, 9, 0), "оплатить"),
        ("завтра в 9 утра", (2, 9, 0), ""),
        ("demain à 9h", (2, 9, 0), ""),
        ("à 14h30 le rdv", (1, 14, 30), "le rdv"),
        ("в 10 созвон", (2, 10, 0), "созвон"),  # 10:00 has gone at 14:00 → tomorrow
        ("ce soir à 8h film", (1, 20, 0), "film"),
        ("après-demain facture", (3, 9, 0), "facture"),
        ("tomorrow at 9am gym", (2, 9, 0), "gym"),
        ("at 5pm call", (1, 17, 0), "call"),
    ],
)
def test_a_day_and_a_clock_time(text, due, about):
    seconds, note, absolute = r.parse_when(text, now=_NOW)
    at = _NOW + timedelta(seconds=seconds)
    assert (at.day, at.hour, at.minute) == due, f"{text!r} -> {at:%d %H:%M}"
    assert note == about and absolute


def test_a_clock_time_lands_on_the_minute_not_one_before():
    """Truncated seconds made "завтра в 10" fire — and read — at 09:59."""
    now = _NOW.replace(second=37, microsecond=500_000)
    seconds, _note, _abs = r.parse_when("завтра в 10", now=now)
    assert (now + timedelta(seconds=seconds)).strftime("%H:%M") == "10:00"


def test_a_time_already_gone_today_says_so():
    late = datetime(2026, 10, 1, 23, 0).astimezone()
    assert r.parse_when("сегодня в 7 вечера ужин", now=late) is None


@pytest.mark.parametrize(
    ("text", "starts"),
    [
        ("Напомните через час про встречу", "⏰ Напомню через 1 ч — про встречу"),
        ("Rappelle-moi dans 2 heures le devis", "⏰ Je vous le rappelle dans 2 h — le devis"),
        ("rappelez-moi dans une heure", "⏰ Je vous le rappelle dans 1 h"),
        ("напомни завтра в 10 про схему", "⏰ Напомню завтра в 10:00 — про схему"),
        ("rappelle demain à 9h", "⏰ Je vous le rappelle demain à 09:00"),
    ],
)
async def test_the_ways_russian_and_french_speakers_ask(text, starts):
    ch = _Chan()
    assert await bc.dispatch(ch, _msg(text), is_owner=False, owner_id=777)
    assert ch.of("sendMessage")[-1]["text"].startswith(starts)
    assert len(r.pending(555)) == 1


# ── weekdays, dates and repeats (asked 2026-10-03) ───────────────────────────
#
# _NOW is Thursday 1 October 2026, 14:00.


@pytest.mark.parametrize(
    ("text", "due", "about"),
    [
        ("в пятницу в 10 созвон", (2, 10, 0), "созвон"),
        ("lundi à 9h réunion", (5, 9, 0), "réunion"),
        ("on friday at 10 standup", (2, 10, 0), "standup"),
        ("friday 10am demo", (2, 10, 0), "demo"),
        ("в четверг в 10 спорт", (8, 10, 0), "спорт"),  # today, but 10:00 has gone
        ("15.10 в 12:00 налог", (15, 12, 0), "налог"),
        ("15 октября в 12 налог", (15, 12, 0), "налог"),
        ("le 15 octobre à 14h devis", (15, 14, 0), "devis"),
        ("october 15 at 3pm tax", (15, 15, 0), "tax"),
        ("Remember me about of Scheme 4 hours", (1, 18, 0), "Remember me about of Scheme"),
        ("about of Scheme 4 hours", (1, 18, 0), "Scheme"),
    ],
)
def test_weekdays_dates_and_a_trailing_duration(text, due, about):
    full = r.parse_full(text, now=_NOW)
    at = _NOW + timedelta(seconds=full["seconds"])
    assert (at.day, at.hour, at.minute) == due, f"{text!r} -> {at:%d %H:%M}"
    assert full["about"] == about


def test_a_date_already_gone_this_year_is_not_silently_moved():
    assert r.parse_full("1.10 в 10", now=_NOW) is None


@pytest.mark.parametrize(
    ("text", "every", "first"),
    [
        ("каждый день в 9 таблетки", "day", (2, 9, 0)),
        ("ежедневно в 21:30 чтение", "day", (1, 21, 30)),
        ("по понедельникам в 10 планёрка", "week", (5, 10, 0)),
        ("tous les lundis à 10h réunion", "week", (5, 10, 0)),
        ("every weekday at 8 standup", "workday", (2, 8, 0)),
        ("every day pills", "day", (2, 9, 0)),
    ],
)
def test_repeats(text, every, first):
    full = r.parse_full(text, now=_NOW)
    at = _NOW + timedelta(seconds=full["seconds"])
    assert full["repeat"]["every"] == every
    assert (at.day, at.hour, at.minute) == first


def test_a_workday_repeat_skips_the_weekend():
    friday = datetime(2026, 10, 2, 9, 0).astimezone()
    nxt = r.next_occurrence({"every": "workday", "clock": (8, 0)}, friday)
    assert (nxt.weekday(), nxt.hour) == (0, 8), "Friday 09:00 → Monday 08:00"


async def test_a_repeat_is_rescheduled_after_it_fires_and_offers_stop():
    item = _due_item(repeat={"every": "day", "clock": (9, 0)})
    r._save([item])
    ch = _Chan()
    await r._deliver(ch, item, 777)
    sent = ch.of("sendMessage")[0]
    labels = [b["text"] for b in sent["reply_markup"]["inline_keyboard"][0]]
    assert labels == ["+1 ч", "+1 день", "✖ Остановить"]
    [kept] = r.pending(555)
    assert datetime.fromisoformat(kept["due"]) > datetime.now(timezone.utc), "moved to tomorrow"
    assert kept["id"] in r._ARMED, "and armed again"


# ── buttons ──────────────────────────────────────────────────────────────────


async def test_the_confirmation_carries_a_cancel_button_that_works():
    ch = _Chan()
    await bc.dispatch(ch, _msg("напомни через 2 часа позвонить"), is_owner=False, owner_id=777)
    confirm = ch.of("sendMessage")[-1]
    [[button]] = confirm["reply_markup"]["inline_keyboard"]
    assert button["text"] == "✖ Отменить" and button["callback_data"].startswith("bzr:x:")
    toast = await r.handle_callback(ch, button["callback_data"], 555, 500, 777)
    assert toast == "Отменено"
    assert r.pending(555) == []
    edited = ch.of("editMessageText")[-1]
    assert edited["text"] == "⏰ Отменено — позвонить"
    assert edited["business_connection_id"] == "bc1"


async def test_snooze_brings_a_delivered_reminder_back_in_an_hour():
    item = _due_item()
    r._save([item])
    ch = _Chan()
    await r._deliver(ch, item, 777)
    assert r.pending(555) == [], "delivered…"
    assert r._find("abc")["delivered"], "…but kept, so the button still works"
    toast = await r.handle_callback(ch, "bzr:s:abc:3600", 555, 500, 777)
    assert toast == "Отложено"
    [again] = r.pending(555)
    delta = datetime.fromisoformat(again["due"]) - datetime.now(timezone.utc)
    assert timedelta(minutes=59) < delta <= timedelta(hours=1)
    assert "ещё раз" in ch.of("editMessageText")[-1]["text"]


async def test_a_button_on_a_reminder_that_is_gone_says_so():
    ch = _Chan()
    assert await r.handle_callback(ch, "bzr:x:nope", 555, 500, 777) == "Already done"
    assert ch.of("editMessageReplyMarkup"), "the dead button is removed"


def test_delivered_reminders_are_forgotten_after_two_days():
    old = _due_item(
        id="old", delivered=(datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    )
    fresh = _due_item(id="new", delivered=datetime.now(timezone.utc).isoformat())
    assert [x["id"] for x in r._prune([old, fresh])] == ["new"]


async def test_list_names_a_repeat():
    ch = _Chan()
    await bc.dispatch(ch, _msg("напомни каждый день в 9 таблетки"), is_owner=False, owner_id=777)
    assert ch.of("sendMessage")[-1]["text"].startswith(
        "⏰ Буду напоминать каждый день в 09:00 — таблетки"
    )
    await bc.dispatch(ch, _msg("напомни список"), is_owner=False, owner_id=777)
    assert "каждый день" in ch.of("sendMessage")[-1]["text"]


def test_a_unit_letter_inside_a_word_is_not_a_unit():
    """The "s" closing "is" read as one second: "Timer is broken" started a timer."""
    assert r._seconds("this is broken") == 0
    assert r.duration_seconds("Timer is broken again") == 0
