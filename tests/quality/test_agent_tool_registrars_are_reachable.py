"""An agent tool registrar that nothing calls registers nothing.

`manage_skills` shipped with a full JSON schema, a working handler, ~200 lines of tests
and **three** independent reasons it could never be called: its registrar invoked a
registry method that has never existed, the registrar had no production caller, and
`register_all_tools()` did not list it. It was absent from every live agent for as long
as it existed.

The neighbouring guard could not see it. `test_toolset_registry_parity` asks *"does every
tool NAMED IN A TOOLSET actually register?"* — and `manage_skills` was named in no
toolset, so it was invisible from that direction. A guard scoped to one path does not
cover the surface; this one asks the other question:

    is every registrar REACHED?

Detection is by **shape, not by name**: a registrar is a function that actually calls
``_AGENT_REGISTRY.register(...)``. A name-prefix rule (`register_*`) flags
``browser_session.register_desktop_endpoint``, which registers a CDP endpoint and has
real callers — a false positive that would push the next person to add an exemption for
working code, which is how an exempt list stops meaning anything.
"""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[2] / "navig" / "agent" / "tools"

#: Registrars deliberately not wired, each with a written reason and what wiring costs.
#: An entry is a promise that the orphan is KNOWN, not an oversight. Both of these were
#: found BY this guard.
KNOWN_UNWIRED = {
    "register_plan_tools": (
        "plan_tools.py — registers plan_add_step/plan_show/plan_approve against the "
        "correct registry API, but requires a live plan `interceptor` and nothing in the "
        "tree constructs one; its tools are also gated on `interceptor.is_active`, so "
        "with no interceptor they would register and never be available. Its sibling "
        "`register_plan_context_tool` IS wired, so the module is half-live. Unlike the "
        "todo tools — whose state is simply per-conversation — this one needs a plan "
        "*execution* to attach to, which is a feature, not a wiring line."
    ),
}


@lru_cache(maxsize=1)
def _registrars() -> dict[str, str]:
    """{function name: file name} for every function that registers an agent tool."""
    found: dict[str, str] = {}
    for path in sorted(TOOLS_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            for call in ast.walk(node):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr in {"register", "register_entry"}
                    and isinstance(call.func.value, ast.Name)
                    and "REGISTRY" in call.func.value.id.upper()
                ):
                    found[node.name] = path.name
                    break
    return found


@lru_cache(maxsize=1)
def _aggregator_names() -> frozenset[str]:
    """Every name `__init__.py` actually refers to — imported, called, or attribute-accessed.

    On the AST, not the text. A substring check is not safe here and this file proves it:
    `plan_tools` defines both ``register_plan_context_tool`` and ``register_plan_tools``,
    while the aggregator defines its own wrapper ``register_plan_context_tools`` — so
    ``"register_plan_context_tool" in source`` is True because it is a PREFIX of the
    plural, whether or not the singular is ever referenced. It happens to be referenced;
    the check would have said so either way.
    """
    tree = ast.parse((TOOLS_DIR / "__init__.py").read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.name)
                if alias.asname:
                    names.add(alias.asname)
    return frozenset(names)


def test_the_scan_actually_found_registrars():
    """A floor. A detector that silently matches nothing looks exactly like a clean run."""
    found = _registrars()
    assert len(found) >= 5, (
        f"only {len(found)} registrars detected under {TOOLS_DIR} — the shape rule has "
        "stopped matching (did _AGENT_REGISTRY get renamed?), so every assertion below "
        "is vacuous"
    )
    assert "register_skill_tools" in found, (
        "the registrar this guard was written for is not being detected"
    )


@pytest.mark.parametrize("name", sorted(_registrars()))
def test_every_registrar_is_reached_by_the_aggregator(name: str):
    if name in KNOWN_UNWIRED:
        pytest.skip(f"documented orphan: {KNOWN_UNWIRED[name]}")

    assert name in _aggregator_names(), (
        f"{_registrars()[name]}::{name}() registers agent tools and "
        f"navig/agent/tools/__init__.py never mentions it — so register_all_tools() "
        f"cannot call it and those tools reach no agent. Either wire it into the group "
        f"list, or add it to KNOWN_UNWIRED in this file with a written reason."
    )


def test_documented_orphans_still_exist():
    """An exemption for something that is gone hides the next real one behind noise."""
    found = _registrars()
    stale = sorted(set(KNOWN_UNWIRED) - set(found))
    assert not stale, (
        f"KNOWN_UNWIRED names registrars that no longer exist: {stale}. Remove them — an "
        "exempt list that is not maintained stops being read."
    )


def test_a_documented_orphan_that_gets_wired_must_leave_the_list():
    """The list is for known gaps, not a place a fixed thing quietly stays."""
    names = _aggregator_names()
    wired = sorted(n for n in KNOWN_UNWIRED if n in names)
    assert not wired, (
        f"{wired} is wired into register_all_tools() but still listed as a known orphan. "
        "Delete its KNOWN_UNWIRED entry so the exemption cannot outlive the problem."
    )
