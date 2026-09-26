"""tools/release.sh — the local release pipeline: its guards run for real, its order is pinned.

The org's GitHub Actions is billing-blocked, so the tag workflow never fires and this
script IS how a release ships (3.25.0 went out by hand, with no tag at all). Found on
2026-09-19 reading it as that pipeline: it never checked that ``pyproject.toml`` carries
the version it was told to release (``release.sh 3.26.0`` on a 3.25.0 tree would have
tagged v3.26.0 over a navig-3.25.0 wheel); it built AFTER tagging; it synced
``latest.json`` BEFORE the GitHub Release existed, so the hand path always recorded a null
``download_url``; its ``gh release create`` pasted a changelog block GitHub refuses over
125,000 chars (3.25.0's is 288 KB) and attached no asset; and it said the tag push
publishes, which it does not here.

Two kinds of test: the GUARDS are executed for real against a throwaway repo (they run
before anything that needs the network or a build, so a refusal is observable and cheap);
the ORDER of the irreversible steps is pinned at source level, because the script commits
to main and pushes tags.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

# Git Bash by full path, never a bare "bash": on Windows CreateProcess searches System32
# before PATH and finds the WSL launcher — see tests/fixtures/posix_shell.py.
from tests.fixtures.posix_shell import POSIX_SHELL as BASH
from tests.fixtures.posix_shell import needs_posix_shell as needs_bash

_CORE = Path(__file__).resolve().parents[2]
_SCRIPT = _CORE / "tools" / "release.sh"


@pytest.fixture(scope="module")
def src() -> str:
    return _SCRIPT.read_text(encoding="utf-8")


def _commands(src: str) -> list[str]:
    """Non-comment lines, so a comment MENTIONING an old name is not a hit."""
    return [ln for ln in src.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=True,
    ).stdout.strip()


@pytest.fixture()
def release_repo(tmp_path: Path) -> Path:
    """A clone of a bare origin, on main, with the three files the guards read and the
    real tools copied in — release.sh cds to its own parent's parent."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
    core = tmp_path / "core"
    core.mkdir()
    _git("init", "-q", "-b", "main", cwd=core)
    _git("config", "user.email", "t@navig.local", cwd=core)
    _git("config", "user.name", "navig-test", cwd=core)
    _git("remote", "add", "origin", str(origin), cwd=core)
    (core / "tools").mkdir()
    for name in ("release.sh", "changelog_assemble.py", "_version_sync.py"):
        shutil.copy(_CORE / "tools" / name, core / "tools" / name)
    (core / "pyproject.toml").write_text('[project]\nname = "navig"\nversion = "3.25.0"\n', encoding="utf-8")
    (core / "latest.json").write_text('{"version": "3.25.0"}\n', encoding="utf-8")
    (core / "CHANGELOG.md").write_text(
        "# C\n\n## [Unreleased]\n\n### Fixed\n- **pending.**\n\n## [3.25.0] \u2014 2026-09-03\n\n### Added\n- **released.**\n",
        encoding="utf-8",
    )
    _git("add", "-A", cwd=core)
    _git("commit", "-qm", "base", cwd=core)
    _git("push", "-q", "-u", "origin", "main", cwd=core)
    return core


def _release(core: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [BASH, str(core / "tools" / "release.sh"), *args], cwd=str(core), capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=120, env=env,
    )


# ── the guards, executed ─────────────────────────────────────────────────────


@needs_bash
def test_refuses_a_version_pyproject_does_not_carry(release_repo: Path) -> None:
    r = _release(release_repo, "3.26.0")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "pyproject.toml says 3.25.0, not 3.26.0" in r.stdout
    assert "version_bump.py" in r.stdout, "the fix is named"
    assert _git("log", "--format=%s", "-1", cwd=release_repo) == "base", "refused means no commit"
    assert _git("tag", "-l", cwd=release_repo) == ""


