"""The briefing says how far behind origin/main each worktree's BASE is — and resolves the
repo root correctly when the hook itself runs from a linked worktree.

"Last commit 5 days ago" says when a worktree was touched; it says nothing about how much
of main it has never seen. Measured 2026-09-19: two worktrees sat 900 and 1,178 commits
behind, still running the pre-#1447/#1459 scripts that killed other sessions' processes.

And the hook's `repo_root()` walked up to the first `.git` — a FILE in a linked worktree,
which `.exists()` accepts — so every session opened in `.dev/worktrees/<x>` got a briefing
calling the main checkout and every sibling "SIBLING checkout OUTSIDE the repo (forbidden)".
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

_HOOK = Path(__file__).resolve().parents[3] / "scripts" / "agent-hooks" / "session_start.py"


@pytest.fixture(scope="module")
def hook():
    spec = importlib.util.spec_from_file_location("session_start_hook_wt_lag", _HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True).stdout.strip()


def _commit(root: Path, name: str) -> None:
    (root / name).write_text("x\n", encoding="utf-8")
    _git("add", name, cwd=root)
    _git("commit", "-q", "-m", f"add {name}", cwd=root)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    bare = tmp_path / "origin.git"
    _git("init", "-q", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-q", "-b", "main", cwd=root)
    _git("config", "user.email", "t@navig.local", cwd=root)
    _git("config", "user.name", "t", cwd=root)
    _commit(root, "base.txt")
    _git("remote", "add", "origin", str(bare), cwd=root)
    _git("push", "-q", "-u", "origin", "main", cwd=root)
    return root


def _add_worktree(repo: Path, slug: str) -> Path:
    wt = repo / ".dev" / "worktrees" / slug
    wt.parent.mkdir(parents=True, exist_ok=True)
    _git("worktree", "add", "-q", str(wt), "-b", f"feat/{slug}", cwd=repo)
    return wt


def _advance_main(repo: Path, n: int) -> None:
    """n commits on local main, pushed, tracking ref updated — the worktree's base stays put."""
    for i in range(n):
        _commit(repo, f"main{i}.txt")
    _git("push", "-q", "origin", "main", cwd=repo)


# ── the behind note ───────────────────────────────────────────────────────────


def test_a_current_worktree_gets_no_behind_note(hook, repo: Path):
    wt = _add_worktree(repo, "fresh")
    text = hook.briefing(repo)
    assert f"extra worktree: {wt.as_posix()}" in text.replace("\\", "/") or "extra worktree:" in text
    assert "behind origin/main; rebase" not in text


def test_below_the_threshold_is_listed_without_a_warning(hook, repo: Path):
    _add_worktree(repo, "recent")
    _advance_main(repo, hook.WORKTREE_BEHIND_WARN - 1)
    text = hook.briefing(repo)
    assert "extra worktree:" in text
    assert "behind origin/main; rebase" not in text


def test_at_the_threshold_the_note_fires_with_the_count_and_the_command(hook, repo: Path):
    wt = _add_worktree(repo, "old")
    _commit(wt, "own.txt")  # unlanded work: stale, not finished (finished has its own test below)
    _advance_main(repo, hook.WORKTREE_BEHIND_WARN)
    text = hook.briefing(repo)
    assert f"{hook.WORKTREE_BEHIND_WARN} commit(s) behind origin/main; rebase before more work lands here" in text
    assert f"git -C {wt}" in text or f"git -C {wt.as_posix()}" in text.replace("\\", "/")


def test_the_note_is_per_worktree(hook, repo: Path):
    """One old, one fresh: only the old one is warned. A count computed once for all
    worktrees would tar the fresh one with the old one's number."""
    old = _add_worktree(repo, "old")
    _commit(old, "own.txt")
    _advance_main(repo, hook.WORKTREE_BEHIND_WARN + 5)
    fresh = _add_worktree(repo, "fresh")  # branched from the NEW main
    text = hook.briefing(repo)
    lines = [ln for ln in text.splitlines() if "extra worktree:" in ln]
    assert len(lines) == 2
    old_line = next(ln for ln in lines if "/old" in ln.replace("\\", "/"))
    fresh_line = next(ln for ln in lines if fresh.name in ln)
    assert "behind origin/main" in old_line
    assert "behind origin/main" not in fresh_line


# ── root resolution from inside a linked worktree ─────────────────────────────


