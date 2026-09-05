"""No hand-rolled `monkeypatch.delitem(sys.modules, …)` — use `evicted_modules`.

`monkeypatch.delitem` restores only what existed **when it ran**, so a module first
imported *inside* the eviction is tracked by nothing. It outlives teardown while its
parent package is rolled back to the original object, which has no such attribute:
`sys.modules` is then holding a child its parent does not know about, and
`importlib.import_module` hands that child back from the cache **without** re-binding the
parent.

The failure lands in a different file and looks nothing like the cause. Dotted
`monkeypatch.setattr` resolves by attribute traversal, so every later
`setattr("navig.gateway.routes.core.X", …)` in the same xdist worker raises
`AttributeError`. It reddened both WS-heartbeat tests in
`tests/gateway/test_gateway_core_routes.py` and blocked four pre-push gate runs (#1109) —
reading as a timing flake the whole time, because whether it bit depended only on how
xdist distributed the two files. Run in one order it failed 100% of the time; in the
other it passed.

`tests.fixtures.module_eviction.evicted_modules` is a context manager precisely so the
purge cannot be forgotten: you cannot evict through it and skip the cleanup. This guard
keeps the raw call from coming back beside it — two implementations of this would drift,
and the drift is invisible until it reddens someone else's test.

Scope: every test in `core/tests/` plus each first-party `plugins/navig-*/tests` and
`private/harbor/tests`. The class is a property of the eviction pattern, not of a
directory — a plugin suite that evicts modules poisons the same interpreter.
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[2]
REPO = CORE.parent

# The one place the pattern is allowed to live: the shared helper that always purges.
_HELPER = "core/tests/fixtures/module_eviction.py"

# Files that match the AST pattern but are not the bug. Each needs a written reason —
# an unexplained entry is how a guard becomes a parking space.
_ALLOWLIST: dict[str, str] = {}


def _test_roots() -> list[Path]:
    roots = [CORE / "tests"]
    plugins = REPO / "plugins"
    if plugins.is_dir():
        roots += sorted(d / "tests" for d in plugins.glob("navig-*") if (d / "tests").is_dir())
    harbor = REPO / "private" / "harbor" / "tests"
    if harbor.is_dir():
        roots.append(harbor)
    return roots


def _is_sys_modules(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "modules"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    )


def _offenders() -> tuple[list[str], int]:
    """(repo-relative "path:line" for each raw delitem, number of files scanned)."""
    hits: list[str] = []
    scanned = 0
    for root in _test_roots():
        for path in sorted(root.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            scanned += 1
            rel = path.relative_to(REPO).as_posix()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                # ANY receiver name: `monkeypatch`, `mp`, `self.mp`, a fixture alias…
                if not (isinstance(func, ast.Attribute) and func.attr == "delitem"):
                    continue
                if node.args and _is_sys_modules(node.args[0]):
                    hits.append(f"{rel}:{node.lineno}")
    return hits, scanned


def test_no_raw_sys_modules_delitem_outside_the_shared_helper() -> None:
    hits, scanned = _offenders()

    # Anti-vacuity: a scan that reads nothing reports no offenders and looks identical to
    # a clean suite. The floor is deliberately far below the real count (~1,900 files).
    assert scanned > 500, (
        f"only {scanned} test files scanned — the roots are wrong, so a clean result here "
        "would mean nothing"
    )

    # The helper itself must still contain the pattern, or this guard is watching a shape
    # that no longer exists anywhere and would stay green through any regression.
    assert any(h.startswith(_HELPER) for h in hits), (
        f"{_HELPER} no longer calls monkeypatch.delitem(sys.modules, …) — either it moved "
        "or the eviction was rewritten. Point this guard at its new home; do not delete it."
    )

    offenders = [
        h for h in hits if not h.startswith(_HELPER) and h.rsplit(":", 1)[0] not in _ALLOWLIST
    ]
    assert not offenders, (
        "hand-rolled sys.modules eviction — a module imported inside it outlives teardown "
        "and breaks dotted monkeypatch.setattr for every later test in the same xdist "
        "worker (#1109). Use the context manager, which cannot skip its purge:\n\n"
        "    from tests.fixtures.module_eviction import evicted_modules\n"
        "    with evicted_modules(monkeypatch, (\"navig.voice\", \"navig_audio\")):\n"
        "        ...\n\n"
        "Offenders:\n  " + "\n  ".join(offenders)
    )


def test_allowlist_has_no_stale_entries() -> None:
    """An allowlist entry that no longer matches is a claim nobody is checking."""
    hits, _ = _offenders()
    files = {h.rsplit(":", 1)[0] for h in hits}
    stale = sorted(set(_ALLOWLIST) - files)
    assert not stale, (
        "allowlist entries that no longer contain a raw sys.modules delitem — delete "
        "them:\n  " + "\n  ".join(stale)
    )
