"""No English literal may reach the operator from the TikTok card.

`tiktok_actions.py` is the largest Telegram module (1533 lines) and had no
translator: 42 messages plus all five buttons were English literals, on an install
whose language is Russian. Most of them are what you read when something went
WRONG — a bot-wall, a file too large to upload, an upload Telegram rejected — which
is exactly when the wording has to be clear rather than merely present.

**This guard reads the SOURCE, not a render, and that is deliberate.** Every one of
these strings sits behind a live TikTok fetch, a downloader and ffmpeg; a render
probe would need all three mocked and would still only reach the branches the mocks
allowed. The property that matters is structural — *no string literal reaches a send
call or a button* — and the AST can assert that over the whole file at once, which
is what a render probe of a few branches cannot.

⚠ The scan covers BOTH halves. A scan of send-call arguments alone finds the
messages and misses the KEYBOARD, because the two are built in different places —
the failure #1242 recorded for the habit card, repeated here. Every button the
operator taps was English while 42 messages were being localized.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from navig.telegram import tiktok_actions

MODULE = Path(tiktok_actions.__file__)
LOCALES = MODULE.parent.parent / "locales"

#: Calls whose string arguments the operator reads.
SEND = ("send_message", "answer_callback", "edit_message", "send_photo", "send_video",
        "send_document", "send_rich_message", "reply_text")

#: A literal is prose when it has two words. Shorter fragments are separators,
#: parse modes and callback ids, none of which are read as language.
PROSE = re.compile(r"[A-Za-z]{2,}\s+[A-Za-z]{2,}")

#: Verified-safe literals that LOOK like prose. Each states why it is not language.
ALLOWED_LITERALS = frozenset(
    {
        # A markdown/HTML join, not a sentence.
        "\n\n",
    }
)


@pytest.fixture(scope="module")
def tree() -> ast.Module:
    return ast.parse(MODULE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def docstrings(tree: ast.Module) -> set[str]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                out.add(doc)
    return out


def _prose_literals(node: ast.AST, docstrings: set[str]) -> list[tuple[int, str]]:
    found = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            v = sub.value
            if v in docstrings or v in ALLOWED_LITERALS:
                continue
            if PROSE.search(v):
                found.append((sub.lineno, v[:70]))
    return found


def test_no_english_literal_reaches_a_send_call(tree: ast.Module, docstrings) -> None:
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else (
            node.func.id if isinstance(node.func, ast.Name) else "")
        if not any(s in name for s in SEND):
            continue
        offenders += _prose_literals(node, docstrings)
    assert not offenders, (
        "these strings are sent to the operator as English literals — route them "
        "through _t():\n" + "\n".join(f"  L{ln}: {v!r}" for ln, v in offenders)
    )


def test_no_english_literal_reaches_a_button(tree: ast.Module, docstrings) -> None:
    """The half a send-call scan cannot see.

    A keyboard is a dict literal built somewhere else entirely, so localizing every
    message can leave every button English and nothing notices.
    """
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant) and key.value == "text"):
                continue
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                offenders.append((value.lineno, value.value[:70]))
    assert not offenders, (
        "these BUTTON labels are English literals — route them through _t():\n"
        + "\n".join(f"  L{ln}: {v!r}" for ln, v in offenders)
    )


def test_the_scan_actually_reads_the_module(tree: ast.Module) -> None:
    """Anti-vacuity. Both assertions above pass trivially over an empty walk, and a
    renamed helper or a moved keyboard would produce exactly that."""
    sends = sum(
        1
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and any(s in (n.func.attr if isinstance(n.func, ast.Attribute) else "") for s in SEND)
    )
    buttons = sum(
        1
        for n in ast.walk(tree)
        if isinstance(n, ast.Dict)
        and any(isinstance(k, ast.Constant) and k.value == "text" for k in n.keys)
    )
    assert sends >= 25, f"only {sends} send calls found — did the scan read the module?"
    assert buttons >= 5, f"only {buttons} buttons found — has the keyboard moved?"


def _keys_read(tree: ast.Module) -> set[str]:
    keys = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else ""
        if name != "_t" or not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            keys.add(arg.value)
    return keys


def test_every_key_exists_in_all_three_locales(tree: ast.Module) -> None:
    """A missing key renders as the key itself — visible to the operator, invisible
    to a test that only checks the language it happens to run under."""
    keys = _keys_read(tree)
    assert len(keys) >= 40, f"only found {len(keys)} keys — did the scan read the module?"
    for lang in ("en", "ru", "fr"):
        data = json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))
        missing = sorted(k for k in keys if k not in data)
        assert not missing, f"{lang}.json is missing: {missing}"


def test_the_russian_strings_are_actually_russian(tree: ast.Module) -> None:
    """A key copied from en.json into ru.json passes every check above.

    Excludes the values that are correctly identical across locales — a brand, a
    command a user types, an emoji-only label — each named rather than pattern-matched.
    """
    identical_by_design = {
        "tiktok.download.caption",  # "via NAVIG" — a brand signature on an upload
    }
    en = json.loads((LOCALES / "en.json").read_text(encoding="utf-8"))
    ru = json.loads((LOCALES / "ru.json").read_text(encoding="utf-8"))
    same = [
        k
        for k in _keys_read(tree)
        if k not in identical_by_design and en.get(k) == ru.get(k)
    ]
    assert not same, f"these ru.json values are still the English text: {sorted(same)}"


def test_a_message_that_names_a_button_takes_it_as_a_placeholder() -> None:
    """⚠ `tiktok.photo.unreadable` tells the operator which buttons still work.

    Spelling those names into the sentence would let it point at words the keyboard
    no longer carries the moment a button is renamed — a Russian sentence naming two
    English buttons. Taking them as placeholders makes that impossible.
    """
    for lang in ("en", "ru", "fr"):
        data = json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))
        text = data["tiktok.photo.unreadable"]
        assert "{analyse}" in text and "{audio}" in text, (
            f"{lang}.json spells the button names into tiktok.photo.unreadable "
            f"instead of taking them as placeholders: {text!r}"
        )
