"""One date reader for every way a reminder is added.

`/remindme` understood only "in N min" and "at HH:MM" — no dates — and the "rappelle-moi
/ напомни / remind me" shortcut kept only an HH:MM and threw the DAY away, so
"rappelle-moi lundi prochain à 9h" became "today or tomorrow at 09:00". Both now go
through `pim.dates.extract_when`, the grammar `/todo` and the appointment capture use.
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime

import pytest

from navig.pim.clock import LOCAL

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=LOCAL)  # Thursday


@pytest.fixture(autouse=True)
def frozen(monkeypatch) -> None:
    monkeypatch.setattr("navig.pim.clock.local_now", lambda: NOW)


def _mixin():
    mod = importlib.import_module("navig.gateway.channels.telegram_commands")
    return mod.TelegramCommandsMixin


class _Bot(_mixin()):  # type: ignore[misc]
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, parse_mode="HTML", **_):
        self.messages.append((chat_id, text))
        return {"ok": True}


def _local(dt: datetime | None) -> datetime | None:
    return dt.astimezone(LOCAL) if dt else None


@pytest.mark.parametrize(
    ("command", "expected", "message"),
    [
        ("/remindme lundi 26 octobre 10h30 appeler Sarah", datetime(2026, 10, 26, 10, 30), "appeler Sarah"),
        ("/remindme 26 октября в 10:30 врач", datetime(2026, 10, 26, 10, 30), "врач"),
        ("/remindme demain 10h appeler le notaire", datetime(2026, 10, 9, 10, 0), "appeler le notaire"),
        ("/remindme завтра в 9:00 позвонить маме", datetime(2026, 10, 9, 9, 0), "позвонить маме"),
        ("/remindme in 10 minutes check logs", datetime(2026, 10, 8, 12, 10), "check logs"),
        ("/remindme at 21:30 deploy review", datetime(2026, 10, 8, 21, 30), "deploy review"),
        ("/remindme check logs in 30 min", datetime(2026, 10, 8, 12, 30), "check logs"),
        ("/remindme через 2 часа выключить духовку", datetime(2026, 10, 8, 14, 0), "выключить духовку"),
    ],
)
def test_remindme_reads_every_date_form(command: str, expected: datetime, message: str) -> None:
    remind_at, msg, err = _Bot()._parse_remindme_request(command)
    assert err is None, err
    assert remind_at is not None and remind_at.tzinfo is UTC  # stored as UTC, as the poller compares
    assert _local(remind_at).replace(tzinfo=None) == expected
    assert msg == message


@pytest.mark.parametrize("command", ["/remindme", "/remindme hello there", "/remindme in 10 minutes"])
def test_remindme_without_a_time_or_text_explains_itself(command: str) -> None:
    remind_at, _msg, err = _Bot()._parse_remindme_request(command)
    assert remind_at is None
    assert err and "/remindme" in err  # the usage examples are shown


def test_a_past_date_is_refused_not_scheduled() -> None:
    """Regression: the lenient passes dropped the year and the time, and this became
    5 January 2027 at 09:00 — scheduled, silently, two years from what was typed."""
    remind_at, _msg, err = _Bot()._parse_remindme_request("/remindme 5 janvier 2025 10h00 old thing")
    assert remind_at is None
    assert err and ("passed" in err or "passée" in err or "прошло" in err)


@pytest.mark.parametrize(
    ("text", "expected", "message"),
    [
        ("Rappelle-moi lundi prochain à 9h d'appeler Sarah", datetime(2026, 10, 12, 9, 0), "appeler Sarah"),
        ("Напомни мне завтра в 9:00 позвонить маме", datetime(2026, 10, 9, 9, 0), "позвонить маме"),
        ("remind me to call mom in 2 hours", datetime(2026, 10, 8, 14, 0), "call mom"),
        ("напомни через 30 минут проверить духовку", datetime(2026, 10, 8, 12, 30), "проверить духовку"),
    ],
)
def test_the_remind_me_shortcut_keeps_the_day(text: str, expected: datetime, message: str) -> None:
    bot = _Bot()
    pseudo = bot._sniff_reminder_intent(text)
    assert pseudo is not None and pseudo.startswith("/remindme ")
    remind_at, msg, err = bot._parse_remindme_request(pseudo)
    assert err is None, err
    assert _local(remind_at).replace(tzinfo=None) == expected
    assert msg == message


@pytest.mark.parametrize("text", ["remind me later", "rappelle-moi", "the dentist tomorrow 10:30"])
def test_the_shortcut_needs_a_verb_and_a_time(text: str) -> None:
    assert _Bot()._sniff_reminder_intent(text) is None


async def test_the_reply_is_in_the_operators_clock(monkeypatch) -> None:
    created: list[tuple] = []

    class _Store:
        def create_reminder(self, user_id, chat_id, message, remind_at):
            created.append((user_id, chat_id, message, remind_at))
            return 5

    monkeypatch.setattr("navig.store.runtime.get_runtime_store", lambda: _Store())
    bot = _Bot()
    await bot._handle_remindme(1, 1, "/remindme lundi 26 octobre 10h30 appeler Sarah")
    assert created and created[0][2] == "appeler Sarah"
    assert "10:30" in bot.messages[-1][1]  # 10:30 on the operator's clock, not UTC 09:30
    assert "#5" in bot.messages[-1][1]
