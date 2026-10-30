"""No Telegram card may answer the operator in English — for EVERY card module.

Three times in one session the same defect shipped in a different module: a card
surface built its strings as English literals on an install whose language is
Russian. Each time it was invisible for the same reason — the guards that existed
each named ONE module (`habit`, `todo`, `extensions`), so a module with no
translator at all is the one shape none of them can see.

This guard is scoped by SHAPE, not by a list: it discovers every module under
`navig/telegram/` that sends a message or builds a button. A card module added
tomorrow is covered the day it gets its first `send_message`, with nobody
remembering to add it here — the same reason `pluginDirs()` discovers plugins
instead of enumerating them.

**It reads the SOURCE, not a render.** These strings sit behind live network calls,
a downloader and ffmpeg; a render probe would need all of it mocked and would still
only reach the branches the mocks allowed. The property is structural — *no string
literal reaches a send call or a button* — and the AST asserts it over every module
at once, which is what a render probe of a few branches cannot.

⚠ **Two rules, because a scan of send calls alone finds the MESSAGES and misses the
KEYBOARD.** They are built in different places. Localizing 42 TikTok messages left
all five buttons English; the same thing happened to the habit card in #1242. One
rule would have passed both times.

The baseline is EMPTY and there is no mechanism to add to it. A module that renders
English to the operator is a defect, not a category — the per-module guards
(`test_{habit,todo,reply,tiktok}_*_are_localized.py`) additionally check that every
key exists in all three locales and that the Russian is not just the English text,
which is a different question from the one asked here.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from navig import telegram as telegram_pkg
from navig.gateway.channels import telegram_extensions

TELEGRAM_DIR = Path(telegram_pkg.__file__).parent

#: Calls whose string arguments the operator reads. The channel methods, plus the
#: shapes the card modules actually send through: the raw ``_api_call("sendMessage",
#: …)`` and the module-local ``_edit`` / ``_send_prompt`` helpers (habit, todo, body,
#: extension, agenda), which take the text positionally. ``edit_message`` already
#: matches ``edit_message_text`` (the match is a substring).
SEND = (
    "send_message", "answer_callback", "edit_message", "send_photo", "send_video",
    "send_document", "send_rich_message", "reply_text",
    "_api_call", "_edit", "_send_prompt",
)

#: A literal is prose when it carries two words. Shorter fragments are separators,
#: parse modes, callback ids and format specs — none of them read as language.
PROSE = re.compile(r"[A-Za-z]{2,}\s+[A-Za-z]{2,}")

#: A BUTTON is judged more strictly than a message: one word is a label. But a
#: symbol-only button is NOT language — `✕`, `⋯`, `←` mean the same in every
#: locale, and `todo_actions` has exactly those two. Requiring a Latin letter is
#: what separates them; it was measured, not assumed (2 findings became 0).
LETTER = re.compile(r"[A-Za-z]")

#: Literals that reach a send call and are verified NOT to be language.
ALLOWED = frozenset({
    "\n\n",
    " — /todo",  # appointments' plain fallback names the command a user types
})


def scope() -> list[Path]:
    """Every Telegram card module, discovered from disk.

    `telegram_extensions` lives under `gateway/channels/` rather than here, and is
    included by name because it IS a card the operator reads — the extensions
    screen. It is the one exception to the directory rule, and it is named rather
    than pattern-matched so the exception cannot quietly grow.
    """
    files = sorted(p for p in TELEGRAM_DIR.glob("*.py") if p.name != "__init__.py")
    files.append(Path(telegram_extensions.__file__))
    return files


def _docstrings(tree: ast.Module) -> set[str]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                out.add(doc)
    return out


def _segments(node: ast.AST) -> list[ast.Constant]:
    """The literal text a composed value contributes: a constant, an f-string's own
    segments, both sides of a ``+`` and both arms of a conditional.

    It does NOT descend into calls or ``{…}`` fields: ``_t("key")`` and
    ``_t("key") or "Tasks"`` are the locale lookup and its fallback, not text the
    module wrote for the operator.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node]
    if isinstance(node, ast.JoinedStr):
        return [v for v in node.values if isinstance(v, ast.Constant)]
    if isinstance(node, ast.BinOp):
        return _segments(node.left) + _segments(node.right)
    if isinstance(node, ast.IfExp):
        return _segments(node.body) + _segments(node.orelse)
    return []


