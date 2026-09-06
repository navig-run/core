"""Every operator-facing surface speaks the ONE global language.

Three subsystems shipped hardcoded English while ``user.language`` was Russian,
and each failed differently:

* ``boot_messages`` and ``engagement`` held English **literals** — no lookup, so
  no setting could reach them;
* habit reminders are worse than hardcoded: the text is **frozen into the cron
  command as base64 when the habit is added**, so changing the language
  afterwards is structurally incapable of affecting it.

The rule these tests hold: a user-facing string resolves at SEND time, through
``user.language``, and degrades to English rather than to nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.core import i18n

LOCALES = Path(i18n.DEFAULT_LOCALES_ROOT)


@pytest.fixture(autouse=True)
def _fresh_translator():
    """The shared translator caches its store — reset around every test."""
    i18n.shared().reset()
    yield
    i18n.shared().reset()


def _speak(monkeypatch, lang: str) -> None:
    monkeypatch.setattr(i18n, "current_language", lambda: lang)
    i18n.shared().reset()


# ── the locale files themselves ───────────────────────────────────────────────


def test_every_locale_defines_the_same_keys():
    """A missing key silently degrades that ONE string to English."""
    sets = {
        p.name: set(json.loads(p.read_text(encoding="utf-8")))
        for p in LOCALES.glob("*.json")
    }
    assert len(sets) == 3, f"expected en/ru/fr, got {sorted(sets)}"
    reference = sets["en.json"]
    for name, keys in sets.items():
        assert keys == reference, f"{name} differs by: {sorted(keys ^ reference)}"


def test_no_locale_value_is_empty():
    for p in LOCALES.glob("*.json"):
        for key, value in json.loads(p.read_text(encoding="utf-8")).items():
            items = value if isinstance(value, list) else [value]
            assert items, f"{p.name}:{key} is an empty pool"
            for item in items:
                assert str(item).strip(), f"{p.name}:{key} has a blank entry"


def test_pools_have_the_same_length_in_every_language():
    """A short pool is not wrong, but it usually means a variant was dropped."""
    loaded = {
        p.stem: json.loads(p.read_text(encoding="utf-8")) for p in LOCALES.glob("*.json")
    }
    for key, en_value in loaded["en"].items():
        if not isinstance(en_value, list):
            continue
        for lang, table in loaded.items():
            assert len(table[key]) == len(en_value), (
                f"{lang}.json:{key} has {len(table[key])} variants, en has {len(en_value)}"
            )


def test_placeholders_match_across_languages():
    """A translation that drops `{total}` renders a sentence with a hole in it."""
    import re

    loaded = {
        p.stem: json.loads(p.read_text(encoding="utf-8")) for p in LOCALES.glob("*.json")
    }

    def holes(value) -> set[str]:
        items = value if isinstance(value, list) else [value]
        return {m for item in items for m in re.findall(r"\{(\w+)\}", str(item))}

    for key, en_value in loaded["en"].items():
        expected = holes(en_value)
        for lang, table in loaded.items():
            assert holes(table[key]) == expected, (
                f"{lang}.json:{key} placeholders {holes(table[key])} != en {expected}"
            )


# ── the resolver ──────────────────────────────────────────────────────────────


def test_language_comes_from_the_accessor_that_can_see_user_language(monkeypatch):
    """`_load_global_config()` drops the whole `user` subtree (#1192).

    Resolving through it would make every surface English forever, silently.
    """
    monkeypatch.setattr("navig.core.language.resolve_language", lambda override="": "Russian")
    assert i18n.current_language() == "ru"


def test_an_unresolvable_language_is_english(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("config on fire")

    monkeypatch.setattr("navig.core.language.resolve_language", boom)
    assert i18n.current_language() == "en"


def test_an_unknown_key_returns_the_key_rather_than_raising():
    assert i18n.t("no.such.key") == "no.such.key"


def test_a_language_with_no_table_falls_back_to_english(monkeypatch):
    _speak(monkeypatch, "zh")
    assert "Position" in i18n.t("boot.position", value="x")


def test_pick_uses_the_fallback_when_the_pool_is_missing(monkeypatch):
    _speak(monkeypatch, "en")
    assert i18n.pick("no.such.pool", fallback=["only option"]) == "only option"


def test_pick_returns_empty_rather_than_raising_on_nothing():
    assert i18n.pick("no.such.pool") == ""


def test_a_broken_placeholder_returns_the_text_not_an_exception(monkeypatch):
    """A locale typo must not take down the surface that renders it."""
    _speak(monkeypatch, "en")
    assert i18n.t("boot.position")  # no `value=` supplied


# ── boot messages ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("lang", "needle"), [("en", "Position:"), ("ru", "Позиция:"), ("fr", "Position :")]
)
def test_boot_suffixes_follow_the_language(monkeypatch, lang, needle):
    _speak(monkeypatch, lang)
    from navig.boot_messages import get_boot_message

    assert needle in get_boot_message(location="48.85N")


def test_boot_message_pool_is_localized(monkeypatch):
    _speak(monkeypatch, "ru")
    from navig.boot_messages import NAVIG_BOOT_MESSAGES, get_boot_message

    seen = {get_boot_message() for _ in range(60)}
    assert seen, "the pool produced nothing"
    assert not (seen & set(NAVIG_BOOT_MESSAGES)), "English literals leaked through"


# ── habit reminders: frozen at creation, resolved at send ─────────────────────


def test_a_builtin_reminder_is_translated_at_send_time(monkeypatch):
    _speak(monkeypatch, "ru")
    from navig.spaces.health import BUILTIN_HABITS, localized_reminder

    baked = BUILTIN_HABITS["out"].reminder_message      # the English default
    out = localized_reminder("habit:out", baked)
    assert out != baked
    assert any("Ѐ" <= ch <= "ӿ" for ch in out), "expected Cyrillic"


def test_a_message_the_operator_wrote_is_never_overwritten(monkeypatch):
    """Translating somebody's own words is worse than leaving them."""
    _speak(monkeypatch, "ru")
    from navig.spaces.health import localized_reminder

    mine = "Мой собственный текст, не трогать"
    assert localized_reminder("habit:out", mine) == mine


def test_an_unknown_habit_key_keeps_its_text(monkeypatch):
    _speak(monkeypatch, "ru")
    from navig.spaces.health import localized_reminder

    assert localized_reminder("habit:invented", "whatever") == "whatever"


def test_every_builtin_habit_has_a_locale_entry():
    """A builtin with no entry silently keeps its English — invisible in review."""
    from navig.spaces.health import BUILTIN_HABITS

    table = json.loads((LOCALES / "ru.json").read_text(encoding="utf-8"))
    missing = [k for k in BUILTIN_HABITS if f"habit.{k}" not in table]
    assert not missing, f"no localized reminder for: {missing}"


def test_localized_reminder_never_raises(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("locale on fire")

    monkeypatch.setattr(i18n, "t", boom)
    from navig.spaces.health import BUILTIN_HABITS, localized_reminder

    baked = BUILTIN_HABITS["out"].reminder_message
    assert localized_reminder("habit:out", baked) == baked


# ── engagement ────────────────────────────────────────────────────────────────


def _coordinator():
    from navig.agent.proactive.engagement import EngagementCoordinator

    return EngagementCoordinator.__new__(EngagementCoordinator)


def test_greetings_follow_the_language(monkeypatch):
    _speak(monkeypatch, "ru")
    from navig.agent.proactive.engagement import TimeOfDay

    text = _coordinator()._build_greeting(TimeOfDay.MORNING, False)
    assert any("Ѐ" <= ch <= "ӿ" for ch in text)


def test_the_returning_greeting_follows_the_language(monkeypatch):
    _speak(monkeypatch, "ru")
    from navig.agent.proactive.engagement import TimeOfDay

    text = _coordinator()._build_greeting(TimeOfDay.NIGHT, True)
    assert any("Ѐ" <= ch <= "ӿ" for ch in text)


def test_every_time_of_day_resolves_to_something(monkeypatch):
    _speak(monkeypatch, "en")
    from navig.agent.proactive.engagement import TimeOfDay

    c = _coordinator()
    for tod in TimeOfDay:
        assert c._build_greeting(tod, False).strip(), f"empty greeting for {tod}"


def test_a_contextual_tip_substitutes_its_count(monkeypatch):
    _speak(monkeypatch, "ru")
    tip = _coordinator()._build_contextual_tip("db", 42)
    assert "42" in tip
    assert "{count}" not in tip


def test_an_unknown_command_has_no_tip(monkeypatch):
    _speak(monkeypatch, "ru")
    assert _coordinator()._build_contextual_tip("not-a-command", 1) is None


def test_a_tip_never_returns_its_own_locale_key(monkeypatch):
    """`t` echoes the key when nothing resolves; that must not become the tip."""
    _speak(monkeypatch, "en")
    monkeypatch.setattr(i18n, "t", lambda key, **_: key)
    assert _coordinator()._build_contextual_tip("db", 1) is None


def test_the_wrapup_substitutes_the_command_total(monkeypatch):
    import types

    _speak(monkeypatch, "ru")
    c = _coordinator()
    c.state = types.SimpleNamespace(stats=types.SimpleNamespace(total_commands=17))
    seen = {c._build_wrapup() for _ in range(60)}
    assert any("17" in s for s in seen)
    assert not any("{total}" in s for s in seen)


# ── the card HEADER, not just the body ────────────────────────────────────────
#
# A translated body under an English bold header is still a bilingual message,
# and it is the half people notice first.


def test_the_reminder_card_header_is_localized(monkeypatch):
    _speak(monkeypatch, "ru")
    header = i18n.t("notify.reminder.header")
    assert "Reminder" not in header
    assert any("Ѐ" <= ch <= "ӿ" for ch in header)


def test_the_missed_reminder_header_keeps_its_due_time(monkeypatch):
    _speak(monkeypatch, "ru")
    text = i18n.t("notify.reminder.missed", due="2026-09-06 10:00")
    assert "2026-09-06 10:00" in text
    assert "{due}" not in text


def test_every_engagement_action_has_a_localized_card_title():
    """The title was built by title-casing the action name — always English."""
    from navig.agent.proactive.engagement import EngagementAction

    table = json.loads((LOCALES / "ru.json").read_text(encoding="utf-8"))
    missing = [
        a.value for a in EngagementAction if f"engagement.title.{a.value}" not in table
    ]
    assert not missing, f"no localized card title for: {missing}"


def test_an_unmapped_action_still_produces_a_title(monkeypatch):
    """The fallback must be the old shape, not the raw locale key."""
    _speak(monkeypatch, "en")
    key = "engagement.title.invented_action"
    assert i18n.t(key) == key  # nothing resolves...
    # ...and the notifications bridge turns that into the title-cased fallback,
    # which is asserted where that code lives rather than duplicated here.


# ── the daily habit card ──────────────────────────────────────────────────────
#
# The card the operator taps every night at 22:15. Its TEXT and its BUTTONS are
# built in different places, and localizing only the text is a half-translated
# card — which is exactly what the first attempt produced.


def _card(tmp_path, monkeypatch, lang: str):
    from datetime import date

    from navig.spaces import habit_tracker
    from navig.telegram import habit_actions as ha

    _speak(monkeypatch, lang)
    path = tmp_path / "habits.csv"
    day = date.today().isoformat()
    for label in ("wake", "out", "ship"):
        habit_tracker.upsert(path, day, label, "yes")
    habit_tracker.upsert(path, day, "score", "8")
    return ha.build_card(path, day)


def test_the_card_text_follows_the_language(tmp_path, monkeypatch):
    text, _ = _card(tmp_path, monkeypatch, "ru")
    assert "Check-in" not in text
    assert any("Ѐ" <= ch <= "ӿ" for ch in text)


def test_the_card_BUTTONS_follow_the_language_too(tmp_path, monkeypatch):
    """CHECKIN_ROWS carries an English word per row, and binding it to the name
    `word` also shadowed the word() function — so the text localized and the
    buttons under it did not."""
    _, keyboard = _card(tmp_path, monkeypatch, "ru")
    labels = [b["text"] for row in keyboard["inline_keyboard"] for b in row]
    assert not any("Wake" in t or "Walk" in t or "Ship" in t for t in labels), labels
    assert any(any("Ѐ" <= ch <= "ӿ" for ch in t) for t in labels)


def test_the_morning_card_names_the_button_the_operator_can_see(tmp_path, monkeypatch):
    """"Tap <b>Wake</b>" is useless when the button says Подъём."""
    from datetime import date

    from navig.telegram import habit_actions as ha

    _speak(monkeypatch, "ru")
    path = tmp_path / "habits.csv"
    text, keyboard = ha.build_card(path, date.today().isoformat(), morning=True)
    wake_button = keyboard["inline_keyboard"][0][0]["text"]
    word = wake_button.split(" ", 1)[1]
    assert word in text, f"card says tap something not on the button: {word!r}"


def test_the_month_is_localized(tmp_path, monkeypatch):
    text, _ = _card(tmp_path, monkeypatch, "ru")
    assert "September" not in text and "August" not in text


def test_english_is_unchanged_which_is_what_the_habit_suite_pins(tmp_path, monkeypatch):
    """The existing 173 habit tests assert the English strings and must keep
    passing — they run hermetically, with no user.language set."""
    text, keyboard = _card(tmp_path, monkeypatch, "en")
    assert "Check-in" in text
    assert keyboard["inline_keyboard"][0][0]["text"] == "✅ Wake"
    assert "Close the day" in keyboard["inline_keyboard"][-1][0]["text"]


def test_every_tracker_label_has_a_locale_entry():
    from navig.telegram.habit_actions import LABEL_WORDS

    table = json.loads((LOCALES / "ru.json").read_text(encoding="utf-8"))
    missing = [k for k in LABEL_WORDS if f"habit.label.{k}" not in table]
    assert not missing, f"labels with no translation: {missing}"


def test_word_falls_back_to_english_rather_than_the_raw_label(monkeypatch):
    from navig.telegram.habit_actions import word

    monkeypatch.setattr(i18n, "t", lambda key, **_: key)  # nothing resolves
    assert word("caffeine_cutoff") == "No coffee"
    # and a label with no English word either is returned as-is, never blank
    assert word("invented_label") == "invented_label"


def test_no_latin_prose_survives_in_a_localized_card(tmp_path, monkeypatch):
    """The sweep that found the last one.

    Localizing a card string by string leaves whichever branch you did not
    exercise — the all-three-done line was translated while the still-open line
    beside it was not, and only rendering the OTHER branch showed it. This
    renders both cards and asserts no Latin prose survives, so the next missed
    string fails here instead of arriving on someone's phone.

    HTML tags are stripped first (`<b>` is not prose), and the tracker labels
    are Cyrillic in this locale, so any Latin word left is untranslated copy.
    """
    import re
    from datetime import date

    from navig.spaces import habit_tracker
    from navig.telegram import habit_actions as ha

    _speak(monkeypatch, "ru")
    path = tmp_path / "habits.csv"
    day = date.today().isoformat()
    habit_tracker.upsert(path, day, "wake", "yes")   # one done, two open:
                                                     # exercises BOTH branches
    for morning in (False, True):
        text, keyboard = ha.build_card(path, day, morning=morning)
        blob = text + " " + " ".join(
            b["text"] for row in keyboard["inline_keyboard"] for b in row
        )
        leftover = sorted(set(re.findall(r"[A-Za-z]{4,}", re.sub(r"<[^>]+>", "", blob))))
        assert not leftover, (
            f"{'morning' if morning else 'evening'} card still has English: {leftover}"
        )
