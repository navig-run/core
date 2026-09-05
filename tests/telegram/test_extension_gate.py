"""Runtime behaviour of the Telegram Extensions gate.

The two guards in ``tests/quality`` prove the catalog is COMPLETE (every command,
prefix and reply action is claimed). These prove it actually BITES: that a
switched-off extension loses its buttons, its callbacks, its autocomplete entry
and its scheduled delivery.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from navig.gateway.channels import telegram_extensions as tx
from navig.gateway.channels.telegram import _strip_disabled_buttons


@pytest.fixture
def habits_off(monkeypatch):
    """Switch off exactly one extension, leaving the rest resolved normally."""
    real = tx.is_enabled
    monkeypatch.setattr(
        tx, "is_enabled", lambda e: False if e == "habits" else real(e)
    )


# ── The single outbound keyboard filter ──────────────────────────────────────


def test_api_payload_without_a_keyboard_is_returned_unchanged():
    payload = {"chat_id": 1, "text": "hi"}
    assert _strip_disabled_buttons(payload) is payload
    assert _strip_disabled_buttons(None) is None


def test_api_payload_keeps_its_identity_when_nothing_is_disabled():
    """No copy, no churn on the hot path when every extension is on."""
    payload = {"chat_id": 1, "reply_markup": {"inline_keyboard": [
        [{"text": "Status", "callback_data": "slash:status"}]
    ]}}
    assert _strip_disabled_buttons(payload) is payload


def test_disabled_extension_buttons_are_stripped_from_an_outbound_payload(habits_off):
    payload = {
        "chat_id": 1,
        "reply_markup": {"inline_keyboard": [
            [{"text": "Done", "callback_data": "hb:t:wake:20260901"}],
            [{"text": "Refresh", "callback_data": "slash:habits"},
             {"text": "Status", "callback_data": "slash:status"}],
            [{"text": "Docs", "url": "https://navig.run"}],
        ]},
    }
    out = _strip_disabled_buttons(payload)
    rows = out["reply_markup"]["inline_keyboard"]
    assert [[b["text"] for b in r] for r in rows] == [["Status"], ["Docs"]]
    # The caller's dict is never mutated in place.
    assert len(payload["reply_markup"]["inline_keyboard"]) == 3


def test_reply_markup_is_dropped_entirely_when_every_button_goes(habits_off):
    """Never send an empty inline_keyboard — it renders as a dead strip."""
    payload = {
        "chat_id": 1,
        "reply_markup": {"inline_keyboard": [
            [{"text": "Done", "callback_data": "hb:t:wake:20260901"}]
        ]},
    }
    out = _strip_disabled_buttons(payload)
    assert "reply_markup" not in out
    assert out["chat_id"] == 1


def test_non_inline_markups_are_never_touched(habits_off):
    for markup in ({"keyboard": [["a"]]}, {"remove_keyboard": True}, {"force_reply": True}):
        payload = {"chat_id": 1, "reply_markup": markup}
        assert _strip_disabled_buttons(payload) is payload


def test_the_filter_never_costs_a_message_when_the_gate_raises(monkeypatch):
    """A cosmetic filter must degrade to 'send it unchanged', never to an error."""
    def boom(_markup):
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(tx, "filter_keyboard", boom)
    payload = {"chat_id": 1, "reply_markup": {"inline_keyboard": [
        [{"text": "Done", "callback_data": "hb:t:1"}]
    ]}}
    assert _strip_disabled_buttons(payload) is payload


# ── The callback gate ────────────────────────────────────────────────────────


def _channel():
    ch = MagicMock()
    ch._api_call = AsyncMock(return_value={})
    return ch


async def test_callback_for_an_enabled_extension_passes_through():
    from navig.gateway.channels.telegram import TelegramChannel

    ch = _channel()
    allowed = await TelegramChannel._reject_disabled_extension_callback(
        ch, {"id": "cb1"}, "slash:status", 42, 7
    )
    assert allowed is True
    ch._api_call.assert_not_awaited()


async def test_callback_for_a_disabled_extension_is_answered_and_cleaned(habits_off):
    from navig.gateway.channels.telegram import TelegramChannel

    ch = _channel()
    cq = {
        "id": "cb1",
        "message": {"reply_markup": {"inline_keyboard": [
            [{"text": "Done", "callback_data": "hb:t:wake:20260901"}]
        ]}},
    }
    allowed = await TelegramChannel._reject_disabled_extension_callback(
        ch, cq, "hb:t:wake:20260901", 42, 7
    )
    assert allowed is False

    methods = [c.args[0] for c in ch._api_call.await_args_list]
    assert "answerCallbackQuery" in methods, "a stale tap must explain itself"
    answer = next(c.args[1] for c in ch._api_call.await_args_list
                  if c.args[0] == "answerCallbackQuery")
    assert answer["show_alert"] is True
    assert "Habits" in answer["text"]

    # The card cleans itself on that first tap — but the TEXT is never rewritten,
    # because the operator may switch the extension back on in a minute.
    assert "editMessageReplyMarkup" in methods
    assert "editMessageText" not in methods


async def test_a_stale_tap_survives_a_failing_edit(habits_off):
    """Telegram refuses edits on old messages; the alert already did the work."""
    from navig.gateway.channels.telegram import TelegramChannel

    ch = _channel()
    ch._api_call = AsyncMock(side_effect=RuntimeError("message is too old"))
    allowed = await TelegramChannel._reject_disabled_extension_callback(
        ch, {"id": "cb1", "message": {"reply_markup": {"inline_keyboard": [[]]}}},
        "hb:t:1", 42, 7,
    )
    assert allowed is False  # still rejected, no exception escaped


async def test_the_callback_gate_fails_open_when_the_registry_raises(monkeypatch):
    from navig.gateway.channels.telegram import TelegramChannel

    def boom(_cb):
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(tx, "callback_enabled", boom)
    ch = _channel()
    allowed = await TelegramChannel._reject_disabled_extension_callback(
        ch, {"id": "cb1"}, "hb:t:1", 42, 7
    )
    assert allowed is True, "a broken gate must never silence the operator's bot"


# ── Autocomplete + the shared visibility predicate ───────────────────────────


def _autocomplete() -> set[str]:
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

    return {
        c["command"] for c in TelegramCommandsMixin._build_command_list_for_registration()
    }


def test_switching_an_extension_off_removes_its_commands_from_autocomplete(habits_off):
    names = _autocomplete()
    assert names, "the payload must not be empty"
    assert names.isdisjoint({"habits", "workout", "stats", "card"})
    # The escape hatch is never affected.
    assert {"start", "help", "status", "extensions"} <= names


def test_habits_and_health_are_switched_independently(habits_off):
    """`/health` moved from Habits to Health, where its name matches its content.

    Switching Habits off must no longer take the body check-in with it — these
    are two features and two switches.
    """
    assert {"health", "weigh", "body"} <= _autocomplete()


def test_switching_health_off_removes_its_three_commands(monkeypatch):
    real = tx.is_enabled
    monkeypatch.setattr(tx, "is_enabled", lambda e: False if e == "health" else real(e))
    names = _autocomplete()
    assert names.isdisjoint({"health", "weigh", "body"})
    # ...and leaves the habit commands alone, the other half of independence.
    assert {"habits", "stats", "card"} <= names


def test_locked_commands_ignore_both_switches(habits_off, monkeypatch):
    from navig.gateway.channels import telegram_commands as tc

    monkeypatch.setattr(tc, "get_disabled_commands", lambda: {"status", "help", "extensions"})
    for name in ("status", "help", "extensions", "start"):
        assert tc.command_is_live(name, "core") is True


def test_command_is_live_honours_the_per_command_switch_too(monkeypatch):
    """The predicate folds BOTH gates — that is the point of having one."""
    from navig.gateway.channels import telegram_commands as tc

    monkeypatch.setattr(tc, "get_disabled_commands", lambda: {"disk"})
    assert tc.command_is_live("disk", "monitoring") is False
    assert tc.command_is_live("cpu", "monitoring") is True


# ── Habits: delivery actually stops ──────────────────────────────────────────


async def test_habit_cron_reminder_is_not_queued_while_the_extension_is_off(habits_off):
    """The headline case: no row is written, so nothing is ever delivered."""
    from navig.scheduler.cron_service import CronService

    job = MagicMock()
    job.command = "NAVIG_HABIT_REMINDER:12345:aGVsbG8="  # "hello"

    result = await CronService._execute_job_command(MagicMock(), job)
    assert "skipped" in result.lower()
    assert "habits extension is off" in result.lower(), (
        "the run log is the only place this decision is recorded — it must say why"
    )


async def test_habit_cron_reminder_is_queued_normally_when_enabled(monkeypatch):
    from navig.scheduler.cron_service import CronService

    created = {}

    class _Store:
        def create_reminder(self, **kw):
            created.update(kw)

    monkeypatch.setattr("navig.store.runtime.get_runtime_store", lambda: _Store())
    job = MagicMock()
    job.command = "NAVIG_HABIT_REMINDER:12345:aGVsbG8="

    result = await CronService._execute_job_command(MagicMock(), job)
    assert created.get("chat_id") == 12345
    assert created.get("message") == "hello"
    assert "queued" in result.lower()


def test_the_habit_banner_appears_only_while_the_extension_is_off(habits_off):
    from navig.telegram import habit_actions

    assert habit_actions.extension_is_off() is True
    assert "not delivered" in habit_actions.extension_banner()
    cli = habit_actions.extension_banner_cli()
    assert cli is not None and len(cli) == 2


def test_the_habit_banner_is_absent_on_a_healthy_install():
    from navig.telegram import habit_actions

    assert habit_actions.extension_banner() == ""
    assert habit_actions.extension_banner_cli() is None
