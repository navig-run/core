"""The memory-heavy whole-tree guards must stay pinned to ONE xdist worker.

Five guards in this directory parse every ``.py`` under ``core/navig`` (some also
``plugins/`` and ``private/harbor``) and, in the worst case, hold every parsed tree at
once (``test_docstring_crossrefs_resolve`` keeps ~1,900 ASTs in a module fixture).
Under pytest-xdist's default ``load`` scheduling eight workers can each be inside one
of them at the same moment; on a shared box that is the shape that kills a worker
with an OOM, and xdist then reports whatever that worker was running as FAILED — a
red gate on an unmodified tree, blamed on an unrelated test.

The fix has two halves that are each inert without the other:

* the module carries ``pytestmark = pytest.mark.xdist_group("whole_tree_ast_<lane>")``
  so its lane lands on one worker and runs serially among itself;
* the gate passes ``--dist loadgroup`` at every ``-n`` site (pinned by
  ``scripts/test/xdist-workers.test.mjs``) — without it the marker is silently
  ignored.

Why TWO lanes and not one: measured 2026-09-17, the five run 16 + 19 + 18 + 20 + 26 s
(~100 s serial in one worker) while the rest of the source-guard step finishes in
~80 s across eight workers — one lane makes the pinned worker the long pole of every
push. Two lanes (~45 s and ~54 s) keep the step flat and still cap the simultaneous
whole-tree ASTs at two, where the crash needed eight. Peak memory scales with the lane
count, so add a lane only with a measurement, never for convenience.

This file pins the first half by reading the AST of each listed module: a marker in a
docstring or a comment does not count, and a renamed group name is a different group.
The list is explicit on purpose. A guard that starts holding whole-tree state is added
here by hand, with the measurement that justified it; a shape-based detector would
either miss it (a scan that delegates its walk) or sweep in the ~60 light tree walkers
that finish in a second and are fine to fan out.

Vacuity floor: the list is non-empty and every listed file exists — a pin over a
module that moved away pins nothing.

The same modules also carry their OWN ``pytest.mark.timeout``. The gate runs pytest with
``--timeout=30/60/120 --timeout-method=thread``, and the thread method does not fail a
slow test — it kills the whole worker process. Measured 2026-10-10: on a busy box
``test_instance_method_contract`` took 90 s standalone (18 s when this list was written),
so a push gate's 60 s ceiling killed both xdist workers mid-lane, xdist then crashed on
its own ``KeyError`` re-scheduling the group, and the step went red on an unmodified tree.
A marker beats the command line, so each module declares a ceiling sized for a loaded
box; the global ceiling keeps guarding every other test.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
GROUP_PREFIX = "whole_tree_ast_"

# Measured 2026-09-17 (pytest 9.1.1, xdist 3.8.0, 8 workers, 32-CPU box under a second
# gate): these five are the ones whose workers died. Each parses the whole tree per test
# and keeps either every tree, a whole-tree Counter, or the imported package alongside.
# Value = lane; the seconds are the standalone wall time that balanced the lanes.
HEAVY_TREE_GUARDS: dict[str, int] = {
    "test_rich_markup_renders.py": 1,  # 26 s
    "test_no_unreachable_private_methods.py": 1,  # 19 s
    "test_dataclass_construction_contract.py": 2,  # 20 s
    "test_instance_method_contract.py": 2,  # 18 s
    "test_docstring_crossrefs_resolve.py": 2,  # 16 s, holds ~1,900 ASTs at once
}


#: The least a whole-tree guard may declare. 90 s was measured standalone on a loaded box
#: (2026-10-10); the gate's own per-test ceilings are 30 / 60 / 120 s, all of which that
#: run would have tripped. Above every gate ceiling with ~2x headroom over the measurement.
MIN_WHOLE_TREE_TIMEOUT_S = 180


def _module_level_marks(path: Path, mark: str) -> list[ast.Call]:
    """``pytest.mark.<mark>(...)`` calls assigned to ``pytestmark`` at module level."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[ast.Call] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets):
            continue
        values = node.value.elts if isinstance(node.value, (ast.List, ast.Tuple)) else [node.value]
        found += [
            v for v in values
            if isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr == mark
        ]
    return found


