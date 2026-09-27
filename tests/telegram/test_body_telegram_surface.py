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
        self.sent.append(
            {"text": payload.get("text"), "method": method, "markup": payload.get("reply_markup")}
        )
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


async def test_health_reports_an_unreadable_record_as_a_warning_not_a_zero(metrics, monkeypatch):
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


# ── settling the prompt in place ──────────────────────────────────────────────


class _Editable(_Chan):
    """Records deletes and edits separately from sends, and can refuse either.

    ``delete_ok=False`` is the over-48h prompt Telegram will not let a bot
    delete — the case the in-place rewrite still exists to cover.
    """

    def __init__(self, edit_ok: bool = True, delete_ok: bool = True):
        super().__init__()
        self.edit_ok = edit_ok
        self.delete_ok = delete_ok
        self.edits: list[dict] = []
        self.deletes: list[dict] = []

    async def _api_call(self, method, payload):
        if method == "deleteMessage":
            if not self.delete_ok:
                return None  # how `_api_call` reports a refusal
            self.deletes.append(payload)
            return True
        if method == "editMessageText":
            if not self.edit_ok:
                raise RuntimeError("message to edit not found")
            self.edits.append(payload)
            return {"result": {"message_id": payload["message_id"]}}
        return await super()._api_call(method, payload)


async def test_answering_rewrites_the_prompt_and_still_confirms(metrics):
    """Both halves, because they are different jobs.

    The prompt is force_reply, and clients re-arm that box after a restart —
    quoting the original text. While it reads as a question, an answered
    check-in looks like it is being asked again, so it must be rewritten.

    ⚠ This test used to also assert that NO confirmation message was sent
    ("rewrites the prompt INSTEAD OF posting a second message"). That assertion
    encoded the defect. An edit is invisible in the clients that matter: it
    raises no notification and does not move the message to the bottom of the
    chat — so once anything arrives afterwards, the answer reads as discarded.
    Measured on the operator's own chat: they answered the weigh-in, a cron
    reminder landed five minutes later, and they reported the weight as not
    counted. It had been recorded correctly; only the receipt was missing.

    The sibling `test_a_failed_edit_still_confirms_by_message` already stated
    the right principle — "an answer that produces no visible acknowledgement is
    the exact failure the disk-backed prompt exists to prevent" — but applied it
    only when the edit FAILED. It is just as true when the edit succeeds.

    Retiring the stale question is CLEANUP; telling the human is a MESSAGE.

    ⚠ Updated 2026-09-27: the cleanup is now a DELETE, not a rewrite. Rewriting
    changes the text the client quotes but leaves its force_reply box pointed at
    the same message id, so the box re-arms on every app restart and the
    operator keeps seeing the morning's question. Measured: not one edit was
    rejected that day, and they still reported being "asked a couple of times a
    day". The rewrite survives as the fallback for prompts too old to delete.
    """
    bm.set_prompt(CHAT, "weigh", date.today().isoformat(), 99)
    ch = _Editable()

    handled = await TelegramChannel._handle_pending_body_input(
        ch, chat_id=CHAT, text="87.4", reply_to_message_id=99
    )

    assert handled is True
    # The cleanup still happens — now by removing the message outright.
    assert len(ch.deletes) == 1
    assert ch.deletes[0]["message_id"] == 99
    assert ch.edits == [], "a deleted prompt must not also be edited"
    # ...and the operator is told, which is the part that was missing.
    receipts = [s for s in ch.sent if s.get("text", "").startswith("✅")]
    assert len(receipts) == 1, (
        "retiring the prompt produced no visible acknowledgement — the operator "
        "answers the weigh-in and sees nothing new"
    )
    assert "87.4" in receipts[0]["text"]


async def test_the_answered_question_is_gone_from_the_chat(metrics):
    """The question must not survive as text a re-armed reply box can quote."""
    bm.set_prompt(CHAT, "weigh", date.today().isoformat(), 99)
    ch = _Editable()
    await TelegramChannel._handle_pending_body_input(
        ch, chat_id=CHAT, text="87.4", reply_to_message_id=99
    )
    assert ch.deletes and ch.deletes[0]["message_id"] == 99
    assert all("?" not in (s.get("text") or "") for s in ch.sent)


