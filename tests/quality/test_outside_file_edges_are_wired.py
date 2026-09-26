"""A test whose subject is a real file outside ``core/navig/`` must be selected from it.

The gate's test selection matches changed ``core/navig/**`` MODULES against test names.
So a pytest file that reads a real repo file *outside* that tree can only ever be
triggered from the core side — editing the file it actually guards runs it in NO tier.
``ci-local.mjs`` already carries hand-written ``INVARIANT_GUARDS`` edges for exactly
this ("Write a core test that reads a path outside ``core/`` → add the edge", CLAUDE.md),
and the edges work. What was missing is the thing that notices when one is absent: the
subject path is written twice — once in the test, once in the runner — and nothing
compared the two copies.

Measured when this was written: **15 (test, subject) pairs**, 12 already wired, and
**3 unwired, all three real**:

  * ``core/pytest.ini`` -> ``tests/core/test_tmp_dir_concurrency.py``. That file once
    pinned ``--basetemp=.dev/tmp/pytest``, which WIPES the directory it points at, so
    starting a run deleted the temp dirs of runs already in flight and turned every
    concurrent push into a false failure.
  * ``core/generated/commands.json`` -> ``tests/commands/test_command_registry_export.py``
    -- the shipped manifest, which had 34 working commands missing from it two commits
    before this one.
  * ``.claude/settings.json`` -> ``tests/repo/test_guard_installer.py``, which asserts
    this repo's own hook wiring is committed and PORTABLE (an absolute path in it works
    here and breaks every other clone).

Two more looked unwired and were not, which is why the reader below understands a
DIRECTORY prefix: the repo guard's ``when`` is ``f.startsWith("scripts/agent-hooks/")``,
so an exact-match-only version called ``agent_lock.py`` and ``session_start.py`` holes.
Fixing the detector rather than waving the pair through beside three real findings is
the whole difference between a guard and a baseline.

Subjects are derived two ways, because a test binds itself to a file two ways. It
READS one (a path literal rooted in ``__file__``), or it IMPORTS one -- and the import
shape is where the most important subject of all lives: ``core/tests/conftest.py``, the
single file every test in this repo depends on. Neither mechanism could see it (not a
module, not a ``test_*.py``), so both of its guards -- the env-leak fixture and the
amortised GC policy that took the suite from 73m to 11m -- ran in no tier.

The conftest pair is ALSO asserted, narrowly, by
``test_source_guards_are_wired.py`` (landed independently as #1341 while this was being
written -- two sessions found the same hole the same afternoon, which is its own argument
for deriving the class rather than noticing instances of it). That one answers "is the
conftest edge present"; this one answers "is ANY subject's edge missing", and found five
more. The overlap is one assertion and costs nothing; the narrow test is left alone
rather than deleted out from under the session that shipped it.

This checks that every derived pair is DECLARED. Whether a declared edge really fires is
pinned separately, by driving the runner itself in
``scripts/test/ci-local-selection.test.mjs`` — completeness here, behaviour there.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

CORE_TESTS = Path(__file__).resolve().parents[1]
REPO = CORE_TESTS.parents[1]
CI_RUNNER = REPO / "scripts" / "ci-local.mjs"


def _reachable_without_an_edge(rel: str) -> bool:
    """Can the selection already reach a change to this file, with no edge declared?

    Two ways, and the second has a hole worth spelling out. `core/navig/**` is reached by
    the module-name match. A changed `test_*.py` is always run — but that is a rule about
    TEST FILES, not about the tests tree: `core/tests/conftest.py` is neither a module nor
    a test file, so both mechanisms miss it, and it is the one file every test depends on.
    """
    if rel.startswith("core/navig/"):
        return True
    return rel.startswith("core/tests/") and Path(rel).name.startswith("test_")


# Pairs that deliberately have no edge, each with a reason. Keep this empty unless there
# is a real one — an entry here is a guard that a change to its own subject does not run.
NOT_WIRED_ON_PURPOSE: dict[tuple[str, str], str] = {}


def _parents_index(node: ast.AST) -> int | None:
    """``Path(__file__).resolve().parents[N]`` -> ``N``."""
    if not isinstance(node, ast.Subscript):
        return None
    val = node.value
    if not (isinstance(val, ast.Attribute) and val.attr == "parents"):
        return None
    if "__file__" not in ast.unparse(val):
        return None
    idx = node.slice
    if isinstance(idx, ast.Constant) and isinstance(idx.value, int):
        return idx.value
    return None


def _flatten(node: ast.AST) -> tuple[str, list[str]] | None:
    """``NAME / "a" / "b"`` -> ``("NAME", ["a", "b"])``; anything else -> ``None``.

    The chain matters. ``_REPO / "apps" / "deck" / "lib" / "api.ts"`` parses as nested
    ``BinOp``s, so a version that matched only ``Name / Constant`` saw ``_REPO / "apps"``
    — a DIRECTORY — and every subject collapsed to its top-level folder.
    """
    segs: list[str] = []
    cur = node
    while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div):
        seg = cur.right
        if not (isinstance(seg, ast.Constant) and isinstance(seg.value, str)):
            return None
        segs.append(seg.value)
        cur = cur.left
    if not isinstance(cur, ast.Name) or not segs:
        return None
    return cur.id, list(reversed(segs))


def _string_sequence(node: ast.AST) -> list[str]:
    """A tuple/list of string literals, as a list. Anything else -> ``[]``.

    Only literals count. A sequence built by a comprehension or a call could name
    anything, and guessing would put paths into the derivation that the file never
    actually reads.
    """
    if not isinstance(node, (ast.Tuple, ast.List)):
        return []
    out: list[str] = []
    for el in node.elts:
        if not (isinstance(el, ast.Constant) and isinstance(el.value, str)):
            return []                        # a mixed sequence is not a path list
        out.append(el.value)
    return out


def _expand_indirect(
    node: ast.AST, roots: dict[str, Path], literals: dict[str, list[str]]
) -> list[Path]:
    """``<root> / <name>`` where *name* is a module-level tuple of path literals.

    The literal-chain walk requires every segment to be a constant, so a parametrised
    guard -- which names its subjects once in a tuple and joins them through a loop
    variable -- is invisible to it. Deliberately NOT resolving the loop variable itself:
    the tuple is the declaration, and `for rel in X` / `parametrize("rel", X)` both read
    from it, so expanding the tuple against the root covers either spelling without
    having to model control flow.
    """
    if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
        return []
    left, right = node.left, node.right
    if not (isinstance(left, ast.Name) and left.id in roots):
        return []
    if not isinstance(right, ast.Name):
        return []
    base = roots[left.id]
    # A name used as a path segment may be the loop variable rather than the sequence,
    # so accept EITHER: the sequence itself, or any sequence in the module when the name
    # is not one. The `is_file()` filter downstream is what keeps this honest -- a wrong
    # guess simply resolves to nothing that exists.
    seqs = [literals[right.id]] if right.id in literals else list(literals.values())
    return [base.joinpath(*rel.split("/")) for seq in seqs for rel in seq]


def _subjects(path: Path) -> set[str]:
    """Every repo file ``path`` binds itself to that the selection cannot already reach.

    Two shapes, unioned. READ: a path rooted in ``__file__`` and required to EXIST as a
    file on disk — both halves load bearing, since ``tmp_path / "plugins" / "x"`` is a
    fixture building a fake tree while ``REPO / "plugins"`` is the real one, textually
    identical, which is why this walks the AST; and requiring a *file* rather than a
    directory is what separates a subject the runner can key an edge on from a tree a
    scanner merely walks. IMPORT: ``from tests import conftest`` and friends, where no
    path literal exists to find (see ``_imported_test_infrastructure``).
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:                         # not our problem; the suite will say so
        return set()
    # Pre-filter: only `parents[N]` roots this file. 135 of 1639 test files contain it, so
    # skipping the parse for the rest takes the derivation from 6.4s to well under one —
    # material for a guard that runs on every change.
    #
    # It loses nothing MEASURED. The other way to reach a repo root is
    # `Path(__file__).parent.parent`, used by 32 files, and exactly one of those also
    # names a directory outside core/ — in a DOCSTRING, quoting the product bug the test
    # is about (`_get_scripts_dir` resolving inside the package). Zero real subjects.
    roots_a_path = "parents[" in text
    # The import markers are the literal spellings `_imported_test_infrastructure`
    # recognises. A looser `"tests" in text` matches nearly every file in a directory
    # called tests/ and put the parse back for all of them: 3.7s -> 9.3s, for nothing.
    imports_infra = "from tests" in text or "import tests" in text
    if not roots_a_path and not imports_infra:
        return set()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    if not roots_a_path:                     # import-derived subjects only
        return _imported_test_infrastructure(tree)

    roots: dict[str, Path] = {}
    literals: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        n = _parents_index(node.value)
        if n is not None:
            roots[target.id] = path.resolve().parents[n]
            continue
        flat = _flatten(node.value)         # ROOT = <known root> / "x"
        if flat and flat[0] in roots:
            roots[target.id] = roots[flat[0]].joinpath(*flat[1])
            continue
        lits = _string_sequence(node.value)  # NAMES = ("a/b.sh", "c/d.ps1")
        if lits:
            literals[target.id] = lits

    found: set[str] = set()
    for node in ast.walk(tree):
        # `REPO / rel` where `rel` ranges over a module-level tuple of path literals.
        # This is not an exotic spelling -- it is how a PARAMETRISED guard addresses its
        # subjects, and `test_installer_no_bom.py` is the case that matters: its
        # `PIPED_INSTALLERS` names `web/www/public/install.sh`, the file navig.run serves,
        # and the literal-chain walk below could not see a single one of them.
        expanded = _expand_indirect(node, roots, literals)
        for joined in expanded:
            if not joined.is_file():
                continue
            try:
                rel = joined.resolve().relative_to(REPO).as_posix()
            except ValueError:
                continue
            if not _reachable_without_an_edge(rel):
                found.add(rel)

        flat = _flatten(node)
        if not flat or flat[0] not in roots:
            continue
        joined = roots[flat[0]].joinpath(*flat[1])
        if not joined.is_file():
            continue
        try:
            rel = joined.resolve().relative_to(REPO).as_posix()
        except ValueError:                  # outside the repo entirely
            continue
        if _reachable_without_an_edge(rel):
            continue
        found.add(rel)

    found |= _imported_test_infrastructure(tree)
    return found


