"""Every function an interactive menu calls must actually exist.

`navig hestia`, `navig assistant`, `navig server`, `navig flow` with no subcommand each open a
menu in `navig.commands.interactive`, which dispatches by calling `<module>.<func>(...)` on a
sibling command module. Nothing checks those names: a typo or a rename on the other side is
invisible until a user picks that option, and then the menu's `except Exception` reports it as

    HestiaCP operation failed: module 'navig.commands.hestia' has no attribute 'list_users'

— which blames the operation for what is a missing attribute. Four had drifted (`list_users` vs
`list_users_cmd`, `list_domains` vs `list_domains_cmd`, `reload_webserver` vs `reload_server`,
`info_template_cmd` vs `show_template_cmd`) and were fixed with this test. Ten more named
functions that existed in no form — menu options for features never built — and were removed,
because implementing them would have meant inventing the features.

This is the repo's recurring "calls to methods that never existed" class (#732, #706). ruff's
F821 cannot see it — the *module* is defined, only the attribute is missing — and mypy is
configured but never run (763 attr-defined findings, mostly inference noise on untyped code).
So the class is pinned here instead, the same way `test_no_hardcoded_home.py` pins its own:
KNOWN_MISSING is a debt list, not a rubber stamp — a NEW dead reference fails, and fixing one
without deleting its entry also fails.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

import navig

pytestmark = pytest.mark.unit

_INTERACTIVE = Path(navig.__file__).resolve().parent / "commands" / "interactive.py"

# Menu options whose backing function does not exist ANYWHERE in the target module — not a
# rename, no near-match: the option was wired up before (or instead of) the implementation.
# Picking it today prints "<area> operation failed: module ... has no attribute ...".
# Each of these is a live user-visible dead end; removing the option or building the function
# is an owner call, so they are recorded rather than silently deleted.
KNOWN_MISSING: dict[str, str] = {
    # Empty on purpose. The ten that lived here were menu options wired up before (or instead
    # of) an implementation — "Restart Webserver", "View Access/Error Logs", HestiaCP
    # show-user / show-domain / system-info, "Run template", and assistant
    # insights / recommendations / apply. None had ever worked; each printed
    # "<area> operation failed: module ... has no attribute ...", which reads as the operation
    # failing rather than the option not existing. Building them would have meant inventing
    # features (a remote webserver restart, HestiaCP verbs, an "insights" concept assistant.py
    # has no notion of), so the options were removed instead.
    #
    # Keep this dict rather than deleting it: a genuinely blocked reference has an obvious
    # place to be recorded WITH its reason, and test_known_missing_has_no_stale_entries makes
    # an entry that starts resolving fail, so it cannot rot into a rubber stamp.
}


def _menu_calls() -> list[tuple[int, str, str, str]]:
    """(lineno, alias, attr, module_path) for every `<alias>.<attr>(...)` where *alias* came
    from `from navig.commands import <alias>` — top-level or inside a function."""
    tree = ast.parse(_INTERACTIVE.read_text(encoding="utf-8"))

    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "navig.commands":
            for a in node.names:
                aliases[a.asname or a.name] = f"navig.commands.{a.name}"

    out: list[tuple[int, str, str, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        owner = node.func.value
        if isinstance(owner, ast.Name) and owner.id in aliases:
            out.append((node.lineno, owner.id, node.func.attr, aliases[owner.id]))
    return out


def _unresolved() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for lineno, alias, attr, module_path in _menu_calls():
        module = importlib.import_module(module_path)
        if not hasattr(module, attr):
            found.setdefault(f"{alias}.{attr}", []).append(lineno)
    return found


def test_the_scan_actually_sees_the_menu_calls() -> None:
    """Anti-vacuity: an import-alias change that made this collect nothing would turn the
    whole guard green over a broken menu."""
    # 14 after the ten dead options were removed. The floor is a tripwire for the scan
    # silently matching nothing (an import-alias change, a refactor to a dispatch dict) —
    # not a target, so it sits just under the real count rather than at it.
    calls = _menu_calls()
    assert len(calls) >= 12, f"only {len(calls)} menu dispatch calls found — the scan broke"
    assert {"hestia", "webserver", "template", "assistant"} <= {a for _, a, _, _ in calls}


def test_no_new_menu_option_calls_a_function_that_does_not_exist() -> None:
    unresolved = _unresolved()
    unexpected = {name: lines for name, lines in unresolved.items() if name not in KNOWN_MISSING}
    assert not unexpected, (
        "an interactive menu dispatches to a function that does not exist — the user gets "
        "'<area> operation failed: module ... has no attribute ...', which reads like the "
        "operation failed rather than the code being wrong:\n  "
        + "\n  ".join(f"{n}: line(s) {ls}" for n, ls in sorted(unexpected.items()))
    )


def test_known_missing_has_no_stale_entries() -> None:
    """Built one of them, or removed the menu option? Delete its entry, so the debt list
    cannot quietly become a place where fixed things keep claiming to be broken."""
    stale = sorted(set(KNOWN_MISSING) - set(_unresolved()))
    assert not stale, (
        "these menu targets now resolve (or are no longer called) — remove them from "
        f"KNOWN_MISSING: {stale}"
    )


def test_the_four_repaired_targets_resolve() -> None:
    """The renames this test was written with. Pinned by name so a future rename on the other
    side breaks here instead of in front of a user."""
    from navig.commands import hestia, template, webserver

    assert hasattr(webserver, "reload_server")
    assert hasattr(hestia, "list_users_cmd")
    assert hasattr(hestia, "list_domains_cmd")
    assert hasattr(template, "show_template_cmd")


# ── an option with no branch is worse than one that errors ───────────────────


def _menu_options_without_a_branch() -> list[tuple[str, list[str]]]:
    """Menu labels declared in an `options = [...]` list that no `selection == "..."`
    comparison in the same function ever matches."""
    tree = ast.parse(_INTERACTIVE.read_text(encoding="utf-8"))
    problems: list[tuple[str, list[str]]] = []

    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        labels: set[str] = set()
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "options" for t in node.targets)
                and isinstance(node.value, ast.List)
            ):
                for elt in node.value.elts:
                    if isinstance(elt, ast.Tuple) and len(elt.elts) == 2:
                        label = elt.elts[1]
                        if isinstance(label, ast.Constant) and isinstance(label.value, str):
                            labels.add(label.value)
        if not labels:
            continue
        # ONLY the strings a `selection == "..."` comparison tests. Collecting every string
        # constant in the function instead makes this vacuous, because each label also appears
        # in the `options` list a few lines above — so every label would look handled and the
        # check would pass over a menu with no branches at all. (It did; caught by removing a
        # branch and watching the test stay green.)
        compared: set[str] = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Compare):
                for operand in [node.left, *node.comparators]:
                    if isinstance(operand, ast.Constant) and isinstance(operand.value, str):
                        compared.add(operand.value)
        orphaned = sorted(lbl for lbl in labels if lbl not in compared and lbl != "Back")
        if orphaned:
            problems.append((fn.name, orphaned))
    return problems


def test_every_menu_option_has_a_branch_that_handles_it() -> None:
    """Removing a dead option means removing its choice AND its branch. Drop only the branch
    and the option still renders, does nothing, and returns to the menu — a silent no-op, which
    is worse than the error it replaced because nothing tells the user it did not run."""
    problems = _menu_options_without_a_branch()
    assert not problems, "menu options that render but are never handled:\n  " + "\n  ".join(
        f"{fn}: {labels}" for fn, labels in problems
    )


def test_the_option_scan_sees_the_menus() -> None:
    """Anti-vacuity: if the `options = [...]` shape changes, the check above must not quietly
    start passing over zero menus."""
    tree = ast.parse(_INTERACTIVE.read_text(encoding="utf-8"))
    option_lists = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "options" for t in node.targets)
        and isinstance(node.value, ast.List)
    ]
    assert len(option_lists) >= 4, f"only {len(option_lists)} menu option lists found"
