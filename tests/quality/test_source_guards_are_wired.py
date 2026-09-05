"""Every whole-tree source guard must actually be executed by something.

A guard that scans the source tree names no module. `tests for changed modules` — the
fast pre-push gate's only behaviour step — maps a changed module to the test files that
mention it, so it can NEVER select one of these. And the fast profile skips the pytest
suite. So a guard that is not listed in `ci-local.mjs`'s `sourceGuardArgs` runs ONLY in
the ~56-minute full suite: it can go red on main and sit there unnoticed, which is
precisely how #723 shipped one.

The guards themselves were each written to close a bug class permanently — unscoped
process kills that shot the operator's live daemon, defeated timeouts, orphaned
subprocesses, config wipes. A guard nobody runs closes nothing.

This is the meta-guard: it fails when a tree-scanning guard exists but is not wired in.

Wiring the backlog cost +14s, measured (5 files / 33 tests / 22.3s -> 22 files / 116
tests / 35.9s): the import-bound dangling-import pair dominates and the AST-only guards
fan out into otherwise-idle xdist workers, but it is not free. If the step ever crowds
the gate, split the import-bound pair out rather than dropping guards — a targeted gate
beats a thorough one nobody runs.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

CORE_TESTS = Path(__file__).resolve().parents[1]
REPO = CORE_TESTS.parents[1]
CI_RUNNER = REPO / "scripts" / "ci-local.mjs"

# EVERY test file is a candidate — the property that makes something a source guard is its
# SHAPE (it walks the tree and derives its own root), not the folder it happens to sit in.
#
# This used to be `GUARD_DIRS = ("quality", "config")`, so the meta-guard whose entire job is
# "no whole-tree guard goes unwired" was looking in two of the ~90 test directories. Its
# NOT_WIRED_ON_PURPOSE map was empty because it was not looking, not because nothing was
# missing: widening the scan surfaced 15 more, and FOUR of them ran in neither wiring
# mechanism — including `test_gateway_telegram_import_boundaries.py` (an architectural
# invariant any gateway file can break) and `test_package_data.py` (whose own docstring
# records the curated glob list once missing 64 assets that then never reached the wheel).
#
# Same mistake the guards themselves keep finding: a check bound to a DIRECTORY protects one
# directory, not the invariant. Derive the scope from the shape that makes it the surface.
TESTS_ROOT = CORE_TESTS

# A guard "scans the source tree" if it WALKS .py files under a root it derived from its
# own location.
#
# This used to require `parents[N] / "navig"` on ONE line, which is only the most common
# spelling of that. Extracting the root into a variable —
#
#     _CORE = Path(__file__).resolve().parents[2]
#     _NAVIG_ROOT = _CORE / "navig"
#
# is an ordinary refactor, and it made the guard invisible here: undetected means
# unlisted, unlisted means it runs only in the full suite, which is exactly the #737 bug
# this file exists to prevent. Broadening it (walks .py + derives a root) took the
# detected set 23 -> 29 with nothing lost, and immediately surfaced a real guard nobody
# had wired.
#
# Err toward noise: a false positive costs one line in `sourceGuardArgs` or one documented
# exemption. A false negative is a guard that silently never runs.
_WALKS_PY = re.compile(r"\b(rglob|glob)\s*\(")
_DERIVES_A_ROOT = re.compile(r'/\s*"(navig|plugins)"|parents\[\d+\]')


def _scans_source(text: str) -> bool:
    return bool(_WALKS_PY.search(text) and _DERIVES_A_ROOT.search(text))


# ── does this file's SUBJECT reach into the real plugins/ tree? ──────────────────
#
# AST, not a regex, and the difference is the whole check: `tmp_path / "plugins"` is a
# FIXTURE (six tests build a fake plugin dir that way) while `REPO / "plugins"` is the
# real tree. Textually they are the same three tokens. A regex version of this demanded
# wiring for all six — measured, before switching to the AST.
#
# Deliberately does NOT require a glob. `test_command_providers_fresh.py` delegates its
# walk to a subprocess (`scripts/gen_command_providers.py --check` over
# `plugins/*/pyproject.toml`), so it looks like an ordinary test to `_scans_source` and
# ran only in the full suite — while being the one guard whose subject is EXCLUSIVELY
# plugins. What decides wiring is what a change to the tree can break, not which idiom
# the file happens to use to read it.
_PLUGIN_DIR_NAMES = {"plugins", "private"}


def _repo_root_names(tree: ast.Module) -> set[str]:
    """Module-level names bound to a path derived from ``__file__``.

    Transitive: ``CORE = Path(__file__)...parents[2]`` then ``PLUGINS = CORE.parents[1]``
    is the idiom two of these guards already use.
    """
    names: set[str] = set()
    for _ in range(3):                      # fixpoint; 3 hops is more than any guard uses
        grew = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            src = ast.dump(node.value)
            derived = "__file__" in src or any(f"id='{n}'" in src for n in names)
            if not derived:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id not in names:
                    names.add(target.id)
                    grew = True
        if not grew:
            break
    return names


def _subject_includes_plugins(text: str) -> bool:
    try:
        tree = ast.parse(text)
    except SyntaxError:                     # not our problem; the suite will say so
        return False
    roots = _repo_root_names(tree)
    if not roots:
        return False

    def rooted(node: ast.AST) -> bool:
        src = ast.dump(node)
        return "__file__" in src or any(f"id='{n}'" in src for n in roots)

    for node in ast.walk(tree):
        # <repo-root expr> / "plugins"
        if (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Div)
            and isinstance(node.right, ast.Constant)
            and node.right.value in _PLUGIN_DIR_NAMES
            and rooted(node.left)
        ):
            return True
        # <repo-root expr>.glob("navig-*...")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("glob", "rglob")
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value.startswith("navig-")
            and rooted(node.func.value)
        ):
            return True
    return False

# Guards that are deliberately NOT in the fast gate, each with a reason. Keep this
# empty unless there is a real one — an exception here is a guard that does not run.
NOT_WIRED_ON_PURPOSE: dict[str, str] = {
    "tests/blocks/test_blocks.py": (
        "not a guard: a 62-test Blocks feature suite (30.8s) in which ONE test happens to "
        "glob BLOCK.md files. The detector errs toward noise on purpose, and this is the "
        "noise — wiring it would put half a minute of feature tests in the always-on step "
        "for an invariant the rest of the file does not assert."
    ),
}

# `isTreeScanner` in ci-local.mjs, transcribed. Guards it matches are run by the
# INVARIANT_GUARDS "any core/navig change" rule, which is a SECOND way to be wired — this
# meta-guard used to know only about sourceGuardArgs, so widening the scan above would
# otherwise have demanded duplicate wiring for 11 files that already run.
_CI_RGLOB = ".rglob("
_CI_ROOT = re.compile(r'navig\.__file__|parents\[\d\]\s*/\s*"navig"')


def _runs_via_tree_scanner_rule(text: str) -> bool:
    return _CI_RGLOB in text and bool(_CI_ROOT.search(text))


def test_the_transcribed_detector_still_matches_the_runner() -> None:
    """The check above is a COPY of the runner's rule, so it can drift from it.

    Drift is silent in the dangerous direction: if the runner's detector is narrowed and
    this copy is not, a guard stops running while this file still calls it wired. Pin the
    runner's source so any edit to it fails here and forces a re-read.
    """
    src = CI_RUNNER.read_text(encoding="utf-8")
    body = re.search(r"const isTreeScanner = \(txt\) =>(.*?);", src, re.S)
    assert body, "isTreeScanner is gone from ci-local.mjs — re-derive _runs_via_tree_scanner_rule"
    text = " ".join(body.group(1).split())
    assert ".rglob(" in text, text
    # The runner spells it as a JS regex literal, so the source really contains the
    # backslashes: /parents\[\d\]\s*\/\s*"navig"/
    assert "navig.__file__" in text and r"parents\[\d\]" in text, (
        "ci-local.mjs's isTreeScanner no longer matches the rule transcribed here:\n  "
        f"{text}\nRe-read it and update _runs_via_tree_scanner_rule, or a guard it stopped "
        "selecting will be reported as wired."
    )


def _listed_in_ci() -> set[str]:
    """The test paths inside `const sourceGuardArgs = [ ... ];`."""
    src = CI_RUNNER.read_text(encoding="utf-8")
    block = re.search(r"const sourceGuardArgs\s*=\s*\[(.*?)\];", src, re.S)
    assert block, (
        f"could not find `const sourceGuardArgs = [...]` in {CI_RUNNER} — the runner was "
        "restructured and this meta-guard is now checking nothing."
    )
    return set(re.findall(r'"(tests/[^"]+\.py)"', block.group(1)))


def _tree_scanning_guards() -> set[str]:
    """Every test file whose SHAPE is a whole-tree scanner, wherever it lives."""
    found: set[str] = set()
    for path in sorted(TESTS_ROOT.rglob("test_*.py")):
        if "__pycache__" in path.parts:
            continue
        if _scans_source(path.read_text(encoding="utf-8-sig")):
            found.add(f"tests/{path.relative_to(TESTS_ROOT).as_posix()}")
    return found


def _wired_by_the_runner() -> set[str]:
    """Guards the runner reaches by EITHER mechanism: the explicit list, or the derived
    tree-scanner rule that fires on any ``core/navig/`` change."""
    wired = set(_listed_in_ci())
    for path in sorted(TESTS_ROOT.rglob("test_*.py")):
        if "__pycache__" in path.parts:
            continue
        if _runs_via_tree_scanner_rule(path.read_text(encoding="utf-8-sig")):
            wired.add(f"tests/{path.relative_to(TESTS_ROOT).as_posix()}")
    return wired


def test_the_detectors_still_find_something() -> None:
    """An empty scan on either side is green, and green is what a broken meta-guard
    looks like."""
    # Floors move WITH the detector. 25 was set when the scan covered two directories and
    # found ~29; against the shape-derived scan's 49 it would let a near-halving pass in
    # silence, which is the failure mode this test exists for.
    assert len(_tree_scanning_guards()) >= 40, (
        "found far fewer tree-scanning guards than expected — the idiom changed or the "
        "tests moved, so this meta-guard is silently checking nothing."
    )
    assert len(_listed_in_ci()) >= 30, (
        "sourceGuardArgs came back much smaller than expected — the parse broke, or the "
        "step was gutted."
    )
    # The second wiring mechanism must be finding guards too: if the transcribed
    # tree-scanner rule matched nothing, every guard would look unwired and the fix for
    # that would be to add 40 duplicate entries to sourceGuardArgs.
    assert len(_wired_by_the_runner()) > len(_listed_in_ci()), (
        "the derived tree-scanner rule contributed nothing beyond the explicit list — "
        "_runs_via_tree_scanner_rule has drifted from ci-local.mjs."
    )


def test_every_tree_scanning_guard_runs_in_the_fast_gate() -> None:
    missing = sorted(_tree_scanning_guards() - _wired_by_the_runner() - set(NOT_WIRED_ON_PURPOSE))
    assert not missing, (
        "These guards scan the source tree, so `tests for changed modules` can never "
        "select them, and the fast profile skips pytest — they run ONLY in the ~56-minute "
        "full suite and can go red on main unnoticed:\n  "
        + "\n  ".join(missing)
        + f"\n\nAdd each to `sourceGuardArgs` in {CI_RUNNER.name}. Breadth is close to "
        "free there — the step is dominated by the import-bound dangling-import guards."
    )


def _plugin_subject_guards() -> set[str]:
    """Every test file whose subject reaches into the real ``plugins/`` tree."""
    found: set[str] = set()
    for path in sorted(TESTS_ROOT.rglob("test_*.py")):
        if "__pycache__" in path.parts:
            continue
        if _subject_includes_plugins(path.read_text(encoding="utf-8-sig")):
            found.add(f"tests/{path.relative_to(TESTS_ROOT).as_posix()}")
    return found


def test_a_plugin_scanning_guard_is_wired_where_a_plugin_change_can_reach_it() -> None:
    """The two wiring mechanisms are NOT interchangeable, and the difference is scope.

    ``sourceGuardArgs`` runs in the fast profile for every change. The tree-scanner rule
    runs through ``INVARIANT_GUARDS``' ``when: f.startsWith("core/navig/")`` — so it fires
    only when a CORE file changes. For a guard that scans navig/ alone that is correct:
    if navig did not change, the guard cannot newly fail.

    For a guard that also scans ``plugins/`` it is backwards. A plugin-only change
    triggers neither that rule nor the pytest selection, which runs the changed plugin's
    OWN suite rather than core's. Measured before this test existed:

        node scripts/ci-local.mjs --explain-selection \\
            --changed=plugins/navig-games/navig_games/engine/library/gog.py
        → exactly ONE core guard (tests/quality/test_rich_markup_renders.py)

    Four guards were wired that way — console_helper API drift, phantom command hints,
    event-loop blocking, and the command→provider map — each reachable only from the one
    direction that cannot break it. So this check deliberately consults ONLY the explicit
    list; being a detected tree scanner does not count here.
    """
    missing = sorted(_plugin_subject_guards() - _listed_in_ci() - set(NOT_WIRED_ON_PURPOSE))
    assert not missing, (
        "These guards read the plugins/ tree, but are not in `sourceGuardArgs` — the only "
        "mechanism a PLUGIN change triggers. A plugin edit runs that plugin's own suite "
        "plus the guard list; the tree-scanner rule fires on core/navig changes only, so "
        "these run against plugins in the ~56-minute full suite alone:\n  "
        + "\n  ".join(missing)
        + f"\n\nAdd each to `sourceGuardArgs` in {CI_RUNNER.name}, or record why not in "
        "NOT_WIRED_ON_PURPOSE."
    )


def test_the_plugin_subject_detector_tells_a_fixture_from_the_real_tree() -> None:
    """The discriminator, named rather than left implicit.

    ``tmp_path / "plugins"`` and ``REPO / "plugins"`` are the same three tokens, so a
    textual version of this check flagged six fixture-building tests and demanded they be
    wired into the pre-push gate. Both directions are pinned here: the fixture file must
    stay out, and a real scanner must stay in, or the check above is either noise or
    nothing.
    """
    fixture = TESTS_ROOT / "cli" / "test_fast_help_output.py"
    real = TESTS_ROOT / "quality" / "test_no_defeated_timeout.py"
    assert fixture.is_file() and real.is_file(), "sample files moved — repoint this test"
    assert not _subject_includes_plugins(fixture.read_text(encoding="utf-8-sig")), (
        f"{fixture.name} only builds a fake plugin dir under tmp_path; treating that as "
        "the real tree puts unrelated tests in the always-on gate"
    )
    assert _subject_includes_plugins(real.read_text(encoding="utf-8-sig")), (
        f"{real.name} scans plugins/ for real and must be detected"
    )


def test_the_plugin_subject_detector_still_finds_something() -> None:
    """Anti-vacuity: an empty set makes the test above green while checking nothing."""
    found = _plugin_subject_guards()
    assert len(found) >= 20, (
        f"only {len(found)} plugin-scanning guards found — the idiom for reaching the "
        "plugin tree changed, so the check above is silently passing."
    )
    # And it must not match everything: a detector that flagged every rooted test file
    # would demand the whole suite be wired, the other way to make this meaningless.
    #
    # 45 is measured, not a round number. Today 22 files match; 90 derive a repo root at
    # all; 1461 test files exist. A bound expressed against the total (`< total // 10`
    # = 146) was slack enough to sit ABOVE the greedy case — verified by mutation: a
    # detector short-circuited to `return True` reported ~90 and sailed through it. 45
    # leaves room for this set to double and still fails that mutation.
    assert len(found) < 45, (
        f"{len(found)} test files matched — the plugin-subject detector has become too "
        "broad to mean anything (it should find guards, not every file with a repo root)."
    )


# -- does this file's SUBJECT reach a navig/ path the SELECTOR CANNOT MAP? --------
#
# The two detectors above both require a glob, and so does the runner's `isTreeScanner`.
# A guard that reads ONE path by name is invisible to all three -- and whether that
# matters depends on something none of them looks at: whether the path is a MODULE.
#
# `tests for changed modules` maps a changed core module onto test files that mention it.
# A test whose subject is `navig/gateway/channels/away_summary.py` is therefore selected
# fine (measured: RUN). A test whose subject is `navig/resources/SOUL.default.md` is
# selected by NOTHING -- an asset has no module name to match on.
#
# Measured with the runner itself, one probe per subject
# (`ci:explain --changed=<that subject>`), which is the only honest way to ask this:
#
#     ABSENT  test_guardrail_floor               <- navig/resources/SOUL.default.md
#     ABSENT  test_help_system                   <- navig/help/
#     ABSENT  test_cortex_engine                 <- navig/browser/templates/example-app.yaml
#     ABSENT  test_bay_catalog_artifact          <- navig/data/bay-catalog.json
#     ABSENT  test_baselined_call_sites_now_work <- navig/  (the tree root itself)
#     RUN     test_persona_reaches_prompt        <- navig/gateway/channel_router.py
#     RUN     test_away_summary                  <- navig/gateway/channels/away_summary.py
#     RUN     test_telegram_no_loop_blocking     <- navig/gateway/channels/telegram_commands.py
#     RUN     test_tray_uninstall_removes_autostart <- navig/commands/tray.py
#
# `SOUL.default.md` is the guardrail floor -- the safety rules the agent inherits whatever
# identity a user writes. Editing it selected zero tests.
#
# This detector reproduces that ABSENT set exactly, which is what makes it trustworthy:
# it was validated against the runner's own answer, not against my reading of the code.
_ASSET_SUBJECT_ROOT = "navig"


def _path_chains(tree: ast.Module) -> list[tuple[ast.AST, list[str]]]:
    """Every MAXIMAL ``base / "a" / "b"`` chain, as (base expr, string parts).

    Maximal matters, and is the whole subtlety: ``ast.walk`` also yields every inner
    chain, so ``.../navig/gateway/channels/away_summary.py`` contains sub-chains ending
    at "gateway" and "channels". Judged on those, EVERY path looks like an asset -- the
    first version of this matched all 10 candidates instead of the 5 real ones.
    """
    inner = {id(n.left) for n in ast.walk(tree)
             if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)}
    chains: list[tuple[ast.AST, list[str]]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
            continue
        if id(node) in inner:               # an intermediate directory, not the subject
            continue
        parts: list[str] = []
        cur: ast.AST = node
        while (
            isinstance(cur, ast.BinOp)
            and isinstance(cur.op, ast.Div)
            and isinstance(cur.right, ast.Constant)
            and isinstance(cur.right.value, str)
        ):
            parts.append(cur.right.value)
            cur = cur.left
        parts.reverse()
        if _ASSET_SUBJECT_ROOT in parts:
            chains.append((cur, parts[parts.index(_ASSET_SUBJECT_ROOT):]))
    return chains


def _subject_is_unselectable_navig_path(text: str) -> bool:
    """True when the file reads a navig/ path that no module name can select."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    roots = _repo_root_names(tree)
    if not roots:
        return False

    def rooted(node: ast.AST) -> bool:
        src = ast.dump(node)
        return "__file__" in src or any(f"id='{n}'" in src for n in roots)

    for base, parts in _path_chains(tree):
        if not rooted(base):
            continue
        leaf = parts[-1]
        # the tree root itself, or an asset (.md / .yaml / .json / a directory)
        if leaf == _ASSET_SUBJECT_ROOT or not leaf.endswith(".py"):
            return True
    return False


