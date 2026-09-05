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
    """The names this module binds the console_helper MODULE to (`ch`, …).

    Import-scoped on purpose: `ch` is also a perfectly ordinary loop variable
    (`for ch in text: ch.isdigit()`), and flagging those would make the guard
    noise instead of signal.

    Only the module itself counts. A name imported *out* of console_helper
    (`from navig.console_helper import console, Table`) is a MEMBER, not the
    module, and its attributes have nothing to do with the module's. Binding
    those as aliases is what made this guard flag ~60 correct
    `console.print(...)` calls: `console` is a `_LazyConsole`, `.print` is its
    real method, and the documented house style is literally
    `ch.console.print(table)` — but the guard resolved it as `ch.print`, the
    one name it was built to reject. A member with a bad name cannot hide
    anyway: `from navig.console_helper import nope` is an ImportError at
    import time, not a lurking AttributeError.
    """
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            # `from navig import console_helper as ch`
            # `from navig.x.y import console_helper`
            for a in node.names:
                if a.name == "console_helper":
                    aliases.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            # `import navig.console_helper as ch`  /  `import navig.console_helper`
            for a in node.names:
                if a.name.endswith("console_helper"):
                    aliases.add(a.asname or a.name.split(".")[-1])
    return {a for a in aliases if a}


def _scanned_roots() -> list[Path]:
    roots = [CORE]
    if PLUGINS.is_dir():
        roots += sorted(p for p in PLUGINS.glob("navig-*") if p.is_dir())
    return roots


def test_the_plugin_tree_is_actually_in_scope() -> None:
    """A scope that silently resolves to nothing passes every assertion.

    This guard now runs for PLUGIN changes too, and running a vacuous check more often
    protects nothing. `PLUGINS` is `CORE.parents[1] / "plugins"` — one layout change, one
    move of this file, and the glob matches zero directories while every test here stays
    green. The sibling `test_sqlite_busy_timeout.py` already pins its scope this way.
    """
    roots = [r.name for r in _scanned_roots()]
    assert any(r.startswith("navig-") for r in roots), (
        f"no plugin package is in scope, so this guard is only checking core: {roots}"
    )


def _python_files() -> list[Path]:
    roots = _scanned_roots()
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


def test_alias_detection_binds_the_module_but_not_its_members():
    """The guard resolves `X.attr()` against the console_helper MODULE, so it
    must only treat X as an alias when X really IS the module.

    It once bound every name imported *out* of console_helper too, which turned
    the documented house style (`from navig.console_helper import console` then
    `console.print(table)`) into ~60 false "function does not exist" hits — it
    was resolving them as `ch.print`, the exact name the guard rejects.
    """
    module_forms = ast.parse(
        "import navig.console_helper as ch\n"
        "import navig.console_helper\n"
        "from navig import console_helper\n"
        "from navig import console_helper as ch2\n"
    )
    assert _console_helper_aliases(module_forms) == {
        "ch", "ch2", "console_helper",
    }

    member_forms = ast.parse(
        "from navig.console_helper import console, Table, _safe_symbol\n"
    )
    assert _console_helper_aliases(member_forms) == set(), (
        "members imported out of console_helper are not the module — binding "
        "them makes the guard check `console.print` against the module"
    )


def test_the_guard_still_catches_a_real_missing_helper():
    """Anti-vacuity: narrowing alias detection must not blind the guard to the
    crash it exists for (`ch.print(...)` — the 86-site bug)."""
    tree = ast.parse("import navig.console_helper as ch\nch.print('x')\nch.warning('y')\n")
    aliases = _console_helper_aliases(tree)
    hits = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in aliases
        and not _exists(node.func.attr)
    ]
    assert hits == ["print"], f"expected the bogus ch.print() to be caught, got {hits}"


def test_the_helpers_the_sweep_relied_on_are_really_there():
    """Guard the guard: if these get renamed, the fixes above silently rot."""
    for name in ("warning", "confirm_action", "console", "kv", "create_table", "raw_print"):
        assert _exists(name), f"console_helper.{name} disappeared — the call sites now crash"


def test_create_table_takes_column_dicts_not_strings():
    """`navig mcp search` crashed on exactly this: columns=["Name", …] blows up
    inside create_table with "'str' object has no attribute 'get'"."""
    table = ch.create_table("t", [{"name": "A"}, {"name": "B", "style": "cyan"}])
    assert len(table.columns) == 2
