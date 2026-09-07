"""Every `/todo` surface, rendered in Russian, must contain no English prose.

The PIM shipped a day before this test and was entirely English — OVERDUE / TODAY /
INBOX, "no date yet", "Done ✓", and every date as "Fri 11 Sep" because `format_local`
used `strftime("%a %-d %b")`, the C locale's names. Exactly the symptom just fixed in
the habit module, in a surface one day old, which is why the check belongs here rather
than in a review habit.

Same shape as `test_habit_surfaces_are_localized`: it probes the RENDER, not the
source. A source scan finds legitimate English fallbacks in every one of these modules
— each `_t(key) or "English"` carries one on purpose, so a locale file that loses a key
degrades to English instead of printing a raw dotted key. Those fallbacks are correct
and unreachable; only a string that actually reaches the operator can fail this.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from navig.core import i18n
from navig.pim.clock import to_utc_iso
from navig.store.board import PROPOSED_ORIGINS, BoardStore
from navig.telegram import todo_actions

TZ = datetime.timezone(datetime.timedelta(hours=2))
NOW = datetime.datetime(2026, 9, 6, 15, 0, tzinfo=TZ)

#: Latin tokens allowed in Russian output — a command, a path, or HTML that survives
#: escaping. Deliberately short: an allowlist is where an untranslated string hides.
ALLOWED = frozenset(
    {
        "todo",  # the command in every empty-state hint
        "extensions",
        "b",  # <b>, <i>: HTML tags
        "i",
        "lt",  # &lt; / &gt; from html.escape
        "gt",
        "amp",
    }
)

_WORD = re.compile(r"[A-Za-z]+")


@pytest.fixture
def store(tmp_path: Path, monkeypatch) -> BoardStore:
    """A list with something in every bucket, including a SUGGESTION.

    Buckets render only when they have rows, so a fixture that misses one leaves that
    heading untested — the probe would pass having never built it.
    """
    st = BoardStore(tmp_path / "board.db")
    monkeypatch.setattr(todo_actions, "_store", lambda: st)
    monkeypatch.setattr(todo_actions, "local_now", lambda: NOW)

    def at(hours: float) -> str:
        return to_utc_iso(NOW + datetime.timedelta(hours=hours))

    st.create_todo("Продлить домен", category="business", due_at=at(-30))  # overdue
    st.create_todo("Стоматолог", category="life", due_at=at(3), remind_before=["1d"])
    st.create_todo("Полить растения", due_at=at(4), recur="daily")
    st.create_todo("Отчёт", due_at=at(72))  # soon
    st.create_todo("Страховка", due_at=at(24 * 40))  # later
    st.create_todo("Позвонить бухгалтеру")  # inbox
    st.create_todo(
        "Обновить сертификат",
        space="homelab-space",
        origin="agent",
        origin_ref="homelab-space:CURRENT_PHASE.md:14",
    )
    return st


@pytest.fixture
def russian(monkeypatch) -> Iterator[None]:
    monkeypatch.setattr(i18n, "current_language", lambda: "ru")
    i18n.shared().reset()
    yield
    i18n.shared().reset()


def english_words(text: str) -> list[str]:
    return sorted({w for w in _WORD.findall(text) if w.lower() not in ALLOWED})


def surfaces(store: BoardStore) -> dict[str, str]:
    """Every view plus its buttons, and the detail card.

    Buttons are included because a card's text and its KEYBOARD are built in different
    functions — #1242's write-up records localizing one and leaving the other.
    """

    def labels(keyboard: dict) -> str:
        return " ".join(
            str(b.get("text", "")) for row in keyboard["inline_keyboard"] for b in row
        )

    out: dict[str, str] = {}
    for view in ("all", "inbox", "today", "cats"):
        text, keyboard = todo_actions.build_view(view, now=NOW)
        out[f"view:{view}"] = f"{text} {labels(keyboard)}"

    todo = store.list_todos()[0]
    rendered = todo_actions.build_detail(todo["id"], now=NOW)
    assert rendered is not None
    out["detail"] = f"{rendered[0]} {labels(rendered[1])}"
    return out


@pytest.mark.parametrize(
    "surface", ["view:all", "view:inbox", "view:today", "view:cats", "detail"]
)
def test_no_english_survives_in_russian(store: BoardStore, russian, surface: str) -> None:
    rendered = surfaces(store)[surface]
    leftover = english_words(rendered)
    assert not leftover, (
        f"{surface} still contains English while the language is ru: {leftover}\n"
        f"Add a key to core/navig/locales/*.json and read it with _t(). Rendered:\n"
        f"{rendered}"
    )


def test_dates_are_built_from_the_locale_not_strftime(store: BoardStore, russian) -> None:
    """The headline defect. `%a` and `%b` are the C locale's names, so a date rendered
    "Fri 11 Sep" no matter what `/lang` said — and nothing else on the row would have
    looked wrong."""
    rendered = surfaces(store)["view:all"]
    for c_locale in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun", "Sep", "Oct"):
        assert c_locale not in rendered, f"{c_locale!r} is a strftime name, not a locale one"
    assert re.search(r"[А-Яа-я]{2,} \d", rendered), "no Russian date rendered at all"


def test_the_countdown_is_localized(store: BoardStore, russian) -> None:
    """"in 3h" / "2d ago" sit on every dated row — the most-repeated string on the card."""
    rendered = surfaces(store)["view:all"]
    assert "через" in rendered
    assert "назад" in rendered


def test_a_suggestion_is_visibly_a_suggestion(store: BoardStore, russian) -> None:
    """The marker and the keep/no buttons must agree about what a suggestion IS.

    Three places decided that independently and two checked only `origin == "ai"`,
    while everything that actually creates a suggestion (`navig todo scan --add`, the
    `task_add` tool) writes `"agent"`. So the row got the unfamiliar two-button
    treatment with NO sparkle and no explanation — indistinguishable from a task the
    operator wrote.
    """
    assert "agent" in PROPOSED_ORIGINS and "ai" in PROPOSED_ORIGINS

    rendered = surfaces(store)
    assert "✨" in rendered["view:inbox"], "the suggestion row has no sparkle"

    suggestion = next(t for t in store.list_todos() if t["origin"] == "agent")
    detail = todo_actions.build_detail(suggestion["id"], now=NOW)
    assert detail is not None
    assert "✨" in detail[0]

    _, keyboard = todo_actions.build_view("inbox", now=NOW)
    payloads = [b["callback_data"] for row in keyboard["inline_keyboard"] for b in row]
    assert any(p.startswith("td:ok:") for p in payloads), "no keep button"


def test_the_probe_actually_builds_every_bucket(store: BoardStore, russian) -> None:
    """Anti-vacuity, twice over.

    Every assertion above is about an EMPTY list, and a bucket renders only when it has
    rows — so a fixture missing a bucket leaves that heading untested while the probe
    stays green. This checks all five headings are really on screen.
    """
    rendered = surfaces(store)["view:all"]
    for heading in ("ПРОСРОЧЕНО", "СЕГОДНЯ", "БЛИЖАЙШИЕ", "ПОЗЖЕ", "ВХОДЯЩИЕ"):
        assert heading in rendered, f"the {heading} bucket never rendered — fixture gap"
    assert len(rendered) > 400


def test_english_still_renders_english(store: BoardStore, monkeypatch) -> None:
    """A key present in ru but missing from en renders the raw dotted KEY.

    The `_t(...) or "English"` fallbacks make that invisible in Russian, so this is the
    only place a half-written en.json shows up.
    """
    monkeypatch.setattr(i18n, "current_language", lambda: "en")
    i18n.shared().reset()
    try:
        for name, text in surfaces(store).items():
            assert not re.search(r"\bpim\.[a-z_.]+\b", text), (
                f"{name} rendered a raw locale key — a key is missing from en.json:\n{text}"
            )
        assert "OVERDUE" in surfaces(store)["view:all"]
    finally:
        i18n.shared().reset()


def test_every_key_these_modules_read_exists_in_all_three_locales() -> None:
    """A key missing from one file falls back to English and looks finished."""
    import ast
    import json

    from navig.pim import dates, render

    keys: set[str] = set()
    for module in (todo_actions, render, dates):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in ("t", "_t"):
                continue
            if node.args and isinstance(node.args[0], ast.Constant):
                value = node.args[0].value
                if isinstance(value, str) and value.startswith(("pim.", "month", "weekday")):
                    keys.add(value)

    assert len(keys) >= 20, f"only {len(keys)} literal locale keys found — did the scan break?"

    locales = Path(i18n.__file__).parent.parent / "locales"
    for lang in ("en", "ru", "fr"):
        data = json.loads((locales / f"{lang}.json").read_text(encoding="utf-8"))
        missing = sorted(k for k in keys if k not in data)
        assert not missing, f"{lang}.json is missing keys the PIM reads: {missing}"


def test_a_seed_category_is_translated_but_an_invented_one_is_not(russian) -> None:
    """Only the four words WE ship get translated.

    `life · business · project · rendezvous` are ours, so on a fresh install they are
    the entire picker and an English one is the first thing a Russian operator sees.
    A category they invented is their own word — translating it would be rewriting
    their data, and there is no dictionary that should try.
    """
    from navig.pim.render import category_label

    assert category_label("life") == "жизнь"
    assert category_label("business") == "бизнес"
    assert category_label("freelance") == "freelance", "an invented category is left alone"
    assert category_label("подработка") == "подработка"
    assert category_label("") == "без категории"


def test_the_stored_value_is_never_the_translation(store: BoardStore, russian) -> None:
    """Storing the label would break `--category life` after a `/lang` and orphan every
    row written before the change. The key is the data; the label is the render."""
    todo = next(t for t in store.list_todos() if t["category"] == "life")
    assert todo["category"] == "life"
    assert store.list_todos(category="life"), "the English key must still filter"