def _navig_asset_subject_guards() -> set[str]:
    found: set[str] = set()
    for path in sorted(TESTS_ROOT.rglob("test_*.py")):
        if "__pycache__" in path.parts:
            continue
        if _subject_is_unselectable_navig_path(path.read_text(encoding="utf-8-sig")):
            found.add(f"tests/{path.relative_to(TESTS_ROOT).as_posix()}")
    return found


def test_a_guard_over_a_navig_asset_is_wired_explicitly() -> None:
    """An asset under core/navig/ has no module name, so nothing selects its tests."""
    missing = sorted(
        _navig_asset_subject_guards() - _wired_by_the_runner() - set(NOT_WIRED_ON_PURPOSE)
    )
    assert not missing, (
        "These tests assert over a navig/ path that is NOT a Python module -- an asset, a "
        "directory, or the tree root. `tests for changed modules` matches on module "
        "names, so editing that asset selects them in no tier and they run only in the "
        "~56-minute full suite:\n  "
        + "\n  ".join(missing)
        + f"\n\nAdd each to `sourceGuardArgs` in {CI_RUNNER.name}, or record why not in "
        "NOT_WIRED_ON_PURPOSE."
    )


def test_the_asset_subject_detector_tells_an_asset_from_a_module() -> None:
    """The discriminator, pinned in both directions.

    Both sample files read exactly one path under ``navig/`` from a ``__file__``-derived
    root; the ONLY difference is that one subject is a module the selector can match on
    and the other is shipped data. If this stops discriminating, the check above becomes
    either noise (demanding half the suite be wired) or nothing.
    """
    asset = TESTS_ROOT / "gateway" / "test_bay_catalog_artifact.py"
    module = TESTS_ROOT / "gateway" / "test_away_summary.py"
    assert asset.is_file() and module.is_file(), "sample files moved -- repoint this test"
    assert _subject_is_unselectable_navig_path(asset.read_text(encoding="utf-8-sig")), (
        f"{asset.name} asserts over navig/data/bay-catalog.json, which no module name "
        "can select -- it must be detected"
    )
    assert not _subject_is_unselectable_navig_path(module.read_text(encoding="utf-8-sig")), (
        f"{module.name}'s subject is a real module (away_summary.py), which the ranked "
        "selection already finds -- flagging it would put ordinary unit suites in the gate"
    )


