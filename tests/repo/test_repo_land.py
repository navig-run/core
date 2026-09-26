"""``navig repo land`` — finish a MERGED branch completely, refuse an unmerged one.

The merge-and-delete contract has four steps (merge · delete remote · delete local ·
remove worktree) and only the merge is reliable. `gh pr merge --delete-branch` run from
a linked worktree merges, then FAILS its remote delete before it happens — the local
branch and worktree linger. Measured: four consecutive PRs left a merged ref on origin,
13 in a week elsewhere. `land` makes the other three steps one verb that always completes.

The load-bearing safety: it REFUSES a branch not provably on the base. "Land" is for a
merged branch; it must never become a way to delete unmerged code — which is exactly what
the operator meant by "not to delete the coded things". The proof is `_prove_merged`, the
same function `sweep` uses, so the two can never disagree.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands import repo as repo_mod
from navig.commands.repo import collect_land, repo_app

runner = CliRunner()


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit(root: Path, name: str, content: str = "x") -> str:
    (root / name).write_text(content + "\n", encoding="utf-8")
    _git("add", name, cwd=root)
    _git("commit", "-q", "-m", f"add {name}", cwd=root)
    return _git("rev-parse", "HEAD", cwd=root)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A clone with a real ``origin`` and a real remote-tracking ref."""
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


@pytest.fixture(autouse=True)
def _no_github(monkeypatch):
    monkeypatch.setattr(repo_mod, "_github_pr_index", lambda root: ({}, "gh not installed"))


def _local_branches(root: Path) -> list[str]:
    return _git("branch", "--format=%(refname:short)", cwd=root).split()


def _remote_branches(root: Path) -> list[str]:
    out = _git("for-each-ref", "refs/remotes/origin", "--format=%(refname:short)", cwd=root)
    return [r[len("origin/"):] for r in out.split() if r.startswith("origin/")]


# -- the refusal (the whole safety story) ------------------------------------


def test_an_unmerged_branch_is_refused_and_nothing_is_deleted(repo: Path):
    _git("checkout", "-q", "-b", "feat/real", cwd=repo)
    _commit(repo, "real.txt")
    _git("push", "-q", "-u", "origin", "feat/real", cwd=repo)
    _git("checkout", "-q", "main", cwd=repo)

    result = runner.invoke(
        repo_app, ["land", "feat/real", "--repo", str(repo), "--no-fetch", "--yes", "--no-github"]
    )

    assert result.exit_code == 1, result.output
    assert "NOT provably on" in result.output
    assert "feat/real" in _local_branches(repo), "an unmerged local branch must survive"
    assert "feat/real" in _remote_branches(repo), "an unmerged remote branch must survive"


def test_a_branch_reused_after_its_merged_pr_is_refused(repo: Path, monkeypatch):
    _git("checkout", "-q", "-b", "feat/reused", cwd=repo)
    merged_at = _commit(repo, "first.txt")
    _commit(repo, "after.txt")  # tip moved past the PR's merged sha
    _git("checkout", "-q", "main", cwd=repo)
    monkeypatch.setattr(
        repo_mod, "_github_pr_index",
        lambda root: ({"feat/reused": {"number": 7, "state": "MERGED", "head": merged_at}}, None),
    )

    result = runner.invoke(repo_app, ["land", "feat/reused", "--repo", str(repo), "--no-fetch", "--yes"])

    assert result.exit_code == 1
    assert "feat/reused" in _local_branches(repo)


def test_the_default_branch_is_refused(repo: Path):
    result = runner.invoke(repo_app, ["land", "main", "--repo", str(repo), "--no-fetch", "--yes"])

    assert result.exit_code == 1
    assert "default branch" in result.output


def test_a_missing_branch_errors(repo: Path):
    result = runner.invoke(repo_app, ["land", "no/such", "--repo", str(repo), "--no-fetch", "--yes"])

    assert result.exit_code == 1
    assert "No such branch" in result.output


# -- the happy path: all three teardown steps complete ------------------------