def _imported_test_infrastructure(tree: ast.Module) -> set[str]:
    """Subjects reached by IMPORT rather than by path: ``from tests import conftest``.

    The other derivation asks which repo file a test READS. A guard whose subject is test
    infrastructure imports it instead, so no path literal exists to find -- and this is
    the shape that matters most, because `core/tests/conftest.py` is the single file
    every test in the repo depends on and neither selection mechanism can see it.

    Only non-``test_*`` modules under ``tests/`` count: importing a sibling test file is
    ordinary, and that file is already always-run when it changes.
    """
    found: set[str] = set()
    dotted: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module == "tests":
                dotted.update(f"tests.{a.name}" for a in node.names)
            elif node.module.startswith("tests."):
                dotted.add(node.module)
        elif isinstance(node, ast.Import):
            dotted.update(a.name for a in node.names if a.name.startswith("tests."))

    for name in dotted:
        parts = name.split(".")[1:]           # drop the leading `tests`
        if not parts or parts[-1].startswith("test_"):
            continue
        for candidate in (
            CORE_TESTS.joinpath(*parts).with_suffix(".py"),
            CORE_TESTS.joinpath(*parts, "__init__.py"),
        ):
            if candidate.is_file():
                found.add(candidate.resolve().relative_to(REPO).as_posix())
                break
    return found


