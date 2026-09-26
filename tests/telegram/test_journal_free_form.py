"""The journal asks for the day, and can read it back.

The card used to ask three numbered questions — proud of / knocked me off /
tomorrow's first task. The operator asked for the opposite: *"free form of
writing of all my day, so i can analyse my psychology"*. A fixed shape collects
three sentences, and a day is not three sentences.

Two properties matter more than the wording:

* The ENTRY must survive regardless. It is the thing being kept; the reflection
  is a bonus on top of it and must never be able to cost it.
* The reflection is OPT-IN. It hands a private journal to a model, which is a
  choice someone makes rather than one they discover afterwards.
"""

from __future__ import annotations

import json
import pathlib

import pytest

LOCALES = pathlib.Path(__file__).resolve().parents[2] / "navig" / "locales"
LOCALE_NAMES = ("en", "ru", "fr")


def _keys(loc: str) -> dict:
    return json.loads((LOCALES / f"{loc}.json").read_text(encoding="utf-8"))


# ── the prompt ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("loc", LOCALE_NAMES)
def test_the_three_questions_are_gone(loc: str) -> None:
    """The bug, stated directly: a fixed form cannot collect a free-form day."""
    d = _keys(loc)
    for k in ("habit.journal.q1", "habit.journal.q2", "habit.journal.q3"):
        assert k not in d, f"{loc} still carries the fixed question {k}"


@pytest.mark.parametrize("loc", LOCALE_NAMES)
def test_every_locale_has_the_invitation(loc: str) -> None:
    """⚠ A key present in one locale and missing in another renders as the RAW
    KEY in front of a user — so the whole set moves together or not at all."""
    d = _keys(loc)
    for k in (
        "habit.journal.invite",
        "habit.journal.reply_hint",
        "habit.journal.where",
        "habit.journal.reflect.heading",
        "habit.journal.reflect.failed",
    ):
        assert d.get(k), f"{loc} is missing {k}"


@pytest.mark.parametrize("loc", LOCALE_NAMES)
def test_the_where_line_no_longer_says_them(loc: str) -> None:
    """A dependent string is part of the same change.

    `where` read "THEY land in … leave the day without THEM" — plural, because it
    described three lines. Left alone it would have survived the rewrite and
    quietly contradicted the invitation directly above it.
    """
    where = _keys(loc)["habit.journal.where"]
    assert "{day}" in where and "{skip}" in where
    for plural in ("Они попадут", "без них", "They land", "without them"):
        assert plural not in where, f"{loc}: `where` still refers to the three lines"


def test_the_rendered_card_invites_rather_than_asks(monkeypatch) -> None:
    """The surface itself, not just the strings."""
    import navig.telegram.habit_actions as ha
    from navig.core import i18n

    # ⚠ monkeypatch, never a bare assignment: i18n.current_language is a
    # process global, and assigning it directly once pinned a language for an
    # entire xdist worker and reddened unrelated tests in another file.
    monkeypatch.setattr(i18n, "current_language", lambda: "en")

    text = ha.journal_prompt_text("2026-09-11")

    assert "Write about your day" in text
    # The mechanical hints survive — they are what makes the answer identifiable
    # and tell the operator where it goes.
    assert "journal/2026-09-11.md" in text
    assert "skip" in text
    # No numbered form. Checked at line STARTS: a bare `"1." in text` also
    # matches the date in `journal/2026-09-11.md`, so it failed on correct
    # output — the assertion has to describe a numbered question (a line opening
    # with "1. "), not any occurrence of those two characters.
    numbered = [ln for ln in text.splitlines() if ln.strip()[:3] in ("1. ", "2. ", "3. ")]
    assert numbered == [], f"the card still renders numbered questions: {numbered}"


# ── the reflection ────────────────────────────────────────────────────────────


def test_reflect_is_a_registered_owner_only_tool() -> None:
    """It reads a private journal, so a non-owner must never reach it."""
    from navig.telegram import permissions
    from navig.telegram.ai_actions import _SYSTEM

    assert "reflect" in _SYSTEM
    assert permissions.can_use("reflect", is_owner=True) is True
    assert permissions.can_use("reflect", is_owner=False) is False


