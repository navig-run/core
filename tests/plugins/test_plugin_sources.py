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

import importlib.metadata
import importlib.util
from pathlib import Path

import pytest

from navig.plugins.sources import (
    audit_plugin_sources,
    dead_editables,
    editable_install_command,
    is_plugin_source_tree,
    uninstall_command,
    with_local_dependencies,
)

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


# ── stale editable installs ──────────────────────────────────────────────────
#
# Measured 2026-09-30: 19 of 24 first-party editable installs pointed at the source but
# carried the version pip recorded at install time. navig-contacts said 0.1.0 while its
# source and core's floor said 0.2.0 — so `pip install -e core` would have replaced the
# editable plugin with the PyPI wheel, silently.


def _versioned_checkout(tmp_path: Path, *, source: str, floor: str | None) -> Path:
    root = _checkout(tmp_path)
    (root / "plugins" / DIST / "pyproject.toml").write_text(
        f'[project]\nname = "{DIST}"\nversion = "{source}"\n', encoding="utf-8"
    )
    deps = f'dependencies = ["{DIST}>={floor}"]\n' if floor else ""
    body = (root / "core" / "pyproject.toml").read_text(encoding="utf-8")
    (root / "core" / "pyproject.toml").write_text(
        f'[project]\nname = "navig"\n{deps}\n' + body, encoding="utf-8"
    )
    return root


def _resolving_to_source(monkeypatch, root: Path, installed: str) -> None:
    origin = root / "plugins" / DIST / PKG / "__init__.py"
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: _spec_at(origin))
    monkeypatch.setattr(importlib.metadata, "version", lambda dist: installed)


def test_an_install_recorded_at_the_source_version_is_not_stale(tmp_path, monkeypatch):
    root = _versioned_checkout(tmp_path, source="0.2.0", floor="0.2.0")
    _resolving_to_source(monkeypatch, root, "0.2.0")
    audit = audit_plugin_sources(root)
    assert audit is not None
    assert audit.verified == (DIST,)
    assert audit.stale == () and audit.below_floor == ()


def test_an_old_version_record_is_stale_even_though_it_resolves_to_source(
    tmp_path, monkeypatch
):
    """Resolving to the source and being current are two different questions."""
    root = _versioned_checkout(tmp_path, source="0.3.0", floor="0.1.0")
    _resolving_to_source(monkeypatch, root, "0.1.0")
    audit = audit_plugin_sources(root)
    assert audit is not None
    assert audit.verified == (DIST,), "still wired to the source — not shadowed"
    assert audit.stale == ((DIST, "0.1.0", "0.3.0"),)
    assert audit.below_floor == (), "0.1.0 still satisfies >=0.1.0"
    assert audit.stale_fix_paths == (f"plugins/{DIST}",)


def test_a_record_below_cores_floor_is_named(tmp_path, monkeypatch):
    """The case that clobbers: core's next resolve fetches the floor from PyPI."""
    root = _versioned_checkout(tmp_path, source="0.2.0", floor="0.2.0")
    _resolving_to_source(monkeypatch, root, "0.1.0")
    audit = audit_plugin_sources(root)
    assert audit is not None
    assert audit.below_floor == (DIST,)


def test_an_extras_floor_counts_too(tmp_path, monkeypatch):
    """`pip install -e core[x]` re-resolves an extra exactly like a hard dependency."""
    root = _versioned_checkout(tmp_path, source="0.2.0", floor=None)
    body = (root / "core" / "pyproject.toml").read_text(encoding="utf-8")
    body = body.replace(
        "[tool.uv.sources]",
        f'[project.optional-dependencies]\ndemo = ["{DIST}>=0.2.0"]\n\n[tool.uv.sources]',
    )
    (root / "core" / "pyproject.toml").write_text(body, encoding="utf-8")
    _resolving_to_source(monkeypatch, root, "0.1.0")
    audit = audit_plugin_sources(root)
    assert audit is not None and audit.below_floor == (DIST,)


def test_a_source_without_a_static_version_yields_no_stale_verdict(tmp_path, monkeypatch):
    """No declared version to compare with is no verdict, never a guessed one."""
    root = _checkout(tmp_path)
    _resolving_to_source(monkeypatch, root, "0.1.0")
    audit = audit_plugin_sources(root)
    assert audit is not None and audit.stale == ()


# ── the remedy ───────────────────────────────────────────────────────────────


def test_every_path_gets_its_own_editable_flag():
    """`pip install -e a b` installs b as an ordinary copy — the very shadowing reported.

    Both `navig doctor` and `navig plugin list` printed exactly that form for more than
    one plugin, so following the advice created the problem it described.
    """
    cmd = editable_install_command(["plugins/navig-a", "plugins/navig-b"])
    assert cmd.endswith("-m pip install -e plugins/navig-a -e plugins/navig-b")


