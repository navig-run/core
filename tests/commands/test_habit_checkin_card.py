"""
Tests for navig.telegram.habit_actions — the one-tap Telegram check-in card.

The card is sent by the CLI and its buttons are handled by the gateway, in a
different process whose active space is very likely something else. The guard
against taps landing in the wrong habits.csv is `remember_target` /
`resolve_target`, so that pairing is tested here explicitly.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from navig.spaces import habit_tracker
from navig.telegram import habit_actions

DAY = "2026-08-10"
STAMP = "20260810"


@pytest.fixture
def tracker(tmp_path):
    return tmp_path / "habits.csv"


class FakeChannel:
    """Records editMessageText payloads instead of talking to Telegram."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def _api_call(self, method, payload):
        self.calls.append((method, payload))
        return {"ok": True}

    @property
    def last_payload(self) -> dict:
        return self.calls[-1][1]


def _tap(channel, tracker, cb_data):
    """Route one button press at an explicit tracker file."""
    habit_tracker.remember_target(-999, tracker)
    return asyncio.run(habit_actions.handle_callback(channel, cb_data, -999, 42))


@pytest.fixture(autouse=True)
def _isolate_targets(tmp_path, monkeypatch):
    monkeypatch.setattr(
        habit_tracker, "_targets_file", lambda: tmp_path / "targets.json"
    )
    # Every assertion below is about the ENGLISH copy ("Wake", "The day counts").
    # The cards are localized off the single global language, and a test elsewhere in
    # the gate's selection can leave it on Russian — measured 2026-10-19: the whole
    # file failed with "✍ Чек-ин · 10 августа" while passing alone. Pin the language
    # so this file proves the card, not the language of whichever test ran first.
    monkeypatch.setattr("navig.core.language.resolve_language", lambda *a, **k: "en")


# ===========================================================================
# build_card
# ===========================================================================

class TestBuildCard:
    def test_empty_day_lists_every_non_negotiable_as_open(self, tracker):
        text, _ = habit_actions.build_card(tracker, DAY)
        for label in habit_tracker.NON_NEGOTIABLES:
            assert habit_actions.LABEL_WORDS[label] in text
        assert "Still open" in text

    def test_all_three_done_says_the_day_counts(self, tracker):
        for label in habit_tracker.NON_NEGOTIABLES:
            habit_tracker.upsert(tracker, DAY, label, "yes")
        text, _ = habit_actions.build_card(tracker, DAY)
        assert "The day counts" in text

    def test_done_habits_render_checked(self, tracker):
        habit_tracker.upsert(tracker, DAY, "wake", "yes")
        _, keyboard = habit_actions.build_card(tracker, DAY)
        wake = keyboard["inline_keyboard"][0][0]
        assert wake["text"] == "✓ Wake"
        assert wake["style"] == "success"
        assert keyboard["inline_keyboard"][0][1]["text"] == "Walk"
        assert "style" not in keyboard["inline_keyboard"][0][1]

    def test_pending_row_renders_unchecked(self, tracker):
        """A day-1 seeded 'pending' row is not a done habit."""
        habit_tracker.upsert(tracker, DAY, "wake", "pending")
        _, keyboard = habit_actions.build_card(tracker, DAY)
        assert keyboard["inline_keyboard"][0][0]["text"] == "Wake"

    def test_callback_data_fits_telegram_budget(self, tracker):
        _, keyboard = habit_actions.build_card(tracker, DAY)
        for row in keyboard["inline_keyboard"]:
            for button in row:
                assert len(button["callback_data"].encode()) <= 64

    def test_every_button_carries_the_day(self, tracker):
        """A card left open past midnight must still write to its own day."""
        _, keyboard = habit_actions.build_card(tracker, DAY)
        for row in keyboard["inline_keyboard"]:
            for button in row:
                assert STAMP in button["callback_data"]

    def test_card_has_a_close_button(self, tracker):
        _, keyboard = habit_actions.build_card(tracker, DAY)
        assert keyboard["inline_keyboard"][-1][0]["callback_data"] == f"hb:x:{STAMP}"