@needs_bash
def test_refuses_a_tag_that_already_exists(release_repo: Path) -> None:
    _git("tag", "-a", "v3.25.0", "-m", "x", cwd=release_repo)
    r = _release(release_repo, "3.25.0")
    assert r.returncode == 1 and "tag v3.25.0 already exists locally" in r.stdout
    _git("tag", "-d", "v3.25.0", cwd=release_repo)
    _git("tag", "-a", "v3.25.0", "-m", "x", cwd=release_repo)
    _git("push", "-q", "origin", "v3.25.0", cwd=release_repo)
    _git("tag", "-d", "v3.25.0", cwd=release_repo)
    r = _release(release_repo, "3.25.0")
    assert r.returncode == 1 and "already exists on origin" in r.stdout, "a remote-only tag is a tag"


@needs_bash
def test_refuses_off_main_and_a_dirty_tree(release_repo: Path) -> None:
    (release_repo / "wip.txt").write_text("x\n", encoding="utf-8")
    _git("add", "wip.txt", cwd=release_repo)
    r = _release(release_repo, "3.25.0")
    assert r.returncode == 1 and "uncommitted changes" in r.stdout
    _git("commit", "-qm", "wip", cwd=release_repo)
    _git("checkout", "-qb", "feat/x", cwd=release_repo)
    r = _release(release_repo, "3.25.0")
    assert r.returncode == 1 and "must be on main" in r.stdout


@needs_bash
def test_an_unknown_option_is_refused_not_ignored(release_repo: Path) -> None:
    r = _release(release_repo, "3.25.0", "--pubilsh")
    assert r.returncode == 1 and "Unknown option: --pubilsh" in r.stdout


# ── the irreversible steps, at source level ──────────────────────────────────


def test_build_and_verify_come_before_the_tag_and_the_notes_before_both(src: str) -> None:
    cmds = "\n".join(_commands(src))
    notes = cmds.index('changelog_assemble.py --release-notes "$VERSION"')
    build = cmds.index("python -m build\n")
    wheel_check = cmds.index('dist/navig-"$VERSION"-*.whl')
    verify = cmds.index("verify-install.mjs")
    tag = cmds.index('git tag -a "$TAG"')
    assert notes < build < wheel_check < verify < tag, (
        "compose the body (a missing block stops here) -> build -> prove the wheel carries "
        "the version -> real-install smoke -> only then tag"
    )


def test_the_version_guard_is_the_pyproject_version(src: str) -> None:
    cmds = "\n".join(_commands(src))
    assert 'if [[ "$PYPROJECT_VERSION" != "$VERSION" ]]' in cmds
    assert cmds.index("PYPROJECT_VERSION=") < cmds.index("git pull --ff-only"), "before anything touches the tree"


def test_latest_json_is_recorded_after_the_release_exists(src: str) -> None:
    cmds = "\n".join(_commands(src))
    release = cmds.index("gh release create")
    record = cmds.index('_version_sync.py --version "$VERSION" --released-at')
    assert release < record, "download_url is only real once the asset it names exists"
    assert cmds.count("git push origin main") == 2, "the release commit, then the manifest commit"


def test_the_release_attaches_the_artifacts_and_uses_the_one_body(src: str) -> None:
    cmds = "\n".join(_commands(src))
    line = next(ln for ln in cmds.splitlines() if "gh release create" in ln and "--notes-file" in ln)
    assert re.search(r"--generate-notes dist/\*;? ", line + " "), "assets attached, so latest.json can verify one"
    assert "--generate-notes" in line, "the PR list appended after our body, as release.yml's action does"
    assert "awk " not in cmds, "the body comes from the one writer, not a second extractor"


def test_publish_is_explicit_and_the_workflow_claim_is_honest(src: str) -> None:
    cmds = "\n".join(_commands(src))
    assert "twine upload dist/*" in cmds
    assert 'if [[ "$PUBLISH" == "true" ]]' in cmds
    assert "publish.yml" not in cmds
    assert not re.search(r"\bdevelop\b", cmds), "there is no develop branch; releases cut from main"
    assert "billing-blocked" in src, "say why this script publishes instead of the tag workflow"


@needs_bash
def test_the_script_parses() -> None:
    res = subprocess.run([BASH, "-n", str(_SCRIPT)], capture_output=True, timeout=30)
    assert res.returncode == 0, res.stderr.decode("utf-8", "replace")
