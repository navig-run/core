"""Scaffolding must not touch anything outside the tree it generates.

Two review findings on PR #843:

* `generate()` merged the staging ROOT into the target with `copytree`, which ends with
  `copystat(staging, target)` — an existing target took the TemporaryDirectory's 0700
  mode on POSIX, stripping group/other access from a shared project directory.
* `_process_structure` joined a rendered path onto the current directory with no
  containment check, so `../x` or an absolute path wrote outside staging — even from
  `preview()` under `--dry-run`, where staging cleanup never removes it.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

from navig.core.scaffolder import Scaffolder

_TEMPLATE = {
    "structure": [
        {"type": "directory", "path": "src", "children": [{"path": "a.py", "content": "x"}]},
        {"path": "README.md", "content": "# demo"},
    ]
}


# ── target metadata ─────────────────────────────────────────────────────────


def test_generate_never_copies_staging_metadata_onto_the_target(tmp_path, monkeypatch):
    """Platform-neutral form of the 0700 bug: `copystat` must never be pointed at the
    target directory itself (it is what carries the staging dir's mode across)."""
    target = tmp_path / "existing"
    target.mkdir()
    (target / "keep.txt").write_text("mine", encoding="utf-8")

    destinations: list[Path] = []
    real_copystat = shutil.copystat

    def recording_copystat(src, dst, *args, **kwargs):
        destinations.append(Path(dst).resolve())
        return real_copystat(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "copystat", recording_copystat)

    Scaffolder().generate(_TEMPLATE, target)

    assert target.resolve() not in destinations
    assert (target / "keep.txt").read_text(encoding="utf-8") == "mine"
    assert (target / "README.md").is_file()
    assert (target / "src" / "a.py").is_file()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_generate_preserves_an_existing_target_mode(tmp_path):
    target = tmp_path / "shared"
    target.mkdir()
    os.chmod(target, 0o755)

    Scaffolder().generate(_TEMPLATE, target)

    assert stat.S_IMODE(target.stat().st_mode) == 0o755


# ── path containment ────────────────────────────────────────────────────────


def _escaping_paths(tmp_path: Path) -> list[str]:
    return ["../escaped.txt", "sub/../../escaped.txt", str(tmp_path / "abs_escaped.txt")]


@pytest.mark.parametrize("index", [0, 1, 2])
def test_preview_rejects_paths_that_escape_staging(tmp_path, monkeypatch, index):
    # Point the staging temp dir inside tmp_path so a `..` escape would land here.
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    bad = _escaping_paths(tmp_path)[index]

    with pytest.raises(ValueError, match="escapes the scaffold root"):
        Scaffolder().preview({"structure": [{"path": bad, "content": "x"}]})

    assert not (tmp_path / "escaped.txt").exists()
    assert not (tmp_path / "abs_escaped.txt").exists()


def test_generate_rejects_an_escaping_child_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    target = tmp_path / "out"
    template = {
        "structure": [
            {"type": "directory", "path": "d", "children": [{"path": "../../x.txt", "content": "x"}]}
        ]
    }

    with pytest.raises(ValueError, match="escapes the scaffold root"):
        Scaffolder().generate(template, target)

    assert not (tmp_path / "x.txt").exists()
    assert not target.exists()


def test_dotdot_that_stays_inside_the_tree_is_allowed():
    entries = Scaffolder().preview(
        {"structure": [{"path": "a/../b.txt", "content": "x"}]}
    )
    assert ("b.txt", "file") in entries
