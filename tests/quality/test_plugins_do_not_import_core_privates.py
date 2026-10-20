"""A plugin must reach core through public names, never a leading-underscore symbol.

The rule in the architecture plan — *"never allow plugins to import private core
implementation modules when stable APIs could be used instead"* — was unenforced, so
four runtime sites had drifted past it:

  · `navig-devnet` and `navig-games` imported `navig.agent.agent_tool_registry._AGENT_REGISTRY`
  · `navig-email` imported `navig.commands.telegram._load_telegram_token`
  · `navig-agentctl` imported `navig.store.base._utcnow`

Each looks harmless and is not: a leading underscore is core's *explicit* signal that the
name may be renamed, re-homed or deleted without notice. Reaching it from outside means a
refactor inside core lands as an ImportError **inside an installed plugin**, at call time,
on a user's machine — with no review, no type error and no failing test in the change that
caused it. The fix is not to rename those symbols; it is to publish a public entry point
(`get_agent_tool_registry()`, `load_telegram_token()`, `utcnow()`) and hold the line.

Scope, deliberately narrow so the rule is enforceable rather than aspirational:

  · **core only** — `from navig.…`. A plugin reaching *another plugin's* private helper
    (`navig_media_core.…._exec_ffmpeg`) is that pair's business, not this contract.
  · **runtime only** — `tests/` is excluded. A test asserting against an internal shape is
    a different, legitimate coupling, and it does not ship.
  · **`_name`, not `__name__`** — `from navig import __version__` is a public dunder and
    is correctly allowed.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PLUGINS = REPO / "plugins"

_SKIP_DIRS = {"tests", "test", "__pycache__", "build", "dist", ".venv", "node_modules"}
# Single leading underscore, not a dunder: `_utcnow` yes, `__version__` no.
_PRIVATE = re.compile(r"^_[^_]")
# A core import: `navig.x.y`. `navig_media_core…` is a different package.
_CORE = re.compile(r"^navig(\.|$)")


def _core_private_imports(plugin_dir: Path) -> list[str]:
    """``file:line: from navig.x import _y`` for one plugin's shipped code."""
    found: list[str] = []
    for f in sorted(plugin_dir.rglob("*.py")):
        if _SKIP_DIRS & set(f.parts):
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module or node.level:
                continue
            if not _CORE.match(node.module):
                continue
            for alias in node.names:
                if _PRIVATE.match(alias.name):
                    found.append(
                        f"{f.relative_to(plugin_dir).as_posix()}:{node.lineno}  "
                        f"from {node.module} import {alias.name}"
                    )
            # `from navig.x import navig.x._y` style is not possible, but a private
            # MODULE is: `import navig.x._y` / `from navig import x` is fine, while
            # `from navig.x import _y` above is the case that matters.
    return found


def _plugin_dirs() -> list[Path]:
    if not PLUGINS.is_dir():
        return []
    return sorted(d for d in PLUGINS.glob("navig-*") if d.is_dir())


def test_discovery_finds_plugins():
    """Anti-vacuity: a scan over nothing reports a clean pass just as happily."""
    dirs = _plugin_dirs()
    assert len(dirs) >= 10, f"only {len(dirs)} plugins under {PLUGINS} — scan is not real"
    scanned = sum(1 for d in dirs for f in d.rglob("*.py") if not (_SKIP_DIRS & set(f.parts)))
    assert scanned >= 200, f"only {scanned} source files scanned — the walk is broken"


def test_no_plugin_reaches_into_a_core_private_symbol():
    offenders: list[str] = []
    for d in _plugin_dirs():
        offenders += [f"{d.name}: {hit}" for hit in _core_private_imports(d)]

    assert not offenders, (
        "plugins must not import core's private (leading-underscore) symbols. Publish a "
        "public entry point instead — a private name is core's signal that it may be "
        "renamed without notice, and an outside import turns that rename into an "
        "ImportError on a user's machine:\n  " + "\n  ".join(sorted(offenders))
    )


def test_the_three_repairs_are_in_place():
    """Pin the public replacements, so the fix cannot be quietly reverted.

    Each of these exists because something outside core needed it; deleting it would
    push callers back to the underscore form and the guard above would catch it — but
    only on the next run, so the intent is recorded here too.
    """
    from navig.agent.agent_tool_registry import get_agent_tool_registry
    from navig.commands.telegram import load_telegram_token
    from navig.store.base import utcnow

    assert callable(get_agent_tool_registry)
    assert get_agent_tool_registry() is not None
    assert callable(load_telegram_token)
    assert callable(utcnow)
    assert isinstance(utcnow(), str) and utcnow().endswith("Z")
