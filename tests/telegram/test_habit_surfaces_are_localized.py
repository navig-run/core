"""Every habit surface, rendered in Russian, must contain no English prose.

#1242 localized the daily card, the reminders and the notification headers, and
shipped behaviour tests for exactly those paths. It did not reach the CLOSING card,
the journal prompt, the stats panel, the pause menu or the toasts — so the operator's
22:15 close-out arrived as:

    🌙 6 сентября closed
    ⚠️ Missed: Подъём, Прогулка.
    One miss is noise. Two in a row is a new habit — … NEVER MISS TWICE.

which is the two-languages-in-one-message symptom that PR was named after, still
happening in the same module.

**Why this probes the RENDER rather than scanning the source.** A source scan over
this module finds ~50 English literals and nearly all of them are legitimate: the
label table, `_MONTHS`, `_WEEKDAY_NAMES` and `FLOOR_TITLE` are deliberate FALLBACKS
that a locale key shadows. Gating that would need a ~50-entry baseline of "this
English is fine", which is exactly the shape that turns a guard into a place to hide
things. What the operator actually experiences is the finished string, so that is what
is asserted: build each surface under `ru` and look for English words in the output.
A fallback that is never reached cannot fail this; one that IS reached always will.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from navig.core import i18n
from navig.spaces import habit_tracker
from navig.telegram import habit_actions

TODAY = datetime.date(2026, 9, 6)
DAY = TODAY.isoformat()

#: Words that are allowed to appear in Russian output. Every one is a token the bot
#: would not translate: a file path, a command the operator types, or a unit.
#: Deliberately short — an allowlist is where an untranslated string goes to hide.
ALLOWED = frozenset(
    {
        "journal",  # journal/2026-09-06.md — a real path on disk
        "md",
        "skip",  # a COMMAND: translating it would document a word the bot rejects
        "navig",  # `navig telegram extensions enable habits`
        "telegram",
        "extensions",
        "enable",
        "habits",
        "b",  # HTML tags survive escaping: <b>, <i>, <pre>, <code>
        "i",
        "pre",
        "code",
    }
)

_WORD = re.compile(r"[A-Za-z]+")


@pytest.fixture
def tracker(tmp_path: Path) -> Path:
    """A realistic day: two of the three missed, an extra done, a score, history."""
    path = tmp_path / "tracker.csv"
    habit_tracker.upsert(path, DAY, "wake", "no")
    habit_tracker.upsert(path, DAY, "out", "no")
    habit_tracker.upsert(path, DAY, "ship", "yes")
    habit_tracker.upsert(path, DAY, "train", "yes")
    habit_tracker.upsert(path, DAY, "score", "7")
    for back in range(1, 12):
        d = (TODAY - datetime.timedelta(days=back)).isoformat()
        habit_tracker.upsert(path, d, "wake", "yes")
        habit_tracker.upsert(path, d, "out", "yes" if back % 3 else "no")
        habit_tracker.upsert(path, d, "ship", "yes" if back % 2 else "no")
    return path


@pytest.fixture(autouse=True)
def reminder_jobs(monkeypatch) -> None:
    """Two configured reminders, one paused.

    Without these `build_pause_menu` renders its "nothing configured" branch, and
    the populated one — the pause explanation, the per-job rows, the next-run
    column and the Pause all / Resume all buttons — is never built. That is where
    most of the untranslated strings were, so a probe that skips it proves nothing.
    """
    from navig.scheduler.habit_store import HABIT_NAME_PREFIX

    monkeypatch.setattr(
        habit_actions,
        "_habit_jobs",
        lambda: [
            {
                "name": f"{HABIT_NAME_PREFIX}wake",
                "schedule": "0 7 * * *",
                "enabled": True,
                "next_run": "2026-09-07T07:00:00",
            },
            {
                "name": f"{HABIT_NAME_PREFIX}out",
                "schedule": "0 18 * * *",
                "enabled": False,
                "next_run": "2026-09-07T18:00:00",
            },
        ],
    )


@pytest.fixture
def russian(monkeypatch) -> Iterator[None]:
    """Force the shared locale to Russian for one test."""
    monkeypatch.setattr(i18n, "current_language", lambda: "ru")
    i18n.shared().reset()
    yield
    i18n.shared().reset()


def english_words(text: str) -> list[str]:
    """The Latin words in *text* that are not on the allowlist."""
    return sorted({w for w in _WORD.findall(text) if w.lower() not in ALLOWED})


def surfaces(path: Path) -> dict[str, str]:
    """Every habit surface as one string — text plus every button label.

    Buttons are included because #1242's own write-up records them as a separate
    failure: the card's text and its BUTTONS are built in different places, so
    localizing one left the other in English.
    """
    card_text, card_kb = habit_actions.build_card(path, DAY)
    stats_text, stats_kb = habit_actions.build_stats(path, TODAY, 7)
    pause_text, pause_kb = habit_actions.build_pause_menu()

    def labels(keyboard: dict) -> str:
        return " ".join(
            str(b.get("text", "")) for row in keyboard["inline_keyboard"] for b in row
        )

    return {
        "daily card": card_text + " " + labels(card_kb),
        "closing card": habit_actions.build_closing_text(path, DAY),
        "journal prompt": habit_actions.journal_prompt_text(DAY),
        "stats": stats_text + " " + labels(stats_kb),
        "pause menu": pause_text + " " + labels(pause_kb),
    }


@pytest.mark.parametrize(
    "surface",
    ["daily card", "closing card", "journal prompt", "stats", "pause menu"],
)
def test_no_english_survives_in_russian(tracker: Path, russian, surface: str) -> None:
    """The headline. One case per surface, so a failure names which card broke."""
    rendered = surfaces(tracker)[surface]
    leftover = english_words(rendered)
    assert not leftover, (
        f"the {surface} still contains English while the language is ru: {leftover}\n"
        f"Add a key to core/navig/locales/*.json and read it with _t(). Rendered:\n"
        f"{rendered}"
    )


def test_the_probe_actually_reads_the_surfaces(tracker: Path, russian) -> None:
    """Anti-vacuity. Every assertion above is about an EMPTY list, which is what a
    probe that rendered nothing would also produce."""
    rendered = surfaces(tracker)
    assert len(rendered) == 5
    for name, text in rendered.items():
        assert len(text) > 80, f"{name} rendered {len(text)} characters — did it build?"
    # Something Russian must actually be in there, or the locale never loaded.
    assert any(re.search(r"[А-Яа-я]", text) for text in rendered.values())


def test_the_probe_would_catch_the_bug_it_was_written_for(tracker: Path, russian) -> None:
    """Teeth, without touching the source.

    The exact strings from the operator's screenshot must be absent — and the check
    that finds them has to be the same one the tests above use, or this passes while
    they are broken.
    """
    closing = surfaces(tracker)["closing card"]
    for gone in ("closed", "Missed", "NEVER MISS TWICE", "Three lines in the journal"):
        assert gone not in closing, f"{gone!r} is back in the Russian closing card"
    assert english_words("🌙 <b>6 сентября closed</b>") == ["closed"], (
        "the detector no longer flags the original bug, so the tests above prove nothing"
    )


def test_english_is_left_alone(tracker: Path, monkeypatch) -> None:
    """The other half of the contract: localizing must not empty the English.

    A key present in `ru` but missing from `en` renders the raw KEY — dotted, lowercase
    and quietly wrong — so an English reader would see `habit.card.missed` where a
    sentence belongs.
    """
    monkeypatch.setattr(i18n, "current_language", lambda: "en")
    i18n.shared().reset()
    try:
        for name, text in surfaces(tracker).items():
            assert not re.search(r"\bhabit\.[a-z_.]+\b", text), (
                f"the {name} rendered a raw locale key — a key is missing from en.json:\n{text}"
            )
    finally:
        i18n.shared().reset()


def test_every_key_this_module_reads_exists_in_all_three_locales() -> None:
    """A key missing from one file falls back to English and looks finished.

    `LocalizationStore.get` resolves ru → en → the raw key, so a half-translated file
    is indistinguishable from a complete one at runtime. This is the only place that
    difference is visible.
    """
    import ast
    import json

    module = Path(habit_actions.__file__)
    tree = ast.parse(module.read_text(encoding="utf-8"))

    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name not in ("t", "_t"):
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            value = node.args[0].value
            if isinstance(value, str) and "." in value:
                keys.add(value)

    assert len(keys) >= 25, f"only {len(keys)} locale keys found — did the scan break?"

    locales = Path(i18n.__file__).parent.parent / "locales"
    for lang in ("en", "ru", "fr"):
        data = json.loads((locales / f"{lang}.json").read_text(encoding="utf-8"))
        missing = sorted(k for k in keys if k not in data)
        assert not missing, f"{lang}.json is missing keys habit_actions reads: {missing}"
