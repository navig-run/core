"""Pills — the safety rules, each as a test.

1. Nothing becomes ``taken`` without the operator (expiry/tidy/timeout ⇒ missed).
2. A second "taken" for one dose asks first (double-dose guard).
3. One missed-dose nudge, never repeated.
4. An open pill prompt is never deleted by chat tidy.
5. No dose advice — the dose is the operator's own text, verbatim.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from datetime import time as dtime
from zoneinfo import ZoneInfo

import pytest

PARIS = ZoneInfo("Europe/Paris")
CHAT = 4242  # must match conftest.CHAT (the chat the health space is pinned to)


def at(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=PARIS)


def _rows(ci_env):
    from navig.checkins import records

    return records.read(ci_env["records"])


async def _ask(tg, now, slot="0800"):
    from navig.checkins import engine

    return await engine.run_scheduled("metformin", CHAT, slot, None, send=tg, now=now)


def _cb(item, action, day, slot="0800"):
    from navig.checkins import cards

    return cards.cb(item, action, day, slot)


# ── scheduled ask ─────────────────────────────────────────────────────────────


async def test_scheduled_ask_sends_a_card_and_opens_a_prompt(ci_env, tg, add_pills):
    from navig.checkins import state

    add_pills()
    out = await _ask(tg, at(2026, 10, 8, 8, 0))
    assert "sent" in out
    payload = tg.sent()[-1]
    assert "Metformin" in payload["text"] and "500 mg" in payload["text"]
    buttons = [
        b["callback_data"] for row in payload["reply_markup"]["inline_keyboard"] for b in row
    ]
    assert all(len(b.encode()) <= 64 for b in buttons)
    assert state.get_prompt(CHAT, tg.next_id)["slot"] == "0800"


async def test_asked_is_marked_before_send_so_a_retry_never_asks_twice(ci_env, tg, add_pills):
    """A run killed at its timeout AFTER Telegram accepted the card must not re-ask."""
    from navig.checkins import state

    add_pills()
    now = at(2026, 10, 8, 8, 0)
    await _ask(tg, now)
    assert state.asked_on(CHAT, "metformin", "0800") == "2026-10-08"
    out = await _ask(tg, now + timedelta(minutes=5))  # the scheduler's retry
    assert out.startswith("skipped") and "already asked" in out
    assert len(tg.sent()) == 1


async def test_a_send_that_failed_is_unmarked_so_the_retry_asks(ci_env, tg, add_pills):
    from navig.checkins import engine, state

    add_pills()
    tg.reject.add("sendMessage")
    with pytest.raises(engine.SendFailed):
        await _ask(tg, at(2026, 10, 8, 8, 0))
    assert state.asked_on(CHAT, "metformin", "0800") is None
    tg.reject.clear()
    assert "sent" in await _ask(tg, at(2026, 10, 8, 8, 5))


async def test_a_daemon_back_hours_late_does_not_ask_an_old_dose(ci_env, tg, add_pills):
    add_pills()
    out = await _ask(tg, at(2026, 10, 8, 13, 30))  # 08:00 dose, past missed_after (120)
    assert out.startswith("skipped") and "too late" in out
    assert tg.sent() == []


# ── 1. never auto-taken ───────────────────────────────────────────────────────


async def test_expiry_records_missed_never_taken(ci_env, tg, add_pills):
    from navig.checkins import engine, records, state

    add_pills()
    await _ask(tg, at(2026, 10, 8, 8, 0))
    mid = tg.next_id
    closed = await engine.tick(send=tg, now=at(2026, 10, 9, 8, 1), force=True)
    assert closed["expired"] == 1
    rows = _rows(ci_env)
    assert [r["status"] for r in rows] == [records.MISSED]
    assert records.dose_status(rows, "metformin", "2026-10-08", "0800") == records.MISSED
    assert not records.taken(rows, "metformin", "2026-10-08", "0800")
    assert state.get_prompt(CHAT, mid) is None
    # the card is rewritten to say so, with no buttons left
    edit = tg.sent("editMessageText")[-1]
    assert edit["message_id"] == mid and edit["reply_markup"] == {"inline_keyboard": []}


async def test_expiry_before_24h_leaves_the_prompt_open(ci_env, tg, add_pills):
    from navig.checkins import engine, state

    add_pills()
    await _ask(tg, at(2026, 10, 8, 8, 0))
    out = await engine.tick(send=tg, now=at(2026, 10, 8, 20, 0), force=True)
    assert out["expired"] == 0 and state.get_prompt(CHAT, tg.next_id) is not None
    assert _rows(ci_env) == []


async def test_expiry_never_overwrites_a_taken_answer(ci_env, tg, add_pills):
    """A late 'missed' must not erase a dose that was taken."""
    from navig.checkins import records

    rows = [
        {
            "date": "2026-10-08",
            "item": "metformin",
            "kind": "dose",
            "slot": "0800",
            "status": "taken",
        },
        {
            "date": "2026-10-08",
            "item": "metformin",
            "kind": "dose",
            "slot": "0800",
            "status": "missed",
        },
    ]
    assert records.dose_status(rows, "metformin", "2026-10-08", "0800") == records.TAKEN


async def test_a_scheduler_timeout_writes_nothing(ci_env, tg, add_pills):
    """A run that raises (Telegram down) records nothing — certainly not 'taken'."""

    add_pills()
    tg.raise_on.add("sendMessage")
    with pytest.raises(RuntimeError):
        await _ask(tg, at(2026, 10, 8, 8, 0))
    assert _rows(ci_env) == []


# ── taps ──────────────────────────────────────────────────────────────────────


async def test_taken_tap_records_once_and_settles_the_card(ci_env, tg, add_pills):
    from navig.checkins import engine, records, state

    add_pills()
    await _ask(tg, at(2026, 10, 8, 8, 0))
    mid = tg.next_id
    toast = await engine.handle_callback(
        None, _cb("metformin", "t", "2026-10-08"), CHAT, mid, send=tg, now=at(2026, 10, 8, 8, 5)
    )
    assert toast
    rows = _rows(ci_env)
    assert len(records.taken(rows, "metformin", "2026-10-08", "0800")) == 1
    assert rows[0]["dose"] == "500 mg" and rows[0]["source"] == "tap"
    assert state.get_prompt(CHAT, mid) is None
    assert tg.sent("editMessageText")[-1]["reply_markup"] == {"inline_keyboard": []}


async def test_second_taken_asks_to_confirm_double_dose(ci_env, tg, add_pills):
    """The double-dose guard: one tap can never record a second dose."""
    from navig.checkins import engine, records

    add_pills()
    await _ask(tg, at(2026, 10, 8, 8, 0))
    mid = tg.next_id
    cb = _cb("metformin", "t", "2026-10-08")
    await engine.handle_callback(None, cb, CHAT, mid, send=tg, now=at(2026, 10, 8, 8, 5))
    # the same button again (a stale card, a double tap, the nudge copy)
    await engine.handle_callback(None, cb, CHAT, mid, send=tg, now=at(2026, 10, 8, 8, 6))
    rows = _rows(ci_env)
    assert len(records.taken(rows, "metformin", "2026-10-08", "0800")) == 1
    confirm = tg.sent("editMessageText")[-1]
    data = [b["callback_data"] for row in confirm["reply_markup"]["inline_keyboard"] for b in row]
    assert any(":y0:" in x for x in data) and any(":n:" in x for x in data)
    assert "08:05" in confirm["text"]  # names when it was first taken


async def test_confirmed_second_dose_is_recorded_and_no_cancels(ci_env, tg, add_pills):
    from navig.checkins import engine, records

    add_pills()
    await _ask(tg, at(2026, 10, 8, 8, 0))
    mid = tg.next_id
    await engine.handle_callback(
        None, _cb("metformin", "t", "2026-10-08"), CHAT, mid, send=tg, now=at(2026, 10, 8, 8, 5)
    )
    await engine.handle_callback(
        None, _cb("metformin", "n", "2026-10-08"), CHAT, mid, send=tg, now=at(2026, 10, 8, 8, 6)
    )
    assert len(records.taken(_rows(ci_env), "metformin", "2026-10-08", "0800")) == 1
    await engine.handle_callback(
        None, _cb("metformin", "y0", "2026-10-08"), CHAT, mid, send=tg, now=at(2026, 10, 8, 8, 7)
    )
    assert len(records.taken(_rows(ci_env), "metformin", "2026-10-08", "0800")) == 2


async def test_taken_earlier_quick_choice_records_the_offset(ci_env, tg, add_pills):
    from navig.checkins import engine, records

    add_pills()
    await _ask(tg, at(2026, 10, 8, 8, 0))
    mid = tg.next_id
    await engine.handle_callback(
        None, _cb("metformin", "e", "2026-10-08"), CHAT, mid, send=tg, now=at(2026, 10, 8, 9, 30)
    )
    assert not _rows(ci_env), "opening the menu records nothing"
    await engine.handle_callback(
        None, _cb("metformin", "e60", "2026-10-08"), CHAT, mid, send=tg, now=at(2026, 10, 8, 9, 30)
    )
    taken = records.taken(_rows(ci_env), "metformin", "2026-10-08", "0800")
    assert len(taken) == 1
    assert records.parse_iso(taken[0]["at"]) == at(2026, 10, 8, 8, 30)


async def test_skip_records_skipped_not_taken(ci_env, tg, add_pills):
    from navig.checkins import engine, records

    add_pills()
    await _ask(tg, at(2026, 10, 8, 8, 0))
    await engine.handle_callback(
        None,
        _cb("metformin", "s", "2026-10-08"),
        CHAT,
        tg.next_id,
        send=tg,
        now=at(2026, 10, 8, 8, 1),
    )
    assert records.dose_status(_rows(ci_env), "metformin", "2026-10-08", "0800") == records.SKIPPED


async def test_snooze_resends_after_15_minutes_and_records_nothing(ci_env, tg, add_pills):
    from navig.checkins import engine, state

    add_pills()
    await _ask(tg, at(2026, 10, 8, 8, 0))
    first = tg.next_id
    await engine.handle_callback(
        None, _cb("metformin", "z", "2026-10-08"), CHAT, first, send=tg, now=at(2026, 10, 8, 8, 1)
    )
    assert _rows(ci_env) == []
    assert state.get_prompt(CHAT, first) is None
    out = await engine.tick(send=tg, now=at(2026, 10, 8, 8, 10), force=True)
    assert out["snoozed"] == 0, "not before its time"
    out = await engine.tick(send=tg, now=at(2026, 10, 8, 8, 17), force=True)
    assert out["snoozed"] == 1
    assert state.get_prompt(CHAT, tg.next_id)["slot"] == "0800"
    assert _rows(ci_env) == []


# ── 3. one nudge ──────────────────────────────────────────────────────────────


async def test_missed_dose_nudge_is_sent_once_and_never_repeats(ci_env, tg, add_pills):
    from navig.checkins import engine

    add_pills()
    await _ask(tg, at(2026, 10, 8, 8, 0))
    out = await engine.run_scheduled(
        "metformin", CHAT, "0800", "nudge", send=tg, now=at(2026, 10, 8, 10, 0)
    )
    assert "nudge" in out and "sent" in out
    for minutes in (5, 60):  # the scheduler's retry, a restart, anything
        again = await engine.run_scheduled(
            "metformin", CHAT, "0800", "nudge", send=tg, now=at(2026, 10, 8, 10, minutes % 60)
        )
        assert again.startswith("skipped")
    assert len(tg.sent()) == 2  # the card and ONE nudge
    assert _rows(ci_env) == [], "a nudge never records anything"


async def test_no_nudge_once_answered_or_never_asked(ci_env, tg, add_pills):
    from navig.checkins import engine

    add_pills()
    never = await engine.run_scheduled(
        "metformin", CHAT, "0800", "nudge", send=tg, now=at(2026, 10, 8, 10, 0)
    )
    assert never.startswith("skipped") and "never asked" in never
    await _ask(tg, at(2026, 10, 9, 8, 0))
    await engine.handle_callback(
        None,
        _cb("metformin", "t", "2026-10-09"),
        CHAT,
        tg.next_id,
        send=tg,
        now=at(2026, 10, 9, 8, 1),
    )
    answered = await engine.run_scheduled(
        "metformin", CHAT, "0800", "nudge", send=tg, now=at(2026, 10, 9, 10, 0)
    )
    assert answered.startswith("skipped") and "answered" in answered


async def test_a_late_evening_dose_is_nudged_after_midnight_as_yesterdays(ci_env, tg, add_pills):
    from navig.checkins import engine

    add_pills(name="Night", times="23:30")
    await engine.run_scheduled("night", CHAT, "2330", None, send=tg, now=at(2026, 10, 8, 23, 30))
    out = await engine.run_scheduled(
        "night", CHAT, "2330", "nudge", send=tg, now=at(2026, 10, 9, 1, 30)
    )
    assert "sent" in out
    from navig.checkins import state

    assert state.get_prompt(CHAT, tg.next_id)["day"] == "2026-10-08"


# ── 4. chat tidy ──────────────────────────────────────────────────────────────


async def test_tidy_never_deletes_an_open_pill_prompt(ci_env, tg, add_pills, monkeypatch):
    from navig.checkins import state
    from navig.telegram import chat_tidy

    add_pills()
    yesterday_noon = datetime.combine(date.today() - timedelta(days=1), dtime(12, 0)).timestamp()
    state.add_prompt(CHAT, 501, item="metformin", day="2026-10-08", slot="0800")
    ledger = chat_tidy.ledger_path()
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "".join(
            json.dumps({"c": CHAT, "m": m, "t": int(yesterday_noon)}) + "\n" for m in (500, 501)
        ),
        encoding="utf-8",
    )
    deleted: list[int] = []

    async def api(method, payload):
        deleted.extend(payload["message_ids"])
        return True

    res = await chat_tidy.sweep(api, now=datetime.now())
    assert deleted == [500]
    assert res.protected == 1
    # kept in the ledger, so it is tidied once answered
    assert any(e["m"] == 501 for e in chat_tidy.read_ledger(ledger))


async def test_tidy_also_protects_the_open_weigh_in_prompt(ci_env):
    from navig.checkins import state
    from navig.spaces import body_metrics as bm

    bm.set_prompt(CHAT, "weigh", "2026-10-08", 777)
    assert (CHAT, 777) in state.protected_messages()
    bm.clear_prompt(CHAT)
    assert (CHAT, 777) not in state.protected_messages()


# ── 5. no advice ──────────────────────────────────────────────────────────────


async def test_the_dose_is_shown_verbatim_and_never_parsed(ci_env, tg, add_pills):
    add_pills(name="Levothyrox", dose="½ tab (75 µg) — with water")
    from navig.checkins import engine

    await engine.run_scheduled("levothyrox", CHAT, "0800", None, send=tg, now=at(2026, 10, 8, 8, 0))
    text = tg.sent()[-1]["text"]
    assert "½ tab (75 µg) — with water" in text


def test_ids_keep_callback_data_under_64_bytes():
    from navig.checkins import cards
    from navig.checkins import defs as d

    long_id = "a" * 20
    assert d.ID_RE.match(long_id)
    assert len(cards.cb(long_id, "y1440", "2026-10-08", "2359").encode()) <= 64