# ===========================================================================
# handle_callback
# ===========================================================================

class TestTapping:
    def test_tap_marks_done(self, tracker):
        channel = FakeChannel()
        toast = _tap(channel, tracker, f"hb:t:wake:{STAMP}")

        assert "Wake" in toast
        assert habit_tracker.day_map(tracker, DAY)["wake"] == "yes"

    def test_tap_again_unmarks(self, tracker):
        channel = FakeChannel()
        _tap(channel, tracker, f"hb:t:wake:{STAMP}")
        _tap(channel, tracker, f"hb:t:wake:{STAMP}")

        assert habit_tracker.day_map(tracker, DAY)["wake"] == "no"

    def test_tapping_never_duplicates_a_row(self, tracker):
        channel = FakeChannel()
        for _ in range(5):
            _tap(channel, tracker, f"hb:t:out:{STAMP}")

        rows = [r for r in habit_tracker.read_tracker(tracker) if r["habit"] == "out"]
        assert len(rows) == 1

    def test_tap_edits_the_card_in_place(self, tracker):
        channel = FakeChannel()
        _tap(channel, tracker, f"hb:t:wake:{STAMP}")

        method, payload = channel.calls[-1]
        assert method == "editMessageText"
        assert payload["message_id"] == 42
        assert payload["reply_markup"]["inline_keyboard"][0][0]["text"] == "✓ Wake"

    def test_score_is_stored(self, tracker):
        channel = FakeChannel()
        toast = _tap(channel, tracker, f"hb:s:8:{STAMP}")

        assert "8" in toast
        assert habit_tracker.day_map(tracker, DAY)["score"] == "8"

    # Closing the day now also sends the journal prompt, so the verdict is no
    # longer the last payload — these assert against the card edit itself.
    def _closing_edit(self, channel) -> dict:
        edits = [p for method, p in channel.calls if method == "editMessageText"]
        assert edits, "closing the day must still edit the card"
        return edits[-1]

    def test_closing_removes_the_keyboard(self, tracker):
        channel = FakeChannel()
        _tap(channel, tracker, f"hb:x:{STAMP}")

        payload = self._closing_edit(channel)
        assert payload["reply_markup"] == {"inline_keyboard": []}
        assert "closed" in payload["text"]

    def test_closing_an_incomplete_day_warns_about_tomorrow(self, tracker):
        habit_tracker.upsert(tracker, DAY, "wake", "yes")
        channel = FakeChannel()
        _tap(channel, tracker, f"hb:x:{STAMP}")

        assert "NEVER MISS TWICE" in self._closing_edit(channel)["text"]

    def test_closing_a_complete_day_confirms_it(self, tracker):
        for label in habit_tracker.NON_NEGOTIABLES:
            habit_tracker.upsert(tracker, DAY, label, "yes")
        channel = FakeChannel()
        _tap(channel, tracker, f"hb:x:{STAMP}")

        assert "The day counts" in self._closing_edit(channel)["text"]


