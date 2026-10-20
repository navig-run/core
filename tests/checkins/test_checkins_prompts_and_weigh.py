"""Open prompts keyed by message id, typed replies, and the weigh-in on the engine."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

PARIS = ZoneInfo("Europe/Paris")
CHAT = 4242


def at(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=PARIS)


# ── two open prompts ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_two_open_prompts_each_answer_only_their_own_message(ci_env, tg, add_pills):
    """The weigh-in prompt and a pill 'what time?' question, open at once."""
    from navig.checkins import cards, engine, records, state
    from navig.spaces import body_metrics as bm

    add_pills()
    bm.set_prompt(CHAT, "weigh", "2026-10-08", 300)  # the morning weigh-in, still open
    await engine.run_scheduled("metformin", CHAT, "0800", None, send=tg, now=at(2026, 10, 8, 8, 0))
    card = tg.next_id
    await engine.handle_callback(
        None,
        cards.cb("metformin", "eo", "2026-10-08", "0800"),
        CHAT,
        card,
        send=tg,
        now=at(2026, 10, 8, 9, 0),
    )
    time_q = tg.next_id
    assert state.get_prompt(CHAT, time_q)["kind"] == "time"
    assert tg.sent()[-1]["reply_markup"]["force_reply"] is True

    # a reply to the WEIGH prompt is not ours …
    assert (
        await engine.consume_reply(None, CHAT, 300, "07:30", send=tg, now=at(2026, 10, 8, 9, 1))
        is None
    )
    # … and a bare time with no reply-to is nobody's
    assert await engine.consume_reply(None, CHAT, None, "07:30", send=tg) is None
    assert records.read(ci_env["records"]) == []

    receipt = await engine.consume_reply(
        None, CHAT, time_q, "07:30", send=tg, now=at(2026, 10, 8, 9, 1)
    )
    assert receipt and "07:30" in receipt
    taken = records.taken(records.read(ci_env["records"]), "metformin", "2026-10-08", "0800")
    assert records.parse_iso(taken[0]["at"]) == at(2026, 10, 8, 7, 30)
    assert taken[0]["source"] == "reply"
    # the time question is deleted (disarms force_reply); the weigh-in is untouched
    assert {"chat_id": CHAT, "message_id": time_q} in tg.sent("deleteMessage")
    assert bm.pending_prompt(CHAT) == ("weigh", "2026-10-08", 300)
    assert state.get_prompt(CHAT, card) is None


@pytest.mark.asyncio
async def test_an_unreadable_time_keeps_the_question_open(ci_env, tg, add_pills):
    from navig.checkins import engine, state

    add_pills()
    state.add_prompt(CHAT, 55, item="metformin", day="2026-10-08", slot="0800", kind="time")
    out = await engine.consume_reply(
        None, CHAT, 55, "this morning", send=tg, now=at(2026, 10, 8, 9, 0)
    )
    assert out and state.get_prompt(CHAT, 55) is not None


@pytest.mark.asyncio
async def test_a_typed_time_after_a_tap_goes_through_the_double_dose_guard(ci_env, tg, add_pills):
    from navig.checkins import cards, engine, records, state

    add_pills()
    await engine.run_scheduled("metformin", CHAT, "0800", None, send=tg, now=at(2026, 10, 8, 8, 0))
    await engine.handle_callback(
        None,
        cards.cb("metformin", "t", "2026-10-08", "0800"),
        CHAT,
        tg.next_id,
        send=tg,
        now=at(2026, 10, 8, 8, 1),
    )
    state.add_prompt(CHAT, 77, item="metformin", day="2026-10-08", slot="0800", kind="time")
    await engine.consume_reply(None, CHAT, 77, "07:45", send=tg, now=at(2026, 10, 8, 9, 0))
    assert (
        len(records.taken(records.read(ci_env["records"]), "metformin", "2026-10-08", "0800")) == 1
    )
    last = tg.sent()[-1]
    assert any(
        ":y" in b["callback_data"] for row in last["reply_markup"]["inline_keyboard"] for b in row
    )


def test_message_id_of_reads_both_send_shapes():
    """The bug fixed in body_actions._send_prompt: the channel returns the Message."""
    from navig.checkins.state import message_id_of

    assert message_id_of({"message_id": 5}) == 5
    assert message_id_of({"ok": True, "result": {"message_id": 6}}) == 6
    assert message_id_of(None) is None


@pytest.mark.asyncio
async def test_on_demand_weigh_prompt_records_its_message_id(ci_env, tg):
    """`/weigh` with no number: its prompt must be answerable (it never was)."""
    from navig.spaces import body_metrics as bm
    from navig.telegram import body_actions as ba

    await ba.ask_for_weight(tg, CHAT, "2026-10-08", ci_env["metrics"])
    assert bm.pending_prompt(CHAT) == ("weigh", "2026-10-08", tg.next_id)


# ── the weigh-in on the engine: identical behaviour ───────────────────────────


@pytest.mark.asyncio
async def test_scheduled_weigh_in_behaves_like_body_checkin_send(ci_env, tg):
    from navig.checkins import engine
    from navig.spaces import body_metrics as bm

    out = await engine.run_scheduled("weigh", CHAT, None, None, send=tg, now=at(2026, 10, 8, 8, 10))
    assert "weigh-in sent" in out
    sent = tg.sent()[-1]
    assert sent["reply_markup"] == {"force_reply": True, "selective": True}
    assert bm.weigh_asked_on(CHAT) == "2026-10-08"
    assert bm.pending_prompt(CHAT) == ("weigh", "2026-10-08", tg.next_id)
    assert bm.resolve_target(CHAT) == ci_env["metrics"]
    # never twice a day — the retry and a restart both skip
    again = await engine.run_scheduled(
        "weigh", CHAT, None, None, send=tg, now=at(2026, 10, 8, 8, 15)
    )
    assert again.startswith("skipped") and len(tg.sent()) == 1


@pytest.mark.asyncio
async def test_weigh_in_follows_its_cadence_and_the_health_extension(ci_env, tg, monkeypatch):
    from navig.checkins import engine
    from navig.gateway.channels import telegram_extensions as te
    from navig.spaces import body_metrics as bm
    from navig.spaces import weigh_cadence as wc

    bm.upsert(ci_env["metrics"], "2026-10-08", {"weight_kg": "88"})
    out = await engine.run_scheduled("weigh", CHAT, None, None, send=tg, now=at(2026, 10, 8, 8, 10))
    assert out.startswith("skipped") and "weighed" in out

    wc.save_settings({"enabled": False})
    out = await engine.run_scheduled("weigh", CHAT, None, None, send=tg, now=at(2026, 10, 9, 8, 10))
    assert out.startswith("skipped") and "off" in out
    wc.save_settings({"enabled": True})

    monkeypatch.setattr(te, "is_enabled", lambda ext: ext != "health")
    out = await engine.run_scheduled("weigh", CHAT, None, None, send=tg, now=at(2026, 10, 9, 8, 10))
    assert "Health extension is off" in out
    # …and switching CHECK-INS off does not silence the weigh-in
    monkeypatch.setattr(te, "is_enabled", lambda ext: ext != "checkins")
    out = await engine.run_scheduled("weigh", CHAT, None, None, send=tg, now=at(2026, 10, 9, 8, 10))
    assert "sent" in out
    assert tg.sent() and len(tg.sent()) == 1


def test_checkins_weigh_is_an_alias_for_health_weigh_in(ci_env):
    from navig.config import get_config_manager
    from navig.spaces import weigh_cadence as wc

    cm = get_config_manager()
    cm.set_global("health.weigh_in.every_days", 3)
    assert wc.load_settings().every_days == 3
    cm.set_global("checkins.weigh.every_days", 2)
    assert wc.load_settings().every_days == 2, "the checkins.weigh key wins"
    # a write through the canonical path must not be shadowed by the alias
    saved = wc.save_settings({"every_days": 5})
    assert saved.every_days == 5


@pytest.mark.asyncio
async def test_weekly_card_runs_in_process_once_a_day(ci_env, tg):
    from navig.checkins import engine

    out = await engine.run_scheduled(
        "weigh", CHAT, "weekly", None, send=tg, now=at(2026, 10, 11, 18, 45)
    )
    assert "weekly card sent" in out
    assert "inline_keyboard" in tg.sent()[-1]["reply_markup"]
    again = await engine.run_scheduled(
        "weigh", CHAT, "weekly", None, send=tg, now=at(2026, 10, 11, 18, 50)
    )
    assert again.startswith("skipped")
