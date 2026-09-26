"""`/help` must be navigable in the operator's language.

The Help Encyclopedia is five screens deep — home, a subcategory chooser, a command
list, a subcategory list, a command detail — and every title, label and button on
the way was an English literal, on an install whose language is Russian. An
operator who cannot read the buttons cannot reach the help.

**What is deliberately NOT localized, and why.** The 115 `SlashCommandEntry`
descriptions ("Wake up greeting") stay English. That same string also feeds
Telegram's `setMyCommands` autocomplete and CLI dispatch — it is a cross-surface
contract, not a `/help` string — so translating it here would change three surfaces
from one edit. `test_command_descriptions_are_deliberately_english` pins that
boundary so the omission reads as a decision rather than an oversight.

⚠ The catalog's `cat.label` stays English too, because it is a MATCHING key:
`/help "Getting Started"` resolves against it. Localizing the button without that
distinction would have broken `/help <the button you can now read>` for exactly the
operator the change is for. Both names resolve now.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from navig.core import i18n
from navig.gateway.channels import telegram_commands as tc
from navig.gateway.channels.telegram_commands import TelegramCommandsMixin as M

LOCALES = Path(tc.__file__).parent.parent.parent / "locales"

#: Latin words that are correct in every locale, each with a reason.
ALLOWED = frozenset(
    {
        # Slash commands and the reply keywords — what the operator TYPES.
        "start", "help", "status", "ping", "about", "version", "skill", "mode",
        "translate", "summarize", "music",
        # Product and vendor names.
        "navig", "docker", "cli",
        # Category values printed verbatim from the registry (core, ops, …) and
        # HTML tags that survive escaping.
        "core", "b", "i", "code",
    }
)

_WORD = re.compile(r"[A-Za-zÀ-ɏ]+")
#: The two shapes that carry a command DESCRIPTION, which is out of scope by design
#: (above): the `• /cmd — desc` bullets on a list screen, and the `📄 desc` line on a
#: command-detail screen. Everything else on these screens is navigation.
_DESCRIPTION_LINE = re.compile(r"^\s*(•\s|📄\s)")


def english_words(text: str) -> list[str]:
    return sorted({w for w in _WORD.findall(text) if w.lower() not in ALLOWED})


@pytest.fixture
def russian(monkeypatch) -> Iterator[None]:
    """⚠ monkeypatch, never assignment — `current_language` is a module global and
    a bare assignment pins it for every later test in the same xdist worker."""
    monkeypatch.setattr(i18n, "current_language", lambda: "ru")
    i18n.shared().reset()
    yield
    i18n.shared().reset()


def _buttons(rows: list[list[dict[str, str]]]) -> str:
    return " ".join(b.get("text", "") for row in rows for b in row)


def _navigation_only(text: str) -> str:
    """Titles, prompts and labels — everything except the description lines."""
    return "\n".join(
        line for line in text.splitlines() if not _DESCRIPTION_LINE.match(line)
    )


def screens() -> dict[str, str]:
    """Every /help screen, as the navigation text plus every button label."""
    out: dict[str, str] = {}
    text, kb = M._build_help_home()
    out["home"] = _navigation_only(text) + " " + _buttons(kb)

    chooser = M._build_help_category("ai_models")
    assert chooser is not None, "the subcategory-chooser fixture category vanished"
    out["chooser"] = _navigation_only(chooser[0]) + " " + _buttons(chooser[1])

    listing = M._build_help_category("getting_started")
    assert listing is not None, "the command-list fixture category vanished"
    out["command list"] = _navigation_only(listing[0]) + " " + _buttons(listing[1])

    subcat = M._build_help_subcategory("ai_models", "models")
    assert subcat is not None, "the subcategory fixture vanished"
    out["subcategory"] = _navigation_only(subcat[0]) + " " + _buttons(subcat[1])

    detail = M._build_help_command_detail("status", "getting_started")
    assert detail is not None, "the command-detail fixture vanished"
    out["detail"] = _navigation_only(detail[0]) + " " + _buttons(detail[1])
    return out


def test_no_english_survives_in_russian(russian) -> None:
    offenders = {
        name: english_words(text) for name, text in screens().items() if english_words(text)
    }
    assert not offenders, (
        "these /help screens still navigate in English while the language is ru:\n"
        + "\n".join(f"  {n}: {w}" for n, w in offenders.items())
    )


def test_the_probe_actually_rendered_every_screen(russian) -> None:
    """Anti-vacuity. The assertion above passes over an empty string, and a builder
    that returned None would produce exactly that — so each screen is required to
    be non-trivial AND to contain Cyrillic."""
    rendered = screens()
    assert len(rendered) == 5, f"only {len(rendered)} screens probed"
    for name, text in rendered.items():
        assert len(text) > 40, f"{name} rendered {len(text)} chars — did it build?"
        assert re.search(r"[А-Яа-я]", text), f"{name} contains no Russian at all"


def test_english_is_left_alone(monkeypatch) -> None:
    """English must still read as English, and no screen may show a raw locale key."""
    monkeypatch.setattr(i18n, "current_language", lambda: "en")
    i18n.shared().reset()
    try:
        rendered = screens()
        assert "NAVIG Command Center" in rendered["home"]
        assert "Getting Started" in rendered["command list"]
        for name, text in rendered.items():
            assert not re.search(r"\bhelp\.[a-z_.]+\b", text), (
                f"the {name} screen rendered a raw locale key — a key is missing "
                f"from en.json:\n{text}"
            )
    finally:
        i18n.shared().reset()


def test_a_category_resolves_by_both_its_english_and_localized_name(russian) -> None:
    """⚠ The regression this change could most easily have introduced.

    `/help <topic>` matches a category by label. Localizing the button while
    matching only the English label would break `/help Диагностика` for the very
    operator who can now read that button; dropping the English label would break
    every doc and habit that uses it. Both must resolve.
    """

    def resolve(topic: str) -> str | None:
        wanted = topic.lower()
        return next(
            (
                cat.key
                for cat in tc._HELP_CATEGORIES
                if wanted
                in {cat.label.lower(), M._help_label("cat", cat.key, cat.label).lower()}
            ),
            None,
        )

    assert resolve("Getting Started") == "getting_started"
    assert resolve("Начало работы") == "getting_started"
    assert resolve("Diagnostics") == "diagnostics"
    assert resolve("Диагностика") == "diagnostics"
    assert resolve("not a category") is None


def test_a_missing_locale_entry_falls_back_to_the_english_label(monkeypatch) -> None:
    """A label is a NAME, so a missing key must render the English word, never the
    raw `help.cat.*` key — the failure mode that turns a menu into debug output."""
    monkeypatch.setattr(M, "_t", staticmethod(lambda key, **_: key))
    assert M._help_label("cat", "docker", "Docker") == "Docker"
    assert M._help_label("sub", "models", "Models & Providers") == "Models & Providers"


def test_every_key_exists_in_all_three_locales() -> None:
    """A missing key renders as the key itself — visible to the operator, invisible
    to a test that only checks the language it happens to run under."""
    src = Path(tc.__file__).read_text(encoding="utf-8")
    keys = set(re.findall(r'_t\(\s*["\'](help\.[a-z_.]+)["\']', src))
    keys |= {f"help.cat.{c.key}" for c in tc._HELP_CATEGORIES}
    keys |= {
        f"help.sub.{s.key}" for c in tc._HELP_CATEGORIES for s in (c.subcategories or [])
    }
    assert len(keys) >= 30, f"only found {len(keys)} keys — did the scan read the module?"
    for code in ("en", "ru", "fr"):
        data = json.loads((LOCALES / f"{code}.json").read_text(encoding="utf-8"))
        missing = sorted(k for k in keys if k not in data)
        assert not missing, f"{code}.json is missing: {missing}"


def test_command_descriptions_are_deliberately_english() -> None:
    """The boundary of this change, asserted so it reads as a decision.

    `SlashCommandEntry.description` also feeds `setMyCommands` and CLI dispatch, so
    localizing it changes three surfaces from one edit. If that work is done, this
    test is what should be updated — not quietly deleted.
    """
    entry = next(e for e in tc._SLASH_REGISTRY if e.command == "start")
    assert entry.description == "Wake up greeting", (
        "a command description changed — if it was localized, `setMyCommands` and "
        "CLI dispatch read the same string and must be handled in the same change"
    )
    src = Path(tc.__file__).read_text(encoding="utf-8")
    assert "setMyCommands" in src, "the surface this boundary exists for has moved"