class TestTheThreeAreNamed:
    """The reported defect: "2/3 non-negotiables" is a score, not an answer.

    It named neither what the three were nor which one was missed — on the one
    card whose whole job is to say how the day went.
    """

    def _closing(self, tracker) -> str:
        return habit_actions.build_closing_text(tracker, DAY)

    def test_closing_names_the_missed_one(self, tracker):
        habit_tracker.upsert(tracker, DAY, "wake", "yes")
        habit_tracker.upsert(tracker, DAY, "ship", "yes")
        text = self._closing(tracker)

        assert "Missed: Walk" in text
        assert "2/3" not in text

    def test_closing_names_every_missed_one(self, tracker):
        habit_tracker.upsert(tracker, DAY, "wake", "yes")
        text = self._closing(tracker)

        assert "Walk" in text
        assert "Ship" in text

    def test_closing_shows_all_three_with_their_state(self, tracker):
        habit_tracker.upsert(tracker, DAY, "wake", "yes")
        text = self._closing(tracker)

        # The closing card renders the same task list the check-in does, so its
        # markers are the card's (☑/☐), not the old dot-joined line's (✓/·).
        assert "☑ Wake" in text
        assert "☐ Walk" in text

    def test_no_card_says_non_negotiable(self, tracker):
        """A word the owner had to ask about does not belong on the card."""
        habit_tracker.upsert(tracker, DAY, "wake", "yes")
        for text in (
            habit_actions.build_card(tracker, DAY)[0],
            habit_actions.build_card(tracker, DAY, morning=True)[0],
            self._closing(tracker),
        ):
            assert "negotiab" not in text.lower()

    def test_every_card_names_the_three(self, tracker):
        for text in (
            habit_actions.build_card(tracker, DAY)[0],
            habit_actions.build_card(tracker, DAY, morning=True)[0],
            self._closing(tracker),
        ):
            # The section is named through the locale now: "Required" / "Обязательные".
            assert habit_actions._t("habit.card.floor_title") in text
            for label in habit_tracker.NON_NEGOTIABLES:
                assert habit_actions.LABEL_WORDS[label] in text

    def test_score_is_not_listed_as_a_habit(self, tracker):
        """"Logged: ..., score" read as if scoring were something you do."""
        for label in habit_tracker.NON_NEGOTIABLES:
            habit_tracker.upsert(tracker, DAY, label, "yes")
        habit_tracker.upsert(tracker, DAY, "score", "8")
        text = self._closing(tracker)

        assert "Also done" not in text
        assert "Day score: 8/10" in text

    def test_extras_are_listed_in_words(self, tracker):
        habit_tracker.upsert(tracker, DAY, "caffeine_cutoff", "yes")
        text = self._closing(tracker)

        # The extra habit is shown inside its section with a real checkbox —
        # the old "Also done: …" sentence listed it apart from the three it sits
        # next to in the same card.
        assert "☑ No coffee" in text
        assert "caffeine_cutoff" not in text

    def test_a_label_without_a_word_still_renders(self, tracker):
        habit_tracker.upsert(tracker, DAY, "made_up_label", "yes")
        assert "made_up_label" in self._closing(tracker)

    def test_unknown_action_is_reported_not_raised(self, tracker):
        channel = FakeChannel()
        assert "⚠️" in _tap(channel, tracker, "hb:zzz")

    def test_malformed_callback_does_not_raise(self, tracker):
        channel = FakeChannel()
        assert _tap(channel, tracker, "hb:t")


# ===========================================================================
# Which tracker a card belongs to
# ===========================================================================