def _pairs() -> set[tuple[str, str]]:
    """Every (test path, subject path) the derivation finds, as the runner spells them."""
    out: set[tuple[str, str]] = set()
    for path in sorted(CORE_TESTS.rglob("test_*.py")):
        if "__pycache__" in path.parts:
            continue
        name = f"tests/{path.relative_to(CORE_TESTS).as_posix()}"
        for subject in _subjects(path):
            out.add((name, subject))
    return out


def _runner_source() -> str:
    return CI_RUNNER.read_text(encoding="utf-8")


def _always_run_tests() -> set[str]:
    """`sourceGuardArgs` — the guards that run on EVERY change, edge or no edge."""
    src = _runner_source()
    # Terminator anchored to the start of a line: a bare `];` matched anywhere would end
    # the block at the first one appearing INSIDE it, and everything below would read as
    # unwired. That is not hypothetical — it happened to this list once, silently.
    block = re.search(r"const sourceGuardArgs\s*=\s*\[(.*?)^\];", src, re.S | re.M)
    assert block, (
        f"could not find `const sourceGuardArgs = [...]` in {CI_RUNNER} — the runner was "
        "restructured and this guard is now checking nothing."
    )
    return set(re.findall(r'"(tests/[^"]+\.py)"', block.group(1)))


def _declared_edges() -> set[tuple[str, str]]:
    """(test, subject) pairs an ``INVARIANT_GUARDS`` entry puts together.

    Entry-scoped on purpose. Asking only "do both strings appear in the file" would call
    a pair wired whenever some *other* entry happened to name the same test — which is
    every one of these tests, since each is named by the entry that wires its real edge.
    """
    src = _runner_source()
    block = re.search(r"const INVARIANT_GUARDS\s*=\s*\[(.*?)^\];", src, re.S | re.M)
    assert block, (
        f"could not find `const INVARIANT_GUARDS = [...]` in {CI_RUNNER} — the runner was "
        "restructured and this guard is now checking nothing."
    )
    edges: set[tuple[str, str]] = set()
    for entry in re.split(r"^  \{$", block.group(1), flags=re.M):
        tests = set(re.findall(r'"(tests/[^"]+\.py)"', entry))
        if not tests:
            continue
        # Every other quoted string in the entry is a path it keys on. Comments are part
        # of the entry text and quote paths too, which only ever ADDS candidate subjects
        # — and a subject named in a comment but not in the `when` is caught by the node
        # side, which drives the real selection rather than reading it.
        subjects = {s for s in re.findall(r'"([^"]+)"', entry) if s not in tests}
        for test in tests:
            for subject in subjects:
                edges.add((test, subject))
    return edges


