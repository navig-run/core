"""``console`` from ``navig.console_helper`` is the Console itself — never ``console.console``.

``console_helper`` exports a ``_LazyConsole`` object. It has ``.print``; it does NOT have a
``.console`` attribute, and nothing in the tree imports the MODULE under the name ``console``. So
every ``console.console.print(...)`` raises ``AttributeError: 'Console' object has no attribute
'console'`` the moment that line executes.

That was not one typo: ``navig/commands/monitoring.py`` carried **60** of them and
``adapters/automation/evolution/evolver.py`` one more. `monitoring` has no tests, so its output
paths had never run; the evolver's single occurrence sat on the ``--dry-run`` branch — the first
thing a cautious user tries — and was the only reason it surfaced at all (one red test).

This guard is cheap and total: no ``console.console`` anywhere under ``core/navig``.
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "navig"
_SKIP_PARTS = {"__pycache__", "scaffold-templates", "templates", "builtin"}


def _sources() -> list[Path]:
    return [p for p in sorted(CORE.rglob("*.py")) if not (_SKIP_PARTS & set(p.parts))]


def _console_console_attrs(tree: ast.AST) -> list[int]:
    """Line numbers of every ``console.console`` attribute access."""
    hits: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "console"
            and isinstance(node.value, ast.Name)
            and node.value.id == "console"
        ):
            hits.append(node.lineno)
    return hits


def test_no_double_console_attribute_access() -> None:
    findings: list[str] = []
    for path in _sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover — a template that isn't valid python
            continue
        rel = path.relative_to(CORE.parent).as_posix()
        findings += [f"{rel}:{line}: console.console — use console.print(...)"
                     for line in _console_console_attrs(tree)]

    assert not findings, (
        "`console` IS the Console (console_helper exports the object, and nothing imports the "
        "module under that name). These lines raise AttributeError when they run:\n"
        + "\n".join(f"  {f}" for f in findings)
    )


def test_console_object_has_print_but_not_console() -> None:
    """Pin the premise, so this guard can never be silently invalidated."""
    from navig.console_helper import console

    assert hasattr(console, "print")
    assert not hasattr(console, "console")


def test_guard_detects_a_planted_violation() -> None:
    """Teeth."""
    tree = ast.parse("from navig.console_helper import console\nconsole.console.print('x')\n")
    assert _console_console_attrs(tree) == [2]
