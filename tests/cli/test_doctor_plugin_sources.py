"""`navig doctor` -> Plugin Sources: an editable-declared plugin that resolves elsewhere.

The failure this guards is invisible from every other surface. `[tool.uv.sources]` says a
plugin resolves to `plugins/navig-<x>`, a non-editable install shadows it, and then the CLI
executes a stale copy while the repo source moves on — with `navig plugin list` printing a
green tick over it, because "wired" means "loaded", not "matches your source".

Measured on the author's machine: `navig mobile ui` answered "No such command" for a
sub-app registered unconditionally in the source, while that plugin's own 39 tests passed.
The gate runs `python -m pytest` with the plugin dir as cwd, and `-m` puts the source on
`sys.path` first: the tests read the repo, the CLI read the install.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from navig.commands.doctor import check_plugin_sources


def _make_checkout(tmp_path: Path, *, dist: str = "navig-demo", pkg: str = "navig_demo") -> Path:
    """A minimal dev checkout: core/pyproject.toml declaring one editable plugin."""
    root = tmp_path / "repo"
    (root / "core").mkdir(parents=True)
    src = root / "plugins" / dist / pkg
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (root / "core" / "pyproject.toml").write_text(
        "[tool.uv.sources]\n"
        f'{dist} = {{ path = "../plugins/{dist}", editable = true }}\n',
        encoding="utf-8",
    )
    return root


@pytest.fixture
def _in_checkout(monkeypatch, tmp_path):
    root = _make_checkout(tmp_path)
    # Clear the hint so these cases exercise the plain path regardless of the ambient
    # environment — a real `navig` run exports NAVIG_INVOCATION_CWD, and inheriting it
    # here would send the resolver down the other branch.
    monkeypatch.delenv("NAVIG_INVOCATION_CWD", raising=False)
    monkeypatch.setattr(
        "navig.commands.repo.repo_root", lambda cwd=None: root, raising=False
    )
    return root


def _spec_at(origin: Path):
    return importlib.util.spec_from_file_location("navig_demo", origin)


def test_reports_nothing_outside_a_development_checkout(monkeypatch):
    """An end user installed from PyPI has no source tree to compare against."""
    monkeypatch.delenv("NAVIG_INVOCATION_CWD", raising=False)
    monkeypatch.setattr(
        "navig.commands.repo.repo_root", lambda cwd=None: None, raising=False
    )
    assert check_plugin_sources() == []


def test_reports_nothing_when_the_checkout_has_no_core_pyproject(monkeypatch, tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.delenv("NAVIG_INVOCATION_CWD", raising=False)
    monkeypatch.setattr(
        "navig.commands.repo.repo_root", lambda cwd=None: bare, raising=False
    )
    assert check_plugin_sources() == []


def test_flags_a_plugin_shadowed_by_an_installed_copy(monkeypatch, _in_checkout, tmp_path):
    installed = tmp_path / "site-packages" / "navig_demo" / "__init__.py"
    installed.parent.mkdir(parents=True)
    installed.write_text("", encoding="utf-8")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: _spec_at(installed))

    rows = check_plugin_sources()
    assert rows, "a shadowed plugin must produce a row"
    icon, ok, line = rows[0][0], rows[0][1], rows[0][2]
    assert ok is False, "a shadowed plugin is not a healthy state"
    assert "navig-demo" in line
    assert "pip install -e" in line, "the row must carry the fix, not just the diagnosis"


def test_confirms_when_the_plugin_resolves_to_the_declared_source(
    monkeypatch, _in_checkout
):
    origin = _in_checkout / "plugins" / "navig-demo" / "navig_demo" / "__init__.py"
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: _spec_at(origin))

    rows = check_plugin_sources()
    assert [r[1] for r in rows] == [True]
    assert "1 of 1" in rows[0][2]


def test_resolution_is_root_agnostic(monkeypatch, _in_checkout, tmp_path):
    """An editable install points at ONE checkout; doctor may run from a worktree copy.

    Comparing against the CURRENT root reported 11 of 12 plugins shadowed on a machine
    where the real answer was 4 — seven correct installs simply pointed at the main
    checkout instead of the `.dev/worktrees/<slug>` this ran from.
    """
    other = tmp_path / "another-checkout" / "plugins" / "navig-demo" / "navig_demo"
    other.mkdir(parents=True)
    origin = other / "__init__.py"
    origin.write_text("", encoding="utf-8")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: _spec_at(origin))

    rows = check_plugin_sources()
    assert [r[1] for r in rows] == [True], "another checkout is still source, not an install"


def test_a_plugin_that_is_not_installed_is_unverified_never_a_tick(
    monkeypatch, _in_checkout
):
    """Absent is not a failure — an optional extra may simply not be installed — but it
    is also not something we may render a green tick over."""
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)

    rows = check_plugin_sources()
    assert rows, "an unverifiable plugin must still say so"
    assert all(r[1] is False for r in rows), "unverified must never render as ok"
    assert any("navig-demo" in r[2] for r in rows)


# ── the chdir trap: repo-scoped checks must resolve the INVOCATION cwd ──────────
#
# `navig` chdir's to the active space during startup (main.py), recording the
# pre-chdir directory in NAVIG_INVOCATION_CWD. A repo-scoped check that calls
# `repo_root()` bare therefore asks about the SPACE. With the active space set to
# `~/.navig-os/workspaces/my-workspace` (not a git repo), `navig doctor`'s documented
# Repo Guard row evaluated to empty and was silently skipped on every run — the check
# existed, was tested, and never reported. Worse when a space IS a repo: the row would
# describe that repo while claiming to describe yours.


def test_invocation_root_prefers_the_directory_navig_was_invoked_from(
    monkeypatch, tmp_path
):
    from navig.commands.doctor import _invocation_repo_root

    invoked = tmp_path / "where-the-user-stood"
    invoked.mkdir()
    space = tmp_path / "active-space"
    space.mkdir()

    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(invoked))
    monkeypatch.setattr(
        "navig.commands.repo.repo_root",
        lambda cwd=None: invoked if cwd == invoked else space,
        raising=False,
    )
    assert _invocation_repo_root() == invoked, (
        "resolved the chdir'd space instead of where navig was invoked"
    )


def test_invocation_root_falls_back_when_the_hint_is_absent(monkeypatch, tmp_path):
    from navig.commands.doctor import _invocation_repo_root

    monkeypatch.delenv("NAVIG_INVOCATION_CWD", raising=False)
    fallback = tmp_path / "cwd-repo"
    fallback.mkdir()
    monkeypatch.setattr(
        "navig.commands.repo.repo_root", lambda cwd=None: fallback, raising=False
    )
    assert _invocation_repo_root() == fallback


def test_plugin_sources_survives_a_chdir_away_from_the_repo(
    monkeypatch, tmp_path, _in_checkout
):
    """The regression: the section must not vanish because navig moved the cwd."""
    not_a_repo = tmp_path / "space-that-is-not-a-repo"
    not_a_repo.mkdir()
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(_in_checkout))
    # repo_root() answers None for the chdir'd space, the checkout for the invocation dir
    monkeypatch.setattr(
        "navig.commands.repo.repo_root",
        lambda cwd=None: _in_checkout if cwd == _in_checkout else None,
        raising=False,
    )
    installed = tmp_path / "site-packages" / "navig_demo" / "__init__.py"
    installed.parent.mkdir(parents=True)
    installed.write_text("", encoding="utf-8")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: _spec_at(installed))

    rows = check_plugin_sources()
    assert rows, "the section disappeared because navig chdir'd to the active space"
    assert "navig-demo" in rows[0][2]
