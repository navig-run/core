"""Tests for the SessionStart briefing hook — the cross-worktree conflict radar.

Loads the hook by file path (stdlib-only script outside the navig package).
"""

from __future__ import annotations

import importlib.util
import subprocess
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_HOOK = _REPO_ROOT / "scripts" / "agent-hooks" / "session_start.py"


@pytest.fixture(scope="module")
def hook():
    spec = importlib.util.spec_from_file_location("session_start_hook", _HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-b", "main", cwd=root)
    _git("config", "user.email", "test@navig.local", cwd=root)
    _git("config", "user.name", "navig-test", cwd=root)
    (root / "f.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")
    _git("add", "f.txt", cwd=root)
    _git("commit", "-m", "base", cwd=root)
    return root


def test_single_worktree_has_no_radar_lines(hook, repo: Path) -> None:
    assert hook.conflict_lines(repo) == []


def test_clean_pair_reports_all_clean(hook, repo: Path) -> None:
    wt = repo / ".dev" / "worktrees" / "wt1"
    _git("worktree", "add", str(wt), "-b", "feat/one", cwd=repo)
    (wt / "g.txt").write_text("new\n", encoding="utf-8")
    _git("add", "g.txt", cwd=wt)
    _git("commit", "-m", "disjoint", cwd=wt)

    lines = hook.conflict_lines(repo)
    assert len(lines) == 1
    assert "1 pair(s), all clean" in lines[0]


def test_conflicting_pair_is_flagged_including_dirty_state(hook, repo: Path) -> None:
    wt = repo / ".dev" / "worktrees" / "wt2"
    _git("worktree", "add", str(wt), "-b", "feat/two", cwd=repo)
    # uncommitted edit in the worktree vs committed edit on main -> collision
    (wt / "f.txt").write_text("WT2-DIRTY\nline2\nline3\n", encoding="utf-8")
    (repo / "f.txt").write_text("MAIN\nline2\nline3\n", encoding="utf-8")
    _git("commit", "-am", "main edit", cwd=repo)

    lines = hook.conflict_lines(repo)
    assert len(lines) == 1
    assert "merge conflict brewing" in lines[0]
    assert "f.txt" in lines[0]
    # the radar must never mutate the worktree
    assert (wt / "f.txt").read_text(encoding="utf-8").startswith("WT2-DIRTY")

    # and the full briefing carries the radar line
    assert "merge conflict brewing" in hook.briefing(repo)


def test_briefing_counts_orphaned_worktree_dirs(hook, repo: Path) -> None:
    """A physical .dev/worktrees dir git no longer tracks is surfaced as a count.

    Registered worktrees are excluded; only the untracked leftover is counted.
    """
    wt = repo / ".dev" / "worktrees" / "live"
    _git("worktree", "add", str(wt), "-b", "feat/live", cwd=repo)
    (repo / ".dev" / "worktrees" / "dead").mkdir(parents=True)  # orphan leftover

    out = hook.briefing(repo)
    assert "1 orphaned worktree dir(s)" in out
    assert "navig repo prune" in out


def test_briefing_names_a_worktree_whose_add_died(hook, repo: Path) -> None:
    """A worktree still locked "initializing" is a partial checkout from an add that died
    (2026-09-15: a 15 s ceiling killed `git` mid-checkout; the tree looked normal in
    `git worktree list` and refused `repo remove` for hours). The briefing must say so
    on the worktree's own line, with the command that clears it - not list it as a
    normal place to work. A deliberate lock with any other reason is left alone."""
    dead = repo / ".dev" / "worktrees" / "dead-add"
    _git("worktree", "add", str(dead), "-b", "feat/dead-add", cwd=repo)
    _git("worktree", "lock", "--reason", "initializing", str(dead), cwd=repo)
    held = repo / ".dev" / "worktrees" / "held"
    _git("worktree", "add", str(held), "-b", "feat/held", cwd=repo)
    _git("worktree", "lock", "--reason", "reviewing", str(held), cwd=repo)

    out = hook.briefing(repo)
    dead_line = next(ln for ln in out.splitlines() if "dead-add" in ln)
    assert "DIED" in dead_line and "locked: initializing" in dead_line
    assert "navig repo remove dead-add --force" in dead_line
    held_line = next(ln for ln in out.splitlines() if "worktrees" in ln and "held" in ln)
    assert "DIED" not in held_line
def test_briefing_counts_changelog_fragments_awaiting_assembly(hook, repo: Path) -> None:
    """core/changelog.d/ fragments pile up until a release; the briefing keeps the pile
    visible with the command that folds it. The README is not a fragment. A repo without
    the directory says nothing."""
    assert "changelog fragment" not in hook.briefing(repo)
    d = repo / "core" / "changelog.d"
    d.mkdir(parents=True)
    (d / "README.md").write_text("# contract\n", encoding="utf-8")
    assert "changelog fragment" not in hook.briefing(repo)
    (d / "one.fixed.md").write_text("- **One.**\n", encoding="utf-8")
    (d / "two.added.md").write_text("- **Two.**\n", encoding="utf-8")
    out = hook.briefing(repo)
    assert "2 changelog fragment(s) awaiting assembly" in out
    assert "npm run changelog:assemble" in out


# ── the lock line says what to DO, not just that a lock exists ───────────────


def _lock(repo: Path, *, minutes_ago: float, sid: str = "abcdef0123456789") -> None:
    import json
    from datetime import datetime, timedelta, timezone

    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    (repo / ".dev").mkdir(exist_ok=True)
    (repo / ".dev" / "agent.lock").write_text(
        json.dumps({"session_id": sid, "branch": "feat/other", "claimed_at": ts, "updated_at": ts}),
        encoding="utf-8",
    )


def test_a_fresh_lock_reads_as_live_with_its_age_and_the_worktree_route(hook, repo: Path) -> None:
    """Before: "another session may be active" for a 7-minute lock and a 3-hour one alike,
    so the reader had to run `navig repo lock status` to learn which. The age and the
    verdict are what an agent needs before its first edit."""
    _lock(repo, minutes_ago=7)
    out = hook.briefing(repo)
    assert "agent lock LIVE" in out
    assert "abcdef01" in out and "feat/other" in out
    assert "touched 7m ago" in out
    assert "navig repo new" in out  # the route, not just the warning


def test_an_expired_lock_reads_as_expired_and_says_the_next_edit_takes_over(hook, repo: Path) -> None:
    _lock(repo, minutes_ago=180)
    out = hook.briefing(repo)
    assert "agent lock EXPIRED" in out
    assert "3.0h ago" in out
    assert "takes the lock over" in out
    assert "LIVE" not in out


def test_the_verdict_flips_exactly_at_the_ttl(hook) -> None:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    just_inside = {"branch": "b", "updated_at": (now - timedelta(minutes=hook.LOCK_TTL_MINUTES - 1)).isoformat()}
    just_past = {"branch": "b", "updated_at": (now - timedelta(minutes=hook.LOCK_TTL_MINUTES + 1)).isoformat()}
    assert "LIVE" in hook._lock_line(just_inside, "s", now=now)
    assert "EXPIRED" in hook._lock_line(just_past, "s", now=now)


def test_an_unreadable_timestamp_is_treated_as_dead_not_live(hook) -> None:
    """agent_lock.py treats a corrupt timestamp as a dead lock (it claims). The briefing
    must not contradict the hook that actually enforces the rule."""
    line = hook._lock_line({"branch": "b", "updated_at": "not-a-date"}, "s")
    assert "treat it as dead" in line
    assert "LIVE" not in line


def test_the_ttl_agrees_with_the_hook_that_enforces_it_and_the_cli(hook) -> None:
    """Three copies of one number (this briefing, agent_lock.py, repo.py) — each says
    "keep in sync" in a comment, which is a claim. Assert it."""
    import importlib.util
    import re

    spec = importlib.util.spec_from_file_location(
        "agent_lock_hook", _REPO_ROOT / "scripts" / "agent-hooks" / "agent_lock.py"
    )
    agent_lock = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent_lock)
    assert hook.LOCK_TTL_MINUTES == agent_lock.TTL_MINUTES

    repo_py = (_REPO_ROOT / "core" / "navig" / "commands" / "repo.py").read_text(encoding="utf-8")
    m = re.search(r"^LOCK_TTL_MINUTES\s*=\s*(\d+)", repo_py, re.M)
    assert m, "repo.py no longer declares LOCK_TTL_MINUTES at module level"
    assert int(m.group(1)) == hook.LOCK_TTL_MINUTES


