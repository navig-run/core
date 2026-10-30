"""
Tests for evening/morning briefing UX: DND, reply capture, backup, eve_log.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def _make_handler():
    """Return a CallbackHandler with mocked channel for isolated testing."""
    from navig.gateway.channels.telegram_keyboards import CallbackHandler

    ch = MagicMock()
    ch.send_message = AsyncMock(return_value={"message_id": 99})
    ch.edit_rich_message = AsyncMock(return_value={"message_id": 99})
    handler = CallbackHandler.__new__(CallbackHandler)
    handler.channel = ch
    handler._answer = AsyncMock()
    handler.store = MagicMock()
    return handler


def _eve_cb(handler, action: str, *, chat_id: int = 1, msg_id: int = 0, user_id: int = 42):
    return _run(
        handler._handle_evening_callback("cb_id", f"eve:{action}", chat_id, msg_id, user_id)
    )


def _morn_cb(handler, action: str, *, chat_id: int = 1, msg_id: int = 0, user_id: int = 42):
    return _run(
        handler._handle_morning_callback("cb_id", f"morn:{action}", chat_id, msg_id, user_id)
    )


def _flat_buttons(send_mock) -> list[dict]:
    call = send_mock.call_args
    if call is None:
        return []
    kb = call.kwargs.get("keyboard") or []
    return [btn for row in kb for btn in row]


def _last_text(send_mock) -> str:
    call = send_mock.call_args
    if call is None:
        return ""
    return call.args[1] if len(call.args) > 1 else ""


# ---------------------------------------------------------------------------
# Eve-log unit tests
# ---------------------------------------------------------------------------


class _IsolatedConfigDir:
    """setup/teardown that swap ``NAVIG_CONFIG_DIR`` and put back what was there.

    The suite's session fixture points ``NAVIG_CONFIG_DIR`` at an isolated temp dir. The
    classes below need their OWN dir, so they overwrite it — and they used to **pop** it in
    teardown instead of restoring it. Nothing sets it again, so from that point on every
    later test in the same xdist worker resolved config to the operator's REAL ``~/.navig``.

    Measured with an audit of every real-home read during a full suite run: 106 reads across
    32 tests, and this file's three teardowns were the largest remaining cause once the
    gateway's own hardcoded ``storage_dir`` was fixed. It is also the upstream cause of
    ``tests/tools/test_doctor_image_generation.py::test_from_env_defaults`` failing only in
    the full suite — the operator's real config pins an image provider, so the assertion
    depended on whether this file had already run in that worker.

    Restoring rather than popping keeps the isolation the session fixture set up.
    """

    def setup_method(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev_config_dir = os.environ.get("NAVIG_CONFIG_DIR")
        os.environ["NAVIG_CONFIG_DIR"] = self._tmpdir.name

    def teardown_method(self):
        self._tmpdir.cleanup()
        if self._prev_config_dir is None:
            os.environ.pop("NAVIG_CONFIG_DIR", None)
        else:
            os.environ["NAVIG_CONFIG_DIR"] = self._prev_config_dir


class TestEveLog(_IsolatedConfigDir):
    def test_save_and_get_shipped(self):
        from navig.agent.proactive.eve_log import get_today, save_shipped

        save_shipped("Fixed login bug · Deployed v2.3")
        entry = get_today()
        assert entry["shipped"] == "Fixed login bug · Deployed v2.3"
        assert "shipped_at" in entry

    def test_save_and_get_priority(self):
        from navig.agent.proactive.eve_log import get_today, save_priority

        save_priority("Ship the auth refactor")
        entry = get_today()
        assert entry["priority"] == "Ship the auth refactor"
        assert "priority_at" in entry

    def test_get_yesterday_returns_previous_date(self):
        from datetime import datetime, timedelta

        from navig.agent.proactive.eve_log import _load, _save, get_yesterday

        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        data = _load()
        data[yesterday] = {"shipped": "old stuff", "shipped_at": "ts"}
        _save(data)
        entry = get_yesterday()
        assert entry["shipped"] == "old stuff"

    def test_empty_dict_for_new_day(self):
        from navig.agent.proactive.eve_log import get_today

        assert get_today() == {}

    def test_trim_to_max_days(self):
        from navig.agent.proactive.eve_log import _MAX_DAYS, _load, _save

        data = {f"2023-01-{i:02d}": {"shipped": "x"} for i in range(1, _MAX_DAYS + 10)}
        _save(data)
        loaded = _load()
        assert len(loaded) == _MAX_DAYS


# ---------------------------------------------------------------------------
# DND on / off
# ---------------------------------------------------------------------------


class TestDNDCallbacks:
    def test_dnd_on_arms_dnd_preference(self):
        handler = _make_handler()
        tracker = MagicMock()
        with patch("navig.agent.proactive.user_state.get_user_state_tracker", return_value=tracker):
            _eve_cb(handler, "dnd_on")
        # DND is its own preference now — the agent mode never gates it.
        tracker.set_preference.assert_any_call("dnd_enabled", True)
        tracker.set_preference.assert_any_call("notifications_enabled", False)
        assert not any(
            c.args[:1] == ("chat_mode",) for c in tracker.set_preference.call_args_list
        ), "the DND button must not touch the agent mode"

    def test_dnd_on_sends_wake_button(self):
        handler = _make_handler()
        tracker = MagicMock()
        with patch("navig.agent.proactive.user_state.get_user_state_tracker", return_value=tracker):
            _eve_cb(handler, "dnd_on")
        cb_datas = [b["callback_data"] for b in _flat_buttons(handler.channel.send_message)]
        assert "eve:dnd_off" in cb_datas, "Expected 🌅 revert button"

    def test_dnd_off_clears_dnd_preference(self):
        handler = _make_handler()
        tracker = MagicMock()
        with patch("navig.agent.proactive.user_state.get_user_state_tracker", return_value=tracker):
            _eve_cb(handler, "dnd_off")
        tracker.set_preference.assert_any_call("dnd_enabled", False)
        tracker.set_preference.assert_any_call("notifications_enabled", True)
        assert not any(
            c.args[:1] == ("chat_mode",) for c in tracker.set_preference.call_args_list
        ), "the wake button must not touch the agent mode"

    def test_dnd_off_sends_confirmation_text(self):
        handler = _make_handler()
        tracker = MagicMock()
        with patch("navig.agent.proactive.user_state.get_user_state_tracker", return_value=tracker):
            _eve_cb(handler, "dnd_off")
        text = _last_text(handler.channel.send_message)
        assert "Back online" in text or "restored" in text.lower()

    def test_dnd_on_survives_tracker_error(self):
        handler = _make_handler()
        with patch(
            "navig.agent.proactive.user_state.get_user_state_tracker",
            side_effect=RuntimeError("no tracker"),
        ):
            _eve_cb(handler, "dnd_on")
        # Message must still be sent despite the tracker error
        handler.channel.send_message.assert_called_once()


# ---------------------------------------------------------------------------
# eve:log_shipped and eve:plan_tomorrow — the picker, drawn in place on the card
# ---------------------------------------------------------------------------


class _FakeAIState:
    """Round-trips the ai_state blob so a picker can write and a tap can read it."""

    def __init__(self) -> None:
        self.state: dict = {"mode": "active", "persona": None, "context": {}}
        self.writes: list[dict] = []

    def get_ai_state(self, user_id):
        return self.state

    def set_ai_state(self, **kwargs):
        self.writes.append(kwargs)
        self.state = {
            "mode": kwargs.get("mode"),
            "persona": kwargs.get("persona"),
            "context": kwargs.get("context") or {},
        }


def _edit_buttons(edit_mock) -> list[dict]:
    call = edit_mock.call_args
    if call is None:
        return []
    kb = call.kwargs.get("keyboard") or []
    return [btn for row in kb for btn in row]


class TestEveningPicker:
    """Pressing a button on the evening card redraws THAT message.

    The old behaviour replied with a sentence asking the operator to type what he
    shipped — a question where he expected an answer, at the end of the day, on a
    phone. The card is now the screen: the picker is the same message, the choices
    are what he already logged, and Cancel puts the card back.
    """

    def _handler(self):
        handler = _make_handler()
        return handler

    def test_a_tap_redraws_the_card_in_place(self):
        handler = self._handler()
        store = _FakeAIState()
        with patch("navig.store.runtime.get_runtime_store", return_value=store):
            _eve_cb(handler, "log_shipped", msg_id=77)
        handler.channel.edit_rich_message.assert_awaited_once()
        args = handler.channel.edit_rich_message.call_args.args
        assert args[0] == 1 and args[1] == 77
        handler.channel.send_message.assert_not_called()

    def test_a_tap_on_an_old_card_still_lands(self):
        handler = self._handler()
        with patch("navig.store.runtime.get_runtime_store", return_value=_FakeAIState()):
            _eve_cb(handler, "plan_tomorrow", msg_id=404)
        assert handler.channel.edit_rich_message.call_args.args[1] == 404

    def test_every_candidate_gets_its_own_one_tap_button(self):
        handler = self._handler()
        with (
            patch("navig.store.runtime.get_runtime_store", return_value=_FakeAIState()),
            patch(
                "navig.telegram.briefing_cards.recent_shipped",
                return_value=["Ship the auth refactor", "Fix login bug"],
            ),
        ):
            _eve_cb(handler, "log_shipped")
        datas = [b.get("callback_data") for b in _edit_buttons(handler.channel.edit_rich_message)]
        assert datas[:2] == ["eve:set:shipped:0", "eve:set:shipped:1"]
        assert "eve:type:shipped" in datas and "eve:cancel" in datas

    def test_the_picker_never_puts_a_candidate_in_a_callback_payload(self):
        """A callback payload is capped at 64 bytes — the words must not ride in it."""
        handler = self._handler()
        long_item = "Refactored the notification router and shipped the dedupe guard " * 2
        with (
            patch("navig.store.runtime.get_runtime_store", return_value=_FakeAIState()),
            patch("navig.telegram.briefing_cards.recent_shipped", return_value=[long_item]),
        ):
            _eve_cb(handler, "log_shipped")
        for btn in _edit_buttons(handler.channel.edit_rich_message):
            assert len(btn.get("callback_data", "").encode()) <= 64

    def test_a_tapped_candidate_is_logged_exactly_as_written(self):
        handler = self._handler()
        store = _FakeAIState()
        with (
            patch("navig.store.runtime.get_runtime_store", return_value=store),
            patch(
                "navig.telegram.briefing_cards.recent_shipped",
                return_value=["Ship the auth refactor", "Fix login bug"],
            ),
            patch("navig.agent.proactive.eve_log.save_shipped") as save,
            patch("navig.agent.proactive.eve_log.get_today", return_value={}),
        ):
            _eve_cb(handler, "log_shipped")
            _eve_cb(handler, "set:shipped:1")
        save.assert_called_once_with("Fix login bug")
        assert handler.channel.edit_rich_message.await_count == 2  # picker, then the card

    def test_a_tapped_one_is_confirmed_with_a_toast(self):
        from navig.core.i18n import t as _t

        handler = self._handler()
        with (
            patch("navig.store.runtime.get_runtime_store", return_value=_FakeAIState()),
            patch(
                "navig.telegram.briefing_cards.recent_shipped",
                return_value=["Ship the auth refactor"],
            ),
            patch("navig.agent.proactive.eve_log.save_shipped"),
            patch("navig.agent.proactive.eve_log.get_today", return_value={}),
        ):
            _eve_cb(handler, "log_shipped")
            _eve_cb(handler, "set:shipped:0")
        assert handler._answer.call_args.args[1] == _t("briefing.toast.picked")

    def test_a_stale_tap_never_logs_a_guess(self):
        from navig.core.i18n import t as _t

        handler = self._handler()
        with (
            patch("navig.store.runtime.get_runtime_store", return_value=_FakeAIState()),
            patch("navig.agent.proactive.eve_log.save_shipped") as save,
        ):
            _eve_cb(handler, "set:shipped:0")
        save.assert_not_called()
        assert handler._answer.call_args.args[1] == _t("briefing.toast.pick_stale")

    def test_a_stale_tap_reopens_the_list_instead_of_dead_ending(self):
        handler = self._handler()
        with patch("navig.store.runtime.get_runtime_store", return_value=_FakeAIState()):
            _eve_cb(handler, "set:priority:3")
        # The empty store writes an empty list, so the card is redrawn as a picker.
        handler.channel.edit_rich_message.assert_awaited_once()

    def test_cancel_puts_the_evening_card_back(self):
        handler = self._handler()
        with (
            patch("navig.store.runtime.get_runtime_store", return_value=_FakeAIState()),
            patch("navig.agent.proactive.eve_log.save_shipped") as save,
            patch("navig.agent.proactive.eve_log.get_today", return_value={}),
        ):
            _eve_cb(handler, "cancel", msg_id=88)
        assert handler.channel.edit_rich_message.call_args.args[1] == 88
        save.assert_not_called()

    def test_typing_still_arms_the_reply_capture(self):
        handler = self._handler()
        store = _FakeAIState()
        with patch("navig.store.runtime.get_runtime_store", return_value=store):
            _eve_cb(handler, "type:shipped")
        pending = (store.state.get("context") or {}).get("eve_pending") or {}
        assert pending.get("active") is True and pending.get("type") == "shipped"
        # The prompt is a new message; the picker message goes back to being a card.
        handler.channel.send_message.assert_awaited_once()

    def test_the_free_text_prompt_is_sent_once_and_the_card_restored(self):
        from navig.telegram import briefing_cards

        handler = self._handler()
        with (
            patch("navig.store.runtime.get_runtime_store", return_value=_FakeAIState()),
            patch("navig.agent.proactive.eve_log.get_today", return_value={}),
        ):
            _eve_cb(handler, "type:priority")
        assert handler.channel.send_message.await_count == 1
        assert handler.channel.send_message.call_args.args[1] == briefing_cards.prompt_html(
            "priority"
        )
        handler.channel.edit_rich_message.assert_awaited_once()  # picker → evening card

    def test_nothing_logged_yet_offers_the_keyboard_and_no_dead_choice(self):
        handler = self._handler()
        with (
            patch("navig.store.runtime.get_runtime_store", return_value=_FakeAIState()),
            patch("navig.telegram.briefing_cards.recent_shipped", return_value=[]),
        ):
            _eve_cb(handler, "log_shipped")
        datas = [b.get("callback_data") for b in _edit_buttons(handler.channel.edit_rich_message)]
        assert "eve:type:shipped" in datas and "eve:cancel" in datas
        assert not [d for d in datas if d.startswith("eve:set:")]


# ---------------------------------------------------------------------------
# _handle_eve_pending_reply — reply capture
# ---------------------------------------------------------------------------


class TestEvePendingReply(_IsolatedConfigDir):
    def _call(self, text: str, pending_type: str | None, active: bool = True):
        from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

        inst = MagicMock(spec=TelegramCommandsMixin)
        inst.send_message = AsyncMock()
        store = MagicMock()
        ctx: dict = {}
        if pending_type is not None:
            ctx["eve_pending"] = {"active": active, "type": pending_type}
        store.get_ai_state.return_value = {"context": ctx}
        store.set_ai_state = MagicMock()
        with patch("navig.store.runtime.get_runtime_store", return_value=store):
            result = _run(TelegramCommandsMixin._handle_eve_pending_reply(inst, 1, 42, text))
        return result, inst, store

    def test_capture_shipped_returns_true(self):
        result, _, _ = self._call("Fixed bug · Deployed v2", "shipped")
        assert result is True

    def test_capture_shipped_persists(self):
        from navig.agent.proactive.eve_log import get_today

        self._call("Fixed bug · Deployed v2", "shipped")
        assert get_today().get("shipped") == "Fixed bug · Deployed v2"

    def test_capture_priority_returns_true(self):
        result, _, _ = self._call("Ship auth refactor", "priority")
        assert result is True

    def test_capture_priority_persists(self):
        from navig.agent.proactive.eve_log import get_today

        self._call("Ship auth refactor", "priority")
        assert get_today().get("priority") == "Ship auth refactor"

    def test_cancel_clears_active_flag(self):
        result, _, store = self._call("cancel", "shipped")
        assert result is True
        ctx = store.set_ai_state.call_args.kwargs.get("context") or {}
        assert ctx["eve_pending"]["active"] is False

    def test_cancel_no_persist(self):
        from navig.agent.proactive.eve_log import get_today

        self._call("cancel", "shipped")
        assert get_today() == {}

    def test_no_pending_returns_false(self):
        result, _, _ = self._call("hello", None)
        assert result is False

    def test_inactive_pending_returns_false(self):
        result, _, _ = self._call("hello", "shipped", active=False)
        assert result is False

    def test_slash_command_returns_false(self):
        result, _, _ = self._call("/help", "shipped")
        assert result is False

    def test_sends_confirmation_with_text(self):
        result, inst, _ = self._call("Auth refactor done", "shipped")
        inst.send_message.assert_called_once()
        assert "Auth refactor done" in inst.send_message.call_args.args[1]


# ---------------------------------------------------------------------------
# Backup check
# ---------------------------------------------------------------------------


class TestBackupCallback:
    def _fake_result(self, stdout: str = "", stderr: str = ""):
        r = MagicMock()
        r.stdout = stdout
        r.stderr = stderr
        return r

    def test_backup_shows_refresh_button(self):
        handler = _make_handler()
        with patch("subprocess.run", return_value=self._fake_result("backup.sql  2.3 MB")):
            _eve_cb(handler, "backup_check")
        cb_datas = [b["callback_data"] for b in _flat_buttons(handler.channel.send_message)]
        assert "eve:backup_check" in cb_datas

    def test_backup_shows_output_text(self):
        handler = _make_handler()
        with patch("subprocess.run", return_value=self._fake_result("my-backup.sql  1.1 MB")):
            _eve_cb(handler, "backup_check")
        assert "my-backup.sql" in _last_text(handler.channel.send_message)

    def test_backup_fallback_shows_refresh(self):
        handler = _make_handler()
        with patch("subprocess.run", side_effect=RuntimeError("timeout")):
            _eve_cb(handler, "backup_check")
        cb_datas = [b["callback_data"] for b in _flat_buttons(handler.channel.send_message)]
        assert "eve:backup_check" in cb_datas

    def test_backup_empty_shows_no_data_found(self):
        handler = _make_handler()
        with patch("subprocess.run", return_value=self._fake_result("", "")):
            _eve_cb(handler, "backup_check")
        assert "No backup data found" in _last_text(handler.channel.send_message)


# ---------------------------------------------------------------------------
# Unknown action
# ---------------------------------------------------------------------------


class TestUnknownAction:
    def test_no_message_sent(self):
        handler = _make_handler()
        _eve_cb(handler, "unknown_xyz_action")
        handler.channel.send_message.assert_not_called()

    def test_answer_called(self):
        handler = _make_handler()
        _eve_cb(handler, "unknown_xyz_action")
        handler._answer.assert_called_once()


# ---------------------------------------------------------------------------
# Morning callback
# ---------------------------------------------------------------------------


class TestMorningCallback:
    def test_anchor_ok_sends_locked_message(self):
        handler = _make_handler()
        _morn_cb(handler, "anchor_ok")
        handler.channel.send_message.assert_called_once()
        text = _last_text(handler.channel.send_message)
        assert "Anchor" in text or "locked" in text.lower()

    def test_anchor_ok_calls_answer(self):
        handler = _make_handler()
        _morn_cb(handler, "anchor_ok")
        handler._answer.assert_called_once()

    def test_unknown_morning_no_message(self):
        handler = _make_handler()
        _morn_cb(handler, "unknown_xyz")
        handler.channel.send_message.assert_not_called()

    def test_unknown_morning_acknowledges(self):
        handler = _make_handler()
        _morn_cb(handler, "unknown_xyz")
        handler._answer.assert_called_once()


# ---------------------------------------------------------------------------
# Notification briefings — morning / evening
# ---------------------------------------------------------------------------


class TestNotificationBriefings(_IsolatedConfigDir):
    def _mgr(self):
        from navig.gateway.notifications import TelegramNotifier

        return object.__new__(TelegramNotifier)

    # Morning

    def test_morning_returns_notification(self):
        n = _run(self._mgr()._morning_briefing())
        assert n is not None and len(n.message) > 5

    def test_morning_shows_anchor_from_yesterday(self):
        from datetime import datetime, timedelta

        from navig.agent.proactive.eve_log import _load, _save

        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        data = _load()
        data[yesterday] = {"priority": "Ship the auth refactor", "priority_at": "ts"}
        _save(data)
        n = _run(self._mgr()._morning_briefing())
        assert "Ship the auth refactor" in n.message
        flat = [b for row in (n.keyboard or []) for b in row]
        assert any(b["callback_data"] == "morn:anchor_ok" for b in flat)

    def test_morning_no_anchor_no_anchor_button(self):
        n = _run(self._mgr()._morning_briefing())
        flat = [b for row in (n.keyboard or []) for b in row]
        assert not any(b["callback_data"] == "morn:anchor_ok" for b in flat)

    # Evening

    def test_evening_returns_notification(self):
        n = _run(self._mgr()._evening_summary())
        assert n is not None and len(n.message) > 5

    def test_evening_has_dnd_and_backup_buttons(self):
        n = _run(self._mgr()._evening_summary())
        flat = [b for row in (n.keyboard or []) for b in row]
        cb = [b["callback_data"] for b in flat]
        assert "eve:dnd_on" in cb
        assert "eve:backup_check" in cb

    def test_evening_shows_shipped_when_logged(self):
        from navig.agent.proactive.eve_log import save_shipped

        save_shipped("Deployed v2 · Fixed AUTH")
        n = _run(self._mgr()._evening_summary())
        assert "Deployed v2 · Fixed AUTH" in n.message

    def test_evening_hides_log_shipped_button_when_done(self):
        from navig.agent.proactive.eve_log import save_shipped

        save_shipped("Already logged")
        n = _run(self._mgr()._evening_summary())
        flat = [b for row in (n.keyboard or []) for b in row]
        assert not any(b["callback_data"] == "eve:log_shipped" for b in flat)

    def test_evening_shows_priority_when_logged(self):
        from navig.agent.proactive.eve_log import save_priority

        save_priority("Auth refactor anchor")
        n = _run(self._mgr()._evening_summary())
        assert "Auth refactor anchor" in n.message

    def test_evening_hides_plan_tomorrow_button_when_done(self):
        from navig.agent.proactive.eve_log import save_priority

        save_priority("Already set")
        n = _run(self._mgr()._evening_summary())
        flat = [b for row in (n.keyboard or []) for b in row]
        assert not any(b["callback_data"] == "eve:plan_tomorrow" for b in flat)
