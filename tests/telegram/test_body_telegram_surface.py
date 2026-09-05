"""Tests for the Telegram Health surface — /health, /weigh, /body, and the reply.

Guards:
  - `/health` reporting a confident number when it could not read anything;
  - a bare number being swallowed into the body record because it happened to be
    sent while a prompt was open but was not a reply TO it;
  - an ordinary sentence being filed as a treatment note for the same reason;
  - a failure in the reply handler making the bot deaf instead of answering.

Hermetic: NAVIG_CONFIG_DIR is redirected, so the pin never touches ~/.navig.
"""

from __future__ import annotations

from datetime import date

import pytest

from navig.gateway.channels.telegram import TelegramChannel
from navig.gateway.channels.telegram_commands import TelegramCommandsMixin
from navig.spaces import body_metrics as bm
from navig.telegram import body_actions as ba

HEADER = "date,weight_kg,body_fat_pct,resting_hr,sleep_hours,steps,mood_1_10,hrv,notes\n"
CHAT = 159901607


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(ba, "_lang", lambda: "en")
    ba._store = None
    yield
    ba._store = None


@pytest.fixture
def metrics(tmp_path):
    path = tmp_path / "metrics.csv"
    path.write_text(HEADER, encoding="utf-8")
    bm.remember_target(CHAT, path)
    return path


class _Chan:
    """Minimal stand-in recording what the handler sent."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_message(self, chat_id, text, parse_mode=None, keyboard=None, **_):
        self.sent.append({"text": text, "keyboard": keyboard})
        return {"result": {"message_id": 555}}

    async def _api_call(self, method, payload):
        self.sent.append({"text": payload.get("text"), "method": method,
                          "markup": payload.get("reply_markup")})
        return {"result": {"message_id": 555}}

    @property
    def last(self) -> str:
        return self.sent[-1]["text"]


# ── /health ───────────────────────────────────────────────────────────────────


async def test_health_reports_the_weight_not_just_habit_counts(metrics):
    """The whole point of the move: /health now means health."""
    bm.upsert(metrics, date.today().isoformat(), {"weight_kg": "88.2"})
    ch = _Chan()
    await TelegramCommandsMixin._handle_health(ch, CHAT, CHAT)
    assert "88.2" in ch.last


async def test_health_on_an_empty_record_says_nothing_recorded(metrics):
    ch = _Chan()
    await TelegramCommandsMixin._handle_health(ch, CHAT, CHAT)
    assert "No weight recorded yet" in ch.last
    assert "/weigh" in ch.last


async def test_health_reports_an_unreadable_record_as_a_warning_not_a_zero(
    metrics, monkeypatch
):
    """A green number over an unknown is worse than a red one — it says don't look."""

    def boom(*_a, **_k):
        raise bm.MetricsReadError("locked")

    monkeypatch.setattr(bm, "summarize", boom)
    ch = _Chan()
    await TelegramCommandsMixin._handle_health(ch, CHAT, CHAT)
    assert "Could not read" in ch.last
    # No body number is rendered at all — the failure must not degrade into a
    # confident reading. (The habit count below it is a real, separate figure.)
    for label in ("Latest:", "7-day avg", "Logged:"):
        assert label not in ch.last


async def test_health_still_shows_the_habit_count(metrics):
    ch = _Chan()
    await TelegramCommandsMixin._handle_health(ch, CHAT, CHAT)
    assert "Active habits" in ch.last


async def test_health_offers_the_two_new_actions(metrics):
    ch = _Chan()
    await TelegramCommandsMixin._handle_health(ch, CHAT, CHAT)
    data = [b["callback_data"] for row in ch.sent[-1]["keyboard"] for b in row]
    assert "slash:weigh" in data
    assert "slash:body" in data


# ── /weigh ────────────────────────────────────────────────────────────────────


async def test_weigh_with_a_number_records_it(metrics):
    ch = _Chan()
    await TelegramCommandsMixin._handle_weigh(ch, CHAT, CHAT, "/weigh 87.4")
    row = next(r for r in bm.read_metrics(metrics)[1] if r["date"] == date.today().isoformat())
    assert row["weight_kg"] == "87.4"


async def test_weigh_accepts_a_decimal_comma(metrics):
    ch = _Chan()
    await TelegramCommandsMixin._handle_weigh(ch, CHAT, CHAT, "/weigh 87,4")
    row = next(r for r in bm.read_metrics(metrics)[1] if r["date"] == date.today().isoformat())
    assert row["weight_kg"] == "87.4"