def _local_assignments(tree: ast.Module) -> tuple[dict[int, ast.AST], dict[tuple[int, str], list]]:
    """(innermost enclosing function of every node, values assigned to each local name).

    A card is often composed before the send — ``card = "…"; send_message(c, card)`` —
    and a walk of the send call alone sees only the name.
    """
    owner: dict[int, ast.AST] = {}
    assigned: dict[tuple[int, str], list] = {}
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(func):
            owner[id(node)] = func  # an inner function walks later and wins
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)) and node.value is not None:
                targets, value = [node.target], node.value
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    assigned.setdefault((id(func), target.id), []).append(value)
    return owner, assigned


def _scan(path: Path) -> tuple[list[str], list[str], int, int]:
    """(message offenders, button offenders, send calls seen, buttons seen)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = _docstrings(tree)
    owner, assigned = _local_assignments(tree)
    messages: list[str] = []
    buttons: list[str] = []
    sends = seen_buttons = 0

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else (
                node.func.id if isinstance(node.func, ast.Name) else "")
            if any(s in name for s in SEND):
                sends += 1
                literals = [
                    sub for sub in ast.walk(node)
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
                ]
                func = owner.get(id(node))
                if func is not None:
                    for arg in [*node.args, *(k.value for k in node.keywords)]:
                        if isinstance(arg, ast.Name):
                            for value in assigned.get((id(func), arg.id), []):
                                literals += _segments(value)
                for sub in literals:
                    value = sub.value
                    if value in docs or value in ALLOWED:
                        continue
                    if PROSE.search(value):
                        messages.append(f"{path.name}:{sub.lineno}: {value[:70]!r}")

        if isinstance(node, ast.Dict):
            for key, value_node in zip(node.keys, node.values):
                if not (isinstance(key, ast.Constant) and key.value == "text"):
                    continue
                seen_buttons += 1
                for sub in _segments(value_node):
                    if sub.value not in ALLOWED and LETTER.search(sub.value):
                        buttons.append(f"{path.name}:{sub.lineno}: {sub.value[:70]!r}")

    return messages, buttons, sends, seen_buttons


@pytest.fixture(scope="module")
def scanned() -> dict[str, object]:
    messages: list[str] = []
    buttons: list[str] = []
    sends = seen_buttons = live = 0
    for path in scope():
        m, b, s, k = _scan(path)
        messages += m
        buttons += b
        sends += s
        seen_buttons += k
        if s or k:
            live += 1
    return {
        "messages": messages, "buttons": buttons,
        "sends": sends, "seen_buttons": seen_buttons, "live": live,
    }


def test_no_english_message_reaches_the_operator(scanned) -> None:
    offenders = scanned["messages"]
    assert not offenders, (
        "these strings are SENT to the operator as English literals — route them "
        "through the module's locale resolver:\n  " + "\n  ".join(offenders)
    )


def test_no_english_button_reaches_the_operator(scanned) -> None:
    """The half a send-call scan cannot see."""
    offenders = scanned["buttons"]
    assert not offenders, (
        "these BUTTON labels are English literals — route them through the module's "
        "locale resolver:\n  " + "\n  ".join(offenders)
    )


def test_the_scan_actually_read_the_modules(scanned) -> None:
    """Anti-vacuity, counting a PRESENCE rather than an absence.

    Both assertions above pass trivially over an empty walk, and a renamed send
    helper, a moved keyboard or a discovery that returns nothing all produce exactly
    that. Floors are set below the measured values (11 live modules, 84 send calls,
    47 buttons) with room for ordinary churn, not at them.
    """
    files = scope()
    assert len(files) >= 20, f"discovery found {len(files)} modules — is the path right?"
    assert scanned["live"] >= 8, f"only {scanned['live']} modules send or build buttons"
    assert scanned["sends"] >= 50, f"only {scanned['sends']} send calls seen"
    assert scanned["seen_buttons"] >= 30, f"only {scanned['seen_buttons']} buttons seen"


def test_the_rules_would_catch_what_they_are_written_for(tmp_path: Path) -> None:
    """Teeth, on a fixture rather than by mutating a real module.

    Asserts BOTH rules fire and — the part that matters — that the symbol-only
    button and the emoji-only one do NOT, which is the distinction that took the
    button rule from 2 false findings to 0.
    """
    sample = tmp_path / "sample_actions.py"
    sample.write_text(
        "async def go(channel, chat_id):\n"
        "    await channel.send_message(chat_id, 'Could not read that video.')\n"
        "    await _edit(channel, 1, 2, 'Could not save that.', None)\n"
        "    text = 'Hello there'\n"
        "    await channel.send_message(chat_id, text)\n"
        "    kb = [\n"
        "        {'text': 'Download', 'callback_data': 'x'},\n"
        "        {'text': '✕', 'callback_data': 'y'},\n"
        "        {'text': '\U0001F3A7', 'callback_data': 'z'},\n"
        "        {'text': f'{MARK_OFF} Switch off', 'callback_data': 'w'},\n"
        "        {'text': f\"\U0001F4CB {_t('pim.rv.tasks') or 'Tasks'}\", 'callback_data': 'v'},\n"
        "    ]\n"
        "    return kb\n",
        encoding="utf-8",
    )
    messages, buttons, sends, seen = _scan(sample)
    assert len(messages) == 3, f"the message rule missed one: {messages}"
    assert any("Could not save" in m for m in messages), "a module-local _edit was not read"
    assert any("Hello there" in m for m in messages), "a variable passed to a send was not traced"
    assert len(buttons) == 2, f"expected only the two worded buttons, got: {buttons}"
    assert "Download" in buttons[0] and "Switch off" in buttons[1]
    assert sends == 3 and seen == 5, "the fixture did not exercise every shape"


def test_the_command_surface_is_out_of_scope_and_says_why() -> None:
    """⚠ A tripwire on the exemption, because an exemption is a claim.

    `gateway/channels/telegram_*` — `telegram_commands.py` (10.7k lines) above all —
    is NOT covered. That is a measurement, not an oversight: the same two rules
    score **539 findings across 33 modules** there, and this repo has twice measured
    a code at ~310 findings and rejected it as ungateable, because a baseline that
    large stops being a record and becomes a place to hide things.

    Those 539 are real English, not artifacts — the `/help`, `/apps` and `/start`
    surfaces are simply not localized yet. So this asserts the exemption is still
    TRUE rather than trusting the sentence above: if the count falls far enough that
    the surface has actually been localized, this fails and tells you to widen
    `scope()`. It can only fire on an improvement.
    """
    channels = Path(telegram_extensions.__file__).parent
    total = 0
    scanned_files = 0
    for path in sorted(channels.glob("telegram*.py")):
        if path.name == "telegram_extensions.py":
            continue  # in scope above
        messages, buttons, _sends, _seen = _scan(path)
        total += len(messages) + len(buttons)
        scanned_files += 1

    assert scanned_files >= 15, f"only scanned {scanned_files} channel modules"
    assert total > 200, (
        f"the Telegram command surface now has only {total} English literals "
        f"(was 539 when this guard was written). It may be localized enough to "
        f"gate — widen scope() to include gateway/channels/telegram_*.py and "
        f"delete this test."
    )