class TestTargetPairing:
    def test_tap_writes_to_the_card_s_own_tracker(self, tmp_path, monkeypatch):
        """The regression this exists to prevent: gateway logging into the wrong space."""
        card_space = tmp_path / "growth-space"
        card_space.mkdir()
        card_tracker = card_space / "habits.csv"

        wrong = tmp_path / "some-other-space"
        wrong.mkdir()
        monkeypatch.setattr(habit_tracker, "tracker_path", lambda *a, **k: wrong / "habits.csv")

        habit_tracker.remember_target(-777, card_tracker)
        asyncio.run(habit_actions.handle_callback(FakeChannel(), f"hb:t:wake:{STAMP}", -777, 1))

        assert habit_tracker.day_map(card_tracker, DAY).get("wake") == "yes"
        assert not (wrong / "habits.csv").exists()

    def test_unknown_chat_falls_back_to_normal_resolution(self, tmp_path, monkeypatch):
        fallback = tmp_path / "fallback.csv"
        monkeypatch.setattr(habit_tracker, "tracker_path", lambda *a, **k: fallback)

        assert habit_tracker.resolve_target(-12345) == fallback

    def test_target_survives_a_second_card_to_another_chat(self, tmp_path):
        a, b = tmp_path / "a.csv", tmp_path / "b.csv"
        habit_tracker.remember_target(1, a)
        habit_tracker.remember_target(2, b)

        assert habit_tracker.resolve_target(1) == a
        assert habit_tracker.resolve_target(2) == b

    def test_two_cards_in_one_chat_each_keep_their_own_tracker(self, tmp_path):
        """A newer card for another space must not redirect taps on the older card."""
        a, b = tmp_path / "a" / "habits.csv", tmp_path / "b" / "habits.csv"
        habit_tracker.remember_target(5, a, message_id=100, day=DAY)
        habit_tracker.remember_target(5, b, message_id=200, day=DAY)

        assert habit_tracker.resolve_target(5, 100) == a.resolve()
        assert habit_tracker.resolve_target(5, 200) == b.resolve()
        # Without a message id (or for a card never recorded) the latest card wins.
        assert habit_tracker.resolve_target(5) == b.resolve()
        assert habit_tracker.resolve_target(5, 999) == b.resolve()

    def test_tap_on_the_older_card_writes_into_its_own_tracker(self, tmp_path):
        a, b = tmp_path / "a.csv", tmp_path / "b.csv"
        habit_tracker.remember_target(6, a, message_id=10, day=DAY)
        habit_tracker.remember_target(6, b, message_id=11, day=DAY)

        asyncio.run(habit_actions.handle_callback(FakeChannel(), f"hb:t:wake:{STAMP}", 6, 10))

        assert habit_tracker.day_map(a, DAY).get("wake") == "yes"
        assert not b.exists()

    def test_relative_tracker_path_is_persisted_absolute(self, tmp_path, monkeypatch):
        """The gateway resolves the path from a different cwd than the CLI recorded it in."""
        recorded_in = tmp_path / "cli-cwd"
        recorded_in.mkdir()
        elsewhere = tmp_path / "daemon-cwd"
        elsewhere.mkdir()

        monkeypatch.chdir(recorded_in)
        habit_tracker.remember_target(7, Path("space") / "habits.csv")
        monkeypatch.chdir(elsewhere)

        resolved = habit_tracker.resolve_target(7)
        assert resolved.is_absolute()
        assert resolved == (recorded_in / "space" / "habits.csv").resolve()

    def test_corrupt_targets_file_does_not_break_taps(self, tmp_path, monkeypatch):
        (tmp_path / "targets.json").write_text("{not json", encoding="utf-8")
        fallback = tmp_path / "fallback.csv"
        monkeypatch.setattr(habit_tracker, "tracker_path", lambda *a, **k: fallback)

        assert habit_tracker.resolve_target(1) == fallback


def _word_of(button: dict) -> str:
    """The button's word, without the tick a done habit carries."""
    text = str(button.get("text", ""))
    return text[len(habit_actions.MARK_DONE) :].strip() if text.startswith(habit_actions.MARK_DONE) else text.strip()