def test_the_asset_subject_detector_still_finds_something() -> None:
    """Anti-vacuity, both ends."""
    found = _navig_asset_subject_guards()
    assert len(found) >= 40, (
        f"only {len(found)} navig-asset subjects found (60 when written) -- the idiom for "
        "reaching a shipped asset changed, so the check above is silently passing."
    )
    # Ceiling catches the greedy mutation, and the number is MEASURED, not estimated:
    # a detector short-circuited to `return True` reports 133 (verified by mutation --
    # I had guessed ~90). 80 sits well below that while leaving today's 60 room to grow.
    assert len(found) < 80, (
        f"{len(found)} test files matched -- the asset-subject detector has become too "
        "broad to mean anything (it should find guards, not every rooted test file)."
    )


# ── the general rule: a trigger per tree a guard READS ────────────────────────
#
# The three checks above each pin ONE direction: wired at all, wired for plugins, wired
# for a navig asset. Each was written after a guard was found unreachable from the one
# direction that could break it — plugins/ (#990), core/tools/ (#992), core/tests/ (#1190,
# which turned main RED: cleaning a Path.home() reference out of a test made that test's
# allowlist entry stale, and the guard that would have caught it fires on core/navig only).
#
# Three instances of one class is a rule, not a coincidence. This derives the answer instead
# of listing it: resolve every root a guard actually WALKS, then require the gate to trigger
# on a change under each of them.