async def test_a_prompt_too_old_to_delete_is_rewritten_instead(metrics):
    """Telegram refuses a bot delete past 48 h. The rewrite still has to retire
    the question then — it is strictly better than leaving it standing."""
    bm.set_prompt(CHAT, "weigh", date.today().isoformat(), 99)
    ch = _Editable(delete_ok=False)

    await TelegramChannel._handle_pending_body_input(
        ch, chat_id=CHAT, text="87.4", reply_to_message_id=99
    )

    assert len(ch.edits) == 1, "the fallback rewrite did not happen"
    assert ch.edits[0]["message_id"] == 99
    assert "87.4" in ch.edits[0]["text"]
    assert "?" not in ch.edits[0]["text"]


async def test_the_fallback_rewrite_clears_any_keyboard(metrics):
    """Omitting reply_markup leaves stale buttons live on a settled message."""
    bm.set_prompt(CHAT, "weigh", date.today().isoformat(), 99)
    ch = _Editable(delete_ok=False)
    await TelegramChannel._handle_pending_body_input(
        ch, chat_id=CHAT, text="87.4", reply_to_message_id=99
    )
    assert ch.edits[0]["reply_markup"] == {"inline_keyboard": []}


async def test_a_failed_cleanup_still_confirms_by_message(metrics):
    """An answer that produces no visible acknowledgement is the exact failure
    the disk-backed prompt exists to prevent — even when BOTH the delete and the
    rewrite fail."""
    bm.set_prompt(CHAT, "weigh", date.today().isoformat(), 99)
    ch = _Editable(edit_ok=False, delete_ok=False)

    handled = await TelegramChannel._handle_pending_body_input(
        ch, chat_id=CHAT, text="87.4", reply_to_message_id=99
    )

    assert handled is True
    assert ch.edits == []
    assert any("87.4" in (s.get("text") or "") for s in ch.sent)
    # and the weight still landed despite the cleanup failing
    row = next(r for r in bm.read_metrics(metrics)[1] if r["date"] == date.today().isoformat())
    assert row["weight_kg"] == "87.4"


async def test_the_seven_day_average_survives_the_delete(metrics):
    """It used to live in the rewritten prompt. With the prompt deleted, the
    receipt is the only message left to carry it."""
    from datetime import timedelta

    end = date.today()
    for i in range(6):
        bm.upsert(metrics, (end - timedelta(days=6 - i)).isoformat(), {"weight_kg": "88"})
    bm.set_prompt(CHAT, "weigh", end.isoformat(), 99)
    ch = _Editable()
    await TelegramChannel._handle_pending_body_input(
        ch, chat_id=CHAT, text="88", reply_to_message_id=99
    )
    receipts = [s for s in ch.sent if (s.get("text") or "").startswith("✅")]
    assert receipts, "no receipt at all"
    assert "average" in receipts[0]["text"].lower()


async def test_the_fallback_rewrite_still_carries_the_seven_day_average(metrics):
    from datetime import timedelta

    end = date.today()
    for i in range(6):
        bm.upsert(metrics, (end - timedelta(days=6 - i)).isoformat(), {"weight_kg": "88"})
    bm.set_prompt(CHAT, "weigh", end.isoformat(), 99)
    ch = _Editable(delete_ok=False)
    await TelegramChannel._handle_pending_body_input(
        ch, chat_id=CHAT, text="88", reply_to_message_id=99
    )
    assert "average" in ch.edits[0]["text"].lower()


async def test_a_skip_does_not_rewrite_the_prompt_with_a_weight(metrics):
    """Nothing was recorded, so there is no value to settle it with."""
    bm.set_prompt(CHAT, "weigh", date.today().isoformat(), 99)
    ch = _Editable()
    await TelegramChannel._handle_pending_body_input(
        ch, chat_id=CHAT, text="skip", reply_to_message_id=99
    )
    assert ch.edits == []
    assert ch.sent, "the operator still gets an acknowledgement"