class TestButtonsAreReadable:
    """The reported defect: buttons showed a white square and an icon, nothing else."""

    def test_every_toggle_button_carries_a_word(self, tracker):
        _, keyboard = habit_actions.build_card(tracker, DAY)
        for row in keyboard["inline_keyboard"][: len(habit_actions.CHECKIN_ROWS)]:
            for button in row:
                word = _word_of(button)
                assert word.strip()
                assert any(c.isalpha() for c in word)

    def test_state_marks_are_distinguishable(self, tracker):
        """One tick where it means something, and a colour under it.

        The old pair (✅ / ❌) read as "good" and "failed" at 22:15. The operator asked
        for fewer symbols, so the done button carries `✓` and a green `style`, and the
        open one carries nothing but its word.
        """
        habit_tracker.upsert(tracker, DAY, "wake", "yes")
        _, keyboard = habit_actions.build_card(tracker, DAY)
        done, *rest = keyboard["inline_keyboard"][0]
        assert done["text"].startswith(habit_actions.MARK_DONE)
        assert done["style"] == "success"
        assert all(not b["text"].startswith(habit_actions.MARK_DONE) for b in rest)
        assert all("style" not in b for b in rest)

    def test_the_card_does_not_explain_its_own_buttons(self, tracker):
        """The hint line left on request: six labelled buttons need no instructions.

        It also cost a whole line of a card that is read on a phone at 22:15.
        """
        text, _ = habit_actions.build_card(tracker, DAY)
        assert "Tap what you did" not in text
        assert habit_actions.MARK_DONE not in text.split("•")[0]

    def test_close_button_is_in_english(self, tracker):
        _, keyboard = habit_actions.build_card(tracker, DAY)
        assert "Close the day" in keyboard["inline_keyboard"][-1][0]["text"]

    def test_no_button_word_needs_explaining(self, tracker):
        """A label the owner had to ask about is a label that failed.

        "Groom" sent them to ask what it meant, so button words are held to plain,
        common English. This pins the vocabulary rather than the exact strings.
        """
        _, keyboard = habit_actions.build_card(tracker, DAY)
        plain = {"wake", "walk", "ship", "train", "no", "coffee", "self", "care"}
        for row in keyboard["inline_keyboard"][: len(habit_actions.CHECKIN_ROWS)]:
            for button in row:
                word = _word_of(button)
                parts = [p for p in word.lower().replace("-", " ").split() if p]
                assert set(parts) <= plain, f"unexplained button word: {word}"


# ===========================================================================
# Morning card
# ===========================================================================

class TestMorningCard:
    """Waking up can only be marked as it happens; by 22:15 it is a memory."""

    def test_morning_card_is_framed_as_morning(self, tracker):
        text, _ = habit_actions.build_card(tracker, DAY, morning=True)
        assert "Morning" in text
        assert "Check-in" not in text

    def test_morning_card_points_at_wake(self, tracker):
        text, _ = habit_actions.build_card(tracker, DAY, morning=True)
        assert "Wake" in text

    def test_morning_card_has_the_same_buttons(self, tracker):
        # Same buttons; only the callbacks differ, by the `:m` frame marker.
        _, evening = habit_actions.build_card(tracker, DAY)
        _, morning = habit_actions.build_card(tracker, DAY, morning=True)

        def texts(kb):
            return [[b["text"] for b in row] for row in kb["inline_keyboard"]]

        assert texts(morning) == texts(evening)

    def test_morning_card_reflects_what_is_already_done(self, tracker):
        habit_tracker.upsert(tracker, DAY, "wake", "yes")
        _, keyboard = habit_actions.build_card(tracker, DAY, morning=True)
        assert keyboard["inline_keyboard"][0][0]["text"] == "✓ Wake"

    def test_evening_card_still_lists_what_is_open(self, tracker):
        text, _ = habit_actions.build_card(tracker, DAY, morning=False)
        assert "Still open" in text


# ===========================================================================
# Closing the day
# ===========================================================================

class TestDayClosing:
    def test_tapping_close_marks_the_day_closed(self, tracker):
        channel = FakeChannel()
        _tap(channel, tracker, f"hb:x:{STAMP}")
        assert habit_tracker.is_day_closed(-999, DAY) is True

    def test_a_day_is_not_closed_before_it_is_closed(self, tracker):
        habit_tracker.remember_target(-999, tracker)
        assert habit_tracker.is_day_closed(-999, DAY) is False

    def test_closing_records_nothing_new(self, tracker):
        """Auto-close must never invent data about a life it did not witness."""
        habit_tracker.upsert(tracker, DAY, "wake", "yes")
        before = habit_tracker.read_tracker(tracker)

        channel = FakeChannel()
        _tap(channel, tracker, f"hb:x:{STAMP}")

        assert habit_tracker.read_tracker(tracker) == before

    def test_closed_flag_is_per_day(self, tracker):
        channel = FakeChannel()
        _tap(channel, tracker, f"hb:x:{STAMP}")
        assert habit_tracker.is_day_closed(-999, "2026-08-11") is False


