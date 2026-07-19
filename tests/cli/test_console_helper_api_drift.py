"""Every `ch.<something>()` we call must exist in console_helper.

console_helper defines a module-level ``__getattr__`` that RAISES on an unknown
name. So a call to a helper that does not exist is not a lint nit — it is an
AttributeError that fires the moment that line runs, taking the command down
with a crash report. And because it only fires on execution, it hides in any
code path that is rarely (or never) exercised.

That is exactly how it hid: `navig mcp search` and `navig mcp remove` crashed
for a long time (`ch.confirm()` does not exist — it is `ch.confirm_action()`)
because nothing could reach commands/mcp.py except the legacy interactive shell.
A sweep for the same shape found **128 call sites** across 27 files, every one a
crash waiting to happen:

    ch.warn(...)     -> ch.warning(...)          (38 sites, 19 files)
    ch.print(...)    -> ch.console.print(...)    (86 sites)
    ch.confirm(...)  -> ch.confirm_action(...)
    ch.kv(...)       -> did not exist at all; it is a real primitive, so it
                        was ADDED rather than rewritten away at each call site

This test is the cheap check that would have caught all of them.
"""

from __future__ import annotations

import ast
from pathlib import Path

import navig.console_helper as ch

CORE = Path(__file__).resolve().parents[2] / "navig"
PLUGINS = CORE.parents[1] / "plugins"


def _exists(name: str) -> bool:
    try:
        getattr(ch, name)
        return True
    except AttributeError:
        return False


def _console_helper_aliases(tree: ast.AST) -> set[str]:
    """The names this module binds console_helper to (`ch`, `console_helper`, …).

    Import-scoped on purpose: `ch` is also a perfectly ordinary loop variable
    (`for ch in text: ch.isdigit()`), and flagging those would make the guard
    noise instead of signal.
    """
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if "console_helper" in node.module or node.module == "navig":
                for a in node.names:
                    if a.name == "console_helper" or "console_helper" in node.module:
                        aliases.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.endswith("console_helper"):
                    aliases.add(a.asname or a.name.split(".")[-1])
    return {a for a in aliases if a}


def _python_files() -> list[Path]:
    roots = [CORE]
    if PLUGINS.is_dir():
        roots += sorted(p for p in PLUGINS.glob("navig-*") if p.is_dir())
    files: list[Path] = []
    for root in roots:
        for f in root.rglob("*.py"):
            if {"build", "dist", "tests", "test", "scaffold-templates"} & set(f.parts):
                continue
            files.append(f)
    return files


def test_no_calls_to_console_helper_functions_that_do_not_exist():
    offenders: list[str] = []
    unparseable: list[str] = []
    scanned_with_aliases = 0
    for path in _python_files():
        try:
            # utf-8-sig, not utf-8: a BOM is a SyntaxError to ast.parse, and twelve files
            # in this repo carry one. Python's own tokenizer strips it, so such a file
            # runs fine — it just becomes invisible to every AST guard.
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except SyntaxError as exc:
            # NEVER `continue` here. A guard that silently skips what it cannot read
            # reports "clean" for a file it never looked at — which is precisely the
            # failure mode this whole guard exists to prevent.
            unparseable.append(f"{path.name}: {exc}")
            continue
        aliases = _console_helper_aliases(tree)
        if not aliases:
            continue
        scanned_with_aliases += 1
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            recv = node.func.value
            if isinstance(recv, ast.Name) and recv.id in aliases and not _exists(node.func.attr):
                offenders.append(f"{path.name}:{node.lineno}  {recv.id}.{node.func.attr}()")

    # An empty scan is green, and green is exactly what a broken guard looks like. If a
    # refactor moves the source tree or breaks alias detection, say so — do not pass.
    assert scanned_with_aliases > 50, (
        f"only {scanned_with_aliases} files were found to import console_helper — this "
        "guard scans the whole of core + the plugins, where hundreds do. It is looking in "
        "the wrong place (or _console_helper_aliases stopped matching), so it is checking "
        "nothing and reporting success."
    )
    assert not unparseable, (
        "these files could not be parsed, so this guard did NOT check them — a silent "
        "hole exactly where a crash could hide:\n  " + "\n  ".join(sorted(unparseable))
    )
    assert not offenders, (
        "called a console_helper function that DOES NOT EXIST — console_helper's "
        "__getattr__ raises, so this is an AttributeError crash the moment the "
        "line runs.\n  " + "\n  ".join(sorted(set(offenders)))
    )


def test_the_helpers_the_sweep_relied_on_are_really_there():
    """Guard the guard: if these get renamed, the fixes above silently rot."""
    for name in ("warning", "confirm_action", "console", "kv", "create_table", "raw_print"):
        assert _exists(name), f"console_helper.{name} disappeared — the call sites now crash"


def test_create_table_takes_column_dicts_not_strings():
    """`navig mcp search` crashed on exactly this: columns=["Name", …] blows up
    inside create_table with "'str' object has no attribute 'get'"."""
    table = ch.create_table("t", [{"name": "A"}, {"name": "B", "style": "cyan"}])
    assert len(table.columns) == 2
