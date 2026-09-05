"""No `with ThreadPoolExecutor() as ex: … future.result(timeout=N)` — it DEFEATS the timeout.

``ThreadPoolExecutor.__exit__`` calls ``shutdown(wait=True)``, which JOINS the worker thread. So
when ``future.result(timeout=N)`` raises ``TimeoutError``, the exception propagates out of the
``with`` block and the exit STILL blocks until the (hung) task finishes — the cap is meaningless,
and a task that never returns wedges the caller forever. Python can't force-kill a thread, so the
correct shapes are:

    ex = ThreadPoolExecutor(...)          # NOT a `with` block
    fut = ex.submit(fn)
    try:
        return fut.result(timeout=N)
    finally:
        ex.shutdown(wait=False)           # abandon a stuck thread instead of joining it

or, for a coroutine, bound it with ``asyncio.wait_for(coro, N)`` inside a ``_run()`` that the
worker runs — then it is *cancelled* cleanly. This class recurred: ``_run_tool_sync`` (#4e9013d7)
and ``probe_llm_sync`` (#651) both had it; this guard locks the door.

Scope: ``core/navig/`` plus every first-party ``plugins/navig-*`` — the same surface as
``test_no_orphan_tasks``. A plugin runs on the same daemon event loop, and four already fan work
out over a ``ThreadPoolExecutor`` (navig-games, navig-github, navig-dedupe, navig-download), so a
future ``.result(timeout=…)`` added there would wedge the daemon just as silently — the guard must
watch that surface too.

A CI guard must be false-positive-free, so files that match the AST pattern but are not the bug
(or are a documented, deferred instance) are an explicit allowlist with a per-entry reason.
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "navig"
REPO = CORE.parents[1]
PLUGINS = REPO / "plugins"

# Keyed repo-relative ("core/navig/<rel>" or "plugins/navig-<pkg>/<rel>") -> reason, so a plugin
# offender allowlists with the same key shape as a core one. Ratcheted by
# test_allowlist_entries_still_match.
_ALLOWLIST: dict[str, str] = {
    "core/navig/commands/matrix.py": (
        "intentional SIGINT-yield POLL loop — result(timeout=0.1) is a poll interval, not a cap; "
        "`while not future.done()` runs to completion, so the with-exit join is instant."
    ),
}


def _has_result_timeout(node: ast.AST) -> bool:
    for n in ast.walk(node):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "result"
            and any(kw.arg == "timeout" for kw in n.keywords)
        ):
            return True
    return False


def _is_defeated_timeout_with(node: ast.AST) -> bool:
    """A ``with …ThreadPoolExecutor(…)…:`` block that contains a ``.result(timeout=…)`` call."""
    if not isinstance(node, ast.With):
        return False
    for item in node.items:
        c = item.context_expr
        if isinstance(c, ast.Call):
            fn = c.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if "ThreadPoolExecutor" in name and _has_result_timeout(node):
                return True
    return False


def _scanned_roots() -> list[Path]:
    """core/navig plus every first-party plugin.

    A defeated timeout in a plugin wedges the same daemon event loop just as silently, and
    four plugins already run work on a ``ThreadPoolExecutor`` (navig-games, navig-github,
    navig-dedupe, navig-download), so a future ``.result(timeout=…)`` there is a live risk.
    First-party plugins depend on core, so the same fix helpers apply. Same scanned surface
    as ``test_no_orphan_tasks``.
    """
    roots = [CORE]
    if PLUGINS.is_dir():
        roots += sorted(p for p in PLUGINS.glob("navig-*") if p.is_dir())
    return roots


def _python_files() -> list[Path]:
    files: list[Path] = []
    for root in _scanned_roots():
        for f in root.rglob("*.py"):
            parts = set(f.parts)
            if "tests" in parts or "test" in parts or "scaffold-templates" in parts:
                continue
            files.append(f)
    return files


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO)).replace("\\", "/")


def _file_matches(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return False
    return any(_is_defeated_timeout_with(n) for n in ast.walk(tree))


def test_no_defeated_timeout():
    offenders = [_rel(p) for p in _python_files() if _rel(p) not in _ALLOWLIST and _file_matches(p)]
    assert not offenders, (
        "`with ThreadPoolExecutor() as ex: … future.result(timeout=N)` DEFEATS the cap — the "
        "with-exit's shutdown(wait=True) JOINS the worker, so a hung call wedges the caller past "
        "N (Python can't kill a thread). Create the executor WITHOUT `with` + `shutdown(wait=False)` "
        "in a finally (see agent_tool_registry._run_tool_sync / llm_probe.probe_llm_sync), or bound "
        "the coroutine with asyncio.wait_for inside it.\n  " + "\n  ".join(offenders)
    )


def test_allowlist_entries_still_match():
    """Ratchet: every allowlisted file must STILL contain the pattern; when a site is fixed, its
    entry must be removed (a file-level allowlist otherwise hides a NEW occurrence)."""
    stale = []
    for rel in _ALLOWLIST:
        path = REPO / rel
        if not path.exists():
            stale.append(f"{rel} (file gone)")
        elif not _file_matches(path):
            stale.append(f"{rel} (no longer matches — remove the allowlist entry)")
    assert not stale, "stale allowlist entries: " + ", ".join(stale)


def test_scans_first_party_plugins():
    """Ratchet the SCOPE, not just the pattern. There are no plugin offenders today, so
    `test_no_defeated_timeout` stays green whether or not plugins are scanned — a refactor that
    reverted to core-only would silently drop the plugin surface with nothing failing. This locks
    it: when the plugins tree exists, at least one `plugins/navig-*` file must be in the scanned
    set (four plugins run `ThreadPoolExecutor` work that could regress)."""
    # `return` here reported PASS -- a ratchet that silently stops ratcheting is worse
    # than no ratchet, because the green tick says the scope was checked. This test only
    # exists inside the source tree (tests/ is not shipped in the wheel), so a missing
    # plugins/ means the tree moved, which is a finding rather than an excuse to pass.
    assert PLUGINS.is_dir(), (
        f"{PLUGINS} is missing -- this ratchet cannot verify the plugin scope. If the tree "
        "moved, repoint REPO/PLUGINS; do not let it pass silently."
    )
    scanned = {_rel(p) for p in _python_files()}
    assert any(r.startswith("plugins/navig-") for r in scanned), (
        "the guard is no longer scanning first-party plugins — restore _scanned_roots()"
    )


def test_the_rule_catches_a_regression():
    """Guard the guard: flag the `with` form, not the safe no-`with` form or a bare result()."""
    bad = ast.parse(
        "import concurrent.futures\n"
        "def f():\n"
        "    with concurrent.futures.ThreadPoolExecutor() as ex:\n"
        "        return ex.submit(g).result(timeout=5)\n"
    )
    assert any(_is_defeated_timeout_with(n) for n in ast.walk(bad)), "rule missed the bug"

    safe = ast.parse(
        "import concurrent.futures\n"
        "def f():\n"
        "    ex = concurrent.futures.ThreadPoolExecutor()\n"
        "    try:\n"
        "        return ex.submit(g).result(timeout=5)\n"
        "    finally:\n"
        "        ex.shutdown(wait=False)\n"
    )
    assert not any(_is_defeated_timeout_with(n) for n in ast.walk(safe)), "flagged the safe form"

    no_timeout = ast.parse(
        "import concurrent.futures\n"
        "def f():\n"
        "    with concurrent.futures.ThreadPoolExecutor() as ex:\n"
        "        return ex.submit(g).result()\n"  # no timeout → not the defeated pattern
    )
    assert not any(_is_defeated_timeout_with(n) for n in ast.walk(no_timeout)), "flagged result()"