def _is_declared(test: str, subject: str, declared: set[tuple[str, str]]) -> bool:
    """Does a declared edge cover this pair — exactly, or by directory prefix?

    A `when` may key on a DIRECTORY: the repo guard's is
    ``f.startsWith("scripts/agent-hooks/")``, which covers every hook in it. An
    exact-match-only reader called two of those hooks unwired — a false positive that
    would have been "fixed" by adding a redundant edge, or worse, waved through as noise
    together with the three real findings beside it. A quoted string ending in ``/`` is
    a prefix; nothing else is.
    """
    if (test, subject) in declared:
        return True
    return any(
        t == test and s.endswith("/") and subject.startswith(s) for t, s in declared
    )


def test_every_outside_subject_can_select_its_test() -> None:
    """Editing the file a test guards must run that test."""
    always = _always_run_tests()
    declared = _declared_edges()

    missing: list[str] = []
    for test, subject in sorted(_pairs()):
        if (test, subject) in NOT_WIRED_ON_PURPOSE:
            continue
        if test in always:                  # runs on every change; no edge needed
            continue
        if _is_declared(test, subject, declared):
            continue
        missing.append(f"  {subject}  ->  {test}")

    assert not missing, (
        "these tests read a real repo file that no selection mechanism can see, so "
        "editing that file runs them in NO tier:\n"
        + "\n".join(missing)
        + f"\n\nAdd an INVARIANT_GUARDS edge in {CI_RUNNER.relative_to(REPO).as_posix()} "
        "keyed on the file, pin it in scripts/test/ci-local-selection.test.mjs, or record "
        "a reason in NOT_WIRED_ON_PURPOSE."
    )


def test_the_detector_still_finds_something() -> None:
    """An empty derivation is green, and green is what a broken guard looks like."""
    pairs = _pairs()
    assert len(pairs) >= 22, (
        f"the subject derivation found only {len(pairs)} (test, subject) pairs — it found "
        "27 once the indirect shape was added (22 before). The AST idiom changed or the "
        "repo root moved, so this guard is checking little or nothing."
    )
    # One anchor per SHAPE. A floor on the total is satisfied by either derivation alone,
    # so the import half could break in silence while the path half carried the count.
    assert ("tests/quality/test_env_leak_guard.py", "core/tests/conftest.py") in pairs, (
        "the import-derived half no longer sees the shared conftest; it is broken."
    )
    # Anchor on a pair that exists for a documented reason: test_cors_parity reads the
    # Deck client because the client is the SOURCE of the requirement. If the derivation
    # stops seeing that, it has stopped seeing the shape.
    assert ("tests/gateway/test_cors_parity.py", "apps/deck/lib/api.ts") in pairs, (
        "the derivation no longer sees a known cross-language subject; it is broken."
    )
    # The THIRD shape: a subject named in a module-level tuple and joined through a loop
    # variable, which is how a parametrised guard addresses its files. This one matters
    # more than the count -- `web/www/public/install.sh` is the file navig.run serves, and
    # for as long as this shape was invisible the derivation could not see it at all.
    assert (
        "tests/packaging/test_installer_no_bom.py",
        "web/www/public/install.sh",
    ) in pairs, "the indirect (tuple-of-literals) half is broken; it sees no piped installer."
    assert len(_declared_edges()) >= 10, (
        "INVARIANT_GUARDS parsed into almost no edges — the parse broke, and every pair "
        "would read as unwired."
    )


def test_exemptions_are_real() -> None:
    """An exemption for a pair that no longer exists quietly grants itself forever.

    Two ways to go stale, and they fail differently. A named FILE can be deleted or
    renamed — then the entry describes nothing. Or the pair can stop being derived at all
    (the test drops the import, the subject moves under ``core/navig``) — then the entry
    is dead weight that still reads as a considered decision. Both are ghosts.
    """
    pairs = _pairs()
    ghosts: list[str] = []
    for test, subject in sorted(NOT_WIRED_ON_PURPOSE):
        if not (CORE_TESTS.parent / test).is_file():
            ghosts.append(f"{test} (no such test file)")
        elif not (REPO / subject).is_file():
            ghosts.append(f"{subject} (no such subject file)")
        elif (test, subject) not in pairs:
            ghosts.append(f"{subject} -> {test} (no longer derived)")
    assert not ghosts, (
        "NOT_WIRED_ON_PURPOSE names pairs that no longer exist — delete them:\n  "
        + "\n  ".join(ghosts)
    )