def _resolve_path_expr(node: ast.AST, here: Path, bound: dict[str, Path]) -> Path | None:
    """Resolve a path expression to a real directory, or None if it is not one.

    Handles the idioms these guards use: ``Path(__file__).resolve().parents[N]``, a chain of
    ``/ "name"`` segments, ``.parent``, and a module-level name bound to any of those.
    """
    if isinstance(node, ast.Name):
        return bound.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        base = _resolve_path_expr(node.left, here, bound)
        if base is not None and isinstance(node.right, ast.Constant):
            if isinstance(node.right.value, str):
                return base / node.right.value
        return None
    if isinstance(node, ast.Subscript):                      # <expr>.parents[N]
        value = node.value
        if isinstance(value, ast.Attribute) and value.attr == "parents":
            base = _resolve_path_expr(value.value, here, bound)
            index = node.slice
            if base is not None and isinstance(index, ast.Constant):
                if isinstance(index.value, int):
                    try:
                        return base.parents[index.value]
                    except IndexError:
                        return None
        return None
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        base = _resolve_path_expr(node.value, here, bound)
        return base.parent if base is not None else None
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("resolve", "absolute"):
            return _resolve_path_expr(func.value, here, bound)
        if isinstance(func, ast.Name) and func.id == "Path" and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Name) and arg.id == "__file__":
                return here
        return None
    return None


