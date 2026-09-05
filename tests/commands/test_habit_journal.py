"""
Tests for the evening journal entry — navig.spaces.journal plus the two writers.

The tracker recorded whether a day happened and nothing recorded what happened
in it, although the card has always ended with "three lines in the journal".
These tests pin the two things that make the entry trustworthy: it never invents
labels for lines the owner did not write, and a redelivered Telegram reply
cannot write the same evening twice.
"""

from __future__ import annotations

import asyncio

import pytest

from navig.spaces import habit_tracker, journal
from navig.telegram import habit_actions

DAY = "2026-08-26"
CHAT = -777


@pytest.fixture
def tracker(tmp_path):
    return tmp_path / "habits.csv"


@pytest.fixture(autouse=True)
def _isolate_targets(tmp_path, monkeypatch):
    monkeypatch.setattr(habit_tracker, "_targets_file", lambda: tmp_path / "targets.json")


class FakeChannel:
    """Records API payloads instead of talking to Telegram."""

    def __init__(self, message_id: int = 5150):
        self.calls: list[tuple[str, dict]] = []
        self.message_id = message_id
        self.sent: list[str] = []

    async def _api_call(self, method, payload):
        self.calls.append((method, payload))
        return {"ok": True, "result": {"message_id": self.message_id}}

    async def send_message(self, chat_id, text, parse_mode=None):
        self.sent.append(text)


# ===========================================================================
# journal.append_entry
# ===========================================================================

class TestAppendEntry:
    def test_new_file_gets_a_title_and_the_three_labels(self, tracker):
        path, written = journal.append_entry(
            tracker, DAY, "shipped the pricing page\nlost an hour to a client\nwrite the README"
        )
        body = path.read_text(encoding="utf-8")

        assert written is True
        assert path == tracker.parent / "journal" / f"{DAY}.md"
        assert body.startswith("# Journal — Wednesday, 26 August 2026")
        assert "1. **Proud of:** shipped the pricing page" in body
        assert "2. **What knocked me off:** lost an hour to a client" in body
        assert "3. **Tomorrow's number one:** write the README" in body

    def test_two_lines_are_written_verbatim_without_labels(self, tracker):
        """Labelling two lines with three questions would invent content."""
        path, _ = journal.append_entry(tracker, DAY, "walked 8 km\nbed by 23:30")
        body = path.read_text(encoding="utf-8")

        assert "- walked 8 km" in body
        assert "- bed by 23:30" in body
        for prompt in journal.PROMPTS:
            assert prompt not in body

    def test_owner_numbering_is_not_doubled(self, tracker):
        path, _ = journal.append_entry(tracker, DAY, "1. finished it\n2. slept badly\n3. call mom")
        body = path.read_text(encoding="utf-8")

        assert "1. **Proud of:** finished it" in body
        assert "1. 1." not in body

    def test_identical_text_is_not_written_twice(self, tracker):
        """Telegram redelivers updates; a retry must not duplicate the evening."""
        text = "shipped it\nnothing broke\nrest"
        journal.append_entry(tracker, DAY, text)
        path, written = journal.append_entry(tracker, DAY, text)

        assert written is False
        assert path.read_text(encoding="utf-8").count("shipped it") == 1

    def test_a_second_different_entry_is_marked_as_added_later(self, tracker):
        journal.append_entry(tracker, DAY, "first pass")
        path, written = journal.append_entry(tracker, DAY, "remembered one more thing")
        body = path.read_text(encoding="utf-8")

        assert written is True
        assert "## Evening check-in" in body
        assert "## Evening check-in (added later)" in body
        assert "first pass" in body and "remembered one more thing" in body

    def test_empty_text_writes_nothing(self, tracker):
        path, written = journal.append_entry(tracker, DAY, "   \n\n  ")
        assert written is False
        assert not path.exists()

    def test_unparseable_day_still_gets_a_file(self, tracker):
        path, written = journal.append_entry(tracker, "not-a-date", "something")
        assert written is True
        assert path.read_text(encoding="utf-8").startswith("# Journal — not-a-date")


# ===========================================================================
# The prompt the closing card leaves open
# ===========================================================================

