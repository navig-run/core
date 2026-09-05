"""Every start-menu button must name a command the CLI actually has.

`ACTION_COMMANDS` is a table of strings, so nothing that watches this repo could see it
rot: ruff sees valid string literals, and `scripts/check_module_attrs.py` — the guard
built for exactly this class after 14 dead interactive-menu targets — only resolves
ATTRIBUTE access, not command text. When measured, 14 of the 17 `type: navig` entries
were unrunnable: five named a verb that has never existed (`host info`, `host inspect`,
`host discover`, `tunnel list`, `tunnel status`) and nine passed `--plain` to a command
that rejects it, including `host show --plain`, which exits 2.

The flag half matters as much as the verb half and is the easier one to miss: `host show`
resolves perfectly, and the entry was still broken. So this resolves the full argv —
command path AND flags — through the real Click tree rather than checking a name exists.
"""

from __future__ import annotations

import click
import pytest
from typer.main import get_command


@pytest.fixture(scope="module")
def root():
    from navig.cli import app
    from navig.cli.registration import _register_external_commands

    _register_external_commands(register_all=True, target_app=app)
    return get_command(app)


def _entries():
    from navig.bot.start_menu import ACTION_COMMANDS

    return [
        (key, spec["cmd"])
        for key, spec in ACTION_COMMANDS.items()
        if spec.get("type") == "navig" and spec.get("cmd")
    ]


def _resolve(root, words: list[str]):
    """Walk the real Click tree; returns (command, unresolved_tail)."""
    cur = root
    for i, word in enumerate(words):
        if not isinstance(cur, click.Group):
            return None, words[i:]
        nxt = cur.get_command(click.Context(cur), word)
        if nxt is None:
            return None, words[i:]
        cur = nxt
    return cur, []


def test_the_table_is_not_empty():
    """A resolver that silently finds nothing looks exactly like a clean run."""
    assert len(_entries()) >= 15


@pytest.mark.parametrize("key,cmd", _entries())
def test_menu_command_resolves(root, key: str, cmd: str) -> None:
    words = [t for t in cmd.split() if not t.startswith("-")]
    flags = [t for t in cmd.split() if t.startswith("-")]

    command, tail = _resolve(root, words)
    assert command is not None, (
        f"menu button {key!r} runs `navig {cmd}`, but `{tail[0]}` is not a command there"
    )

    accepted: set[str] = set()
    for param in command.get_params(click.Context(command)):
        accepted.update(getattr(param, "opts", []) or [])
    rejected = [f for f in flags if f not in accepted]
    assert not rejected, (
        f"menu button {key!r} runs `navig {cmd}`, but that command rejects {rejected} "
        f"— it would exit 2 (accepted: {sorted(accepted)})"
    )
