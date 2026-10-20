"""Regression: a trigger firing on the 'telegram' channel reaches the REAL
notification pipeline.

History, two fixes deep: `_send_notification` first did `from
navig.commands.gateway import send_telegram_message` — a name that has never
existed — so every telegram trigger notification hit the `except` and returned
"Telegram notification failed: cannot import name ..." (the channel was 100%
dead). The next fix called `telegram_send` directly, which delivered — but
posted straight to the Bot API: no routing matrix, no quiet hours, no master
toggle, no delivery verification, and an action `target` param could aim the
bot at an arbitrary chat outside `telegram.allowed_users`.

The channel now goes through `navig.notify.dispatch` (Gate 1e). These tests pin
that contract at the seam: dispatch is called with the right shape, the Bot API
transport is never touched by the trigger path itself, and the pipeline's
verdict (delivered / every channel failed / master off / a raise) is returned
as the action outcome so the trigger history records what really happened.
The real-router fan-out — quiet hours, deck fallback, history rows — is pinned
in test_commands_triggers.py.
"""

from __future__ import annotations

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


def _wire_dispatch(monkeypatch, outcome: dict | None = None):
    """Replace `navig.notify.dispatch` with a recorder; return its call log.

    The triggers code imports dispatch from the package AT CALL TIME, so
    patching the package attribute is the seam.
    """
    import navig.notify as notify_mod

    calls: list[dict] = []

    async def fake_dispatch(type_key, title, body="", **kwargs):
        calls.append({"type": type_key, "title": title, "body": body, **kwargs})
        if outcome is not None:
            return outcome
        return {
            "type": type_key,
            "priority": kwargs.get("priority", "normal"),
            "channels": [{"channel": "telegram", "ok": True, "detail": "sent"}],
        }

    monkeypatch.setattr(notify_mod, "dispatch", fake_dispatch)
    return calls


def test_telegram_routes_through_notify_dispatch(monkeypatch):
    calls = _wire_dispatch(monkeypatch)

    ok, err, suppressed = _mgr()._send_notification(
        "telegram", _trigger(), _event(), {"message": "hi"}
    )

    assert (ok, err) == (True, "")
    assert suppressed is False
    assert len(calls) == 1
    assert calls[0]["type"] == "system_alert"
    assert calls[0]["title"] == "Disk Alert"
    assert calls[0]["body"] == "hi"
    assert calls[0]["priority"] == "normal"
    assert calls[0]["data"]["trigger_id"] == "t1"
    assert calls[0]["data"]["trigger_type"] == "manual"


def test_an_explicit_target_param_no_longer_bypasses_the_pipeline(monkeypatch):
    """The old direct send honoured `params.target` unchecked — any trigger YAML
    could aim the bot at an arbitrary chat, outside `telegram.allowed_users`. The
    chat now resolves INSIDE the pipeline, and the transport is never called by
    the trigger path itself."""
    sent: list[dict] = []
    monkeypatch.setattr(tg_mod, "telegram_send", lambda **kw: sent.append(kw))
    calls = _wire_dispatch(monkeypatch)

    ok, err, suppressed = _mgr()._send_notification(
        "telegram", _trigger(), _event(), {"message": "hi", "target": "@ops"}
    )

    assert (ok, err) == (True, ""), "the action still delivers — through dispatch"
    assert suppressed is False
    assert len(calls) == 1
    assert sent == [], "the trigger path must never post to the Bot API itself"


def test_every_channel_failed_is_reported_as_a_failed_action(monkeypatch):
    _wire_dispatch(
        monkeypatch,
        outcome={
            "type": "system_alert",
            "priority": "normal",
            "channels": [
                {"channel": "telegram", "ok": False, "detail": "telegram not configured"}
            ],
        },
    )

    ok, err, suppressed = _mgr()._send_notification(
        "telegram", _trigger(), _event(), {"message": "hi"}
    )

    assert ok is False, "a dispatch that delivered nothing must not report success"
    assert "notify:" in err and "telegram not configured" in err
    assert suppressed is False, (
        "channels that ERRORED are a genuine failure, not policy suppression"
    )


def test_master_off_is_reported_not_phantom_delivered(monkeypatch):
    _wire_dispatch(
        monkeypatch,
        outcome={"type": "system_alert", "skipped": "master_off", "channels": []},
    )

    ok, err, suppressed = _mgr()._send_notification(
        "telegram", _trigger(), _event(), {"message": "hi"}
    )

    assert ok is False
    assert "master toggle" in err
    assert suppressed is True, (
        "the master toggle is POLICY suppression — it must not stop an "
        "on_failure:stop chain (see test_commands_triggers)"
    )


def test_real_send_seam_exists_not_the_phantom():
    import navig.commands.gateway as gw

    assert callable(tg_mod.telegram_send)
    assert not hasattr(gw, "send_telegram_message")
