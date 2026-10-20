"""The health alert says how to fix itself, can re-check, and speaks Russian.

The operator's 9 Oct alert: two modes on anthropic, "key rejected (401 …)", with a
`navig mode set` hint and nothing else. A swap button existed (#1815) — but a
swap moves the route, it cannot fix the KEY. The operator asked for "resolve
buttons and instructions".
"""

from __future__ import annotations

import pytest

from navig.core import i18n
from navig.heartbeat import alert_text as at

ISSUES = [
    "[HIGH] LLM mode 'big_tasks' → anthropic:claude-opus-4-8 is auth "
    "(auth failed (401 — key/token invalid or expired)). "
    "Fix: navig mode set big_tasks --provider <p> --model <m>",
    "[HIGH] LLM mode 'coding' → anthropic:claude-sonnet-4-6 is auth "
    "(auth failed (401 — key/token invalid or expired)). "
    "Fix: navig mode set coding --provider <p> --model <m>",
]


@pytest.fixture
def lang(monkeypatch):
    def use(code: str) -> None:
        monkeypatch.setattr(i18n, "current_language", lambda: code)

    return use


def _data(kb):
    return [b["callback_data"] for row in kb for b in row]


def test_the_alert_carries_switch_howto_and_recheck(lang):
    lang("en")
    kb = at.alert_keyboard(ISSUES)
    assert _data(kb) == ["hbfix:big_tasks,coding", "hbhow:auth.anthropic", "hbchk:big_tasks,coding"]
    assert all(len(d.encode()) <= 64 for d in _data(kb))


def test_buttons_and_title_are_russian_for_a_russian_operator(lang):
    lang("ru")
    title, body = at.issues_alert(ISSUES)
    assert title == "⚠️ Проверка здоровья · проблем: 2"
    assert "Режим `big_tasks`" in body and "ключ отклонён" in body and "Исправить:" in body
    labels = [b["text"] for row in at.alert_keyboard(ISSUES) for b in row]
    assert labels == ["🔁 Переключить на рабочие модели", "🔑 Как починить", "🔄 Проверить снова"]


def test_english_output_is_unchanged(lang):
    lang("en")
    title, body = at.issues_alert(ISSUES)
    assert title == "⚠️ Health check: 2 problems"
    assert "Mode `big_tasks` → `anthropic:claude-opus-4-8` — key rejected" in body


def test_how_to_fix_names_real_commands_for_the_provider(lang):
    lang("ru")
    text = at.howto_text("hbhow:auth.anthropic")
    assert "<code>navig vault set anthropic &lt;новый-ключ&gt;</code>" in text
    assert "<code>navig vault test anthropic</code>" in text
    assert text.startswith("<b>🔑 Как починить</b>")


def test_how_to_fix_escapes_the_provider_id(lang):
    lang("en")
    assert "&lt;b&gt;" in at.howto_text("hbhow:nokey.<b>")


def test_how_to_fix_for_a_retired_model_does_not_ask_for_a_key(lang):
    lang("en")
    text = at.howto_text("hbhow:dead.nvidia")
    assert "retired" in text and "vault set" not in text


def test_recheck_all_working(lang):
    lang("ru")
    text, ok = at.recheck_text([
        {"mode": "big_tasks", "provider": "anthropic", "model": "claude-opus-4-8", "status": "live", "detail": "ok"},
    ])
    assert ok and text.startswith("✅ Починено") and "работает" in text


def test_recheck_still_failing(lang):
    lang("en")
    text, ok = at.recheck_text([
        {"mode": "coding", "provider": "anthropic", "model": "claude-sonnet-4-6",
         "status": "auth", "detail": "auth failed (401 — key/token invalid or expired)"},
    ])
    assert not ok and text.startswith("Still failing:") and "key rejected" in text


def test_a_free_text_finding_gets_no_fix_buttons():
    assert at.alert_keyboard(["[HIGH] Disk 96% full on /var"]) is None


def test_many_modes_still_fit_the_callback_cap():
    issues = [
        f"[HIGH] LLM mode 'mode_with_a_long_name_{i}' → anthropic:m is auth (x). Fix: y"
        for i in range(8)
    ]
    assert all(len(d.encode()) <= 64 for d in _data(at.alert_keyboard(issues)))
