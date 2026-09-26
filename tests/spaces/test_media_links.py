"""`navig space media` — every failure mode here was observed live first.

The engine is :mod:`navig.spaces.media_links`. These cover the link primitives
(which must answer correctly for Windows junctions, where the stdlib does not)
and every finding the scan reports.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from navig.spaces.media_links import (
    Finding,
    MediaConfig,
    ScanRoot,
    inspect_link,
    is_link,
    make_link,
    read_link,
    remove_link,
    scan,
)


@pytest.fixture
def estate(tmp_path: Path) -> tuple[Path, Path]:
    """A miniature of the real layout: <projects>/<category>/<project> + <media root>."""
    projects = tmp_path / "projects"
    mroot = tmp_path / "media"
    (projects / "apps").mkdir(parents=True)
    mroot.mkdir()
    return projects, mroot


def _cfg(projects: Path, mroot: Path, **kwargs: object) -> MediaConfig:
    return MediaConfig(
        media_root=mroot,
        roots=(ScanRoot(path=projects, project_depth=2, max_depth=2),),
        **kwargs,  # type: ignore[arg-type]
    )


def _project(projects: Path, name: str) -> Path:
    path = projects / "apps" / name
    path.mkdir(parents=True)
    return path


# ─────────────────────────── link primitives ────────────────────────────────


def test_read_link_sees_through_a_junction(estate: tuple[Path, Path]) -> None:
    projects, mroot = estate
    media = mroot / "alpha"
    media.mkdir()
    (media / "clip.mp4").write_text("x")
    link = _project(projects, "alpha") / ".media"

    make_link(link, media)

    assert is_link(link)
    assert read_link(link) == media
    assert (link / "clip.mp4").read_text() == "x"


def test_read_link_returns_none_for_a_real_directory(estate: tuple[Path, Path]) -> None:
    projects, _ = estate
    real = _project(projects, "beta") / ".media"
    real.mkdir()

    assert read_link(real) is None
    assert not is_link(real)


def test_a_broken_link_still_looks_like_a_directory(estate: tuple[Path, Path]) -> None:
    """The trap this whole plugin exists for: a severed link reads as empty."""
    projects, mroot = estate
    media = mroot / "gamma"
    media.mkdir()
    link = _project(projects, "gamma") / ".media"
    make_link(link, media)

    media.rmdir()

    # Windows keeps reporting the junction as a directory, and listing it is
    # empty rather than an error — indistinguishable from "no media yet".
    assert list(link.iterdir()) == [] if link.is_dir() else True
    # ...but reading the reparse point tells the truth.
    assert read_link(link) == media
    assert not media.exists()


def test_remove_link_refuses_a_real_directory(estate: tuple[Path, Path]) -> None:
    """The guard that stops a media tree being deleted by mistake."""
    projects, _ = estate
    real = _project(projects, "delta") / ".media"
    real.mkdir()
    (real / "keep.txt").write_text("precious")

    with pytest.raises(ValueError, match="refusing"):
        remove_link(real)

    assert (real / "keep.txt").read_text() == "precious"


def test_remove_link_leaves_the_target_untouched(estate: tuple[Path, Path]) -> None:
    projects, mroot = estate
    media = mroot / "epsilon"
    media.mkdir()
    (media / "keep.txt").write_text("precious")
    link = _project(projects, "epsilon") / ".media"
    make_link(link, media)

    remove_link(link)

    assert not link.exists()
    assert (media / "keep.txt").read_text() == "precious"


# ───────────────────────────── the findings ─────────────────────────────────


def test_healthy_link_has_no_findings(estate: tuple[Path, Path]) -> None:
    projects, mroot = estate
    media = mroot / "alpha"
    media.mkdir()
    link = _project(projects, "alpha") / ".media"
    make_link(link, media)

    report = scan(_cfg(projects, mroot), check_git=False)

    assert [e.name for e in report.entries] == ["alpha"]
    assert report.entries[0].ok
    assert report.problems == []


def test_dangling_link_is_reported_broken(estate: tuple[Path, Path]) -> None:
    projects, mroot = estate
    media = mroot / "gone"
    media.mkdir()
    link = _project(projects, "gone") / ".media"
    make_link(link, media)
    media.rmdir()

    report = scan(_cfg(projects, mroot), check_git=False)

    assert Finding.BROKEN in report.entries[0].findings
    assert report.severe


def test_real_directory_where_a_link_belongs_is_reported(
    estate: tuple[Path, Path],
) -> None:
    """Media living inside the code repo — the drift the split prevents."""
    projects, mroot = estate
    real = _project(projects, "webpocket") / ".media"
    real.mkdir()
    (real / "icon.png").write_bytes(b"\x89PNG")

    report = scan(_cfg(projects, mroot), check_git=False)

    entry = report.entries[0]
    assert Finding.NOT_A_LINK in entry.findings
    assert "1 file" in entry.detail
    assert entry.severe


def test_project_with_waiting_media_but_no_link_is_unlinked(
    estate: tuple[Path, Path],
) -> None:
    projects, mroot = estate
    (mroot / "blindspot").mkdir()
    _project(projects, "blindspot")

    report = scan(_cfg(projects, mroot), check_git=False)

    entry = report.entries[0]
    assert entry.findings == [Finding.UNLINKED]
    assert entry.target == mroot / "blindspot"
    # Untidy, not broken: it should not fail a non-strict verify.
    assert not entry.severe


def test_link_outside_the_media_root_is_foreign(
    estate: tuple[Path, Path], tmp_path: Path
) -> None:
    projects, mroot = estate
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link = _project(projects, "stray") / ".media"
    make_link(link, elsewhere)

    report = scan(_cfg(projects, mroot), check_git=False)

    assert Finding.FOREIGN in report.entries[0].findings


def test_project_without_media_anywhere_is_not_reported(
    estate: tuple[Path, Path],
) -> None:
    """No link and nothing waiting in the media tree is a normal code-only project."""
    projects, mroot = estate
    _project(projects, "pure-code")

    report = scan(_cfg(projects, mroot), check_git=False)

    assert report.entries == []


# ───────────────────────────── orphan folders ───────────────────────────────


def test_unreferenced_media_folder_is_an_orphan(estate: tuple[Path, Path]) -> None:
    projects, mroot = estate
    (mroot / "nobody-points-here").mkdir()

    report = scan(_cfg(projects, mroot), check_git=False)

    assert report.orphans == [mroot / "nobody-points-here"]


def test_shared_trees_are_never_orphans(estate: tuple[Path, Path]) -> None:
    """A leading '.' or '_' marks a tree owned by no single project."""
    projects, mroot = estate
    for name in (".assets", ".presence", ".private", ".trash", "_archive"):
        (mroot / name).mkdir()

    report = scan(_cfg(projects, mroot), check_git=False)

    assert report.orphans == []


def test_a_linked_folder_is_not_an_orphan(estate: tuple[Path, Path]) -> None:
    projects, mroot = estate
    media = mroot / "alpha"
    media.mkdir()
    make_link(_project(projects, "alpha") / ".media", media)

    report = scan(_cfg(projects, mroot), check_git=False)

    assert report.orphans == []


# ──────────────────────── mixed-depth link roots ────────────────────────────


def test_links_are_found_at_every_depth_of_a_space_root(tmp_path: Path) -> None:
    """A space links at depth 1, its sub-spaces at depth 3 — both must be seen."""
    spaces = tmp_path / "spaces"
    mroot = tmp_path / "media"
    presence = mroot / ".presence"
    (presence / "persona").mkdir(parents=True)

    top = spaces / "presence-space"
    sub = top / "spaces" / "persona"
    sub.mkdir(parents=True)
    make_link(top / ".media", presence)
    make_link(sub / ".media", presence / "persona")

    cfg = MediaConfig(
        media_root=mroot,
        roots=(ScanRoot(path=spaces, project_depth=0, max_depth=3),),
    )
    report = scan(cfg, check_git=False)

    assert {e.name for e in report.entries} == {"presence-space", "persona"}
    assert report.problems == []


def test_project_depth_zero_skips_the_unlinked_check(tmp_path: Path) -> None:
    """A folder in a space tree is not a project, so a name match is not a finding."""
    spaces = tmp_path / "spaces"
    mroot = tmp_path / "media"
    (spaces / "somaneo").mkdir(parents=True)
    (mroot / "somaneo").mkdir(parents=True)

    cfg = MediaConfig(
        media_root=mroot,
        roots=(ScanRoot(path=spaces, project_depth=0, max_depth=2),),
    )
    report = scan(cfg, check_git=False)

    assert report.entries == []


# ─────────────────────────────── report shape ───────────────────────────────


def test_report_counts_and_ordering(estate: tuple[Path, Path]) -> None:
    projects, mroot = estate
    healthy = mroot / "good"
    healthy.mkdir()
    make_link(_project(projects, "good") / ".media", healthy)

    broken_target = mroot / "bad"
    broken_target.mkdir()
    make_link(_project(projects, "bad") / ".media", broken_target)
    broken_target.rmdir()

    report = scan(_cfg(projects, mroot), check_git=False)
    counts = report.to_dict()["counts"]

    assert counts == {
        "checked": 2,
        "ok": 1,
        "problems": 1,
        "severe": 1,
        "orphans": 0,
    }
    # Problems sort first so the eye lands on them.
    assert report.entries[0].name == "bad"


def test_json_round_trips_to_plain_types(estate: tuple[Path, Path]) -> None:
    import json  # noqa: PLC0415

    projects, mroot = estate
    media = mroot / "alpha"
    media.mkdir()
    make_link(_project(projects, "alpha") / ".media", media)

    payload = json.loads(json.dumps(scan(_cfg(projects, mroot), check_git=False).to_dict()))

    assert payload["entries"][0]["findings"] == []
    assert payload["entries"][0]["target"] == str(media)


# ───────────────────────────── the git check ────────────────────────────────


@pytest.mark.skipif(not os.environ.get("PATH"), reason="needs a shell PATH")
def test_unignored_link_in_a_repo_is_flagged(estate: tuple[Path, Path]) -> None:
    import subprocess  # noqa: PLC0415

    if not __import__("shutil").which("git"):
        pytest.skip("git not installed")

    projects, mroot = estate
    project = _project(projects, "repo")
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    media = mroot / "repo"
    media.mkdir()
    make_link(project / ".media", media)

    entry = inspect_link(project / ".media", _cfg(projects, mroot), check_git=True)
    assert Finding.UNIGNORED in entry.findings

    (project / ".gitignore").write_text(".media/\n", encoding="utf-8")
    entry = inspect_link(project / ".media", _cfg(projects, mroot), check_git=True)
    assert Finding.UNIGNORED not in entry.findings


def test_tracked_media_is_reported_as_tracked_not_unignored(
    estate: tuple[Path, Path],
) -> None:
    """Regression: an ignore rule cannot take effect on an already-tracked path.

    Found on a live repo — the media had been committed, so `check-ignore` kept
    answering "not ignored" however many rules were appended, and the obvious
    automatic fix (append a rule) silently did nothing.
    """
    import subprocess  # noqa: PLC0415

    if not shutil.which("git"):
        pytest.skip("git not installed")

    projects, mroot = estate
    project = _project(projects, "committed")
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(
        ["git", "-C", str(project), "config", "user.email", "t@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(project), "config", "user.name", "t"], check=True)

    # Media committed while it still lived inside the repo.
    inside = project / ".media"
    inside.mkdir()
    (inside / "icon.png").write_bytes(b"\x89PNG")
    subprocess.run(["git", "-C", str(project), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(project), "commit", "-qm", "media"], check=True)

    # Now do the migration: move it out, link it back, add the ignore rule.
    media = mroot / "committed"
    media.mkdir()
    shutil.move(str(inside / "icon.png"), str(media / "icon.png"))
    inside.rmdir()
    make_link(project / ".media", media)
    (project / ".gitignore").write_text(".media/\n", encoding="utf-8")

    entry = inspect_link(project / ".media", _cfg(projects, mroot), check_git=True)

    assert Finding.TRACKED in entry.findings
    # The misleading one must NOT also fire — it would send you to add a rule
    # that is already there and cannot work.
    assert Finding.UNIGNORED not in entry.findings
    assert "rm -r --cached" in entry.detail


def test_missing_root_is_skipped_not_fatal(tmp_path: Path) -> None:
    cfg = MediaConfig(
        media_root=tmp_path / "mroot",
        roots=(ScanRoot(path=tmp_path / "does-not-exist", project_depth=2),),
    )
    assert scan(cfg, check_git=False).entries == []


@pytest.mark.skipif(sys.platform != "win32", reason="junction semantics are Windows-only")
def test_junction_is_not_a_symlink_to_python(estate: tuple[Path, Path]) -> None:
    """Why os.path.islink() cannot be used to find these links."""
    projects, mroot = estate
    media = mroot / "alpha"
    media.mkdir()
    link = _project(projects, "alpha") / ".media"
    make_link(link, media)

    assert not os.path.islink(link)  # the trap
    assert is_link(link)  # the fix