class TestJournalPrompt:
    def test_closing_the_day_asks_for_the_three_lines(self, tracker):
        channel = FakeChannel()
        habit_tracker.remember_target(CHAT, tracker)

        asyncio.run(habit_actions.handle_callback(channel, "hb:x:20260826", CHAT, 42))

        methods = [m for m, _ in channel.calls]
        assert methods == ["editMessageText", "sendMessage"]
        prompt = channel.calls[-1][1]
        assert prompt["reply_markup"] == {"force_reply": True, "selective": True}
        assert "Three lines" in prompt["text"]
        assert f"journal/{DAY}.md" in prompt["text"]

    def test_the_prompt_is_remembered_so_a_restart_cannot_drop_it(self, tracker):
        channel = FakeChannel(message_id=909)
        habit_tracker.remember_target(CHAT, tracker)

        asyncio.run(habit_actions.handle_callback(channel, "hb:x:20260826", CHAT, 42))

        assert habit_tracker.journal_prompt(CHAT) == (DAY, 909)

    def test_a_failed_prompt_does_not_break_closing_the_day(self, tracker):
        class Broken(FakeChannel):
            async def _api_call(self, method, payload):
                self.calls.append((method, payload))
                if method == "sendMessage":
                    raise RuntimeError("telegram down")
                return {"ok": True, "result": {}}

        channel = Broken()
        habit_tracker.remember_target(CHAT, tracker)

        toast = asyncio.run(habit_actions.handle_callback(channel, "hb:x:20260826", CHAT, 42))

        assert toast == "Day closed"
        assert habit_tracker.is_day_closed(CHAT, DAY) is True
        assert habit_tracker.journal_prompt(CHAT) == (None, None)

    def test_clear_removes_the_prompt(self, tracker):
        habit_tracker.set_journal_prompt(CHAT, DAY, 11)
        habit_tracker.clear_journal_prompt(CHAT)
        assert habit_tracker.journal_prompt(CHAT) == (None, None)

    def test_clearing_keeps_the_tracker_pin(self, tracker):
        """The chat must still know which habits.csv it belongs to afterwards."""
        habit_tracker.remember_target(CHAT, tracker)
        habit_tracker.set_journal_prompt(CHAT, DAY, 11)
        habit_tracker.clear_journal_prompt(CHAT)
        assert habit_tracker.resolve_target(CHAT) == tracker


# ===========================================================================
# The gateway consuming the reply
# ===========================================================================

class TestGatewayCapture:
    def _handler(self, channel):
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        return lambda text, reply_to: asyncio.run(
            TelegramCommandsMixin._handle_pending_journal_input(channel, CHAT, text, reply_to)
        )

    def test_reply_to_the_prompt_is_written_and_confirmed(self, tracker):
        channel = FakeChannel()
        habit_tracker.remember_target(CHAT, tracker)
        habit_tracker.set_journal_prompt(CHAT, DAY, 909)

        consumed = self._handler(channel)("shipped\nslept badly\ncall mom", 909)

        assert consumed is True
        assert "journal" in channel.sent[-1]
        assert "shipped" in journal.entry_path(tracker, DAY).read_text(encoding="utf-8")
        assert habit_tracker.journal_prompt(CHAT) == (None, None)

    def test_an_ordinary_message_is_never_swallowed(self, tracker):
        """No reply id, so it stays a question for the agent — not a journal entry."""
        channel = FakeChannel()
        habit_tracker.remember_target(CHAT, tracker)
        habit_tracker.set_journal_prompt(CHAT, DAY, 909)

        assert self._handler(channel)("what's the weather tomorrow?", None) is False
        assert not journal.entry_path(tracker, DAY).exists()

    def test_a_reply_to_a_different_message_is_ignored(self, tracker):
        channel = FakeChannel()
        habit_tracker.remember_target(CHAT, tracker)
        habit_tracker.set_journal_prompt(CHAT, DAY, 909)

        assert self._handler(channel)("some other reply", 123) is False
        assert not journal.entry_path(tracker, DAY).exists()

    def test_skip_closes_the_prompt_without_writing(self, tracker):
        channel = FakeChannel()
        habit_tracker.remember_target(CHAT, tracker)
        habit_tracker.set_journal_prompt(CHAT, DAY, 909)

        assert self._handler(channel)("skip", 909) is True
        assert not journal.entry_path(tracker, DAY).exists()
        assert habit_tracker.journal_prompt(CHAT) == (None, None)

    def test_no_open_prompt_means_nothing_is_consumed(self, tracker):
        channel = FakeChannel()
        habit_tracker.remember_target(CHAT, tracker)

        assert self._handler(channel)("three lines", 909) is False
