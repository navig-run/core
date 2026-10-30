"""Closing the day edits the card the operator is already looking at, and the
next morning the chat holds exactly one habit card again.

The behaviour the operator asked for, in their words: "if i click close day close
card appears edit inline or its end of the day it sends and after at the morning
deletes". Two paths, one rule:

* tapping `Закрыть день` (or the 23:55 job) turns that exact message into the
  verdict — rich where the chat supports it, never a second card;
* posting a new verdict message is only the fallback for an edit Telegram refuses
  (deleted card, too old to edit), and that fallback is what gets deleted at 08:00.

Everything here is about which message Telegram is told to touch, so the fake
records method names and payloads rather than rendering anything.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from navig.commands import habit as habit_cmd
from navig.spaces import habit_tracker

DAY = "2026-08-10"
STAMP = "20260810"


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    monkeypatch.setattr(habit_tracker, "_targets_file", lambda: tmp_path / "targets.json")
    monkeypatch.setattr(habit_tracker, "tracker_path", lambda space=None: tmp_path / "habits.csv")


@pytest.fixture
def tracker(tmp_path) -> Path:
    path = tmp_path / "habits.csv"
    path.write_text("date,habit,completed,notes\n", encoding="utf-8")
    return path


class FakeTelegram:
    """Answers the Bot API the way a rich-capable server does."""

    def __init__(self, *, rich_edit_ok: bool = True, send_ok: bool = True):
        self.calls: list[tuple[str, dict]] = []
        self.rich_edit_ok = rich_edit_ok
        self.send_ok = send_ok
        self._next_id = 500

    def __call__(self, token, method, payload):
        self.calls.append((method, payload))
        if method == "editMessageText":
            if "rich_message" in payload and not self.rich_edit_ok:
                raise RuntimeError("Bad Request: can't parse entities")
            return {"ok": True, "result": True}
        if method in ("sendRichMessage", "sendMessage"):
            if not self.send_ok:
                raise RuntimeError("Bad Request: chat not found")
            self._next_id += 1
            return {"ok": True, "result": {"message_id": self._next_id}}
        if method == "deleteMessage":
            return {"ok": True, "result": True}
        raise AssertionError(f"unexpected method {method}")

    @property
    def methods(self) -> list[str]:
        return [m for m, _ in self.calls]

    def payload(self, method: str) -> dict:
        found = [p for m, p in self.calls if m == method]
        assert found, f"no {method} was sent"
        return found[-1]


@pytest.fixture
def bot(monkeypatch):
    fake = FakeTelegram()
    import navig.telegram.api as api

    monkeypatch.setattr(api, "_api_call", fake)
    monkeypatch.setattr(api, "_load_telegram_token", lambda: "token")
    return fake


class TestCloseEditsInPlace:
    def test_the_last_card_becomes_the_verdict(self, tracker, bot):
        habit_tracker.remember_target(159901607, tracker, message_id=4242, day=DAY)

        habit_cmd._close_day(tracker, DAY, 159901607)

        assert "editMessageText" in bot.methods
        payload = bot.payload("editMessageText")
        assert payload["message_id"] == 4242
        assert payload["reply_markup"] == {"inline_keyboard": []}
        # Rich first, so the checklist and pull quote survive into the verdict.
        assert "<h2>" in payload["rich_message"]["html"]

    def test_no_second_card_is_posted_when_the_edit_lands(self, tracker, bot):
        habit_tracker.remember_target(159901607, tracker, message_id=4242, day=DAY)

        habit_cmd._close_day(tracker, DAY, 159901607)

        assert "sendMessage" not in bot.methods
        assert "sendRichMessage" not in bot.methods

    def test_a_refused_rich_edit_falls_back_to_the_plain_body(self, tracker, bot):
        bot.rich_edit_ok = False
        habit_tracker.remember_target(159901607, tracker, message_id=4242, day=DAY)

        habit_cmd._close_day(tracker, DAY, 159901607)

        edits = [p for m, p in bot.calls if m == "editMessageText"]
        assert len(edits) == 2
        assert "text" in edits[-1] and "parse_mode" in edits[-1]
        assert "sendRichMessage" not in bot.methods

    def test_an_undeletable_card_still_gets_a_verdict_message(self, tracker, bot):
        # No stored last card: the 22:15 card was sent by an older build.
        habit_cmd._close_day(tracker, DAY, 159901607)

        assert "sendRichMessage" in bot.methods
        assert "editMessageText" not in bot.methods

    def test_the_verdict_message_is_remembered_for_the_morning(self, tracker, bot):
        habit_cmd._close_day(tracker, DAY, 159901607)

        message_id, day = habit_tracker.closing_card(159901607)
        assert message_id == 501
        assert day == DAY
        assert habit_tracker.is_day_closed(159901607, DAY)

    def test_an_already_closed_day_is_left_alone(self, tracker, bot):
        habit_tracker.mark_day_closed(159901607, DAY)

        habit_cmd._close_day(tracker, DAY, 159901607)

        assert bot.calls == []


class TestTheMorningClearsTheOldVerdict:
    def test_yesterdays_verdict_is_deleted(self, bot):
        habit_tracker.remember_closing(159901607, 777, "2026-08-09")

        removed = habit_cmd._delete_closing_card(159901607, bot, "token", keep_day=DAY)

        assert removed is True
        assert bot.payload("deleteMessage") == {"chat_id": 159901607, "message_id": 777}
        assert habit_tracker.closing_card(159901607) == (None, None)

    def test_todays_verdict_is_kept(self, bot):
        habit_tracker.remember_closing(159901607, 777, DAY)

        removed = habit_cmd._delete_closing_card(159901607, bot, "token", keep_day=DAY)

        assert removed is False
        assert bot.calls == []
        assert habit_tracker.closing_card(159901607) == (777, DAY)

    def test_a_card_telegram_refuses_loses_its_pointer_anyway(self, bot):
        habit_tracker.remember_closing(159901607, 777, "2026-08-09")

        def boom(*_a, **_k):
            raise RuntimeError("Bad Request: message to delete not found")

        removed = habit_cmd._delete_closing_card(159901607, boom, "token", keep_day=DAY)

        assert removed is False
        assert habit_tracker.closing_card(159901607) == (None, None)


class TestTappingClose:
    """The handler path: a tap must produce the same verdict, in the same message."""

    def _tap(self, tracker, channel, cb_data):
        from navig.telegram import habit_actions

        habit_tracker.remember_target(-999, tracker)
        return asyncio.run(habit_actions.handle_callback(channel, cb_data, -999, 31337))

    def _channel(self):
        from navig.telegram import habit_actions

        habit_actions_mod = habit_actions

        class Channel:
            def __init__(self):
                self.calls = []

            async def _api_call(self, method, payload):
                self.calls.append((method, payload))
                return {"ok": True}

        return Channel(), habit_actions_mod

    def test_the_tapped_message_becomes_the_verdict(self, tracker):
        channel, _mod = self._channel()

        self._tap(tracker, channel, f"hb:x:{STAMP}")

        edits = [p for m, p in channel.calls if m == "editMessageText"]
        assert edits, "closing must edit the card the operator tapped"
        assert edits[0]["message_id"] == 31337
        assert edits[0]["reply_markup"] == {"inline_keyboard": []}

    def test_the_tapped_card_is_remembered_for_the_morning(self, tracker):
        channel, _mod = self._channel()

        self._tap(tracker, channel, f"hb:x:{STAMP}")

        assert habit_tracker.closing_card(-999) == (31337, DAY)
