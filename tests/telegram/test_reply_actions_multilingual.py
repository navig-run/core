"""Multilingual (FR/RU/ES) reply-keyword aliases resolve to the right native action.

Recovered capability from the retired telegram-bot-navig nlp_aliases pack — this is
the regression test proving the multilingual triggers map correctly, are
case/accents tolerant, and never collide with the tiktok 'analyse' keyword.
"""

from __future__ import annotations

import pytest

from navig.telegram import reply_actions


@pytest.mark.parametrize(
    "keyword,expected",
    [
        # ── French ──
        ("traduis", "translate"), ("traduction", "translate"),
        ("résume", "summarize"), ("résumé", "summarize"),
        ("explique", "explain"), ("contexte", "context"),
        ("améliore", "improve"), ("corrige", "fix"),
        ("réécris", "rewrite"), ("raccourcis", "shorten"),
        # ── Russian ──
        ("переведи", "translate"), ("резюме", "summarize"),
        ("объясни", "explain"), ("контекст", "context"),
        ("улучши", "improve"), ("исправь", "fix"),
        ("перепиши", "rewrite"), ("сократи", "shorten"),
        # ── Spanish ──
        ("traduce", "translate"), ("resumen", "summarize"),
        ("explica", "explain"), ("contexto", "context"),
        ("mejora", "improve"), ("corrige", "fix"),
        ("reescribe", "rewrite"), ("acorta", "shorten"),
    ],
)
def test_multilingual_alias_resolves(keyword, expected):
    action, arg = reply_actions.parse(keyword)
    assert action == expected, f"{keyword!r} → {action!r}, expected {expected!r}"
    assert arg == ""


def test_case_and_accent_tolerant():
    assert reply_actions.parse("RÉSUME")[0] == "summarize"
    assert reply_actions.parse("Объясни")[0] == "explain"


def test_no_collision_with_tiktok_analyse():
    # analyse/analyze remain the tiktok trigger — the context aliases
    # (contexte/контекст/contexto) must NOT have hijacked them.
    assert reply_actions.parse("analyse")[0] == "tiktok"
    assert reply_actions.parse("analyze")[0] == "tiktok"
    assert reply_actions.parse("contexte")[0] == "context"


def test_multiword_reply_not_hijacked():
    # A normal multi-word reply that happens to start with a non-arg trigger word
    # must fall through to the agent, not be swallowed as an action.
    assert reply_actions.parse("explique moi ça stp")[0] is None


def test_translate_still_takes_a_target_arg():
    # translate is arg-accepting, so a multilingual trigger + target still works.
    action, arg = reply_actions.parse("traduis ru")
    assert action == "translate"
    assert arg == "ru"


@pytest.mark.parametrize(
    "keyword,expected",
    [
        # ── German ──
        ("übersetze", "translate"), ("zusammenfassen", "summarize"), ("erkläre", "explain"),
        ("verbessere", "improve"), ("korrigiere", "fix"), ("kürzen", "shorten"),
        ("umschreiben", "rewrite"),
        # ── Portuguese ──
        ("traduza", "translate"), ("resuma", "summarize"), ("melhore", "improve"),
        ("corrija", "fix"), ("reescreva", "rewrite"),
    ],
)
def test_de_pt_aliases_resolve(keyword, expected):
    assert reply_actions.parse(keyword)[0] == expected


def test_music_reply_keywords():
    assert reply_actions.parse("music")[0] == "music"
    assert reply_actions.parse("song")[0] == "music"


def test_help_text_lists_keywords_media_and_languages():
    # The /help transforms card shows the keyword to TYPE (not the output label),
    # plus media and the multilingual note — sourced so it can't drift.
    t = reply_actions.help_text()
    assert "<code>translate</code>" in t and "<code>music</code>" in t and "<code>tiktok</code>" in t
    for lang in ("traduis", "переведи", "resumen", "übersetze", "traduza"):
        assert lang in t


def test_help_transforms_screen_builds():
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

    text, keyboard = TelegramCommandsMixin._build_help_transforms()
    assert text.startswith("🎛")
    assert keyboard[0][0]["callback_data"] == "help:home"  # back to help home