# ── lost-work radar: commits only a reflog still holds ───────────────────────
#
# 2026-10-13 a reset dropped seven unpushed Schema commits; reading the reflog by hand
# three days later found two MORE nobody had noticed (a lost test file among them).
# Each filter below is one shape of "not actually lost" measured in real reflogs.


def _commit(repo: Path, name: str, body: str, message: str) -> str:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(body, encoding="utf-8")
    _git("add", name, cwd=repo)
    _git("commit", "-m", message, cwd=repo)
    return subprocess.run(
        ["git", "rev-parse", "--short=10", "HEAD"], cwd=str(repo), capture_output=True, text=True, check=True
    ).stdout.strip()


def _drop_last(repo: Path) -> None:
    """What the 10-13 reset did: move the branch off its newest commit."""
    _git("reset", "--hard", "HEAD~1", cwd=repo)


def test_a_dropped_commit_is_named_with_its_rescue_line(hook, repo: Path) -> None:
    sha = _commit(repo, "forgeFinish.test.ts", "the test nobody pushed\n", "every forged passport reaches the Grid")
    _drop_last(repo)
    lines = hook.lost_work_lines(repo, "main")
    assert lines and "held by NO branch, tag or remote" in lines[0]
    short = lines[1].split()[0]  # git abbreviates by repo size: 7 chars here, 10 on navig
    assert sha.startswith(short)
    assert lines[1].endswith(f"rescue: git branch rescue/{short} {short}")
    assert "every forged passport reaches the Grid" in lines[1]
    assert all(line.isascii() for line in lines)


