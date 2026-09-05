"""Guard: don't loop over a producer this file itself proves can return nothing.

``for x in result: assert P(x)`` asserts nothing when ``result`` is empty, so a producer
that regresses to returning nothing turns its own test green. That shape alone is far too
common to gate -- 653 occurrences, and most are legitimate, because a loop whose correct
expectation IS an empty result is indistinguishable from one that should have had data
(measured and written up in CLAUDE.md; the sites were fixed, the shape was not gated).

**The signal that resolves that ambiguity comes from the file itself.** When another test in
the same file asserts ``producer(...) == []``, the file is stating that an empty return is a
reachable state of that producer -- so a loop elsewhere in the same file, over the same
producer, with nothing asserting it is non-empty, is a test that a silently-emptied producer
would satisfy. No judgement about the code under test is needed; the evidence is already
written down two tests up.

Measured 2026-09-04 across every pytest suite: **2 findings, both real, both fixed**, and in
both the file already contained the correct pattern somewhere else:

  ``test_all_chunks_within_limit`` -- the reply-chunker's CONTRACT test. Four lines above it,
  ``test_empty_returns_empty`` asserts ``chunk_text("") == []``. Verified by running the old
  body against a chunker stubbed to return ``[]``: it PASSES. The test that exists to prove
  "all chunks are within the limit" was satisfied by a chunker that emitted nothing at all.

  ``test_openai_labels_are_strings`` -- looped directly over
  ``vault_labels_for_env("OPENAI_API_KEY")``. Three tests in that file assert the function
  returns ``[]`` for unknown names, and the sibling ``test_github_token_returns_labels``
  already binds the result and asserts ``len(labels) > 0``. A resolver that stopped knowing
  ``OPENAI_API_KEY`` -- the mapping from env var to vault entry -- would have satisfied it.

Both the bound form (``xs = producer(); for x in xs:``) and the direct form
(``for x in producer():``) are checked; the second is how the vault one hid.

⚠ **Evidence is the explicit ``== []`` / ``== {}`` / ``== ()`` comparison ONLY.** Accepting
``assert not producer(...)`` as evidence too was measured and rejected: callee extraction
picks up generic method names from such expressions (``any``, ``values``, ``get``), so
``assert not any(before["wired"].values())`` marked ``values`` as a proven-empty producer and
flagged an unrelated ``for path in data["script_paths"].values()`` loop -- in a test that
asserts two other things outside the loop. One extra finding, and it was noise.

Escape hatch: when a loop legitimately may iterate zero times, put ``# may-be-empty`` on the
``for`` line with a reason, the same way ``# path-sep-ok`` works for the path-separator guard.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

VENDOR_DIRS = {"node_modules", "venv", "site-packages", "__pycache__", "target", "dist", "build"}

# Vacuity floors, matching the other test-honesty guards. Measured: 1,709 test files, 150
# under plugins/. The plugin floor counts a PRESENCE -- a floor phrased as "files not under
# core/" is satisfied by a walk that excluded everything.
MIN_FILES = 800
MIN_PLUGIN_FILES = 100

_EXEMPT_MARKER = "may-be-empty"


def _test_files() -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(REPO):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in VENDOR_DIRS]
        found += [
            Path(dirpath, f) for f in filenames if f.startswith("test_") and f.endswith(".py")
        ]
    return found


def _callee_names(node: ast.AST) -> set[str]:
    """Every callee name invoked anywhere in ``node`` (``f(...)`` and ``obj.f(...)``)."""
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name:
                names.add(name)
    return names


def _is_empty_literal(node: ast.expr) -> bool:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return not node.elts
    if isinstance(node, ast.Dict):
        return not node.keys
    return False


def _producers_proven_empty(tree: ast.Module) -> set[str]:
    """Producers this file asserts can return an empty collection."""
    proven: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        test = node.test
        if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq):
            if _is_empty_literal(test.comparators[0]):
                proven |= _callee_names(test.left)
    return proven


def _offenders() -> tuple[list[str], int, int]:
    offenders: list[str] = []
    files = _test_files()
    plugin_files = 0
    for path in sorted(files):
        rel = path.relative_to(REPO).as_posix()
        if rel.startswith("plugins/"):
            plugin_files += 1
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover - a broken test file is another guard's job
            continue

        proven = _producers_proven_empty(tree)
        if not proven:
            continue
        lines = source.split("\n")

        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not func.name.startswith("test_"):
                continue

            # local name -> the producer it was assigned from
            produced_by: dict[str, str] = {}
            for stmt in ast.walk(func):
                if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call):
                    call_func = stmt.value.func
                    producer = (
                        call_func.attr
                        if isinstance(call_func, ast.Attribute)
                        else getattr(call_func, "id", "")
                    )
                    for target in stmt.targets:
                        if isinstance(target, ast.Name):
                            produced_by[target.id] = producer

            body_src = ast.unparse(func)
            for loop in ast.walk(func):
                if not isinstance(loop, (ast.For, ast.AsyncFor)):
                    continue
                if not any(isinstance(x, ast.Assert) for x in ast.walk(loop)):
                    continue
                if _EXEMPT_MARKER in lines[loop.lineno - 1]:
                    continue

                if isinstance(loop.iter, ast.Name):
                    name = loop.iter.id
                    if produced_by.get(name) not in proven:
                        continue
                    # anything establishing non-emptiness clears it
                    if f"assert {name}" in body_src or f"len({name})" in body_src:
                        continue
                    detail = f"for … in {name} = {produced_by[name]}()"
                elif isinstance(loop.iter, ast.Call):
                    if not (_callee_names(loop.iter) & proven):
                        continue
                    detail = f"for … in {ast.unparse(loop.iter)[:56]}"
                else:
                    continue

                offenders.append(f"{rel}:{loop.lineno}  {func.name}\n        {detail}")
    return offenders, len(files), plugin_files


def test_no_loop_asserts_over_a_producer_proven_to_return_nothing():
    # Anchor REPO structurally before counting anything: a miscomputed root makes every
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
        "these loops assert over a producer that this same file proves can return an empty "
        "collection, with nothing checking it is non-empty — so a producer that silently "
        "stops returning anything satisfies them:\n  "
        + "\n  ".join(offenders)
        + "\n\nBind the result and `assert <name>` before the loop. If the loop legitimately "
        f"may iterate zero times, put `# {_EXEMPT_MARKER}` on the `for` line with a reason."
    )
