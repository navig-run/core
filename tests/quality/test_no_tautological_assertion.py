"""Guard: an assertion must be able to be false.

``assert X or not X`` is true for every possible value of X. It looks like a check, it
counts in the pass total, and it constrains nothing::

    result = _get_paramiko()
    assert result is not False or result is False   # "always returns something"

    result = cs.validate_host_config({...}, strict=False)
    assert result is None or result is not None     # "should not raise"

This is the same defect as a test that cannot fail, wearing an ``assert``. What makes it
worth its own guard is that it is *invisible in review*: the line reads as an assertion, and
the accompanying comment usually states a real intention ("always returns something",
"returns none on invalid") that the code does not check.

Measured 2026-09-03 across every pytest suite in the repo: **7 findings, all 7 real, all
fixed in the same change** -- and in every one of the 7 the test's own NAME already said what
the assertion should have been:

    test_returns_module_or_false          -> assert result is False or hasattr(result, "SSHClient")
    test_default_root_is_set              -> assert store._root.exists()
    test_returns_none_on_invalid_config…  -> assert result is None
    test_returns_none_on_invalid          -> assert result is None
    test_valid_host_config_minimal        -> assert result is not None  (+ its keys were wrong)
    test_minimal_host_does_not_raise      -> assert result is not None  (+ its keys were wrong)
    test_global_settings_dir_not_home     -> redirect the delegate and assert it is followed

Two of the seven were also hiding a real bug: they passed ``host``/``user`` where the schema
defines ``hostname``/``username``, so the "valid minimal host" each claimed to validate was
in fact invalid and came back ``None``. The tautology is what let that sit there -- with a
real assertion the wrong keys fail immediately.

The seventh is the instructive one. Its own comment explains why it was written as a
tautology: on a machine with no ``NAVIG_CONFIG_DIR``, ``paths.config_dir()`` legitimately
resolves to ``~/.navig``, so comparing the returned path against home cannot tell
"delegated correctly" from "hardcoded home", and the author widened the assertion until it
was always true rather than leave it flaky. The fix was to redirect the delegate instead of
inspecting its output. **When an assertion has to be widened to always-true, the test is
observing the wrong thing.**

Scope note -- deliberately NOT included:

* ``assert f(a) == f(a)`` is a determinism test and fails for a non-deterministic ``f``.
* ``assert obj.attr == obj.attr`` looks tautological but is not when ``attr`` is a
  **property**: both live instances of this shape in the tree (``ve.fernet is ve.fernet``,
  ``log.session_id == log.session_id``) read lazily-cached properties and would fail if the
  property rebuilt its value. Nothing in the AST distinguishes an attribute from a property,
  so that shape cannot be gated -- checking it flagged 2 false positives out of 2.
* ``assert True`` adds nothing but does not stop a raise above it from failing the test.

That leaves the boolean ``X or not-X`` form, which is true regardless of what any call
returns and so needs no knowledge of the code under test. 55 candidate "tautologies" under
the loose reading, 7 under this one, all 7 real.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

VENDOR_DIRS = {"node_modules", "venv", "site-packages", "__pycache__", "target", "dist", "build"}

# Vacuity floors, matching this guard's siblings. Measured 2026-09-03: 1,709 test files,
# 150 under plugins/. The plugin floor counts a PRESENCE, never an absence -- a floor
# phrased as "files not under core/" is satisfied by a walk that excluded everything.
MIN_FILES = 800
MIN_PLUGIN_FILES = 100

# `is` / `is not`, `==` / `!=`, `in` / `not in`: a comparison and its exact negation.
_OPPOSITE_OPS = ({"Is", "IsNot"}, {"Eq", "NotEq"}, {"In", "NotIn"})


def _test_files() -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(REPO):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in VENDOR_DIRS]
        found += [
            Path(dirpath, f) for f in filenames if f.startswith("test_") and f.endswith(".py")
        ]
    return found


def _is_x_or_not_x(test: ast.expr) -> str | None:
    """Name the tautology when ``test`` is an ``or`` of a thing and its own negation."""
    if not (isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or) and len(test.values) == 2):
        return None
    left, right = test.values

    # `a is None or a is not None`  /  `a == b or a != b`  /  `a in b or a not in b`
    if isinstance(left, ast.Compare) and isinstance(right, ast.Compare):
        if len(left.ops) == len(right.ops) == 1:
            same_subject = ast.unparse(left.left) == ast.unparse(right.left)
            same_object = ast.unparse(left.comparators[0]) == ast.unparse(right.comparators[0])
            ops = {type(left.ops[0]).__name__, type(right.ops[0]).__name__}
            if same_subject and same_object and ops in _OPPOSITE_OPS:
                return "a comparison OR its exact negation"

    # `x or not x`  /  `not x or x`
    for a, b in ((left, right), (right, left)):
        if isinstance(b, ast.UnaryOp) and isinstance(b.op, ast.Not):
            if ast.unparse(b.operand) == ast.unparse(a):
                return "an expression OR its own negation"
    return None


def _offenders() -> tuple[list[str], int, int]:
    offenders: list[str] = []
    files = _test_files()
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
            if not isinstance(node, ast.Assert):
                continue
            kind = _is_x_or_not_x(node.test)
            if kind:
                offenders.append(
                    f"{rel}:{node.lineno}  [{kind}]  assert {ast.unparse(node.test)[:70]}"
                )
    return offenders, len(files), plugin_files


def test_no_assertion_is_true_for_every_possible_value():
    # Anchor REPO structurally before counting anything. A miscomputed root makes every
    # count below it meaningless while still looking plausible.
    assert (REPO / "core" / "navig").is_dir() and (REPO / "plugins").is_dir(), (
        f"{REPO} is not the repo root — REPO is derived by parent count, so a file moved "
        "between directories silently narrows the whole scan."
    )

    offenders, n_files, n_plugin = _offenders()

    assert n_files >= MIN_FILES, (
        f"scanned only {n_files} test file(s), expected >= {MIN_FILES} — the walk collapsed "
        "and a guard that reads nothing passes while checking nothing."
    )
    assert n_plugin >= MIN_PLUGIN_FILES, (
        f"only {n_plugin} test file(s) under plugins/ reached, expected >= "
        f"{MIN_PLUGIN_FILES}. The walk degraded to core/ alone."
    )

    assert not offenders, (
        "these assertions are true for every possible value of their operands, so they "
        "constrain nothing:\n  "
        + "\n  ".join(offenders)
        + "\n\nThe test's own name usually states the assertion it should make. If the "
        "honest assertion looks flaky — as with a path that legitimately equals its own "
        "default — redirect the dependency and assert that it is followed, rather than "
        "widening the assertion until it is always true."
    )