def test_the_remedy_names_the_running_interpreter(monkeypatch):
    """Two Pythons on PATH: a bare `pip` repairs whichever one it belongs to."""
    import sys

    monkeypatch.setattr(sys, "executable", r"C:\Program Files\Py\python.exe")
    cmd = editable_install_command(["plugins/navig-a"])
    assert cmd.startswith(r'"C:\Program Files\Py\python.exe" -m pip install')


def test_a_source_outside_plugins_is_matched_by_its_own_container():
    """navig-sdk is declared at registry/sdk/python — editable, yet reported shadowed."""
    origin = Path("/repo/registry/sdk/python/navig_sdk/__init__.py")
    assert is_plugin_source_tree(origin, "python", "sdk") is True
    assert is_plugin_source_tree(origin, "python") is False, "the default stays plugins/"


# ── a remedy must carry the local packages it depends on ─────────────────────


def _graph(tmp_path: Path, deps: dict[str, list[str]]) -> Path:
    """A checkout declaring each key local, with the given hard dependencies."""
    root = tmp_path / "graph"
    (root / "core").mkdir(parents=True)
    sources = []
    for dist, needs in deps.items():
        src = root / "plugins" / dist
        src.mkdir(parents=True)
        listed = ", ".join(f'"{n}>=0.1"' for n in needs)
        (src / "pyproject.toml").write_text(
            f'[project]\nname = "{dist}"\nversion = "0.1.0"\ndependencies = [{listed}]\n',
            encoding="utf-8",
        )
        sources.append(f'{dist} = {{ path = "../plugins/{dist}", editable = true }}')
    (root / "core" / "pyproject.toml").write_text(
        "[tool.uv.sources]\n" + "\n".join(sources) + "\n", encoding="utf-8"
    )
    return root


def test_a_local_dependency_joins_the_remedy(tmp_path):
    """Refreshing navig-download pulled its new dependency navig-browser from PyPI."""
    root = _graph(tmp_path, {"navig-download": ["navig-browser", "yt-dlp"], "navig-browser": []})
    assert with_local_dependencies(["plugins/navig-download"], root) == (
        "plugins/navig-download",
        "plugins/navig-browser",
    ), "a third-party dependency (yt-dlp) is pip's business, not the remedy's"


def test_local_dependencies_are_followed_transitively_and_cycles_end(tmp_path):
    root = _graph(tmp_path, {"navig-a": ["navig-b"], "navig-b": ["navig-c"], "navig-c": ["navig-a"]})
    assert with_local_dependencies(["plugins/navig-a"], root) == (
        "plugins/navig-a",
        "plugins/navig-b",
        "plugins/navig-c",
    )


def test_given_paths_keep_their_order_and_are_not_repeated(tmp_path):
    root = _graph(tmp_path, {"navig-a": ["navig-b"], "navig-b": []})
    assert with_local_dependencies(["plugins/navig-b", "plugins/navig-a"], root) == (
        "plugins/navig-b",
        "plugins/navig-a",
    )


def test_without_a_checkout_the_paths_pass_through(monkeypatch):
    monkeypatch.setattr(
        "navig.commands.repo.resolve_repo_root", lambda *a, **k: None, raising=False
    )
    assert with_local_dependencies(["plugins/navig-x"]) == ("plugins/navig-x",)


# ── dead editable installs ───────────────────────────────────────────────────


def _rec(path: Path, *, editable: bool = True) -> list:
    import json

    info = {"url": path.as_uri(), "dir_info": {"editable": editable}}
    return ["0.1.0", json.dumps(info)]


def test_an_editable_install_whose_folder_is_gone_is_dead(tmp_path):
    """navig-paperwork stayed installed from a deleted .dev/worktrees checkout."""
    gone = tmp_path / "worktrees" / "paperwork" / "plugins" / "navig-paperwork"
    live = tmp_path / "plugins" / "navig-cabinet"
    live.mkdir(parents=True)
    dead = dead_editables({"navig-paperwork": _rec(gone), "navig-cabinet": _rec(live)})
    assert [d for d, _ in dead] == ["navig-paperwork"]
    assert Path(dead[0][1]) == gone


def test_a_plain_install_is_never_called_dead(tmp_path):
    assert dead_editables({"navig-x": _rec(tmp_path / "nowhere", editable=False)}) == ()


def test_a_record_without_direct_url_is_ignored():
    assert dead_editables({"navig-x": ["0.1.0", None]}) == ()


def test_a_windows_drive_url_is_read_as_a_drive_path(tmp_path):
    """file:///E:/x parses to "/E:/x"; read naively, every Windows install looks dead."""
    import json

    from navig.plugins.sources import _editable_dir

    url = json.dumps({"url": "file:///E:/projects/apps/navig/core", "dir_info": {"editable": True}})
    assert str(_editable_dir(url)).replace("\\", "/") == "E:/projects/apps/navig/core"


def test_the_uninstall_remedy_names_the_interpreter():
    cmd = uninstall_command(["navig-paperwork"], executable=r"C:\Py 3\python.exe")
    assert cmd == r'"C:\Py 3\python.exe" -m pip uninstall -y navig-paperwork'