# ===========================================================================
# Card identity — so the day can be closed by editing the right message
# ===========================================================================

class TestCardIdentity:
    def test_message_id_and_day_are_remembered(self, tmp_path):
        habit_tracker.remember_target(-55, tmp_path / "h.csv", message_id=1234, day=DAY)
        assert habit_tracker.last_card(-55) == (1234, DAY)

    def test_unknown_chat_has_no_card(self):
        assert habit_tracker.last_card(-987654) == (None, None)

    def test_a_new_card_replaces_the_remembered_one(self, tmp_path):
        p = tmp_path / "h.csv"
        habit_tracker.remember_target(-55, p, message_id=1, day="2026-08-11")
        habit_tracker.remember_target(-55, p, message_id=2, day=DAY)
        assert habit_tracker.last_card(-55) == (2, DAY)

    def test_legacy_bare_path_state_still_resolves(self, tmp_path, monkeypatch):
        """State written before message ids existed must not break on upgrade."""
        import json

        target = tmp_path / "legacy.csv"
        (tmp_path / "targets.json").write_text(
            json.dumps({"-77": str(target)}), encoding="utf-8"
        )
        assert habit_tracker.resolve_target(-77) == target
        assert habit_tracker.last_card(-77) == (None, None)

    def test_remembering_a_card_keeps_the_legacy_path(self, tmp_path):
        import json

        target = tmp_path / "legacy.csv"
        (tmp_path / "targets.json").write_text(
            json.dumps({"-78": str(target)}), encoding="utf-8"
        )
        habit_tracker.remember_target(-78, target, message_id=9, day=DAY)
        assert habit_tracker.resolve_target(-78) == target
        assert habit_tracker.last_card(-78) == (9, DAY)


# ===========================================================================
# Morning card stays a morning card; midday repost; 19:00 removal
# ===========================================================================

class TestMorningFrameSurvivesATap:
    """A tap on the 08:00 card used to re-render it as the evening "Check-in"."""

    def test_morning_buttons_carry_the_frame_marker(self, tracker):
        _, keyboard = habit_actions.build_card(tracker, DAY, morning=True)
        toggles = [b["callback_data"] for b in keyboard["inline_keyboard"][0]]
        assert all(cb.endswith(":m") for cb in toggles)
        assert keyboard["inline_keyboard"][-1][0]["callback_data"] == f"hb:x:{STAMP}"

    def test_evening_buttons_do_not(self, tracker):
        _, keyboard = habit_actions.build_card(tracker, DAY)
        assert not any(b["callback_data"].endswith(":m")
                       for row in keyboard["inline_keyboard"] for b in row)

    def test_a_tap_on_the_morning_card_keeps_it_morning(self, tracker):
        channel = FakeChannel()
        _tap(channel, tracker, f"hb:t:wake:{STAMP}:m")
        assert habit_tracker.day_map(tracker, DAY)["wake"] == "yes"
        text = channel.last_payload["text"]
        assert "Morning" in text and "Check-in" not in text

    def test_a_score_tap_on_the_morning_card_keeps_it_morning(self, tracker):
        channel = FakeChannel()
        _tap(channel, tracker, f"hb:s:3:{STAMP}:m")
        assert "Morning" in channel.last_payload["text"]

    def test_an_old_card_without_the_marker_still_works(self, tracker):
        channel = FakeChannel()
        _tap(channel, tracker, f"hb:t:wake:{STAMP}")
        assert habit_tracker.day_map(tracker, DAY)["wake"] == "yes"
        assert "Check-in" in channel.last_payload["text"]