def _module_level_timeout(path: Path) -> float | None:
    """The literal seconds of a module-level ``pytest.mark.timeout(N)``, or None."""
    for call in _module_level_marks(path, "timeout"):
        if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, (int, float)):
            return float(call.args[0].value)
    return None


def _module_level_xdist_groups(path: Path) -> list[str]:
    """Group names assigned to ``pytestmark`` at module level, via the AST."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    groups: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets):
            continue
        # pytestmark = pytest.mark.xdist_group("name")  |  pytestmark = [ ... ]
        values = node.value.elts if isinstance(node.value, (ast.List, ast.Tuple)) else [node.value]
        for v in values:
            if (
                isinstance(v, ast.Call)
                and isinstance(v.func, ast.Attribute)
                and v.func.attr == "xdist_group"
                and v.args
                and isinstance(v.args[0], ast.Constant)
                and isinstance(v.args[0].value, str)
            ):
                groups.append(v.args[0].value)
    return groups


def test_list_is_not_vacuous() -> None:
    assert HEAVY_TREE_GUARDS, "an empty pin list pins nothing"
    missing = [n for n in HEAVY_TREE_GUARDS if not (HERE / n).is_file()]
    assert not missing, f"listed guards that no longer exist here (moved? renamed?): {missing}"
    assert set(HEAVY_TREE_GUARDS.values()) == {1, 2}, "both lanes must carry work"


@pytest.mark.parametrize("name", sorted(HEAVY_TREE_GUARDS))
def test_heavy_guard_is_pinned_to_its_lane(name: str) -> None:
    expected = f"{GROUP_PREFIX}{HEAVY_TREE_GUARDS[name]}"
    groups = _module_level_xdist_groups(HERE / name)
    assert expected in groups, (
        f"{name} is a whole-tree AST guard but carries no module-level "
        f'`pytestmark = pytest.mark.xdist_group("{expected}")` (found: {groups or "none"}); '
        "without it the gate can run it on eight workers at once and OOM-kill one."
    )


@pytest.mark.parametrize("name", sorted(HEAVY_TREE_GUARDS))
def test_heavy_guard_declares_its_own_timeout(name: str) -> None:
    seconds = _module_level_timeout(HERE / name)
    assert seconds is not None and seconds >= MIN_WHOLE_TREE_TIMEOUT_S, (
        f"{name} is a whole-tree AST guard but declares "
        f"{'no' if seconds is None else f'only a {seconds:g} s'} module-level "
        f"`pytest.mark.timeout(N)` (need N >= {MIN_WHOLE_TREE_TIMEOUT_S}). Under the gate's "
        "`--timeout-method=thread` a slow test does not fail — it kills the xdist worker, "
        "and the whole step goes red on an unmodified tree."
    )


def test_timeout_detector_reads_the_ast_not_the_text(tmp_path: Path) -> None:
    fake = tmp_path / "test_fake.py"
    fake.write_text("# pytestmark = pytest.mark.timeout(300)\nimport pytest\n", encoding="utf-8")
    assert _module_level_timeout(fake) is None
    fake.write_text(
        'import pytest\npytestmark = [pytest.mark.xdist_group("g"), pytest.mark.timeout(300)]\n',
        encoding="utf-8",
    )
    assert _module_level_timeout(fake) == 300.0


def test_pin_detector_reads_the_ast_not_the_text(tmp_path: Path) -> None:
    """A marker in a comment or docstring must not count — that is how a pin rots."""
    fake = tmp_path / "test_fake.py"
    fake.write_text(
        '"""pytestmark = pytest.mark.xdist_group(\"whole_tree_ast\")"""\n'
        "# pytestmark = pytest.mark.xdist_group('whole_tree_ast')\n"
        "import pytest\n",
        encoding="utf-8",
    )
    assert _module_level_xdist_groups(fake) == []
    fake.write_text(
        "import pytest\n"
        'pytestmark = [pytest.mark.slow, pytest.mark.xdist_group("whole_tree_ast")]\n',
        encoding="utf-8",
    )
    assert _module_level_xdist_groups(fake) == ["whole_tree_ast"]
