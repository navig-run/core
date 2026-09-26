"""SMS is for appointments, not for routine nudges.

The operator's report: a habit reminder — *"Блок на 90 минут. Телефон в другой
комнате."* — arrived **by SMS**. That is a nudge you can read whenever; it is not
worth a text message. Meanwhile their real appointments (`rdv:orl-mardi`,
`bilan:vendredi-a-jeun`) were sent with `navig telegram send`, pinned to one
transport, and could never reach SMS however urgent.

Two causes, one shape:

* Both kinds shared the ``reminder`` type, and channels are resolved PER TYPE —
  so no setting could separate them. ``appointment`` now exists precisely so the
  matrix can say "this one earns a text message and that one does not".
* Nothing on the CLI could dispatch through the router, so a cron's only option
  was a direct single-transport send. ``navig notify send`` closes that.

⚠ These tests deliberately do NOT touch the real notify store. `NAVIG_DATA_DIR`
is documented as the isolation knob and has been measured NOT to cover every
store in this tree, so a test that "isolates" and then writes could edit the
operator's live preferences.
"""

from __future__ import annotations

import pytest

from navig.notify.types import NOTIFICATION_TYPES


def _type(key: str) -> dict:
    return next(t for t in NOTIFICATION_TYPES if t["key"] == key)


def test_appointments_reach_sms_by_default() -> None:
    """The capability the operator asked for, stated directly."""
    assert "sms" in _type("appointment")["default_channels"]


def test_routine_reminders_do_not() -> None:
    """The regression guard.

    If someone puts `sms` back into `reminder`'s defaults, every habit nudge
    starts texting the operator again — which is the report that started this.
    """
    assert "sms" not in _type("reminder")["default_channels"], (
        "routine reminders route to SMS again — a 90-minute focus nudge is not "
        "worth a text message"
    )


def test_appointment_is_a_separate_type_from_reminder() -> None:
    """Channels resolve per TYPE, so one shared type cannot express 'text me for
    this one but not that one'. Splitting them is the mechanism, not cosmetics."""
    assert _type("appointment")["key"] != _type("reminder")["key"]
    assert _type("appointment")["category"] == _type("reminder")["category"] == "Personal"


def test_sms_stays_rare() -> None:
    """SMS costs money and interrupts. Keep the set that earns it small and
    deliberate — if this count grows, someone should have to justify it."""
    smsy = sorted(t["key"] for t in NOTIFICATION_TYPES if "sms" in (t.get("default_channels") or []))
    assert smsy == ["appointment", "security_alert"], (
        f"types defaulting to SMS changed: {smsy}"
    )


# ── the CLI ───────────────────────────────────────────────────────────────────


def test_an_unknown_type_is_rejected_not_silently_deck_only(monkeypatch, capsys) -> None:
    """⚠ The router's unknown-type fallback delivers deck-only and WARNS.

    That is right for library callers, but from a cron it looks like the message
    was sent. A typo'd type must fail loudly at the command boundary instead.
    """
    import typer

    from navig.commands import notify as cmd

    with pytest.raises(typer.Exit) as exc:
        cmd.send(type_key="appointmnet", title="Blood test", body="", json_out=False)
    assert exc.value.exit_code == 1


def test_a_send_that_reaches_nothing_is_not_reported_as_success(monkeypatch) -> None:
    """A muted type legitimately reaches no channel — that is the operator's
    choice, not an error — but it must never print a success line."""
    from navig.commands import notify as cmd

    async def _dispatch(*_a, **_k):
        return {"channels": []}

    monkeypatch.setattr("navig.notify.router.dispatch", _dispatch)
    said: list[str] = []
    monkeypatch.setattr(cmd.ch, "success", lambda *a, **k: said.append("success"))
    monkeypatch.setattr(cmd.ch, "warning", lambda *a, **k: said.append("warning"))

    cmd.send(type_key="appointment", title="x", body="", json_out=False)

    assert said == ["warning"], f"a send that reached nobody reported {said}"


def test_total_failure_exits_non_zero(monkeypatch) -> None:
    """A cron needs to be able to tell that nothing landed."""
    import typer

    from navig.commands import notify as cmd

    async def _dispatch(*_a, **_k):
        return {"channels": [{"name": "sms", "ok": False}]}

    monkeypatch.setattr("navig.notify.router.dispatch", _dispatch)
    monkeypatch.setattr(cmd.ch, "warning", lambda *a, **k: None)

    with pytest.raises(typer.Exit) as exc:
        cmd.send(type_key="appointment", title="x", body="", json_out=False)
    assert exc.value.exit_code == 1


def test_a_partial_send_succeeds(monkeypatch) -> None:
    """⚠ Telegram landed, SMS did not — the operator HAS the message.

    Exiting non-zero here would make a retrying cron deliver it twice, which for
    an appointment alert is worse than the failed channel.
    """
    from navig.commands import notify as cmd

    async def _dispatch(*_a, **_k):
        return {"channels": [{"name": "telegram", "ok": True}, {"name": "sms", "ok": False}]}

    monkeypatch.setattr("navig.notify.router.dispatch", _dispatch)
    monkeypatch.setattr(cmd.ch, "success", lambda *a, **k: None)
    monkeypatch.setattr(cmd.ch, "warning", lambda *a, **k: None)

    cmd.send(type_key="appointment", title="x", body="", json_out=False)  # must not raise


def test_the_command_is_registered() -> None:
    """A command nobody can invoke is not a feature."""
    from navig.cli.registration import _EXTERNAL_CMD_MAP

    assert _EXTERNAL_CMD_MAP["notify"] == ("navig.commands.notify", "notify_app")
