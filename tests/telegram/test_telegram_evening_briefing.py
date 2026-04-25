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
    handler = CallbackHandler.__new__(CallbackHandler)
    handler.channel = ch
    handler._answer = AsyncMock()
    handler.store = MagicMock()
    return handler


def _eve_cb(handler, action: str, *, chat_id: int = 1, msg_id: int = 0, user_id: int = 42):
    return _run(handler._handle_evening_callback("cb_id", f"eve:{action}", chat_id, msg_id, user_id))


def _morn_cb(handler, action: str, *, chat_id: int = 1, msg_id: int = 0, user_id: int = 42):
    return _run(handler._handle_morning_callback("cb_id", f"morn:{action}", chat_id, msg_id, user_id))


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

class TestEveLog:
    def setup_method(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NAVIG_CONFIG_DIR"] = self._tmpdir.name

    def teardown_method(self):
        self._tmpdir.cleanup()
        os.environ.pop("NAVIG_CONFIG_DIR", None)

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
    def test_dnd_on_calls_set_preference_sleep(self):
        handler = _make_handler()
        tracker = MagicMock()
        with patch("navig.agent.proactive.user_state.get_user_state_tracker", return_value=tracker):
            _eve_cb(handler, "dnd_on")
        tracker.set_preference.assert_any_call("chat_mode", "sleep")
        tracker.set_preference.assert_any_call("notifications_enabled", False)

    def test_dnd_on_sends_wake_button(self):
        handler = _make_handler()
        tracker = MagicMock()
        with patch("navig.agent.proactive.user_state.get_user_state_tracker", return_value=tracker):
            _eve_cb(handler, "dnd_on")
        cb_datas = [b["callback_data"] for b in _flat_buttons(handler.channel.send_message)]
        assert "eve:dnd_off" in cb_datas, "Expected 🌅 revert button"

    def test_dnd_off_calls_set_preference_work(self):
        handler = _make_handler()
        tracker = MagicMock()
        with patch("navig.agent.proactive.user_state.get_user_state_tracker", return_value=tracker):
            _eve_cb(handler, "dnd_off")
        tracker.set_preference.assert_any_call("chat_mode", "work")
        tracker.set_preference.assert_any_call("notifications_enabled", True)

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
# eve:log_shipped and eve:plan_tomorrow — set eve_pending
# ---------------------------------------------------------------------------

class TestEvePrompts:
    def _store(self) -> MagicMock:
        store = MagicMock()
        store.get_ai_state.return_value = {"context": {}}
        store.set_ai_state = MagicMock()
        return store

    def _pending(self, store: MagicMock) -> dict:
        ctx = store.set_ai_state.call_args.kwargs.get("context") or {}
        return ctx.get("eve_pending", {})

    def test_log_shipped_sets_eve_pending_active_shipped(self):
        handler = _make_handler()
        store = self._store()
        with patch("navig.store.runtime.get_runtime_store", return_value=store):
            _eve_cb(handler, "log_shipped")
        store.set_ai_state.assert_called_once()
        p = self._pending(store)
        assert p["active"] is True and p["type"] == "shipped"

    def test_plan_tomorrow_sets_eve_pending_active_priority(self):
        handler = _make_handler()
        store = self._store()
        with patch("navig.store.runtime.get_runtime_store", return_value=store):
            _eve_cb(handler, "plan_tomorrow")
        store.set_ai_state.assert_called_once()
        p = self._pending(store)
        assert p["active"] is True and p["type"] == "priority"

    def test_log_shipped_sends_prompt_message(self):
        handler = _make_handler()
        store = self._store()
        with patch("navig.store.runtime.get_runtime_store", return_value=store):
            _eve_cb(handler, "log_shipped")
        text = _last_text(handler.channel.send_message)
        assert text and ("shipped" in text.lower() or "What" in text)

    def test_plan_tomorrow_sends_prompt_message(self):
        handler = _make_handler()
        store = self._store()
        with patch("navig.store.runtime.get_runtime_store", return_value=store):
            _eve_cb(handler, "plan_tomorrow")
        text = _last_text(handler.channel.send_message)
        assert text and (
            "tomorrow" in text.lower() or "priority" in text.lower() or "anchor" in text.lower()
        )


# ---------------------------------------------------------------------------
# _handle_eve_pending_reply — reply capture
# ---------------------------------------------------------------------------

class TestEvePendingReply:
    def setup_method(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NAVIG_CONFIG_DIR"] = self._tmpdir.name

    def teardown_method(self):
        self._tmpdir.cleanup()
        os.environ.pop("NAVIG_CONFIG_DIR", None)

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

class TestNotificationBriefings:
    def setup_method(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NAVIG_CONFIG_DIR"] = self._tmpdir.name

    def teardown_method(self):
        self._tmpdir.cleanup()
        os.environ.pop("NAVIG_CONFIG_DIR", None)

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
