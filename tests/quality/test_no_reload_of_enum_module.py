"""No test may `importlib.reload` a navig module that defines an Enum.

`importlib.reload` rebinds a module's globals **in place**: every class defined
there is replaced by a fresh object, while every name already imported elsewhere
still holds the original. For a plain class that is survivable — a stale
`AuditLog` still instantiates and behaves. For an **Enum** it is not: members are
compared by identity, so a pre-reload member stops equalling its post-reload twin
and the failure prints as nonsense::

    assert <CommandState.FAILED: 'failed'> == <CommandState.FAILED: 'failed'>

`RemoteResult.success` — ``state == CommandState.COMPLETED and return_code == 0``
— went **False for a completed, zero-exit result**, because the property resolves
`CommandState` out of the reloaded globals while the caller holds an old member.
Nine sibling tests went red whenever the relevance-ranked gate ordered the
reloading file first; alphabetically it sorted second, so it read as flake (#829).

**This guard is deliberately narrow, and the narrowness is measured.** All twelve
reload sites in the suite were audited and every risky pair run in hostile order
(reloading file first):

    navig.remote            -> 19 siblings, 142 tests   PASS
    navig.gateway.audit_log
      + billing_emitter     ->  9 siblings, 202 tests   PASS
    navig.cli.selector      ->  2 siblings,  44 tests   PASS
    navig.agent.remote_agent                            9 FAILED  <- the only one

The discriminator is exact: `remote_agent` was the **only** reloaded module that
defines an Enum. Banning reload outright would churn eleven legitimate sites that
re-read an env var at import time and demonstrably harm nothing; banning the Enum
case closes the bug class at its real cause.

The fix when this trips: extract the value the test wants into a plain function
the module itself calls, and pass it a value — see
`navig.agent.remote_agent.resolve_command_timeout`.
"""
from __future__ import annotations

import ast
import importlib
import re
from enum import Enum
from pathlib import Path

CORE_TESTS = Path(__file__).resolve().parents[1]

_RELOAD_RE = re.compile(r"importlib\.reload\(\s*([A-Za-z_][\w\.]*)\s*\)")


def _resolve_alias(tree: ast.AST, alias: str) -> str | None:
    """Map the local name inside `importlib.reload(...)` back to a dotted module."""
    root = alias.split(".")[0]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if (a.asname or a.name.split(".")[0]) == root:
                    # `import navig.x.y as mod`  ->  navig.x.y
                    # `import navig` + reload(navig.x.y)  ->  the full dotted text
                    return a.name if a.asname else alias
        elif isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                if (a.asname or a.name) == root:
                    return f"{node.module}.{a.name}"

    # `mod = importlib.import_module("navig.x.y")`
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not (isinstance(target, ast.Name) and target.id == root):
                continue
            call = node.value
            if isinstance(call, ast.Call):
                fname = getattr(call.func, "attr", getattr(call.func, "id", ""))
                if fname == "import_module" and call.args:
                    first = call.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        return first.value
    return None


def _defines_an_enum(module_name: str) -> tuple[bool, str | None]:
    """(module defines its own Enum, name) — or (False, reason) if unimportable."""
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001
        return False, f"UNIMPORTABLE: {type(exc).__name__}: {exc}"

    for name in dir(module):
        if name.startswith("_"):
            continue
        value = getattr(module, name, None)
        if (
            isinstance(value, type)
            and getattr(value, "__module__", "") == module_name
            and issubclass(value, Enum)
        ):
            return True, name
    return False, None


def _reload_targets() -> list[tuple[str, int, str]]:
    """(test file, line, dotted module) for every reload of a navig module."""
    found: list[tuple[str, int, str]] = []
    for path in sorted(CORE_TESTS.rglob("test_*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "importlib.reload(" not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue

        rel = path.relative_to(CORE_TESTS.parent).as_posix()
        for lineno, line in enumerate(text.splitlines(), start=1):
            for alias in _RELOAD_RE.findall(line):
                module_name = _resolve_alias(tree, alias)
                if module_name and module_name.split(".")[0] == "navig":
                    found.append((rel, lineno, module_name))
    return found


def test_no_test_reloads_a_module_that_defines_an_enum() -> None:
    offenders: list[str] = []
    unverifiable: list[str] = []

    for rel, lineno, module_name in _reload_targets():
        defines_enum, detail = _defines_an_enum(module_name)
        if defines_enum:
            offenders.append(f"{rel}:{lineno}: reloads {module_name} (defines Enum {detail})")
        elif detail:
            # Could-not-check is not a pass: say so rather than going quietly green.
            unverifiable.append(f"{rel}:{lineno}: {module_name} — {detail}")

    assert not offenders, (
        "`importlib.reload` replaces every class the module defines while callers keep the "
        "originals. For an Enum that breaks identity comparison, and the failure surfaces in "
        "an UNRELATED test file as `assert <X.A: 'a'> == <X.A: 'a'>` — order-dependently, so "
        "it reads as flake. Extract what the test needs into a plain function the module "
        "itself calls and pass it a value (see "
        "navig.agent.remote_agent.resolve_command_timeout):\n"
        + "\n".join(f"  {o}" for o in offenders)
    )

    assert not unverifiable, (
        "these reloaded modules could not be imported, so this guard could not check them — "
        "an unknown is not a pass:\n" + "\n".join(f"  {u}" for u in unverifiable)
    )


def test_the_scan_still_finds_the_reload_sites_it_is_meant_to_police() -> None:
    """Anti-vacuity. The suite legitimately reloads several Enum-free modules; if
    the resolver ever stops recognising them this guard would pass having checked
    nothing. It does NOT assert an exact count — that would break on every
    unrelated test edit."""
    targets = _reload_targets()

    assert targets, (
        "no `importlib.reload(<navig module>)` found anywhere — either the suite stopped "
        "reloading entirely (then delete this guard deliberately) or the alias resolver broke"
    )
    assert len({m for _, _, m in targets}) >= 3, (
        f"only {len({m for _, _, m in targets})} distinct module(s) resolved; the resolver "
        "handles `import x.y as m`, `from x import y` and `m = import_module('x.y')` — a "
        "regression there would silently shrink this guard's scope"
    )
