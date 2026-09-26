"""tools/version_bump.py — the documented release path — rotates the changelog in the release commit.

The 3.25.0 release rotated ``[Unreleased]`` under the version heading BY HAND in the
release commit; ``version_bump.py`` touched only the version manifests. So the first bump
after changelog fragments landed (#1458) would have shipped a tag whose changelog still
said "Unreleased" and left every fragment on disk. These run the real script against a
real git repo under ``tmp_path`` (manifest sync stubbed — its subject is tested in
test_version_sync.py) and pin: fragments folded + rotated in the SAME commit as the
version, the refusals firing BEFORE pyproject.toml is rewritten, and ``--no-changelog``
as the one deliberate hatch.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parents[2] / "tools"
EM_DASH = "—"


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=True,
    ).stdout.strip()


@pytest.fixture()
def bump(tmp_path: Path, monkeypatch):
    """The script, retargeted at a throwaway core/ that is a real git repo on main."""
    core = tmp_path / "core"
    (core / "changelog.d").mkdir(parents=True)
    (core / "changelog.d" / "README.md").write_text("# contract\n", encoding="utf-8")
    (core / "pyproject.toml").write_text(
        '[project]\nname = "navig"\nversion = "3.25.0"\n', encoding="utf-8"
    )
    (core / "latest.json").write_text('{"version": "3.25.0"}\n', encoding="utf-8")
    (core / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n<!-- t -->\n\n### Fixed\n- **Old unreleased fix.** o\n\n"
        f"## [3.25.0] {EM_DASH} 2026-09-03\n\n### Added\n- **released.**\n",
        encoding="utf-8",
    )
    _git("init", "-q", "-b", "main", cwd=core)
    _git("config", "user.email", "t@navig.local", cwd=core)
    _git("config", "user.name", "navig-test", cwd=core)
    _git("add", "-A", cwd=core)
    _git("commit", "-qm", "base", cwd=core)

    # Load fresh under a private name: the module inserts tools/ on sys.path and imports
    # its siblings, so a stale cached copy would carry another test's REPO_ROOT.
    for name in ("navig_version_bump_under_test", "_version_sync", "changelog_assemble"):
        sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        "navig_version_bump_under_test", _TOOLS / "version_bump.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "REPO_ROOT", core)
    monkeypatch.setattr(mod, "PYPROJECT_PATH", core / "pyproject.toml")
    monkeypatch.setattr(mod, "_sync_manifests", lambda **kw: str(core / "latest.json"))
    return mod, core


def _run(mod, argv: list[str]) -> int:
    sys.argv = ["version_bump.py", *argv]
    return mod.main()


def test_bump_commit_folds_fragments_and_rotates_the_changelog_in_the_release_commit(bump) -> None:
    mod, core = bump
    # A real fragment is TRACKED — its PR committed it — so consuming it is a deletion
    # the release commit must carry; an untracked file would vanish without a trace.
    (core / "changelog.d" / "late.added.md").write_text("- **Late add.** l\n", encoding="utf-8")
    _git("add", "changelog.d/late.added.md", cwd=core)
    _git("commit", "-qm", "feat: late (fragment)", cwd=core)
    assert _run(mod, ["bump", "patch", "--commit"]) == 0

    text = (core / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [3.25.1] {EM_DASH} " in text
    released = text[text.index("## [3.25.1]"): text.index("## [3.25.0]")]
    assert "- **Late add.** l" in released and "- **Old unreleased fix.** o" in released
    assert not (core / "changelog.d" / "late.added.md").exists()
    assert (core / "changelog.d" / "README.md").exists()

    assert mod.read_version() == "3.25.1"
    shown = _git("show", "--stat", "--format=%s", "HEAD", cwd=core)
    assert shown.startswith("chore(release): bump version to 3.25.1")
    assert "CHANGELOG.md" in shown and "pyproject.toml" in shown
    assert "changelog.d/late.added.md" in shown, "the consumed fragment's deletion is in the same commit"
    assert _git("status", "--porcelain", cwd=core) == "", "nothing left half-done"


def test_an_empty_release_is_refused_before_pyproject_is_touched(bump) -> None:
    mod, core = bump
    (core / "CHANGELOG.md").write_text(
        f"# C\n\n## [Unreleased]\n\n<!-- t -->\n\n## [3.25.0] {EM_DASH} 2026-09-03\n\n### Added\n- **released.**\n",
        encoding="utf-8",
    )
    _git("commit", "-qam", "empty unreleased", cwd=core)
    with pytest.raises(RuntimeError, match="nothing to put under"):
        _run(mod, ["bump", "patch", "--commit"])
    assert mod.read_version() == "3.25.0", "the refusal fired before the version was rewritten"
    assert _git("status", "--porcelain", cwd=core) == ""
    assert _git("log", "--format=%s", "-1", cwd=core) == "empty unreleased"


def test_no_changelog_is_the_hatch_for_a_release_with_no_entries(bump) -> None:
    mod, core = bump
    (core / "CHANGELOG.md").write_text(
        f"# C\n\n## [Unreleased]\n\n<!-- t -->\n\n## [3.25.0] {EM_DASH} 2026-09-03\n\n### Added\n- **released.**\n",
        encoding="utf-8",
    )
    _git("commit", "-qam", "empty unreleased", cwd=core)
    assert _run(mod, ["bump", "patch", "--commit", "--no-changelog"]) == 0
    assert mod.read_version() == "3.25.1"
    text = (core / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [3.25.1]" not in text, "--no-changelog means the changelog is left alone"
    assert "CHANGELOG.md" not in _git("show", "--stat", "--format=", "HEAD", cwd=core)


def test_a_malformed_fragment_refuses_the_bump_naming_it(bump) -> None:
    mod, core = bump
    (core / "changelog.d" / "oops.bugfix.md").write_text("- **x.**\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="oops.bugfix.md"):
        _run(mod, ["bump", "patch", "--commit"])
    assert mod.read_version() == "3.25.0"
    assert (core / "changelog.d" / "oops.bugfix.md").exists()


def test_dry_run_reports_the_changelog_plan_and_writes_nothing(bump, capsys) -> None:
    mod, core = bump
    (core / "changelog.d" / "one.fixed.md").write_text("- **One.** 1\n", encoding="utf-8")
    before = (core / "CHANGELOG.md").read_bytes()
    assert _run(mod, ["bump", "minor", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "3.25.0 -> 3.26.0" in out
    assert "1 fragment(s) folded" in out and "## [3.26.0]" in out
    assert (core / "CHANGELOG.md").read_bytes() == before
    assert (core / "changelog.d" / "one.fixed.md").exists()
    assert mod.read_version() == "3.25.0"


def test_push_sends_the_release_commit_to_main_and_then_the_tag(bump) -> None:
    """The tag alone used to be pushed: origin/main kept the OLD version and none of the
    changelog rotation. A tag whose commit is not on main is half a release."""
    mod, core = bump
    origin = core.parent / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
    _git("remote", "add", "origin", str(origin), cwd=core)
    _git("push", "-q", "-u", "origin", "main", cwd=core)
    (core / "changelog.d" / "one.fixed.md").write_text("- **One.** 1\n", encoding="utf-8")
    _git("add", "changelog.d/one.fixed.md", cwd=core)
    _git("commit", "-qm", "feat: one (fragment)", cwd=core)
    _git("push", "-q", "origin", "main", cwd=core)

    assert _run(mod, ["bump", "patch", "--commit", "--tag", "--push"]) == 0
    local_main = _git("rev-parse", "main", cwd=core)
    remote_main = _git("rev-parse", "refs/heads/main", cwd=origin)
    assert remote_main == local_main, "the release commit is on origin/main, not only reachable from the tag"
    assert _git("rev-parse", "v3.25.1^{commit}", cwd=origin) == local_main
    assert "version" in _git("show", "refs/heads/main:pyproject.toml", cwd=origin)
    assert '3.25.1' in _git("show", "refs/heads/main:pyproject.toml", cwd=origin)