def test_a_kept_commit_is_silent(hook, repo: Path) -> None:
    _commit(repo, "a.txt", "work\n", "work")
    _git("branch", "keep/main", "main", cwd=repo)
    _drop_last(repo)
    assert hook.lost_work_lines(repo, "main") == []


def test_a_rebased_or_amended_copy_is_silent(hook, repo: Path) -> None:
    """The old copy's subject still lives on a ref -- the work moved, it was not lost."""
    _commit(repo, "a.txt", "draft\n", "the board learns its name")
    _git("commit", "--amend", "--no-edit", "--allow-empty", "-m", "the board learns its name", cwd=repo)
    (repo / "a.txt").write_text("final\n", encoding="utf-8")
    _git("commit", "-a", "--amend", "--no-edit", cwd=repo)
    assert hook.lost_work_lines(repo, "main") == []


def test_a_squash_landed_subject_is_silent(hook, repo: Path) -> None:
    """A squash merge names it "<subject> (#123)" -- the suffix must not hide the match."""
    _git("checkout", "-b", "feat/x", cwd=repo)
    _commit(repo, "a.txt", "feature\n", "feat(guard): refuse a reset")
    _git("checkout", "main", cwd=repo)
    _commit(repo, "a.txt", "feature\n", "feat(guard): refuse a reset (#1981)")
    _git("branch", "-D", "feat/x", cwd=repo)
    assert hook.lost_work_lines(repo, "main") == []


def test_a_shared_subject_with_other_content_does_not_hide_a_loss(hook, repo: Path) -> None:
    """`fix tests` on main says nothing about a lost `fix tests` elsewhere (F2953)."""
    _commit(repo, "lost/only_here.py", "the lost change\n", "fix tests")
    _drop_last(repo)
    _commit(repo, "other/unrelated.py", "an unrelated change\n", "fix tests")
    lines = hook.lost_work_lines(repo, "main")
    assert lines and "fix tests" in lines[1]


def test_work_on_base_under_another_subject_is_superseded(hook, repo: Path) -> None:
    """Redone, squashed with more, or re-picked: every file it touched is on base."""
    _commit(repo, "a.txt", "the fix\n", "fix: the first attempt")
    _drop_last(repo)
    _commit(repo, "a.txt", "the fix, better\n", "fix: the real one, later")
    assert hook.lost_work_lines(repo, "main") == []


_SRC = "".join(f"line {i}\n" for i in range(1, 21))


def test_a_later_unrelated_edit_of_the_same_file_does_not_hide_a_loss(hook, repo: Path) -> None:
    """A lost function plus a later comment fix in that file is still lost (F2952)."""
    _commit(repo, "mod.py", _SRC, "the module")
    _commit(repo, "mod.py", _SRC.replace("line 15\n", "line 15\ndef lost_function(): ...\n"),
            "add the function only the reflog keeps")
    _drop_last(repo)
    time.sleep(1.1)  # --since is second-granular: the edit must be AFTER the commit
    _commit(repo, "mod.py", _SRC.replace("line 2\n", "# a comment fix\n"), "fix a comment")
    lines = hook.lost_work_lines(repo, "main")
    assert lines and "add the function only the reflog keeps" in lines[1]


