"""Regression: every key in the dashboard's Keyboard Shortcuts notice survives markup.

`notify()` renders its message as Rich markup, so a bare `[e]` is consumed as a style
tag and the edit shortcut vanished from the help text. Each bracketed key must be
escaped (`\\[e]`).

The message is read from the source with `ast` — the TUI test conftest stubs textual,
and that stub cannot import the dashboard screen.
"""

from __future__ import annotations

import ast
from pathlib import Path

from rich.markup import render

import navig

_DASHBOARD = Path(navig.__path__[0]) / "tui" / "screens" / "dashboard.py"


def _help_message() -> str:
    tree = ast.parse(_DASHBOARD.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "action_show_help":
            for call in ast.walk(node):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "notify"
                ):
                    return ast.literal_eval(call.args[0])
    raise AssertionError("action_show_help notify() call not found")


def test_help_notice_shows_every_shortcut_key():
    plain = render(_help_message()).plain

    for key in ("[i]", "[s]", "[r]", "[q]", "[e]"):
        assert key in plain, f"{key} was eaten by markup: {plain!r}"