def test_an_ancestor_branch_is_fully_torn_down(repo: Path):
    """An ancestor tip is merged by every definition. Local + remote both go."""
    base_sha = _git("rev-parse", "HEAD", cwd=repo)
    _git("branch", "done/pointer", base_sha, cwd=repo)
    _git("push", "-q", "origin", "done/pointer", cwd=repo)
    assert "done/pointer" in _remote_branches(repo)

    result = runner.invoke(
        repo_app, ["land", "done/pointer", "--repo", str(repo), "--no-fetch", "--yes", "--json"]
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["proof"] == "ancestor"
    assert data["done"]["local"].startswith("deleted")
    assert data["done"]["remote"] == "deleted"
    assert "done/pointer" not in _local_branches(repo)
    assert "done/pointer" not in _remote_branches(repo)


def test_a_squash_merged_branch_lands_by_its_tree(repo: Path):
    """Off the ancestry, on the base by content — deleted with -D, which -d cannot do."""
    _git("checkout", "-q", "-b", "feat/squashed", cwd=repo)
    _commit(repo, "sq.txt", "content")
    _git("push", "-q", "-u", "origin", "feat/squashed", cwd=repo)
    _git("checkout", "-q", "main", cwd=repo)
    (repo / "sq.txt").write_text("content\n", encoding="utf-8")
    _git("add", "sq.txt", cwd=repo)
    _git("commit", "-q", "-m", "feat: squashed (#1)", cwd=repo)
    _git("push", "-q", "origin", "main", cwd=repo)

    result = runner.invoke(
        repo_app, ["land", "feat/squashed", "--repo", str(repo), "--no-fetch", "--yes", "--json"]
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["proof"] == "tree"
    assert "feat/squashed" not in _local_branches(repo)
    assert "feat/squashed" not in _remote_branches(repo)


def test_a_merged_pr_at_this_tip_lands(repo: Path, monkeypatch):
    _git("checkout", "-q", "-b", "feat/by-pr", cwd=repo)
    tip = _commit(repo, "pr.txt")
    _git("push", "-q", "-u", "origin", "feat/by-pr", cwd=repo)
    _git("checkout", "-q", "main", cwd=repo)
    monkeypatch.setattr(
        repo_mod, "_github_pr_index",
        lambda root: ({"feat/by-pr": {"number": 42, "state": "MERGED", "head": tip}}, None),
    )

    result = runner.invoke(repo_app, ["land", "feat/by-pr", "--repo", str(repo), "--no-fetch", "--yes"])

    assert result.exit_code == 0, result.output
    assert "feat/by-pr" not in _local_branches(repo)
    assert "feat/by-pr" not in _remote_branches(repo)


def test_a_worktree_on_the_branch_is_removed_before_the_branch(repo: Path):
    """git refuses to delete a checked-out branch, so the worktree must go first."""
    base_sha = _git("rev-parse", "HEAD", cwd=repo)
    _git("branch", "done/held", base_sha, cwd=repo)
    wt = repo / ".dev" / "worktrees" / "held"
    wt.parent.mkdir(parents=True)
    _git("worktree", "add", "-q", str(wt), "done/held", cwd=repo)

    result = runner.invoke(
        repo_app, ["land", "done/held", "--repo", str(repo), "--no-fetch", "--yes", "--json"]
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["done"]["worktree"] == "removed"
    assert data["done"]["local"].startswith("deleted")
    assert "done/held" not in _local_branches(repo)
    assert not wt.exists()


# -- dry run + the leaked-ref case land exists for ----------------------------


def test_dry_run_changes_nothing(repo: Path):
    base_sha = _git("rev-parse", "HEAD", cwd=repo)
    _git("branch", "done/pointer", base_sha, cwd=repo)
    _git("push", "-q", "origin", "done/pointer", cwd=repo)

    result = runner.invoke(repo_app, ["land", "done/pointer", "--repo", str(repo), "--no-fetch", "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["dry_run"] is True
    assert data["done"] == {}
    assert "done/pointer" in _local_branches(repo)
    assert "done/pointer" in _remote_branches(repo)


def test_it_deletes_a_leaked_remote_ref_when_the_local_is_already_gone(repo: Path):
    """The exact bug: gh merged, deleted the local, but left the remote ref. `land`
    on that name (recreated locally at the base, i.e. an ancestor) finishes the job —
    but the real value is the standalone remote delete, so assert that path directly."""
    base_sha = _git("rev-parse", "HEAD", cwd=repo)
    # Simulate the leak: a remote ref exists, matching an ancestor-state local.
    _git("branch", "leaked", base_sha, cwd=repo)
    _git("push", "-q", "origin", "leaked", cwd=repo)
    assert "leaked" in _remote_branches(repo)

    plan = collect_land(repo, "leaked", github=False, fetch=False)
    assert plan["remote_exists"] is True
    assert plan["proof"] == "ancestor"

    runner.invoke(repo_app, ["land", "leaked", "--repo", str(repo), "--no-fetch", "--yes", "--no-github"])

    assert "leaked" not in _remote_branches(repo), "the leaked remote ref must be deleted"


def test_current_branch_is_the_default_target(repo: Path):
    base_sha = _git("rev-parse", "HEAD", cwd=repo)
    _git("checkout", "-q", "-b", "done/current", base_sha, cwd=repo)
    # on an ancestor branch, but checked out — git can't delete the current branch,
    # so land must still refuse to delete what HEAD points at. It falls to the
    # worktree/branch step which git rejects; assert the branch survives.
    result = runner.invoke(repo_app, ["land", "--repo", str(repo), "--no-fetch", "--yes", "--no-github"])

    # The primary checkout is on done/current; `git branch -d` of the current branch fails.
    assert "done/current" in _local_branches(repo)
    assert result.exit_code == 1


# -- robustness: a failed remote delete must not also strand the local branch -----------


def test_land_deletes_the_proven_local_branch_even_if_the_remote_delete_fails(repo: Path, monkeypatch):
    """land deletes the remote ref (step 1) before the local branch (step 3), so in the
    happy path the stale upstream is gone by step 3 and raw `-d` would work. But if the
    remote delete FAILS — no push permission, a protected ref — origin/<branch> stays,
    still stale, and raw `git branch -d` then refuses the LOCAL branch too ("not yet merged
    to refs/remotes/origin/…, even though it is merged to HEAD"): a permission problem on
    the remote would strand a provably-merged local branch. Sharing sweep's
    delete_proven_branch (#1492) re-verifies ancestry and falls back to -D, so the local
    branch is still cleaned up and only the remote is reported failed."""
    _git("checkout", "-q", "-b", "feat/stuck", cwd=repo)
    _commit(repo, "work.txt")
    _git("push", "-q", "-u", "origin", "feat/stuck", cwd=repo)     # upstream set, origin/feat/stuck = T1
    _git("checkout", "-q", "main", cwd=repo)
    _commit(repo, "mainmove.txt")
    _git("push", "-q", "origin", "main", cwd=repo)
    _git("checkout", "-q", "feat/stuck", cwd=repo)
    _git("rebase", "-q", "origin/main", cwd=repo)                  # tip no longer on origin/feat/stuck
    _git("checkout", "-q", "main", cwd=repo)
    _git("merge", "-q", "--ff-only", "feat/stuck", cwd=repo)       # tip is on main; origin/feat/stuck still stale
    _git("push", "-q", "origin", "main", cwd=repo)             # main lands the tip; origin/feat/stuck stays stale

    # Sanity: raw -d refuses while origin/feat/stuck exists and is stale.
    import subprocess
    r = subprocess.run(["git", "branch", "-d", "feat/stuck"], cwd=str(repo), capture_output=True, text=True)
    assert r.returncode != 0 and "not yet merged to" in (r.stderr + r.stdout), (r.stderr + r.stdout)

    # Make the remote delete fail, leaving origin/feat/stuck in place.
    real = repo_mod._git

    def _no_remote_delete(args, cwd, **kw):
        if args[:3] == ["push", "origin", "--delete"]:
            return subprocess.CompletedProcess(args, 1, "", "remote: permission denied")
        return real(args, cwd, **kw)

    monkeypatch.setattr(repo_mod, "_git", _no_remote_delete)
    result = runner.invoke(
        repo_app, ["land", "feat/stuck", "--repo", str(repo), "--no-fetch", "--yes", "--no-github", "--json"]
    )
    import json as _json
    payload = _json.loads(result.output)
    assert payload["failed"].get("remote"), "the remote delete was supposed to fail in this test"
    assert payload["done"].get("local", "").startswith("deleted"), payload
    assert "feat/stuck" not in _local_branches(repo), "a failed remote delete must not strand the local branch"