_WALK_CALLS = ("rglob", "glob", "iterdir")


def _subject_roots(path: Path, tree: ast.Module) -> set[str]:
    """Repo-relative roots this guard walks. Empty for a test that walks nothing real."""
    bound: dict[str, Path] = {}
    for _ in range(4):                       # fixpoint: roots defined in terms of roots
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name):
                    resolved = _resolve_path_expr(node.value, path, bound)
                    if resolved is not None:
                        bound[target.id] = resolved

    roots: set[str] = set()

    def record(expr: ast.AST) -> None:
        resolved = _resolve_path_expr(expr, path, bound)
        if resolved is None:
            return
        try:
            rel = resolved.resolve().relative_to(REPO.resolve()).as_posix()
        except ValueError:                   # outside the repo (tmp_path fixtures)
            return
        roots.add("" if rel == "." else rel)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in _WALK_CALLS:
                record(node.func.value)
            elif node.func.attr == "walk":   # os.walk(root)
                for arg in node.args:
                    record(arg)
    return roots


def _invariant_guard_triggers() -> list[tuple[set[str], set[str]]]:
    """``INVARIANT_GUARDS`` as (path prefixes it fires on, test files it then runs)."""
    src = CI_RUNNER.read_text(encoding="utf-8")
    block = re.search(r"const INVARIANT_GUARDS = \[(.*?)\n\];", src, re.S)
    assert block, (
        f"could not find `const INVARIANT_GUARDS = [...]` in {CI_RUNNER} — the runner was "
        "restructured and this check is now reading nothing."
    )
    entries: list[tuple[set[str], set[str]]] = []
    pairs = re.findall(r"when:\s*\(f\)\s*=>(.*?)tests:\s*\[(.*?)\]", block.group(1), re.S)
    for when, tests in pairs:
        prefixes = set(re.findall(r'startsWith\("([^"]+)"\)', when))
        prefixes |= set(re.findall(r'f === "([^"]+)"', when))
        entries.append((prefixes, set(re.findall(r'"(tests/[^"]+\.py)"', tests))))
    return entries


