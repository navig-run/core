"""Modules documented as NOT WIRED must stay that way — or the documentation is a lie.

Three modules in core are fully implemented, heavily tested, and connected to nothing:
``core/model_routing.py`` (222 lines, 158 tests across four files),
``connectors/smart_linker.py``, and ``agent/delegate.py``. Each carries a "NOT WIRED"
warning in its docstring explaining the consequences and the decision still to be made.

A "not wired" note is only useful while it is true, and it goes stale in the dangerous
direction: a reader who believes a live path is dormant will reason about the system
incorrectly. Worse, the dormancy is *why* certain known-odd behaviour inside these
modules was deliberately left alone — `model_routing._coerce_bool` is blacklist-falsy,
`smart_linker.find_related` calls connectors without hydrating their vault token, and
`delegate._run_child` drops the `depth` it is handed so `MAX_AGENT_DEPTH` cannot hold.
Those are safe only while nothing calls them.

So the claim is asserted rather than merely written. If one of these gets wired, this
fails and names the file that did it; the fix is to remove the entry here, delete the
warning block from the module docstring, and revisit what the dormancy was excusing.

Registry-only reachability is deliberately NOT attempted in general. A repo-wide
"module with no non-test importer" scan flags 483 of 1286 modules under core/navig —
registry self-registration, CLI entry points and dynamic imports are invisible to an
AST scan. Precision here comes from naming the two modules explicitly.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_CORE_NAVIG = Path(__file__).resolve().parents[2] / "navig"

# module path (relative to core/navig) -> public names that would signal a live caller
_DORMANT: dict[str, set[str]] = {
    "core/model_routing.py": {
        "choose_cheap_model_route",
        "get_routing_config",
        "is_simple_turn",
    },
    "connectors/smart_linker.py": {"SmartLinker"},
    # Third of the same shape, and the one whose dormancy is load-bearing for a
    # SAFETY property rather than a quirk: `_run_child` accepts a `depth` argument
    # and never uses it, and `run_agentic` has no depth parameter to carry it, so a
    # child would register its own delegate_task at parent_depth=0 and
    # MAX_AGENT_DEPTH could not hold — delegation would recurse without bound, each
    # level holding a semaphore slot and spending tokens. Nothing calls
    # `register_delegate_tool`, and `register_all_tools` does not include it, so the
    # `delegation` toolset resolves to zero tools today (also pinned, from the other
    # direction, by tests/agent/test_toolset_registry_parity.py's KNOWN_EMPTY entry).
    "agent/delegate.py": {"DelegateTool", "register_delegate_tool", "AgentDepthError"},
}


def _module_stem(rel_path: str) -> str:
    """``core/model_routing.py`` -> ``model_routing`` (what an import mentions)."""
    return Path(rel_path).stem


def _references(rel_path: str, public: set[str]) -> list[str]:
    """Production files that import the module or any of its public names."""
    stem = _module_stem(rel_path)
    target = (_CORE_NAVIG / rel_path).resolve()
    found: list[str] = []

    for path in _CORE_NAVIG.rglob("*.py"):
        if path.resolve() == target or "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # a template / relic, not shipped code
            continue

        rel = path.relative_to(_CORE_NAVIG).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if stem in node.module.split("."):
                    found.append(f"{rel}: from {node.module} import …")
                else:
                    hit = public & {a.name for a in node.names}
                    if hit:
                        found.append(f"{rel}: imports {sorted(hit)}")
            elif isinstance(node, ast.Import):
                if any(stem in a.name.split(".") for a in node.names):
                    found.append(f"{rel}: import …{stem}")
    return found


@pytest.mark.parametrize("rel_path", sorted(_DORMANT))
def test_documented_dormant_module_is_still_unreferenced(rel_path: str):
    callers = _references(rel_path, _DORMANT[rel_path])
    assert not callers, (
        f"{rel_path} is documented as NOT WIRED but production code now references it:\n  "
        + "\n  ".join(callers)
        + f"\n\nIf that is intentional, drop it from _DORMANT here, remove the warning "
        f"block from the module docstring, and re-check what the dormancy was excusing "
        f"(see the module's own notes)."
    )


@pytest.mark.parametrize("rel_path", sorted(_DORMANT))
def test_the_module_still_exists_and_still_carries_the_warning(rel_path: str):
    """Guard the guard: a deleted or silently un-warned module must not pass quietly."""
    path = _CORE_NAVIG / rel_path
    assert path.is_file(), (
        f"{rel_path} is listed as dormant but no longer exists — if it was deleted, "
        "remove its entry here too."
    )
    assert "NOT WIRED" in path.read_text(encoding="utf-8"), (
        f"{rel_path} is listed here as dormant but its docstring no longer says so; "
        "the two must agree or one of them is misleading."
    )
