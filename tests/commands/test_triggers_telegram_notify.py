"""Regression: a trigger firing on the 'telegram' channel actually sends.

`_send_notification` used to `from navig.commands.gateway import
send_telegram_message` — a name that has never existed — so every telegram
trigger notification hit the `except` and returned "Telegram notification
failed: cannot import name ...". The channel was 100% dead. The fix calls the
real `telegram_send` API (as `navig gateway test telegram` already does) with a
target resolved from the action params or the default allowed-user chat.
"""

from __future__ import annotations

import navig.commands.habit as habit_mod
import navig.commands.telegram as tg_mod
from navig.commands.triggers import Trigger, TriggerEvent, TriggerManager, TriggerType


def _mgr() -> TriggerManager:
    # The telegram branch uses no instance state, so skip __init__ (which would
    # create ~/.navig/triggers/ on the real machine).
    return TriggerManager.__new__(TriggerManager)


def _event() -> TriggerEvent:
    return TriggerEvent(type=TriggerType.MANUAL, source="test", data={})


def _trigger() -> Trigger:
    return Trigger(id="t1", name="Disk Alert", type=TriggerType.MANUAL)


def test_telegram_uses_explicit_target(monkeypatch):
    calls = {}

    def fake_send(*, target, message, **kw):
        calls["target"] = target
        calls["message"] = message
        return 123

    monkeypatch.setattr(tg_mod, "telegram_send", fake_send)

    ok, err = _mgr()._send_notification(
        "telegram", _trigger(), _event(), {"message": "hi", "target": "@ops"}
    )

    assert ok is True
    assert err == ""
    assert calls == {"target": "@ops", "message": "hi"}


def test_telegram_falls_back_to_default_chat(monkeypatch):
    calls = {}

    def fake_send(*, target, message, **kw):
        calls["target"] = target

    monkeypatch.setattr(tg_mod, "telegram_send", fake_send)
    monkeypatch.setattr(habit_mod, "_resolve_default_chat_id", lambda: 55501)

    ok, _err = _mgr()._send_notification("telegram", _trigger(), _event(), {"message": "hi"})

    assert ok is True
    assert calls["target"] == "55501"  # coerced to str for the send API


def test_telegram_no_target_returns_clear_error(monkeypatch):
    sent = {"called": False}

    def fake_send(**kw):
        sent["called"] = True

    monkeypatch.setattr(tg_mod, "telegram_send", fake_send)
    monkeypatch.setattr(habit_mod, "_resolve_default_chat_id", lambda: None)

    ok, err = _mgr()._send_notification("telegram", _trigger(), _event(), {"message": "hi"})

    assert ok is False
    assert "no Telegram target" in err
    assert sent["called"] is False  # never attempt a send without a target


def test_real_send_seam_exists_not_the_phantom():
    import navig.commands.gateway as gw

    assert callable(tg_mod.telegram_send)
    assert not hasattr(gw, "send_telegram_message")