def _uncovered_roots() -> list[str]:
    """Guards that read a tree no gate mechanism triggers them on."""
    always = _listed_in_ci()
    invariants = _invariant_guard_triggers()
    findings: list[str] = []

    for path in sorted(TESTS_ROOT.rglob("test_*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8-sig")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        roots = _subject_roots(path, tree)
        if not roots:
            continue
        name = f"tests/{path.relative_to(TESTS_ROOT).as_posix()}"
        if name in always or name in NOT_WIRED_ON_PURPOSE:
            continue                          # runs on every change

        covered: set[str] = set()
        if _runs_via_tree_scanner_rule(text):
            covered.add("core/navig")
        for prefixes, tests in invariants:
            if name in tests:
                covered |= {p.rstrip("/") for p in prefixes}

        missing = sorted(
            r
            for r in roots
            if not any(c == "" or r == c or r.startswith(c + "/") for c in covered)
        )
        if missing:
            findings.append(
                f"{name}\n        reads {sorted(roots)}\n"
                f"        triggers on {sorted(covered) or ['NOTHING']}\n"
                f"        NOT triggered by a change under {missing}"
            )
    return findings


def test_every_guard_is_triggered_by_a_change_to_each_tree_it_reads() -> None:
    """A guard that reads N trees needs N triggers.

    Wired-ness is not one property. ``sourceGuardArgs`` fires on every change; the
    tree-scanner rule fires only on ``core/navig/**``; an ``INVARIANT_GUARDS`` entry fires
    on whatever prefixes its ``when`` names. A guard reachable from one tree it reads and
    not another is protected in exactly the direction that cannot break it.

    Teeth, measured against the real failure rather than a fixture: delete the
    ``core/tests/**`` entry that #1190 added and this reports

        tests/platform/test_no_hardcoded_home.py
            reads ['core/tests'], triggers on ['core/navig'],
            NOT triggered by a change under ['core/tests']

    which is precisely the state in which main went red — and the pre-push gate for the PR
    that broke it was green, because the guard was never in it.

    Zero findings today; this is a floor placed after the third instance of the class, not
    a debt list.

    ⚖ The neighbouring theory was measured and REJECTED, so nobody re-runs the sweep: the
    #1190 failure was NOT a missing staleness check. Of the 23 test files holding a named
    allowlist or baseline of specific entries, **every one already asserts its own entries
    are still needed** — by a `..._has_no_stale_entries` test, a differently-named one
    (`..._that_starts_working_updates_its_note`), or an inline assertion in the same test
    (`stale = set(_ALLOWED) - set(offenders)`). A first pass reported 26 unprotected and a
    second 7; both were artifacts of matching test NAMES, and the true count is zero. The
    guard that went stale had a staleness test too. What it did not have was a trigger on
    the tree it reads — which is this check.
    """
    findings = _uncovered_roots()
    assert not findings, (
        "These guards read a tree that no gate mechanism triggers them on, so a change "
        "there cannot make them run — they will go red on main with a green push:\n  "
        + "\n  ".join(findings)
        + f"\n\nAdd each to `sourceGuardArgs` in {CI_RUNNER.name} (fires on every change), "
        "or give it an `INVARIANT_GUARDS` entry whose `when` covers the missing root."
    )


def test_the_root_resolver_still_resolves_something() -> None:
    """Anti-vacuity, both halves.

    A resolver that returns nothing makes the check above green while reading no guard at
    all, and an INVARIANT_GUARDS parse that returns nothing would make every derived
    trigger look absent — the failure that reports itself as "everything is broken" and so
    gets an exemption rather than a fix.
    """
    resolved = 0
    seen: set[str] = set()
    for path in sorted(TESTS_ROOT.rglob("test_*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except SyntaxError:
            continue
        roots = _subject_roots(path, tree)
        if roots:
            resolved += 1
            seen |= roots

    assert resolved >= 40, (
        f"only {resolved} test files resolved a walked root (expected ~50) — the path "
        "idiom changed and the trigger check is reading almost nothing."
    )
    # The three trees the class has already been missed in must all still be resolvable.
    for root in ("core/navig", "plugins", "core/tests"):
        assert root in seen, (
            f"no guard resolved {root!r} as a subject root — the resolver has stopped "
            "seeing a tree that guards demonstrably read."
        )
    assert len(_invariant_guard_triggers()) >= 10, (
        "INVARIANT_GUARDS parsed to almost nothing — the regex has drifted from the "
        "runner, and every derived trigger would look absent."
    )


def test_the_ci_list_has_no_stale_entries() -> None:
    """A renamed or deleted guard must not linger in the list: pytest would fail the
    whole step on a missing path, which reads as a broken gate rather than a stale line."""
    stale = sorted(p for p in _listed_in_ci() if not (CORE_TESTS.parent / p).is_file())
    assert not stale, (
        f"`sourceGuardArgs` names test files that no longer exist: {stale}. They were "
        "moved or renamed — follow them, or the pre-push gate fails for everyone."
    )


def test_exemptions_are_real() -> None:
    """An exemption for a guard that no longer exists quietly grants itself forever."""
    ghosts = sorted(p for p in NOT_WIRED_ON_PURPOSE if not (CORE_TESTS.parent / p).is_file())
    assert not ghosts, f"NOT_WIRED_ON_PURPOSE names files that do not exist: {ghosts}"
