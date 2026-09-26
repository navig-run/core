"""The SessionStart briefing: judged against the REMOTE default, counts what is sweepable,
and survives a broken venv.

Three defects in one file, found while wiring ``navig repo sweep`` into it:

1. The hook imported ``navig.platform.process`` UNGUARDED, in direct contradiction of
   its own docstring ("never imports navig — must work even when the venv is broken").
   With navig un-importable, ImportError escaped the ``except``, ``briefing()`` died and
   ``main()`` swallowed it: exit 0, ZERO bytes. Measured. The briefing vanished in exactly
   the broken-environment case it exists for.
2. "Not merged" was judged against LOCAL ``main`` — the ref that goes stale in a shared
   checkout because agents merge through GitHub (measured 107 PRs behind). A branch merged
   on the remote read as unmerged, so the briefing cried wolf at every session start.
3. Branches provably already on the base were reported only as a hedge —
   "(remote gone - merged remotely? verify then delete)" — or not at all. The two proofs
   that need no network (ancestor tip, identical tree) are now counted and named, pointing
   at the command that deletes them.

Loads the hook by file path, exactly like its sibling test.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_HOOK = _REPO_ROOT / "scripts" / "agent-hooks" / "session_start.py"
_TEMPLATE = _REPO_ROOT / "core" / "navig" / "guard" / "session_start.py"


@pytest.fixture(scope="module")
def hook():
    spec = importlib.util.spec_from_file_location("session_start_hook_sweep", _HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(*args: str, cwd: Path) -> str:
    res = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )
    return res.stdout.strip()


def _commit(root: Path, name: str, content: str = "x") -> str:
    (root / name).write_text(content + "\n", encoding="utf-8")
    _git("add", name, cwd=root)
    _git("commit", "-q", "-m", f"add {name}", cwd=root)
    return _git("rev-parse", "HEAD", cwd=root)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A clone with a real ``origin``, so ``origin/main`` exists — the base the hook must use."""
    bare = tmp_path / "origin.git"
    _git("init", "-q", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-q", "-b", "main", cwd=root)
    _git("config", "user.email", "test@navig.local", cwd=root)
    _git("config", "user.name", "navig-test", cwd=root)
    _commit(root, "base.txt")
    _git("remote", "add", "origin", str(bare), cwd=root)
    _git("push", "-q", "-u", "origin", "main", cwd=root)
    return root


# -- 1. the contract: stdlib + git only ---------------------------------------


def test_the_briefing_survives_a_broken_venv(tmp_path: Path, repo: Path):
    """Run the hook as Claude Code does — a subprocess — with navig UN-importable.

    Before: exit 0, zero bytes. The only acceptable outcome is a briefing.
    """
    shadow = tmp_path / "nonavig"
    shadow.mkdir()
    (shadow / "navig.py").write_text('raise ImportError("simulated broken venv")\n', encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(shadow), "PYTHONUTF8": "1"}
    # The hook discovers the repo from its own file location, so copy it INTO the temp repo.
    hook_copy = repo / "scripts" / "agent-hooks" / "session_start.py"
    hook_copy.parent.mkdir(parents=True)
    hook_copy.write_text(_HOOK.read_text(encoding="utf-8"), encoding="utf-8")

    res = subprocess.run(
        [sys.executable, str(hook_copy)], cwd=str(repo), capture_output=True, text=True,
        env=env, timeout=60, encoding="utf-8",
    )

    assert res.returncode == 0
    assert "[repo-guard]" in res.stdout, (
        f"the briefing vanished with navig un-importable — stdout was {len(res.stdout)} bytes"
    )


def test_spawn_kwargs_degrades_to_nothing_without_navig(hook, monkeypatch):
    """The helper itself, isolated: an import failure yields {} and never raises."""
    monkeypatch.setitem(sys.modules, "navig.platform.process", None)  # forces ImportError

    assert hook._spawn_kwargs() == {}


def test_the_two_copies_are_still_identical():
    """Synaptic: the packaged template is what `guard install` ships; scripts/ is
    this repo's live copy. CRLF-insensitive, because the working tree may carry it."""
    live = _HOOK.read_text(encoding="utf-8")
    template = _TEMPLATE.read_text(encoding="utf-8")

    assert live == template


# -- 2. the base is the remote default ----------------------------------------


def test_merged_is_judged_against_the_remote_default(hook, repo: Path):
    """A branch merged on the remote while local main sits behind must NOT be reported
    as unmerged — that false alarm is what a stale local base produces."""
    _git("checkout", "-q", "-b", "feat/landed", cwd=repo)
    _commit(repo, "landed.txt")
    _git("push", "-q", "origin", "feat/landed:main", cwd=repo)  # merged remotely
    _git("checkout", "-q", "main", cwd=repo)                    # local main is BEHIND
    _git("fetch", "-q", "origin", cwd=repo)

    text = hook.briefing(repo)

    assert "not merged into origin/main: feat/landed" not in text
    assert "not merged into main:" not in text, "must name the remote base, never the local one"


def test_without_a_remote_the_local_branch_is_the_base(hook, tmp_path: Path):
    root = tmp_path / "lonely"
    root.mkdir()
    _git("init", "-q", "-b", "main", cwd=root)
    _git("config", "user.email", "t@navig.local", cwd=root)
    _git("config", "user.name", "t", cwd=root)
    _commit(root, "a.txt")
    _git("checkout", "-q", "-b", "feat/x", cwd=root)
    _commit(root, "b.txt")
    _git("checkout", "-q", "main", cwd=root)

    text = hook.briefing(root)

    assert "not merged into main: feat/x" in text


# -- 3. the sweep line --------------------------------------------------------


def test_an_ancestor_branch_is_counted_and_named(hook, repo: Path):
    base_sha = _git("rev-parse", "HEAD", cwd=repo)
    _git("branch", "old/pointer", base_sha, cwd=repo)

    text = hook.briefing(repo)

    assert "1 branch(es) provably already on origin/main (old/pointer)" in text
    assert "navig repo sweep --yes" in text
    assert "not merged into origin/main: old/pointer" not in text


def test_a_squash_merged_branch_is_counted_by_its_tree(hook, repo: Path):
    """Off the ancestry, on the base by content — the case ancestry cannot see."""
    _git("checkout", "-q", "-b", "feat/squashed", cwd=repo)
    _commit(repo, "sq.txt", "content")
    _git("checkout", "-q", "main", cwd=repo)
    (repo / "sq.txt").write_text("content\n", encoding="utf-8")
    _git("add", "sq.txt", cwd=repo)
    _git("commit", "-q", "-m", "feat: squashed (#1)", cwd=repo)
    _git("push", "-q", "origin", "main", cwd=repo)

    text = hook.briefing(repo)

    assert "provably already on origin/main (feat/squashed)" in text
    assert "not merged into origin/main: feat/squashed" not in text


def test_real_unmerged_work_is_still_reported_not_swept(hook, repo: Path):
    _git("checkout", "-q", "-b", "feat/real", cwd=repo)
    _commit(repo, "real.txt")
    _git("checkout", "-q", "main", cwd=repo)

    text = hook.briefing(repo)

    assert "not merged into origin/main: feat/real" in text
    assert "provably already on" not in text


def test_a_worktree_held_branch_is_never_counted_as_sweepable(hook, repo: Path):
    """Mirrors the sweep's PROTECTED set: a briefing must not advertise deleting a
    branch some worktree is standing on."""
    base_sha = _git("rev-parse", "HEAD", cwd=repo)
    _git("branch", "held/pointer", base_sha, cwd=repo)
    wt = repo / ".dev" / "worktrees" / "held"
    wt.parent.mkdir(parents=True)
    _git("worktree", "add", "-q", str(wt), "held/pointer", cwd=repo)

    text = hook.briefing(repo)

    assert "provably already on" not in text


def test_the_default_branch_is_never_counted(hook, repo: Path):
    """main is trivially its own ancestor; it must never appear as sweepable."""
    text = hook.briefing(repo)

    assert "provably already on" not in text


def test_the_sweep_line_stays_ascii(hook, repo: Path):
    """The Windows hook pipe garbles non-ASCII — the contract at the top of the file."""
    base_sha = _git("rev-parse", "HEAD", cwd=repo)
    _git("branch", "old/pointer", base_sha, cwd=repo)

    text = hook.briefing(repo)

    assert text.isascii(), text
