"""A passive link handler that nothing calls is dead code that looks like a feature.

``navig/telegram/tiktok_actions.py`` shipped a complete TikTok card — metadata
plus ⬇️ Download / 🔍 Analyse buttons, permission-gated, with its ``tk:`` button
callbacks routed in ``telegram_keyboards`` — and the ONLY thing that ever called
it was the Telegram **Business** layer. In the owner's own chat with the bot the
link fell through to the chat model, which has no fetch tool for it and could
only answer "Can't open external links." Every part existed and worked; the one
missing line was the call.

``music_actions`` was explicitly written as a copy of that module's contract and
DID get its 1:1 hook, which is what makes this a wiring omission rather than a
design decision — two sibling handlers, one wired, one not, and nothing failing.

So: every passive link handler must be reachable from the channel that owns 1:1
messages. This checks the CALL EXISTS, by AST (a mention in a comment or
docstring must not satisfy it — prose has fooled a textual scan in this repo
before). It does not prove the surrounding branch is correct; it is a tripwire
against the specific, silent, already-shipped failure of writing a handler and
never calling it.
"""

from __future__ import annotations

import ast
from pathlib import Path

_CORE = Path(__file__).resolve().parents[2] / "navig"
_CHANNEL = _CORE / "gateway" / "channels" / "telegram.py"

#: handler module → the passive entry point the 1:1 channel must call.
_PASSIVE_LINK_HANDLERS: dict[str, str] = {
    "tiktok_actions": "offer_card_dm",
    "music_actions": "offer_links",
}


def _called_names(tree: ast.AST) -> set[str]:
    """Every attribute/plain name that appears in CALL position."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


def test_every_passive_link_handler_is_called_by_the_dm_channel():
    assert _CHANNEL.is_file(), f"channel module moved: {_CHANNEL}"
    called = _called_names(ast.parse(_CHANNEL.read_text(encoding="utf-8")))

    missing = [
        f"{module}.{entry}"
        for module, entry in _PASSIVE_LINK_HANDLERS.items()
        if entry not in called
    ]
    assert not missing, (
        "passive link handler(s) never called from "
        f"gateway/channels/telegram.py: {missing}. A handler nothing invokes is "
        "dead code that reads like a shipped feature — the link reaches the chat "
        "model instead, which cannot fetch it."
    )


def test_the_entry_points_actually_exist_on_their_modules():
    """Guards the other direction: the channel calling a name that was renamed
    away would fail at runtime, inside a try/except that logs at DEBUG."""
    for module, entry in _PASSIVE_LINK_HANDLERS.items():
        source = _CORE / "telegram" / f"{module}.py"
        assert source.is_file(), f"{module} moved — update this guard"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        defined = {
            node.name
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
        }
        assert entry in defined, (
            f"{module}.{entry} is called by the channel but not defined here "
            f"(found: {sorted(defined)})"
        )
