"""`navig.plugins.sources` — does each editable-declared plugin resolve to your source?

The failure this answers is invisible from every other surface: `[tool.uv.sources]` says a
plugin resolves to `plugins/navig-<x>`, a non-editable install shadows it, and the CLI then
executes code that exists nowhere in the repo — while the test suites keep reading the
repo, because the gate runs `python -m pytest` with the plugin dir as cwd and `-m` puts the
source on `sys.path` first.

Two surfaces render this (`navig doctor` -> Plugin Sources, and `navig plugin list`'s
per-row state), which is why the detection lives in one module rather than in either.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from navig.plugins.sources import audit_plugin_sources, is_plugin_source_tree

DIST = "navig-demo"
PKG = "navig_demo"


def _checkout(tmp_path: Path, *, declare: bool = True) -> Path:
    """A minimal dev checkout declaring one editable-local plugin."""
    root = tmp_path / "repo"
    (root / "core").mkdir(parents=True)
    src = root / "plugins" / DIST / PKG
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    body = f'[tool.uv.sources]\n{DIST} = {{ path = "../plugins/{DIST}", editable = true }}\n'
    (root / "core" / "pyproject.toml").write_text(
        body if declare else "[project]\nname = 'x'\n", encoding="utf-8"
    )
    return root


def _spec_at(origin: Path):
    return importlib.util.spec_from_file_location(PKG, origin)


def _installed_copy(tmp_path: Path) -> Path:
    origin = tmp_path / "site-packages" / PKG / "__init__.py"
    origin.parent.mkdir(parents=True)
    origin.write_text("", encoding="utf-8")
    return origin


# ── nothing to compare against ───────────────────────────────────────────────


def test_none_without_a_repo(monkeypatch):
    """An end user who installed from PyPI has no source tree to compare against."""
    monkeypatch.setattr(
        "navig.commands.repo.resolve_repo_root", lambda *a, **k: None, raising=False
    )
    assert audit_plugin_sources() is None


def test_none_without_a_core_pyproject(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    assert audit_plugin_sources(bare) is None


def test_none_when_nothing_is_declared_local(tmp_path):
    assert audit_plugin_sources(_checkout(tmp_path, declare=False)) is None


def test_none_when_the_pyproject_is_unreadable(tmp_path):
    """Malformed TOML must yield no verdict, never a confident wrong one."""
    root = _checkout(tmp_path)
    (root / "core" / "pyproject.toml").write_text("[tool.uv.sources\n", encoding="utf-8")
    assert audit_plugin_sources(root) is None


# ── the verdicts ─────────────────────────────────────────────────────────────


def test_flags_a_plugin_shadowed_by_an_installed_copy(tmp_path, monkeypatch):
    root = _checkout(tmp_path)
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name: _spec_at(_installed_copy(tmp_path))
    )
    audit = audit_plugin_sources(root)
    assert audit is not None
    assert audit.shadowed == (DIST,)
    assert audit.verified == ()
    assert audit.ok is False


def test_confirms_a_plugin_resolving_to_the_declared_source(tmp_path, monkeypatch):
    root = _checkout(tmp_path)
    origin = root / "plugins" / DIST / PKG / "__init__.py"
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: _spec_at(origin))
    audit = audit_plugin_sources(root)
    assert audit is not None
    assert audit.verified == (DIST,)
    assert audit.shadowed == ()
    assert audit.ok is True


def test_resolution_is_root_agnostic(tmp_path, monkeypatch):
    """An editable install points at ONE checkout; this may run from a worktree copy.

    Comparing against the CURRENT root reported 11 of 12 plugins shadowed on a machine
    where the real answer was 4 — seven correct installs simply pointed at the main
    checkout rather than the `.dev/worktrees/<slug>` in use.
    """
    root = _checkout(tmp_path)
    other = tmp_path / "another-checkout" / "plugins" / DIST / PKG
    other.mkdir(parents=True)
    origin = other / "__init__.py"
    origin.write_text("", encoding="utf-8")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: _spec_at(origin))
    audit = audit_plugin_sources(root)
    assert audit is not None and audit.verified == (DIST,)


def test_a_plugin_that_is_not_installed_is_unresolved_not_shadowed(tmp_path, monkeypatch):
    """Absent is not a failure — an optional extra may simply not be installed."""
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    audit = audit_plugin_sources(_checkout(tmp_path))
    assert audit is not None
    assert audit.shadowed == ()
    assert audit.unresolved == ((DIST, "not installed"),)
    assert audit.ok is True


def test_a_declared_path_that_does_not_exist_is_unresolved(tmp_path):
    root = _checkout(tmp_path)
    import shutil

    shutil.rmtree(root / "plugins" / DIST)
    audit = audit_plugin_sources(root)
    assert audit is not None
    assert audit.unresolved == ((DIST, "declared path missing"),)


def test_fix_paths_are_repo_relative_and_aligned_with_shadowed(tmp_path, monkeypatch):
    """The row prints these as a `pip install -e` remedy, so order and form matter."""
    root = _checkout(tmp_path)
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name: _spec_at(_installed_copy(tmp_path))
    )
    audit = audit_plugin_sources(root)
    assert audit is not None
    assert len(audit.fix_paths) == len(audit.shadowed)
    assert audit.fix_paths == (f"plugins/{DIST}",)


# ── the root-agnostic predicate ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        (Path("/x/plugins/navig-demo/navig_demo/__init__.py"), True),
        (Path("/anywhere/else/plugins/navig-demo/navig_demo/sub/mod.py"), True),
        (Path("/x/site-packages/navig_demo/__init__.py"), False),
        # the directory name must sit directly under `plugins/`, not merely appear
        (Path("/x/navig-demo/navig_demo/__init__.py"), False),
    ],
)
def test_is_plugin_source_tree(origin: Path, expected: bool):
    assert is_plugin_source_tree(origin, "navig-demo") is expected