def test_repo_root_from_a_linked_worktree_is_the_main_tree(hook, repo: Path, tmp_path: Path):
    """THE regression. Copy the hook INTO a worktree (as a checkout would carry it) and
    resolve from there: it must answer the main tree, not the worktree."""
    wt = _add_worktree(repo, "wt")
    dest = wt / "scripts" / "agent-hooks"
    dest.mkdir(parents=True)
    shutil.copy2(_HOOK, dest / "session_start.py")

    spec = importlib.util.spec_from_file_location("session_start_from_wt", dest / "session_start.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod.repo_root().resolve() == repo.resolve(), (
        f"root resolved to {mod.repo_root()} — a linked worktree's .git is a FILE and the "
        "old .exists() walk stopped there"
    )


def test_a_briefing_from_inside_a_worktree_does_not_call_the_main_tree_a_sibling(hook, repo: Path):
    wt = _add_worktree(repo, "wt")
    dest = wt / "scripts" / "agent-hooks"
    dest.mkdir(parents=True)
    shutil.copy2(_HOOK, dest / "session_start.py")
    spec = importlib.util.spec_from_file_location("session_start_from_wt2", dest / "session_start.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    text = mod.briefing(mod.repo_root())
    assert "SIBLING checkout OUTSIDE the repo" not in text, text
    # and the main tree is not listed as an "extra worktree" of itself
    assert not any(
        ln.replace("\\", "/").rstrip("/").endswith(repo.as_posix().rstrip("/"))
        for ln in text.splitlines() if "extra worktree:" in ln
    ), text


# ── parity with navig repo ────────────────────────────────────────────────────


def test_the_threshold_matches_navig_repo(hook):
    """The hook cannot import navig, so the number is repeated. Pin the two together."""
    from navig.commands import repo as R

    assert hook.WORKTREE_BEHIND_WARN == R.WORKTREE_BEHIND_WARN


# ── finished, not merely stale ────────────────────────────────────────────────


def test_a_clean_merged_far_behind_worktree_is_called_finished_with_the_sweep_command(hook, repo: Path):
    """Its HEAD is on origin/main, its tree is clean, main has moved ≥ threshold past it:
    nothing here is not already on main. That is a `navig repo sweep --yes` candidate, and
    the briefing must say so rather than "rebase before more work lands" — there is no
    work to land. Measured before this existed: one sat 904 behind for eight weeks."""
    wt = _add_worktree(repo, "done")
    _advance_main(repo, hook.WORKTREE_BEHIND_WARN)
    text = hook.briefing(repo)
    line = next(ln for ln in text.splitlines() if "extra worktree:" in ln and wt.name in ln)
    assert "FINISHED" in line and "navig repo sweep --yes" in line, line
    assert "rebase before" not in line
    assert "1 finished worktree(s)" in text and "done" in text


def test_a_far_behind_worktree_with_its_own_commit_is_stale_not_finished(hook, repo: Path):
    wt = _add_worktree(repo, "live")
    _commit(wt, "work.txt")  # unlanded work
    _advance_main(repo, hook.WORKTREE_BEHIND_WARN)
    text = hook.briefing(repo)
    line = next(ln for ln in text.splitlines() if "extra worktree:" in ln and wt.name in ln)
    assert "rebase before" in line and "FINISHED" not in line
    assert "finished worktree(s)" not in text


def test_a_dirty_merged_far_behind_worktree_is_stale_not_finished(hook, repo: Path):
    wt = _add_worktree(repo, "wip")
    (wt / "draft.txt").write_text("uncommitted", encoding="utf-8")
    _advance_main(repo, hook.WORKTREE_BEHIND_WARN)
    text = hook.briefing(repo)
    line = next(ln for ln in text.splitlines() if "extra worktree:" in ln and wt.name in ln)
    assert "FINISHED" not in line, "uncommitted work is still work — never call it finished"
    assert "finished worktree(s)" not in text


def test_no_worktrees_at_all_does_not_crash_the_summary(hook, repo: Path):
    """`finished_wts` used to be declared inside the worktree block — a repo whose porcelain
    listing came back empty would have hit a NameError at the summary line."""
    assert "finished worktree" not in hook.briefing(repo)


# -- a worktree left holding the default branch --------------------------------


def test_the_briefing_flags_a_worktree_holding_main(hook, repo: Path):
    _git("checkout", "-q", "-b", "feat/primary", cwd=repo)
    wt = repo / ".dev" / "worktrees" / "merged-here"
    wt.parent.mkdir(parents=True, exist_ok=True)
    _git("worktree", "add", "-q", str(wt), "main", cwd=repo)
    normal = _add_worktree(repo, "normal")
    text = hook.briefing(repo)
    flagged = [ln for ln in text.splitlines() if "holds `main`" in ln]
    assert len(flagged) == 1, text
    assert "merged-here" in flagged[0] and "navig repo sweep --yes" in flagged[0]
    assert normal.name not in flagged[0]
    assert flagged[0].isascii(), "the hook pipe garbles non-ASCII"