def test_the_reflection_prompt_forbids_diagnosis() -> None:
    """⚠ The constraints ARE the feature.

    Without them a tired evening sentence comes back labelled as a condition.
    This pins the three that matter, so a later reword cannot quietly drop them.
    """
    from navig.telegram.ai_actions import _SYSTEM

    p = _SYSTEM["reflect"].lower()
    assert "no diagnosis" in p
    assert "clinical" in p
    assert "same language" in p
    # Crisis text must not be analysed at someone.
    assert "crisis" in p or "self-harm" in p


def test_the_reflection_hook_is_actually_on_the_mixin() -> None:
    """⚠ The call site swallows exceptions, so this is the only thing watching.

    `_handle_pending_journal_input` wraps the call in try/except — deliberately:
    the entry is already written and confirmed by then, and a reflection must
    never be able to cost it. The cost of that guarantee is that a rename or a
    deletion of this method becomes a logged warning nobody reads. The test
    doubles cannot catch it either, since every one of them is a plain object
    that never had the method. Assert the wiring on the class itself.
    """
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

    hook = getattr(TelegramCommandsMixin, "_maybe_reflect_on_journal", None)
    assert callable(hook), (
        "_maybe_reflect_on_journal is gone from TelegramCommandsMixin — the call "
        "site catches Exception, so the journal reflection would be silently dead"
    )

    # And that the handler still CALLS it: a method nothing invokes is the same
    # dead feature by a different route.
    #
    # ⚠ On the AST, not the text. The first version asserted
    # `"_maybe_reflect_on_journal" in inspect.getsource(...)` and passed with the
    # call deleted — the comment above the call explains why it is guarded, so
    # it carries the name too. A substring cannot tell an invocation from prose
    # about one. (Teeth-tested: removing the call now fails here.)
    import ast
    import inspect
    import textwrap

    tree = ast.parse(
        textwrap.dedent(inspect.getsource(TelegramCommandsMixin._handle_pending_journal_input))
    )
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "_maybe_reflect_on_journal" in called, (
        "the journal handler no longer calls _maybe_reflect_on_journal — the "
        f"reflection is dead code. Calls found: {sorted(called)}"
    )


class _Chan:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, chat_id, text, **_):
        self.sent.append(text)
        return {"message_id": 1}


async def _reflect(channel, entry: str):
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

    await TelegramCommandsMixin._maybe_reflect_on_journal(channel, 42, entry)


@pytest.mark.asyncio
async def test_it_stays_silent_when_not_enabled(monkeypatch) -> None:
    """The default. Nothing reads the journal until the operator says so."""
    from navig.config import get_config_manager

    monkeypatch.setattr(
        type(get_config_manager()), "get", lambda self, k, d=None: False, raising=False
    )
    ch = _Chan()
    await _reflect(ch, "A long entry about the whole of my day, with detail." * 3)
    assert ch.sent == []


@pytest.mark.asyncio
async def test_a_broken_reflection_never_reaches_the_operator_as_a_crash(monkeypatch) -> None:
    """⚠ The entry is already written and already confirmed by this point.

    Whatever happens here must not raise into a path that has told the operator
    their day was recorded.
    """
    from navig.config import get_config_manager
    from navig.telegram import ai_actions

    monkeypatch.setattr(
        type(get_config_manager()), "get", lambda self, k, d=None: True, raising=False
    )

    async def _boom(*_a, **_k):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(ai_actions, "run_text_action", _boom)

    ch = _Chan()
    await _reflect(ch, "A long entry about the whole of my day, with detail." * 3)  # must not raise


@pytest.mark.asyncio
async def test_a_one_word_entry_is_not_reflected_on(monkeypatch) -> None:
    """Below a sentence there is nothing to read back, and saying something
    anyway is how a reflection becomes noise."""
    from navig.config import get_config_manager
    from navig.telegram import ai_actions

    monkeypatch.setattr(
        type(get_config_manager()), "get", lambda self, k, d=None: True, raising=False
    )
    called: list[str] = []

    async def _spy(tool, content, **_k):
        called.append(tool)
        return {"ok": True, "result": "..."}

    monkeypatch.setattr(ai_actions, "run_text_action", _spy)

    ch = _Chan()
    await _reflect(ch, "ok")
    assert called == [], "a two-character entry was sent to the model"