class TestMorningSlot:
    def test_morning_slot_is_separate_from_the_last_card(self, tmp_path):
        p = tmp_path / "h.csv"
        habit_tracker.remember_target(-55, p, message_id=10, day=DAY)
        habit_tracker.remember_morning(-55, 10, DAY)
        habit_tracker.remember_target(-55, p, message_id=20, day=DAY)   # 22:15 card
        assert habit_tracker.morning_card(-55) == (10, DAY)
        assert habit_tracker.last_card(-55) == (20, DAY)

    def test_forget_morning(self):
        habit_tracker.remember_morning(-55, 10, DAY)
        habit_tracker.forget_morning(-55)
        assert habit_tracker.morning_card(-55) == (None, None)


class _FakeBot:
    """Stands in for `navig.commands.telegram._api_call` (sync, raises on refusal)."""

    def __init__(self, refuse_delete: bool = False):
        self.calls: list[tuple[str, dict]] = []
        self.next_id = 500
        self.refuse_delete = refuse_delete

    def __call__(self, token, method, payload):
        self.calls.append((method, payload))
        if method == "deleteMessage" and self.refuse_delete:
            raise RuntimeError("Bad Request: message to delete not found")
        if method in ("sendMessage", "sendRichMessage"):
            self.next_id += 1
            return {"ok": True, "result": {"message_id": self.next_id}}
        return {"ok": True, "result": True}

    def methods(self):
        return [m for m, _ in self.calls]


@pytest.fixture
def bot(monkeypatch):
    import navig.commands.telegram as tg_cmd
    import navig.telegram.api as api
    from navig.gateway.channels import telegram_extensions

    fake = _FakeBot()
    # The habit path sends through `navig.telegram.api` (core no longer imports a command
    # module); the command module re-exports the same names, so patch BOTH — a test that
    # drives either surface then sees the fake instead of hitting the Bot API.
    for mod in (api, tg_cmd):
        monkeypatch.setattr(mod, "_api_call", fake)
        monkeypatch.setattr(mod, "_load_telegram_token", lambda: "T")
    monkeypatch.setattr(telegram_extensions, "is_enabled", lambda name: True)
    return fake


def _checkin(tracker, **kw):
    from navig.commands import habit

    args = dict(send=False, morning=False, close=False, midday=False,
                expire_morning=False, chat_id=-999, on_date=DAY, space=str(tracker))
    args.update(kw)
    habit.habit_checkin(**args)


class TestRichTransport:
    def test_a_server_without_rich_still_gets_the_card(self, tracker, monkeypatch):
        """An older Bot API answers "unknown method"; the plain body must follow."""
        import navig.commands.telegram as tg_cmd
        import navig.telegram.api as api
        from navig.gateway.channels import telegram_extensions

        calls: list[str] = []

        def fake(token, method, payload):  # noqa: ARG001
            calls.append(method)
            if method == "sendRichMessage":
                raise RuntimeError("Bad Request: unknown method")
            return {"ok": True, "result": {"message_id": 700}}

        for mod in (api, tg_cmd):
            monkeypatch.setattr(mod, "_api_call", fake)
            monkeypatch.setattr(mod, "_load_telegram_token", lambda: "T")
        monkeypatch.setattr(telegram_extensions, "is_enabled", lambda name: True)

        _checkin(tracker, send=True)

        assert calls == ["sendRichMessage", "sendMessage"]


