"""Pins the tmp-dir boundary that has silently broken tests three times.

``pytest.ini`` sets ``--basetemp=.pytest_tmp``, which lives INSIDE ``core/`` — inside the
git checkout. So every ``tmp_path`` has a ``.git`` **ancestor** *and* a ``.navig``
**ancestor**. Anything that walks upward looking for one of those markers finds NAVIG's
own repo, and a test that used ``tmp_path`` to mean "a directory that is not in a
repo / not in a NAVIG project" silently asserted the opposite.

It is a real trap, not a hypothetical. Two markers, three casualties:

* ``.git`` — broke ``test_returns_none_when_no_git`` (``_find_git_root`` handed back the
  worktree root instead of ``None``) and confused the agent-lock tests.
* ``.navig`` — broke the two ``plans`` "no spaces discovered" tests: space discovery
  (``spaces/resolver.py::_find_project_navig_root``) walked up from ``tmp_path``, found
  ``core/.navig``, and discovered a real project-scope space named ``core``. Those tests
  passed only where no ``.navig`` happened to sit above the checkout.

The escape hatch already exists — the ``temp_dir`` fixture uses the system temp dir. These
tests pin BOTH markers and BOTH halves of the contract, so the difference is discoverable,
and so that moving ``basetemp`` later fails loudly here instead of silently changing what a
hundred tests mean.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _marker_ancestor(start: Path, marker: str) -> Path | None:
    """Nearest ancestor (inclusive) containing *marker*, else None."""
    cur = start.resolve()
    while True:
        if (cur / marker).exists():
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent


def _git_ancestor(start: Path) -> Path | None:
    return _marker_ancestor(start, ".git")


def test_tmp_path_is_inside_the_repo_but_temp_dir_is_not(tmp_path: Path, temp_dir: Path) -> None:
    """The trap, stated as an executable fact."""
    assert _git_ancestor(tmp_path) is not None, (
        "tmp_path no longer has a .git ancestor — basetemp moved outside the checkout. "
        "That is a fine change, but it silently alters what every tmp_path-based test "
        "means; re-read the module docstring before accepting this."
    )
    assert _git_ancestor(temp_dir) is None, (
        "the `temp_dir` fixture is supposed to be OUTSIDE any git repo — it is the only "
        "sanctioned way to test code that walks upward looking for a repository."
    )


def test_use_temp_dir_when_a_test_must_not_be_in_a_repo(temp_dir: Path) -> None:
    """The real consumer: git-root discovery must return None outside a repo."""
    from navig.agent.tools.git_tools import _find_git_root

    assert _find_git_root(temp_dir) is None


def test_tmp_path_would_have_given_the_wrong_answer(tmp_path: Path) -> None:
    """...and the same call under `tmp_path` finds a repo — which is why it was failing."""
    from navig.agent.tools.git_tools import _find_git_root

    found = _find_git_root(tmp_path)
    assert found is not None
    assert (found / ".git").exists()


@pytest.mark.parametrize("fixture_name", ["tmp_path", "temp_dir"])
def test_both_fixtures_are_usable_scratch_space(fixture_name, request) -> None:
    """Whichever you pick, it is still a writable directory."""
    d: Path = request.getfixturevalue(fixture_name)
    probe = d / "probe.txt"
    probe.write_text("ok", encoding="utf-8")
    assert probe.read_text(encoding="utf-8") == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# The second marker: `.navig`. Same trap, different consumer — space discovery.
#
# These call `_find_project_navig_root` (a pure upward walk) rather than
# `discover_space_paths`, which AUTO-REGISTERS what it finds into spaces.json —
# a guard test must not seed the registry it is guarding.
# ─────────────────────────────────────────────────────────────────────────────


def test_tmp_path_sits_inside_a_navig_project_but_temp_dir_does_not(
    tmp_path: Path, temp_dir: Path
) -> None:
    """Why `plans ... --path tmp_path` could never report "no spaces discovered"."""
    from navig.spaces.resolver import _find_project_navig_root

    found = _find_project_navig_root(tmp_path.resolve())
    assert found is not None, (
        "tmp_path no longer has a project .navig/ ancestor — basetemp moved outside the "
        "checkout. Fine in itself, but re-read the module docstring: it silently changes "
        "what every tmp_path-based space-discovery test means."
    )
    assert found.name == ".navig"

    assert _find_project_navig_root(temp_dir.resolve()) is None, (
        "`temp_dir` must stand outside every NAVIG project — it is the only sanctioned "
        "fixture for a test that asserts nothing is discovered."
    )


def test_home_navig_is_the_global_layer_not_a_project(temp_dir: Path) -> None:
    """`temp_dir` lives under $HOME on Windows, and `~/.navig` exists — the resolver
    skips it on purpose (global layer ≠ project), which is *why* the fixture is safe."""
    from navig.spaces.resolver import _find_project_navig_root

    if not (Path.home() / ".navig").is_dir():
        pytest.skip("no ~/.navig on this machine — nothing to skip over")
    assert _find_project_navig_root(temp_dir.resolve()) is None
