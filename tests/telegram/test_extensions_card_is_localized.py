"""The `/extensions` card, rendered in Russian, must contain no English prose.

It is the card every other message points at — "Turn it on: /extensions" — so a Russian
warning was leading to an English screen. It is also the screen the operator READS to
decide what to switch off, which makes the 21 descriptions matter more than the chrome
around them.

Third instance of the same probe (habits · todo · extensions). The shape is the point:
localize a surface, then render it and look, because a locale key that RESOLVES is not
the same as a card that reads correctly — and every miss so far was invisible in the
source and obvious in the output.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from navig.core import i18n
from navig.gateway.channels.telegram_extensions import (
    EXTENSIONS,
    GROUP_ORDER,
    ext_about,
    ext_description,
    ext_label,
    group_label,
    list_extensions,
)
from navig.telegram import extension_actions

#: Latin tokens allowed in Russian output. Every one is a command, a protocol name or
#: a product name that is not translated in any language.
ALLOWED = frozenset(
    {
        "extensions", "help", "todo", "t", "task", "restart", "briefing",
        "card", "habits", "stats", "workout", "health", "weigh", "body",
        "b", "i", "pre", "code",  # HTML tags
        "dns", "ssl", "whois", "sql", "ssh", "docker", "tiktok", "telegram",
        "business", "navig", "csv", "metrics", "id",
    }
)

_WORD = re.compile(r"[A-Za-z]+")


@pytest.fixture
def russian(monkeypatch) -> Iterator[None]:
    monkeypatch.setattr(i18n, "current_language", lambda: "ru")
    i18n.shared().reset()
    yield
    i18n.shared().reset()


def english_words(text: str) -> list[str]:
    # Commands arrive as "/card /habits"; strip the slash so the allowlist matches.
    cleaned = text.replace("/", " ")
    return sorted({w for w in _WORD.findall(cleaned) if w.lower() not in ALLOWED})


def surfaces() -> dict[str, str]:
    def labels(keyboard: dict) -> str:
        return " ".join(
            str(b.get("text", "")) for row in keyboard["inline_keyboard"] for b in row
        )

    listing, list_kb = extension_actions.build_list()
    index, index_kb = extension_actions.build_detail(None)
    detail, detail_kb = extension_actions.build_detail("habits")
    return {
        "list": f"{listing} {labels(list_kb)}",
        "index": f"{index} {labels(index_kb)}",
        "detail": f"{detail} {labels(detail_kb)}",
    }


@pytest.mark.parametrize("surface", ["list", "index", "detail"])
def test_no_english_survives_in_russian(russian, surface: str) -> None:
    rendered = surfaces()[surface]
    leftover = english_words(rendered)
    assert not leftover, (
        f"the {surface} still contains English while the language is ru: {leftover}\n"
        f"Add a key to core/navig/locales/*.json. Rendered:\n{rendered}"
    )


def test_every_extension_and_group_is_translated(russian) -> None:
    """The catalog, not just the chrome.

    A card whose headings are Russian and whose 21 rows are English is still two
    languages in one message — the exact thing this sweep exists to end.
    """
    untranslated = [e.id for e in EXTENSIONS if ext_label(e) == e.label and e.label != "Core"]
    assert not untranslated, f"these extensions still show their English label: {untranslated}"

    same = [e.id for e in EXTENSIONS if ext_description(e) == e.description]
    assert not same, f"these descriptions are still English: {same}"

    for group in GROUP_ORDER:
        assert group_label(group) != group, f"group {group!r} is untranslated"


def test_about_bullets_keep_their_order(russian) -> None:
    """The bullets are ordered prose — "nothing is deleted" always comes last — so
    they are keyed by INDEX. A dict keyed by content would reorder on translation."""
    habits = next(e for e in EXTENSIONS if e.id == "habits")
    bullets = ext_about(habits)
    assert len(bullets) == len(habits.about)
    assert all(b != en for b, en in zip(bullets, habits.about, strict=True))
    assert "остаются как есть" in bullets[-1], "the reassurance must stay last"


def test_the_shared_payload_carries_the_localized_text(russian) -> None:
    """`list_extensions()` is the ONE producer the bot card, the CLI and the Deck read.

    Localizing there is what stops the three disagreeing — the property that producer
    exists for. The Deck groups by `group`, so those KEYS stay English and the display
    side travels beside them.
    """
    payload = list_extensions()
    rows = {r["key"]: r for r in payload["extensions"]}
    assert rows["habits"]["label"] == "Привычки"
    assert "чек-ина" in rows["habits"]["description"]

    assert payload["group_order"] == list(GROUP_ORDER), "grouping keys must stay stable"
    assert payload["group_labels"]["Life"] == "Жизнь"
    assert {r["group"] for r in payload["extensions"]} <= set(GROUP_ORDER)


def test_english_still_renders_english(monkeypatch) -> None:
    monkeypatch.setattr(i18n, "current_language", lambda: "en")
    i18n.shared().reset()
    try:
        for name, text in surfaces().items():
            assert not re.search(r"\btgext\.[a-z_.0-9]+\b", text), (
                f"the {name} rendered a raw locale key — a key is missing from en.json:\n{text}"
            )
        assert "Habits" in surfaces()["list"]
    finally:
        i18n.shared().reset()


def test_every_catalog_entry_has_keys_in_all_three_locales() -> None:
    """Derived from the CATALOG, so an extension added without translations fails here
    rather than rendering silently in English on the operator's phone."""
    expected: set[str] = {f"tgext.group.{g}" for g in GROUP_ORDER}
    for ext in EXTENSIONS:
        expected.add(f"tgext.{ext.id}.label")
        expected.add(f"tgext.{ext.id}.description")
        expected.update(f"tgext.{ext.id}.about.{i}" for i in range(len(ext.about)))

    assert len(expected) >= 50, f"only {len(expected)} keys derived — did the catalog shrink?"

    locales = Path(i18n.__file__).parent.parent / "locales"
    for lang in ("en", "ru", "fr"):
        data = json.loads((locales / f"{lang}.json").read_text(encoding="utf-8"))
        missing = sorted(k for k in expected if k not in data)
        assert not missing, f"{lang}.json is missing catalog keys: {missing}"


def test_the_probe_actually_rendered_something(russian) -> None:
    """Anti-vacuity: every assertion above is about an EMPTY list."""
    rendered = surfaces()
    assert len(rendered) == 3
    for name, text in rendered.items():
        assert len(text) > 200, f"{name} rendered {len(text)} characters — did it build?"
    assert "Расширения" in rendered["list"]