def test_an_edit_whose_hunks_reached_base_with_more_is_superseded(hook, repo: Path) -> None:
    _commit(repo, "mod.py", _SRC, "the module")
    added = _SRC.replace("line 15\n", "line 15\ndef kept(): ...\n")
    _commit(repo, "mod.py", added, "add kept, first copy")
    _drop_last(repo)
    _commit(repo, "mod.py", added.replace("line 2\n", "# a comment fix\n"), "kept, landed with a fix")
    assert hook.lost_work_lines(repo, "main") == []


def test_a_branch_its_own_session_rewrote_still_holding_the_work_is_silent(hook, repo: Path) -> None:
    """feat/x@{1}: the old tip of a rewritten branch whose current tip carries the change."""
    _git("checkout", "-b", "feat/x", cwd=repo)
    _commit(repo, "n.txt", "notes core\n", "wip: notes core sync")
    _drop_last(repo)
    _commit(repo, "n.txt", "notes core\n", "notes: the core sync, finished")
    _git("checkout", "main", cwd=repo)
    assert hook.lost_work_lines(repo, "main") == []


def test_a_dropped_stash_is_not_lost_work(hook, repo: Path) -> None:
    (repo / "f.txt").write_text("scratch edit\n", encoding="utf-8")
    _git("stash", "push", "-m", "scratch", cwd=repo)
    _git("stash", "drop", cwd=repo)
    assert hook.lost_work_lines(repo, "main") == []


def test_a_subject_that_merely_starts_with_on_is_not_a_stash(hook, repo: Path) -> None:
    _commit(repo, "m.txt", "map\n", "On the map, every city breathes")
    _drop_last(repo)
    assert any("On the map" in line for line in hook.lost_work_lines(repo, "main"))


def test_a_non_ascii_subject_reaches_the_pipe_as_ascii(hook, repo: Path) -> None:
    _commit(repo, "r.txt", "ru\n", "Страж на посту")
    _drop_last(repo)
    lines = hook.lost_work_lines(repo, "main")
    assert lines and all(line.isascii() for line in lines)


def test_the_briefing_carries_the_radar(hook, repo: Path) -> None:
    _commit(repo, "lost.txt", "only here\n", "a commit only the reflog remembers")
    _drop_last(repo)
    assert "a commit only the reflog remembers" in hook.briefing(repo)


# ── the rename rule#
# Measured 2026-10-17: the radar's first real run was 2-for-2 false. A manual moved
# blackbook/ -> cookbook/ WITH edits (path and blob both differ), and this repo renames
# paths as a workflow -- so path-only supersession had to learn the move.




def test_a_commit_whose_files_arrived_at_new_paths_is_superseded(hook, repo: Path) -> None:
    _git("checkout", "-b", "feat/moved", cwd=repo)
    _commit(repo, "docs/blackbook/lensManual.ts", "the lens page" + "\n", "the black book takes its name")
    _drop_last(repo)
    _git("checkout", "main", cwd=repo)
    time.sleep(1.1)  # --since is second-granular: the rename must be AFTER the commit
    _commit(
        repo,
        "docs/cookbook/lensManual.ts",
        "the lens page, renamed and edited" + "\n",
        "the cookbook takes its name",
    )
    assert hook.lost_work_lines(repo, "main") == []


def test_a_new_file_that_reached_nothing_is_still_reported(hook, repo: Path) -> None:
    _commit(repo, "brand/uniqueOrphan.ts", "nobody merged me" + "\n", "a file that reached nothing")
    _drop_last(repo)
    lines = hook.lost_work_lines(repo, "main")
    assert lines and "a file that reached nothing" in lines[1]


def test_a_generic_basename_never_masks_a_loss(hook, repo: Path) -> None:
    """An unrelated index.ts on main must not make an unlanded index.ts vanish."""
    _commit(repo, "index.ts", "root" + "\n", "root index")
    _git("checkout", "-b", "feat/idx", cwd=repo)
    _commit(repo, "other/place/index.ts", "feature" + "\n", "an index that never landed")
    _drop_last(repo)
    _git("checkout", "main", cwd=repo)
    lines = hook.lost_work_lines(repo, "main")
    assert lines and "an index that never landed" in lines[1]
