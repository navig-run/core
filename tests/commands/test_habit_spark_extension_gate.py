"""`navig habit spark --send` honours the Habits extension switch.

Regression (PR #1218 review): the cron-driven spark called sendMessage directly
with no `is_enabled("habits")` check, so switching Habits off still delivered
two sparks a day. `habit checkin` already had the gate; spark now matches it.
"""

from __future__ import annotations

from typer.testing import CliRunner

from navig.commands import habit as H
from navig.gateway.channels import telegram_extensions as tx


def _stub_spark(monkeypatch, tmp_path):
    from navig.spaces import sparks
    from navig.telegram import api, chat_tidy

    sent: list[dict] = []
    monkeypatch.setattr(chat_tidy, "mark_routine", lambda *a, **k: None)
    monkeypatch.setattr(sparks, "load", lambda space=None: {"ambient": ("keep going",)})
    monkeypatch.setattr(sparks, "pick", lambda pool, **kw: ("ambient", "keep going"))
    monkeypatch.setattr(H, "_tracker_path", lambda space: tmp_path / "habits.csv")
    monkeypatch.setattr(H, "_read_tracker", lambda path: [])
    monkeypatch.setattr(H.habit_tracker, "recent_sparks", lambda chat: [])
    monkeypatch.setattr(H.habit_tracker, "remember_spark", lambda chat, key: None)
    monkeypatch.setattr(api, "_load_telegram_token", lambda: "t")
    monkeypatch.setattr(api, "_api_call", lambda tok, method, payload: sent.append(payload))
    return sent


def test_spark_send_is_withheld_while_habits_is_off(monkeypatch, tmp_path):
    sent = _stub_spark(monkeypatch, tmp_path)
    monkeypatch.setattr(tx, "is_enabled", lambda e: e != "habits")
    result = CliRunner().invoke(H.habit_app, ["spark", "--send", "--chat-id", "5"])
    assert result.exit_code == 0, result.output
    assert sent == [], "Habits off: nothing may reach Telegram"


def test_spark_send_goes_out_while_habits_is_on(monkeypatch, tmp_path):
    sent = _stub_spark(monkeypatch, tmp_path)
    monkeypatch.setattr(tx, "is_enabled", lambda e: True)
    result = CliRunner().invoke(H.habit_app, ["spark", "--send", "--chat-id", "5"])
    assert result.exit_code == 0, result.output
    assert len(sent) == 1 and sent[0]["chat_id"] == 5