class TestMiddayAndExpiry:
    def test_morning_send_remembers_the_morning_slot(self, tracker, bot):
        _checkin(tracker, send=True, morning=True)
        assert habit_tracker.morning_card(-999) == (501, DAY)

    def test_midday_reposts_with_the_marks_and_removes_the_08_00_card(self, tracker, bot):
        _checkin(tracker, send=True, morning=True)          # 08:00 → id 501
        habit_tracker.upsert(tracker, DAY, "wake", "yes")   # tapped at 08:20
        _checkin(tracker, send=True, midday=True)           # 11:30 → id 502

        assert bot.methods() == ["sendRichMessage", "sendRichMessage", "deleteMessage"]
        sent = bot.calls[1][1]
        assert "Morning" in sent["rich_message"]["html"]
        assert sent["reply_markup"]["inline_keyboard"][0][0]["text"] == "✓ Wake"
        assert bot.calls[2][1]["message_id"] == 501
        assert habit_tracker.morning_card(-999) == (502, DAY)

    def test_midday_on_a_closed_day_sends_nothing(self, tracker, bot):
        habit_tracker.mark_day_closed(-999, DAY)
        _checkin(tracker, send=True, midday=True)
        assert bot.calls == []

    def test_expire_deletes_the_morning_card_once(self, tracker, bot):
        _checkin(tracker, send=True, morning=True)
        _checkin(tracker, expire_morning=True)
        _checkin(tracker, expire_morning=True)               # nothing left: no call
        assert bot.methods() == ["sendRichMessage", "deleteMessage"]
        assert habit_tracker.morning_card(-999) == (None, None)

    def test_expire_leaves_the_evening_card_alone(self, tracker, bot):
        _checkin(tracker, send=True, morning=True)           # 501
        _checkin(tracker, send=True)                         # 22:15 card → 502
        _checkin(tracker, expire_morning=True)
        assert bot.calls[-1] == ("deleteMessage", {"chat_id": -999, "message_id": 501})
        assert habit_tracker.last_card(-999) == (502, DAY)

    def test_a_refused_delete_drops_the_pointer(self, tracker, bot):
        _checkin(tracker, send=True, morning=True)
        bot.refuse_delete = True
        _checkin(tracker, expire_morning=True)
        assert habit_tracker.morning_card(-999) == (None, None)


# ===========================================================================
# A refused edit and a closed day
# ===========================================================================

class RefusingChannel(FakeChannel):
    """Telegram refused the edit: the real ``_api_call`` returns None, never raises."""

    async def _api_call(self, method, payload):
        self.calls.append((method, payload))
        return None if method == "editMessageText" else {"message_id": 7}


class TestRefusedEditsAndClosedDays:
    def test_a_tap_on_a_closed_day_changes_nothing(self, tracker):
        habit_tracker.upsert(tracker, DAY, "wake", "yes")
        habit_tracker.remember_target(-999, tracker)
        habit_tracker.mark_day_closed(-999, DAY)
        before = habit_tracker.read_tracker(tracker)

        channel = FakeChannel()
        toast = _tap(channel, tracker, f"hb:t:wake:{STAMP}")
        score = _tap(channel, tracker, f"hb:s:3:{STAMP}")

        assert habit_tracker.read_tracker(tracker) == before
        assert "closed" in toast and "closed" in score
        assert channel.calls == []

    def test_a_refused_close_does_not_mark_the_day_closed(self, tracker):
        channel = RefusingChannel()
        toast = _tap(channel, tracker, f"hb:x:{STAMP}")

        assert habit_tracker.is_day_closed(-999, DAY) is False
        assert "try again" in toast
        assert [m for m, _ in channel.calls] == ["editMessageText"], "no journal prompt"

    def test_a_refused_tap_edit_does_not_report_plain_success(self, tracker):
        channel = RefusingChannel()
        toast = _tap(channel, tracker, f"hb:t:wake:{STAMP}")

        assert habit_tracker.day_map(tracker, DAY)["wake"] == "yes", "the tap is saved"
        assert "didn't update" in toast, toast

    def test_not_modified_counts_as_an_updated_card(self, tracker, monkeypatch):
        monkeypatch.setattr(habit_actions, "_last_telegram_error",
                            lambda: "Bad Request: message is not modified")
        channel = RefusingChannel()
        toast = _tap(channel, tracker, f"hb:x:{STAMP}")

        assert habit_tracker.is_day_closed(-999, DAY) is True
        assert toast == "Day closed"