async def test_weigh_with_junk_records_nothing_and_says_so(metrics):
    ch = _Chan()
    await TelegramCommandsMixin._handle_weigh(ch, CHAT, CHAT, "/weigh heavy")
    assert "⚠️" in ch.last
    assert not [r for r in bm.read_metrics(metrics)[1] if r.get("weight_kg")]


async def test_weigh_with_no_argument_opens_the_prompt(metrics):
    ch = _Chan()
    await TelegramCommandsMixin._handle_weigh(ch, CHAT, CHAT, "/weigh")
    assert ch.sent[-1]["markup"]["force_reply"] is True
    assert bm.pending_prompt(CHAT) is not None


# ── /body ─────────────────────────────────────────────────────────────────────


async def test_body_sends_the_weekly_card_and_pins_the_target(metrics):
    ch = _Chan()
    await TelegramCommandsMixin._handle_body(ch, CHAT, CHAT)
    assert ch.sent[-1]["keyboard"]["inline_keyboard"]
    assert bm.resolve_target(CHAT) == metrics
    assert bm.last_card(CHAT)[0] == 555


# ── the reply router — the safety property ───────────────────────────────────


class _Self(_Chan):
    pass


async def test_a_reply_to_the_prompt_is_consumed(metrics):
    bm.set_prompt(CHAT, "weigh", date.today().isoformat(), 99)
    ch = _Self()
    handled = await TelegramChannel._handle_pending_body_input(
        ch, chat_id=CHAT, text="87.4", reply_to_message_id=99
    )
    assert handled is True
    row = next(r for r in bm.read_metrics(metrics)[1] if r["date"] == date.today().isoformat())
    assert row["weight_kg"] == "87.4"


async def test_a_bare_number_that_is_not_a_reply_is_left_alone(metrics):
    """The safety property: an ordinary "87.4" must stay an ordinary message."""
    bm.set_prompt(CHAT, "weigh", date.today().isoformat(), 99)
    ch = _Self()
    handled = await TelegramChannel._handle_pending_body_input(
        ch, chat_id=CHAT, text="87.4", reply_to_message_id=None
    )
    assert handled is False
    assert not [r for r in bm.read_metrics(metrics)[1] if r.get("weight_kg")]


async def test_a_reply_to_a_DIFFERENT_message_is_left_alone(metrics):
    bm.set_prompt(CHAT, "weigh", date.today().isoformat(), 99)
    ch = _Self()
    handled = await TelegramChannel._handle_pending_body_input(
        ch, chat_id=CHAT, text="87.4", reply_to_message_id=12345
    )
    assert handled is False
    assert not [r for r in bm.read_metrics(metrics)[1] if r.get("weight_kg")]


async def test_with_no_prompt_open_nothing_is_consumed(metrics):
    ch = _Self()
    handled = await TelegramChannel._handle_pending_body_input(
        ch, chat_id=CHAT, text="87.4", reply_to_message_id=99
    )
    assert handled is False


async def test_an_ordinary_sentence_is_never_filed_as_a_treatment_note(metrics):
    """Without the reply-id check this would land verbatim in the record."""
    bm.set_prompt(CHAT, "note", date.today().isoformat(), 99)
    ch = _Self()
    handled = await TelegramChannel._handle_pending_body_input(
        ch, chat_id=CHAT, text="what is the weather tomorrow?", reply_to_message_id=None
    )
    assert handled is False
    assert not [r for r in bm.read_metrics(metrics)[1] if r.get("notes")]


async def test_a_failure_answers_rather_than_going_silent(metrics, monkeypatch):
    """The question was ours; swallowing the answer with no reply is the bug."""
    bm.set_prompt(CHAT, "weigh", date.today().isoformat(), 99)

    def boom(*_a, **_k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(ba, "consume_reply", boom)
    ch = _Self()
    handled = await TelegramChannel._handle_pending_body_input(
        ch, chat_id=CHAT, text="87.4", reply_to_message_id=99
    )
    assert handled is True
    assert "Could not record" in ch.last


async def test_a_failure_BEFORE_the_prompt_matches_lets_the_message_through(monkeypatch):
    """A broken gate must never make the bot deaf to ordinary messages."""

    def boom(*_a, **_k):
        raise RuntimeError("pin unreadable")

    monkeypatch.setattr(bm, "pending_prompt", boom)
    ch = _Self()
    handled = await TelegramChannel._handle_pending_body_input(
        ch, chat_id=CHAT, text="hello", reply_to_message_id=99
    )
    assert handled is False
