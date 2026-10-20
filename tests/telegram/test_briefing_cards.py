"""The morning/evening briefing cards: Russian text, a real off switch, a schedule.

The operator received "🌙 Friday Evening · Evening Shift … The graveyard is quiet.
Keep it that way. 🪦" — English, in a Russian NAVIG — and could not stop it: the
`briefings` extension promised "the scheduled morning and evening cards are not
sent" while the notifier never asked it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time

import pytest

from navig.core import i18n
from navig.telegram import briefing_cards as bc

FRIDAY = datetime(2026, 10, 9, 18, 0)


@pytest.fixture
def lang(monkeypatch):
    def use(code: str) -> None:
        monkeypatch.setattr(i18n, "current_language", lambda: code)

    return use


@pytest.fixture
def cfg(monkeypatch):
    values: dict = {}
    monkeypatch.setattr(bc, "_cfg_get", lambda key, default=None: values.get(key, default))
    monkeypatch.setattr(bc, "extension_on", lambda: values.get("__ext__", True))
    return values


# ── text ──────────────────────────────────────────────────────────────────────


def test_the_evening_card_is_russian_for_a_russian_operator(lang):
    lang("ru")
    title, text, keyboard = bc.build_evening(FRIDAY)
    assert title == "Пятница, 9 октября · вечер"
    assert "Пятница. Отпусти неделю." in text
    assert "Закрой день:" in text
    for english in ("Evening Shift", "graveyard", "Friday", "Close out"):
        assert english not in text
    labels = [b["text"] for row in keyboard for b in row]
    assert labels == ["✅ Что сделано", "🎯 План на завтра", "💾 Бэкапы", "🌑 Тишина"]


def test_the_morning_card_is_russian_and_carries_the_anchor(lang):
    lang("ru")
    text, keyboard = bc.build_morning(datetime(2026, 10, 9, 7, 0), anchor="Сдать <отчёт>")
    assert "Доброе утро" in text and "Пятница, 9 октября" in text
    assert "Сдать &lt;отчёт&gt;" in text            # the operator's text is escaped
    assert [b["callback_data"] for b in keyboard[0]] == ["morn:anchor_ok", "eve:plan_tomorrow"]


def test_the_morning_card_without_an_anchor_has_no_buttons(lang):
    lang("ru")
    _, keyboard = bc.build_morning(datetime(2026, 10, 9, 7, 0))
    assert keyboard is None


def test_logged_items_replace_their_buttons(lang):
    lang("en")
    _, text, keyboard = bc.build_evening(FRIDAY, shipped="v2.3", priority="auth")
    assert "Done: v2.3" in text and "Tomorrow: auth" in text
    assert [b["callback_data"] for row in keyboard for b in row] == [
        "eve:backup_check", "eve:dnd_on"]


def test_english_and_russian_define_the_same_briefing_keys():
    import json
    from pathlib import Path

    root = Path(i18n.DEFAULT_LOCALES_ROOT)
    en = {k for k in json.loads((root / "en.json").read_text(encoding="utf-8")) if k.startswith("briefing.")}
    ru = {k for k in json.loads((root / "ru.json").read_text(encoding="utf-8")) if k.startswith("briefing.")}
    assert en == ru and len(en) >= 40


def test_the_follow_up_prompt_is_localized(lang):
    lang("ru")
    out = bc.prompt_html("shipped")
    assert "Что сделано сегодня?" in out and "<code>cancel</code>" in out


@pytest.mark.parametrize("word", ["отмена", "Пропустить", "cancel", "skip"])
def test_cancel_words_in_both_languages(word):
    assert word.strip().lower() in bc.CANCEL_WORDS


# ── switches and schedule ────────────────────────────────────────────────────


def test_both_cards_are_on_by_default(cfg):
    assert bc.card_enabled("morning") and bc.card_enabled("evening")


def test_the_extension_switches_both_off(cfg):
    cfg["__ext__"] = False
    assert not bc.card_enabled("morning") and not bc.card_enabled("evening")


def test_one_card_off_by_its_own_switch_raw_string(cfg):
    cfg["briefings.evening.enabled"] = "false"     # `navig config set` stores a string
    assert bc.card_enabled("morning") and not bc.card_enabled("evening")


def test_time_and_days_from_config(cfg):
    cfg["briefings.morning.time"] = "07:30"
    cfg["briefings.days"] = "daily"
    assert bc.card_time("morning") == time(7, 30)
    assert bc.card_time("evening") == time(18, 0)
    assert bc.card_days() == (0, 1, 2, 3, 4, 5, 6)


@pytest.mark.parametrize("raw", ["25:00", "seven", "7"])
def test_a_bad_time_keeps_the_default(cfg, raw):
    cfg["briefings.morning.time"] = raw
    assert bc.card_time("morning") == time(7, 0)


@pytest.mark.parametrize("raw,expected", [
    (None, (0, 1, 2, 3, 4)), ("weekdays", (0, 1, 2, 3, 4)), ("0,6", (0, 6)),
    ([5, 6], (5, 6)), ("junk", (0, 1, 2, 3, 4)), ("9,10", (0, 1, 2, 3, 4)),
])
def test_parse_days(raw, expected):
    assert bc.parse_days(raw) == expected


# ── the notifier asks ─────────────────────────────────────────────────────────


def _notifier():
    from navig.gateway.notifications import TelegramNotifier

    n = TelegramNotifier.__new__(TelegramNotifier)
    n.scheduled_tasks = []
    n._setup_default_schedules()
    return n


def _task(n, name):
    return next(t for t in n.scheduled_tasks if t.name == name)


def test_the_scheduler_skips_a_switched_off_card(cfg):
    n = _notifier()
    cfg["briefings.evening.enabled"] = False
    assert n._briefing_due_today(_task(n, "evening_summary"), FRIDAY) is False
    assert n._briefing_due_today(_task(n, "morning_briefing"), FRIDAY) is True


def test_the_scheduler_follows_a_new_time_without_a_restart(cfg):
    n = _notifier()
    cfg["briefings.evening.time"] = "19:15"
    cfg["briefings.days"] = "daily"
    task = _task(n, "evening_summary")
    n._briefing_due_today(task, FRIDAY)
    assert task.time == time(19, 15) and task.days == [0, 1, 2, 3, 4, 5, 6]


def test_other_tasks_are_untouched(cfg):
    n = _notifier()
    cfg["__ext__"] = False
    assert n._briefing_due_today(_task(n, "heartbeat_check"), FRIDAY) is True


def test_the_notifier_evening_card_uses_the_builder(cfg, lang, monkeypatch):
    lang("ru")
    n = _notifier()
    note = asyncio.run(n._evening_summary())
    assert note.title.endswith("· вечер") and "Закрой день:" in note.message
