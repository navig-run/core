"""Guard: a test must be able to fail.

A test whose body is wrapped in a swallowing ``try``/``except`` and which asserts nothing
passes no matter what the code under test does::

    def test_password_auth_without_password_raises(self):
        try:
            result = validate_host_config({...}, strict=True)
            if result is None:
                return
        except (ConfigValidationError, Exception):
            pass  # expected: password auth without password raises

That reports PASS if the call raises, if it raises something completely unrelated, and if it
does not raise at all. It is not a weak test -- it is a **green light wired to nothing**,
which is worse than no test, because the suite says the behaviour is covered.

Measured 2026-09-03 across every pytest suite in the repo (1,709 files / 28,626 test
functions): **9 findings, all 9 real, all fixed in the same change** --

    test_raises_on_bad_field_strict_mode          could not fail
    test_password_auth_without_password_raises    could not fail
    test_strict_raises_config_validation_error    could not fail
    test_password_auth_without_password_raises_strict
        could not fail AND misattributed: its keys were "host"/"user", not the schema's
        "hostname"/"username", so the raise it saw was "hostname: Field required" -- it
        passed identically with `auth_method` deleted, never touching the password rule
    test_does_not_raise_on_missing_path           swallowed the raise it exists to detect
    test_ms_small_dispatches                      asserted nothing about the dispatch
    test_update_packages_dry_run                  \\
    test_import_error_silently_suppressed          > each carried a comment saying "should
    test_runs_without_error_with_mocked_live       / not raise" directly above a try/except
    test_run_browser_task_accepts_spec            /  that made raising acceptable

so the baseline is EMPTY and stays that way: unlike a suppression list, an entry here would
assert that a test which cannot fail is acceptable, and there is no such case. (All nine
passed once the try/except came off, so the swallowing was never load-bearing.)

The rule is deliberately narrower than "has no assert" in two ways, and both narrowings
were measured rather than assumed:

* A plain smoke test (call the function, let an exception fail it) *can* fail and is a
  legitimate shape -- 368 test functions have no assert statement and nearly all are that.
  So the offending shape needs BOTH halves: no failure mechanism at all AND a ``try`` that
  swallows. That pairing took the count from 368 mostly-noise to 14.
* Of those 14, five swallow through a NARROW handler (``except KeyError``), which still
  lets an unrelated error fail the test. Requiring a handler that catches everything took
  it to 9 -- all real. The five are a weaker smell, not this defect; see
  ``_catches_everything``.

Repo-wide by construction, not scoped to ``core/tests``: the surface is "every pytest suite"
-- plugins, private/harbor, registry, apps/os/resources all included -- because a guard
pointed at a path stops protecting the moment the code it guards lives somewhere else.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

# Directories that hold no live suite: dot-dirs (.lab reference corpus, .dev worktrees,
# .backup, .git) plus vendored trees. Pruned during the walk, not filtered after, so the
# scan never descends into node_modules or a vendored site-packages.
VENDOR_DIRS = {"node_modules", "venv", "site-packages", "__pycache__", "target", "dist", "build"}

# Vacuity floors. Measured today: 1,709 files / 28,626 test functions / 150 plugin files.
# Set far below so ordinary growth or pruning never trips them, but high enough that a walk
# which silently collapsed to one directory fails instead of reporting a cheerful zero.
MIN_FILES = 800
MIN_FUNCTIONS = 10_000
MIN_PLUGIN_FILES = 100

# The plugin floor counts files under a directory that must exist, rather than files that
# merely do NOT start with "core/". The first version used the negative form and was itself
# vacuous: teeth-testing it by repointing REPO at core/ did not trip it, because from there
# no path starts with "core/" any more, so every file counted as non-core and the floor
# passed while the walk covered a fifth of the tree. A floor phrased as an absence cannot
# tell "nothing was excluded" from "everything was excluded".

_FAILURE_CALLS = {
    "raises",
    "fail",
    "warns",
    "deprecated_call",
    "xfail",
    "approx",
    "exit",
}


def _test_files() -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(REPO):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in VENDOR_DIRS]
        found += [
            Path(dirpath, f) for f in filenames if f.startswith("test_") and f.endswith(".py")
        ]
    return found


def _has_failure_mechanism(fn: ast.AST) -> bool:
    """True when anything in this function can turn a wrong answer into a failure."""
    for node in ast.walk(fn):
        if isinstance(node, (ast.Assert, ast.Raise)):
            return True
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            # `assert_called_once`, `assertEqual`, `assert_frame_equal`, ... all start with
            # "assert"; pytest's own failure helpers are named explicitly.
            if name.startswith("assert") or name in _FAILURE_CALLS:
                return True
        if isinstance(node, ast.With):
            for item in node.items:
                expr = ast.unparse(item.context_expr)
                if "raises" in expr or "warns" in expr:
                    return True
    return False


def _discards_the_exception(handler: ast.ExceptHandler) -> bool:
    """True when the handler body does nothing: only pass/return/a bare docstring."""
    return all(
        isinstance(stmt, (ast.Pass, ast.Return))
        or (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))
        for stmt in handler.body
    )


def _catches_everything(handler: ast.ExceptHandler) -> bool:
    """True when the handler catches any exception the body could raise.

    A NARROW handler (``except KeyError``) is deliberately not flagged: an unrelated error
    still fails the test, so such a test *can* fail and the claim in this guard's failure
    message would be false of it. Measured: 5 tests have a narrow swallowing handler and no
    assertion -- a weaker smell (they pass whether or not their premise holds) but not the
    defect this guard names, so they stay out rather than being waved through by a message
    that overstates what was found.
    """
    if handler.type is None:  # bare `except:`
        return True
    names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(getattr(n, "id", "") in {"Exception", "BaseException"} for n in names)


def _swallows(handler: ast.ExceptHandler) -> bool:
    return _discards_the_exception(handler) and _catches_everything(handler)


def _offenders() -> tuple[list[str], int, int, int]:
    offenders: list[str] = []
    files = _test_files()
    functions = 0
    plugin_files = 0
    for path in sorted(files):
        rel = path.relative_to(REPO).as_posix()
        if rel.startswith("plugins/"):
            plugin_files += 1
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:  # pragma: no cover - a broken test file is another guard's job
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            functions += 1
            if _has_failure_mechanism(node):
                continue
            # ast.walk, not node.body: the first version of this rule looked only at
            # top-level statements and so missed every swallowing try nested inside a
            # `with patch(...)` or a loop -- 8 more tests, in a codebase where mocking
            # under `with` is the house style. A guard that reads one shape of a defect
            # protects against that shape, not against the defect.
            tries = [stmt for stmt in ast.walk(node) if isinstance(stmt, ast.Try)]
            if not tries:
                continue  # a plain smoke test: an exception still fails it
            if all(any(_swallows(h) for h in t.handlers) for t in tries):
                offenders.append(f"{rel}:{node.lineno}  {node.name}")
    return offenders, len(files), functions, plugin_files


def test_no_test_can_be_written_so_that_it_cannot_fail():
    # Anchor REPO structurally before counting anything. A miscomputed root is the one
    # failure that makes every count below meaningless while still looking plausible.
    assert (REPO / "core" / "navig").is_dir() and (REPO / "plugins").is_dir(), (
        f"{REPO} is not the repo root — REPO is derived by parent count, so a file moved "
        "between directories silently narrows the whole scan."
    )

    offenders, n_files, n_functions, n_plugin = _offenders()

    assert n_files >= MIN_FILES, (
        f"scanned only {n_files} test file(s), expected >= {MIN_FILES} — the walk collapsed "
        "and a guard that reads nothing passes while checking nothing."
    )
    assert n_functions >= MIN_FUNCTIONS, (
        f"found only {n_functions} test function(s), expected >= {MIN_FUNCTIONS} — parsing "
        "is finding files but not their tests."
    )
    assert n_plugin >= MIN_PLUGIN_FILES, (
        f"only {n_plugin} test file(s) under plugins/ reached, expected >= "
        f"{MIN_PLUGIN_FILES}. The walk degraded to core/ alone, so plugins, private/harbor, "
        "registry and apps/os/resources are unguarded — the exact failure this guard is "
        "scoped to avoid."
    )

    assert not offenders, (
        "these tests cannot fail — every path through them reports PASS, because nothing "
        "asserts and a `try` swallows whatever happens:\n  "
        + "\n  ".join(offenders)
        + "\n\nAssert the behaviour the test is named for. For a test named `...raises...` "
        "that is `with pytest.raises(TheError):`; for one named `...does_not_raise...` it is "
        "simply calling it, since an uncaught exception is the failure signal."
    )
