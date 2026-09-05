"""A `BaseTool.run` override must name its callback `on_status`.

Dispatch calls it **by keyword**::

    return await tool.run(args, on_status=None)     # agent_tool_registry._run_tool_sync

so a subclass that names that parameter anything else raises ``TypeError: run() got an
unexpected keyword argument 'on_status'`` on **every** call. Not sometimes — every call.

Seven tools shipped that way (`status_callback`): the three todo tools and four plan
tools. Six were unreachable, which is why nobody noticed — but the seventh,
``get_plan_context``, is registered by ``register_all_tools()`` and offered to the model,
so it failed 100% of the time it was invoked. Verified by probe before the fix:

    GetPlanContextTool.run() got an unexpected keyword argument 'on_status'

**The `override` mypy code that this repo already gates does NOT catch it.** That code
catches an incompatible *type*; a pure *rename* passed cleanly (`mypy` on the unfixed
file reported only the documented `parameters: list[dict]` artifacts). So this is a
separate, cheap AST check rather than a fourth mypy code.

Detection is by **shape**: only classes deriving from ``BaseTool``. A name-based rule over
every ``def run(...)`` produces seven false positives — `ConnectionAdapter.run(…,
capture_output)`, `DeployEngine.run(…, skip_backup)`, `UpdateEngine.run(…, force)` and
friends are unrelated classes with their own perfectly good ``run``. Measured: 83 `run`
overrides carry a third positional parameter; 14 do not call it `on_status`; exactly 7 of
those are `BaseTool` subclasses.
"""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
ROOTS = [REPO / "core" / "navig", *sorted((REPO / "plugins").glob("navig-*"))]

#: The parameter name every BaseTool.run must use — dispatch passes it by keyword.
CANONICAL = "on_status"


def _is_base_tool_subclass(cls: ast.ClassDef) -> bool:
    """Does this class derive from BaseTool?

    Matches the trailing name so both `BaseTool` and `registry.BaseTool` count. A plugin
    subclassing an intermediate NAVIG tool base is out of scope here — it inherits the
    same contract, but proving that statically needs cross-module resolution, and this
    guard is deliberately cheap.
    """
    for base in cls.bases:
        if isinstance(base, ast.Name) and base.id == "BaseTool":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "BaseTool":
            return True
    return False


@lru_cache(maxsize=1)
def _scan() -> tuple[list[str], int]:
    """(offenders, number_of_BaseTool_run_overrides_examined)."""
    offenders: list[str] = []
    examined = 0
    for root in ROOTS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
                if not _is_base_tool_subclass(cls):
                    continue
                for fn in cls.body:
                    if not isinstance(fn, (ast.AsyncFunctionDef, ast.FunctionDef)):
                        continue
                    if fn.name != "run":
                        continue
                    names = [a.arg for a in fn.args.args]
                    if len(names) < 3:
                        continue  # (self, args) only — inherits the default
                    examined += 1
                    if names[2] != CANONICAL:
                        rel = path.relative_to(REPO).as_posix()
                        offenders.append(f"{rel}::{cls.name}.run(…, {names[2]})")
    return offenders, examined


def test_the_scan_examined_something():
    """A detector that silently matches nothing looks exactly like a clean run."""
    _offenders, examined = _scan()
    assert examined >= 20, (
        f"only {examined} BaseTool.run overrides examined — the shape rule stopped "
        "matching (was BaseTool renamed or re-exported?), so the assertion below is "
        "vacuous"
    )


def test_every_tool_names_its_status_callback_canonically():
    offenders, _examined = _scan()
    assert not offenders, (
        "these tools rename BaseTool.run's third parameter, so dispatch's "
        f"`run(args, {CANONICAL}=None)` raises TypeError on EVERY call:\n  "
        + "\n  ".join(offenders)
        + f"\nRename the parameter to `{CANONICAL}`."
    )


@pytest.mark.parametrize("tool_name", ["get_plan_context", "todo_show"])
def test_the_previously_broken_tools_can_actually_be_called(tool_name):
    """The regression, at the level that matters: dispatch's exact call must not raise.

    A signature assertion alone would not have caught the original defect if the base had
    also drifted — this drives the real call convention.
    """
    import asyncio

    from navig.agent.agent_tool_registry import _AGENT_REGISTRY
    from navig.agent.tools import register_all_tools

    register_all_tools()
    entry = _AGENT_REGISTRY.get_entry(tool_name)
    assert entry is not None, f"{tool_name} is not registered"

    async def _call():
        return await entry.tool_ref.run({}, on_status=None)

    result = asyncio.run(_call())  # must not raise TypeError
    assert result is not None
