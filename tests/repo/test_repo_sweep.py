"""``navig repo sweep`` — delete only what is PROVABLY on the remote default.

Branches accumulate faster than anyone sweeps them: measured in one repo,
8 -> 20 in eleven days and 37 at the worst, 28 of which were already on main.
Each was deleted by hand after proving it, twice, a week apart. The proof is
mechanical — an ancestor tip, an identical tree, or a MERGED PR that landed
exactly this tip — so it belongs in a command.

The contract that matters is the asymmetry: every failure mode (no remote, a
fetch that fails, no ``gh``, a PR index that misses an old PR) must make the
sweep keep MORE, never delete more. A branch is deleted only under a proof
that would hold in a fresh clone.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands import repo as repo_mod
from navig.commands.repo import collect_stale, collect_sweep, merge_base_ref, repo_app

runner = CliRunner()


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


def _branches(root: Path) -> list[str]:
    return _git("branch", "--format=%(refname:short)", cwd=root).split()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A clone with a real ``origin`` so ``origin/main`` exists and can be fetched."""
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
    """Tests never reach GitHub; the ``merged-pr`` cases install their own index."""
    monkeypatch.setattr(repo_mod, "_github_pr_index", lambda root: ({}, "gh not installed"))


# -- the base ref ------------------------------------------------------------


def test_the_base_is_the_remote_default_when_one_exists(repo: Path):
    assert merge_base_ref(repo, "main") == "origin/main"


def test_without_a_remote_it_falls_back_to_the_local_branch(tmp_path: Path):
    root = tmp_path / "lonely"
    root.mkdir()
    _git("init", "-q", "-b", "main", cwd=root)
    _git("config", "user.email", "t@navig.local", cwd=root)
    _git("config", "user.name", "t", cwd=root)
    _commit(root, "a.txt")

    assert merge_base_ref(root, "main") == "main"


def test_stale_no_longer_lies_when_local_main_is_behind(repo: Path):
    """The bug this closes: ``collect_stale`` judged against LOCAL main.

    A branch merged on the remote while local main sat behind read as "not
    merged" — measured at 5 and then 107 PRs behind in one week. The report
    now judges against the ref merges actually land on.
    """
    _git("checkout", "-q", "-b", "feat/landed", cwd=repo)
    _commit(repo, "landed.txt")
    _git("push", "-q", "origin", "feat/landed:main", cwd=repo)  # merged remotely
    _git("checkout", "-q", "main", cwd=repo)                    # local main is BEHIND
    _git("fetch", "-q", "origin", cwd=repo)

    data = collect_stale(repo)

    assert data["base_ref"] == "origin/main"
    assert "feat/landed" not in {b["name"] for b in data["unmerged_branches"]}, (
        "a branch merged on the remote was reported unmerged — judged against stale local main"
    )


# -- classification ----------------------------------------------------------


def test_an_ancestor_tip_is_redundant(repo: Path):
    base_sha = _git("rev-parse", "HEAD", cwd=repo)
    _git("branch", "old/pointer", base_sha, cwd=repo)

    data = collect_sweep(repo, github=False, fetch=False)

    names = {b["name"]: b for b in data["redundant"]}
    assert "old/pointer" in names
    assert names["old/pointer"]["proof"] == "ancestor"
    assert names["old/pointer"]["sha"] == base_sha[:9]


def test_real_unmerged_work_is_kept_with_its_ahead_count(repo: Path):
    _git("checkout", "-q", "-b", "feat/real", cwd=repo)
    _commit(repo, "one.txt")
    _commit(repo, "two.txt")
    _git("checkout", "-q", "main", cwd=repo)

    data = collect_sweep(repo, github=False, fetch=False)

    assert [b["name"] for b in data["redundant"]] == []
    kept = {b["name"]: b for b in data["kept"]}
    assert kept["feat/real"]["ahead"] == 2


def test_an_identical_tree_is_redundant_even_off_the_ancestry(repo: Path):
    """A squash-merge leaves the branch's commit off main's history while main
    already holds its exact contents. Ancestry cannot see that; the tree can."""
    _git("checkout", "-q", "-b", "feat/squashed", cwd=repo)
    _commit(repo, "sq.txt", "content")
    # Simulate the squash: main gets the same CONTENT as a different commit.
    _git("checkout", "-q", "main", cwd=repo)
    (repo / "sq.txt").write_text("content\n", encoding="utf-8")
    _git("add", "sq.txt", cwd=repo)
    _git("commit", "-q", "-m", "feat: squashed (#1)", cwd=repo)
    _git("push", "-q", "origin", "main", cwd=repo)

    data = collect_sweep(repo, github=False, fetch=False)

    names = {b["name"]: b for b in data["redundant"]}
    assert "feat/squashed" in names
    assert names["feat/squashed"]["proof"] == "tree"
    # Prove the ancestry test alone would have kept it — this is the case the
    # tree proof exists for.
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", "feat/squashed", "origin/main"],
        cwd=str(repo), capture_output=True,
    )
    assert ancestry.returncode != 0


# -- protection --------------------------------------------------------------


def test_the_default_branch_is_never_classified(repo: Path):
    data = collect_sweep(repo, github=False, fetch=False)

    assert "main" in data["protected"]
    listed = {b["name"] for b in data["redundant"]} | {b["name"] for b in data["kept"]}
    assert "main" not in listed


def test_a_branch_checked_out_in_a_worktree_is_protected(repo: Path):
    """Even a provably-redundant branch is untouched while a worktree holds it —
    git would refuse the delete anyway, but the contract must not lean on that."""
    base_sha = _git("rev-parse", "HEAD", cwd=repo)
    _git("branch", "held/pointer", base_sha, cwd=repo)
    wt = repo / ".dev" / "worktrees" / "held"
    wt.parent.mkdir(parents=True)
    _git("worktree", "add", "-q", str(wt), "held/pointer", cwd=repo)

    data = collect_sweep(repo, github=False, fetch=False)

    assert "held/pointer" in data["protected"]
    assert "held/pointer" not in {b["name"] for b in data["redundant"]}


# -- the merged-PR proof -----------------------------------------------------


def test_a_merged_pr_that_landed_this_exact_tip_is_redundant(repo: Path, monkeypatch):
    _git("checkout", "-q", "-b", "feat/by-pr", cwd=repo)
    tip = _commit(repo, "pr.txt")
    _git("checkout", "-q", "main", cwd=repo)
    monkeypatch.setattr(
        repo_mod, "_github_pr_index",
        lambda root: ({"feat/by-pr": {"number": 42, "state": "MERGED", "head": tip}}, None),
    )

    data = collect_sweep(repo, github=True, fetch=False)

    names = {b["name"]: b for b in data["redundant"]}
    assert names["feat/by-pr"]["proof"] == "merged-pr"
    assert names["feat/by-pr"]["pr"] == "#42 MERGED"


def test_a_branch_reused_after_its_pr_merged_is_kept_and_says_why(repo: Path, monkeypatch):
    """The detail that makes "merged PR" a proof: the PR's merged sha must be the
    branch's CURRENT tip. New commits after the merge mean new, unmerged work."""
    _git("checkout", "-q", "-b", "feat/reused", cwd=repo)
    merged_at = _commit(repo, "first.txt")
    _commit(repo, "after.txt")  # tip moved on
    _git("checkout", "-q", "main", cwd=repo)
    monkeypatch.setattr(
        repo_mod, "_github_pr_index",
        lambda root: ({"feat/reused": {"number": 7, "state": "MERGED", "head": merged_at}}, None),
    )

    data = collect_sweep(repo, github=True, fetch=False)

    assert "feat/reused" not in {b["name"] for b in data["redundant"]}
    kept = {b["name"]: b for b in data["kept"]}
    assert kept["feat/reused"]["note"] == "commits after its merged PR"


def test_a_closed_pr_is_not_a_proof(repo: Path, monkeypatch):
    _git("checkout", "-q", "-b", "feat/rejected", cwd=repo)
    tip = _commit(repo, "no.txt")
    _git("checkout", "-q", "main", cwd=repo)
    monkeypatch.setattr(
        repo_mod, "_github_pr_index",
        lambda root: ({"feat/rejected": {"number": 9, "state": "CLOSED", "head": tip}}, None),
    )

    data = collect_sweep(repo, github=True, fetch=False)

    kept = {b["name"]: b for b in data["kept"]}
    assert kept["feat/rejected"]["pr"] == "#9 CLOSED"


def test_without_gh_the_other_two_proofs_still_work(repo: Path):
    """Absence of ``gh`` narrows what can be PROVEN; it must not disable the sweep."""
    base_sha = _git("rev-parse", "HEAD", cwd=repo)
    _git("branch", "old/pointer", base_sha, cwd=repo)

    data = collect_sweep(repo, github=True, fetch=False)  # the autouse fake says "not installed"

    assert data["github"] is False
    assert "gh not installed" in data["github_note"]
    assert "old/pointer" in {b["name"] for b in data["redundant"]}


# -- the command -------------------------------------------------------------


def test_dry_run_deletes_nothing(repo: Path):
    base_sha = _git("rev-parse", "HEAD", cwd=repo)
    _git("branch", "old/pointer", base_sha, cwd=repo)

    result = runner.invoke(repo_app, ["sweep", "--repo", str(repo), "--no-fetch", "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["dry_run"] is True
    assert data["deleted"] == []
    assert "old/pointer" in _branches(repo)


def test_yes_deletes_the_redundant_and_keeps_the_real(repo: Path):
    base_sha = _git("rev-parse", "HEAD", cwd=repo)
    _git("branch", "old/pointer", base_sha, cwd=repo)
    _git("checkout", "-q", "-b", "feat/real", cwd=repo)
    _commit(repo, "real.txt")
    _git("checkout", "-q", "main", cwd=repo)

    result = runner.invoke(
        repo_app, ["sweep", "--repo", str(repo), "--no-fetch", "--yes", "--json"]
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert [d["name"] for d in data["deleted"]] == ["old/pointer"]
    assert data["deleted"][0]["sha"] == base_sha[:9], "the sha is the recovery handle"
    remaining = _branches(repo)
    assert "old/pointer" not in remaining
    assert "feat/real" in remaining


def test_the_human_output_names_the_recovery_handle(repo: Path):
    base_sha = _git("rev-parse", "HEAD", cwd=repo)
    _git("branch", "old/pointer", base_sha, cwd=repo)

    result = runner.invoke(repo_app, ["sweep", "--repo", str(repo), "--no-fetch", "--yes"])

    assert result.exit_code == 0, result.output
    assert base_sha[:9] in result.output, "a deleted branch must print the sha it was at"


def test_a_failed_fetch_is_reported_and_still_safe(repo: Path):
    """Point origin at nothing: the fetch fails, the cached ref is used, and the
    result can only be MORE conservative."""
    _git("remote", "set-url", "origin", str(repo.parent / "does-not-exist.git"), cwd=repo)
    base_sha = _git("rev-parse", "HEAD", cwd=repo)
    _git("branch", "old/pointer", base_sha, cwd=repo)

    data = collect_sweep(repo, github=False, fetch=True)

    assert data["fetched"] is False
    assert data["base_ref"] == "origin/main", "the cached remote ref still serves as the base"
    assert "old/pointer" in {b["name"] for b in data["redundant"]}


# -- finished worktrees --------------------------------------------------------
#
# A worktree left behind on a branch that is provably on main was invisible to sweep:
# its branch is protected (checked out), so nothing ever named it. Measured on the real
# repo: one sat 904 commits behind main for eight weeks, provably merged, clean.
#
# ⚠ "Merged" is not "finished". A brand-new worktree sits AT the base tip, so its branch is
# trivially an ancestor with zero commits of its own; the first run listed five other
# sessions' fresh worktrees as removable. --yes removes only what is proven merged AND
# clean AND at least WORKTREE_BEHIND_WARN behind.


def _worktree(root: Path, slug: str) -> Path:
    wt = root / ".dev" / "worktrees" / slug
    wt.parent.mkdir(parents=True, exist_ok=True)
    _git("worktree", "add", "-q", str(wt), "-b", f"feat/{slug}", cwd=root)
    return wt


def _advance_main(root: Path, n: int) -> None:
    for i in range(n):
        _commit(root, f"m{i}.txt")
    _git("push", "-q", "origin", "main", cwd=root)


def _finished(root: Path) -> dict:
    return {w["slug"]: w for w in collect_sweep(root, github=False, fetch=False)["finished_worktrees"]}


@pytest.fixture(autouse=True)
def _small_threshold(monkeypatch):
    """These tests pin the RULE relative to WORKTREE_BEHIND_WARN, not its value. At the
    real 50 each test made ~50 commits and the section took 150 s; at 3 it takes seconds."""
    monkeypatch.setattr(repo_mod, "WORKTREE_BEHIND_WARN", 3)


def test_a_merged_clean_far_behind_worktree_is_removable(repo: Path):
    _worktree(repo, "done")
    _advance_main(repo, repo_mod.WORKTREE_BEHIND_WARN)
    w = _finished(repo)["done"]
    assert w["proof"] == "ancestor" and not w["dirty"]
    assert w["behind"] == repo_mod.WORKTREE_BEHIND_WARN
    assert w["removable"] is True


def test_a_fresh_worktree_at_the_tip_is_listed_but_never_removable(repo: Path):
    """The false positive that would have deleted other sessions' fresh worktrees."""
    _worktree(repo, "fresh")
    w = _finished(repo)["fresh"]
    assert w["behind"] == 0
    assert w["removable"] is False
    # a brand-new branch has only its creation in the reflog, so the reason names that;
    # once a commit lands on it the reason falls back to the behind-count wording
    assert "never committed" in w["why_kept"] or "fresh" in w["why_kept"]


def test_a_merged_but_recent_worktree_is_kept(repo: Path):
    _worktree(repo, "recent")
    _advance_main(repo, repo_mod.WORKTREE_BEHIND_WARN - 1)
    w = _finished(repo)["recent"]
    assert w["removable"] is False
    assert "remove by hand" in w["why_kept"]


def test_a_dirty_merged_worktree_is_kept_however_far_behind(repo: Path):
    wt = _worktree(repo, "wip")
    (wt / "draft.txt").write_text("uncommitted\n", encoding="utf-8")
    _advance_main(repo, repo_mod.WORKTREE_BEHIND_WARN + 2)
    w = _finished(repo)["wip"]
    assert w["dirty"] and w["removable"] is False
    assert "--force" in w["why_kept"]


def test_a_worktree_with_unlanded_commits_is_not_finished_at_all(repo: Path):
    wt = _worktree(repo, "live")
    _commit(wt, "work.txt")
    _advance_main(repo, repo_mod.WORKTREE_BEHIND_WARN + 1)
    assert "live" not in _finished(repo)


def test_sweep_yes_removes_the_removable_worktree_and_its_branch_only(repo: Path):
    done = _worktree(repo, "done")
    fresh = _worktree(repo, "fresh0")  # created before main moves → it will also be behind
    _advance_main(repo, repo_mod.WORKTREE_BEHIND_WARN)
    wip = _worktree(repo, "wip")       # created AFTER → 0 behind
    (wip / "d.txt").write_text("x", encoding="utf-8")

    result = runner.invoke(repo_app, ["sweep", "--repo", str(repo), "--no-fetch", "--yes", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)

    removed = {w["slug"] for w in payload["worktrees_removed"]}
    # `done` and `fresh0` are indistinguishable by refs (both clean, both ≥ threshold behind,
    # both provably on main) — and both are safe: clean, every commit on main. Removing a
    # never-used worktree that nobody touched for days of merges loses nothing.
    assert removed == {"done", "fresh0"}, payload["worktrees_removed"]
    assert not done.exists() and not fresh.exists()
    assert wip.exists(), "a dirty worktree must survive --yes"
    assert "feat/done" not in _branches(repo)
    assert "feat/wip" in _branches(repo)


def test_sweep_yes_never_forces_a_dirty_worktree_even_if_told_it_is_removable(repo: Path, monkeypatch):
    """Second net: git's own dirty refusal, because sweep never passes --force."""
    wt = _worktree(repo, "wip")
    (wt / "d.txt").write_text("x", encoding="utf-8")
    _advance_main(repo, repo_mod.WORKTREE_BEHIND_WARN)
    real = repo_mod.collect_sweep

    def _lying(root, **kw):
        d = real(root, **kw)
        for w in d["finished_worktrees"]:
            w["removable"] = True  # the collector is wrong
        return d

    monkeypatch.setattr(repo_mod, "collect_sweep", _lying)
    result = runner.invoke(repo_app, ["sweep", "--repo", str(repo), "--no-fetch", "--yes", "--json"])
    payload = json.loads(result.output)
    assert wt.exists(), "git refused the dirty worktree — sweep must not have forced it"
    assert payload["worktrees_removed"] == []
    assert payload["worktrees_failed"] and "wip" in payload["worktrees_failed"][0]["slug"]


def test_dry_run_lists_finished_worktrees_and_removes_nothing(repo: Path):
    done = _worktree(repo, "done")
    _advance_main(repo, repo_mod.WORKTREE_BEHIND_WARN)
    result = runner.invoke(repo_app, ["sweep", "--repo", str(repo), "--no-fetch"])
    assert result.exit_code == 0, result.output
    assert "Finished worktrees" in result.output and "removable" in result.output
    assert done.exists()
    assert "feat/done" in _branches(repo)


# -- `-d` refuses on a stale upstream, not on unmerged work ---------------------------


def _with_stale_upstream(root: Path, branch: str) -> None:
    """Push the branch, then rebase it past its own upstream — the shape `gh pr merge` +
    a later rebase leaves behind: local tip merged to HEAD, upstream pointing elsewhere."""
    _git("push", "-q", "-u", "origin", branch, cwd=root)          # upstream = origin/<branch>
    _git("checkout", "-q", branch, cwd=root)
    _git("reset", "-q", "--hard", "origin/main", cwd=root)         # local tip now = main; upstream stale
    _git("checkout", "-q", "main", cwd=root)


def test_a_proven_ancestor_with_a_stale_upstream_is_still_deleted(repo: Path):
    """The first live run removed a worktree and left its branch behind on git's message
    'not yet merged to refs/remotes/origin/<branch>, even though it is merged to HEAD'.
    The proof is against origin/main; the upstream is debris."""
    from navig.commands.repo import delete_proven_branch

    _git("branch", "old", cwd=repo)
    _commit(repo, "after.txt")
    _git("push", "-q", "origin", "main", cwd=repo)
    _with_stale_upstream(repo, "old")
    # Sanity: git's own -d refuses exactly as measured.
    r = subprocess.run(["git", "branch", "-d", "old"], cwd=str(repo), capture_output=True, text=True)
    assert r.returncode != 0 and "not yet merged to" in (r.stderr + r.stdout)

    deleted, err = delete_proven_branch(repo, "old", "ancestor", "origin/main")
    assert deleted is True, err
    assert "old" not in _branches(repo)


def test_a_branch_that_is_not_an_ancestor_is_not_force_deleted_on_that_message(repo: Path):
    """The -D fallback fires ONLY when the ancestry re-verifies; a refusal for real unmerged
    work must stay a refusal."""
    from navig.commands.repo import delete_proven_branch

    _git("checkout", "-q", "-b", "work", cwd=repo)
    _commit(repo, "w.txt")
    _git("push", "-q", "-u", "origin", "work", cwd=repo)
    _commit(repo, "w2.txt")  # ahead of its upstream AND not on main
    _git("checkout", "-q", "main", cwd=repo)

    deleted, err = delete_proven_branch(repo, "work", "ancestor", "origin/main")
    assert deleted is False
    assert "work" in _branches(repo)


def test_sweep_yes_deletes_the_finished_worktrees_branch_even_with_a_stale_upstream(repo: Path):
    """The exact shape from the first live run: the branch was pushed (upstream set), then
    REBASED onto a newer main and fast-forwarded in — so its tip is an ancestor of
    origin/main, while origin/<branch> still holds the pre-rebase commit and does not
    contain the tip. `git branch -d` refuses on the upstream check; the proof says delete."""
    wt = _worktree(repo, "done")
    _commit(wt, "work.txt")                                            # T1 on feat/done
    _git("push", "-q", "-u", "origin", "feat/done", cwd=wt)            # upstream = T1
    _commit(repo, "bump.txt")                                        # main moves
    _git("push", "-q", "origin", "main", cwd=repo)
    _git("rebase", "-q", "origin/main", cwd=wt)                        # feat/done = R1 (rebased; T1 no longer its ancestor)
    _git("merge", "-q", "--ff-only", "feat/done", cwd=repo)            # main = R1 → the tip is ON main
    _git("push", "-q", "origin", "main", cwd=repo)
    _advance_main(repo, repo_mod.WORKTREE_BEHIND_WARN)                 # and main moves on past it
    # (The refusal itself — "not yet merged to refs/remotes/origin/…, even though it is merged
    # to HEAD" — is pinned by test_a_proven_ancestor_with_a_stale_upstream_is_still_deleted;
    # here the branch is still checked out, so git would refuse for that reason first.)
    result = runner.invoke(repo_app, ["sweep", "--repo", str(repo), "--no-fetch", "--yes", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    ours = [w for w in payload["worktrees_removed"] if w["slug"] == "done"]
    assert ours and ours[0]["branch_deleted"] is True, payload
    assert "feat/done" not in _branches(repo)


# -- the GitHub PR index: complete for MERGED, and honest when it is not ------------------
#
# Nothing exercised the real builder — every test above mocks it — which is how its window
# shrank from "months" to 25 days without a signal: _PR_INDEX_LIMIT was 500 while the repo
# landed ~20 PRs a day, and 138 remote refs, each a merged PR with a tip identical to its
# PR head, sat unrecognised as "no PR" for ten weeks. These drive the builder through its
# one seam, _gh_pr_rows.


# The autouse `_no_github` fixture above replaces repo_mod._github_pr_index for every test;
# these test the REAL builder, captured here at import time, before any fixture runs.
_REAL_INDEX = repo_mod._github_pr_index


def _rows(*pairs):
    return [{"headRefName": h, "number": n, "state": st, "headRefOid": f"{h}-sha"} for h, n, st in pairs]


def test_index_holds_every_merged_pr_and_annotates_open_ones(repo: Path, monkeypatch):
    calls = []

    def _fake(gh, root, state, limit):
        calls.append((state, limit))
        if state == "merged":
            return _rows(("feat/a", 10, "MERGED"), ("feat/b", 11, "MERGED")), None
        return _rows(("feat/c", 12, "OPEN")), None

    monkeypatch.setattr(repo_mod, "_gh_pr_rows", _fake)
    import shutil as _sh
    monkeypatch.setattr(_sh, "which", lambda name: "gh")
    index, note = _REAL_INDEX(repo)
    assert note is None
    assert index["feat/a"]["state"] == "MERGED" and index["feat/c"]["state"] == "OPEN"
    assert [c[0] for c in calls] == ["merged", "open"], "merged first — it is the proof"
    assert calls[0][1] == repo_mod._PR_INDEX_LIMIT


def test_a_branch_with_a_merged_and_an_open_pr_is_judged_by_the_merge(repo: Path, monkeypatch):
    def _fake(gh, root, state, limit):
        if state == "merged":
            return _rows(("feat/x", 5, "MERGED")), None
        return _rows(("feat/x", 9, "OPEN")), None   # reopened later under a new PR
    import shutil as _sh
    monkeypatch.setattr(_sh, "which", lambda name: "gh")
    monkeypatch.setattr(repo_mod, "_gh_pr_rows", _fake)
    index, _ = _REAL_INDEX(repo)
    assert index["feat/x"]["state"] == "MERGED", "the proof must not be overwritten by annotation"


def test_a_full_page_of_merged_rows_is_reported_as_truncated(repo: Path, monkeypatch):
    """Exactly `limit` rows back means there may be more — the branches beyond are KEPT,
    which is safe, but silently safe is how 138 refs piled up. The note must say so."""
    import shutil as _sh
    monkeypatch.setattr(_sh, "which", lambda name: "gh")
    monkeypatch.setattr(repo_mod, "_PR_INDEX_LIMIT", 3)
    monkeypatch.setattr(repo_mod, "_gh_pr_rows",
                        lambda gh, root, state, limit: (_rows(*[(f"b{i}", i, "MERGED") for i in range(limit)]) if state == "merged" else [], None))
    _index, note = _REAL_INDEX(repo)
    assert note and "truncated" in note and "KEPT" in note


def test_open_fetch_failing_does_not_lose_the_merged_index(repo: Path, monkeypatch):
    import shutil as _sh
    monkeypatch.setattr(_sh, "which", lambda name: "gh")
    monkeypatch.setattr(repo_mod, "_gh_pr_rows",
                        lambda gh, root, state, limit: (_rows(("feat/a", 1, "MERGED")), None) if state == "merged" else (None, "rate limited"))
    index, note = _REAL_INDEX(repo)
    assert index["feat/a"]["state"] == "MERGED"
    assert note is None, "annotation failing is not a proof failing"


def test_merged_fetch_failing_returns_no_index_and_says_why(repo: Path, monkeypatch):
    import shutil as _sh
    monkeypatch.setattr(_sh, "which", lambda name: "gh")
    monkeypatch.setattr(repo_mod, "_gh_pr_rows", lambda gh, root, state, limit: (None, "gh failed: 401"))
    index, note = _REAL_INDEX(repo)
    assert index == {} and "401" in note


def test_the_limit_is_large_enough_to_be_complete_for_this_repo():
    """The measured floor: 1,491 merged PRs on 2026-09-19 at ~20 a day. A limit below a
    year of that is the 500 mistake again."""
    assert repo_mod._PR_INDEX_LIMIT >= 7_000


# -- never committed: the reflog signal ---------------------------------------


def test_branch_never_committed_reads_the_reflog(repo: Path):
    from navig.commands.repo import branch_never_committed

    _git("branch", "fresh", cwd=repo)
    never, created = branch_never_committed(repo, "fresh")
    assert never is True and created is not None and created > 0
    _git("checkout", "-q", "fresh", cwd=repo); _commit(repo, "x.txt"); _git("checkout", "-q", "main", cwd=repo)
    assert branch_never_committed(repo, "fresh") == (False, None)


def test_an_empty_reflog_is_unknown_not_never_committed(repo: Path):
    """Measured: an eight-week-old finished worktree had ZERO reflog entries. Unknown must
    not become a claim — it would make every expired branch look never-used."""
    from navig.commands.repo import branch_never_committed

    _git("branch", "old", cwd=repo)
    (repo / ".git" / "logs" / "refs" / "heads" / "old").unlink()  # expire it
    assert branch_never_committed(repo, "old") == (False, None)


def test_a_never_committed_worktree_under_the_behind_threshold_is_unused_after_the_age_threshold(repo: Path, monkeypatch):
    """The case behind-count cannot see: a quiet fortnight moves main 30 commits, and a
    worktree created on day one and never touched reads as 'fresh' the whole time."""
    _worktree(repo, "idle")
    _advance_main(repo, repo_mod.WORKTREE_BEHIND_WARN - 1)   # under the behind threshold
    # age the reflog entry: pretend the branch was created 20 days ago
    real = repo_mod.branch_never_committed
    monkeypatch.setattr(repo_mod, "branch_never_committed",
                        lambda root, name: (True, time.time() - 20 * 86400) if name == "feat/idle" else real(root, name))
    w = _finished(repo)["idle"]
    assert w["never_committed"] is True and w["age_days"] >= 19
    assert w["removable"] is True, w["why_kept"]


def test_a_never_committed_worktree_younger_than_the_age_threshold_is_kept_with_its_age(repo: Path):
    _worktree(repo, "today")
    _advance_main(repo, repo_mod.WORKTREE_BEHIND_WARN - 1)
    w = _finished(repo)["today"]
    assert w["never_committed"] is True and w["removable"] is False
    assert "never committed" in w["why_kept"] and "day(s) ago" in w["why_kept"]


def test_a_committed_then_merged_worktree_is_not_called_never_committed(repo: Path):
    wt = _worktree(repo, "worked")
    _commit(wt, "w.txt")
    _git("merge", "-q", "--ff-only", "feat/worked", cwd=repo); _git("push", "-q", "origin", "main", cwd=repo)
    _advance_main(repo, repo_mod.WORKTREE_BEHIND_WARN)
    w = _finished(repo)["worked"]
    assert w["never_committed"] is False
    assert w["removable"] is True  # finished by the behind rule, as before


# -- a worktree left holding the DEFAULT branch --------------------------------
# `gh pr merge --delete-branch` run inside a worktree switches that worktree to main
# (measured twice in one afternoon). While it stands the main checkout cannot
# `checkout main` ("already used by worktree"), and the fresh-worktree caution kept it
# forever: it sits at the tip, "only 0 behind — may be a fresh worktree".


def _worktree_on_main(root: Path, slug: str) -> Path:
    """The real shape: the primary is on a feature branch, a worktree holds main."""
    _git("checkout", "-q", "-b", "feat/primary", cwd=root)
    wt = root / ".dev" / "worktrees" / slug
    wt.parent.mkdir(parents=True, exist_ok=True)
    _git("worktree", "add", "-q", str(wt), "main", cwd=root)
    return wt


@pytest.fixture()
def _idle_two_hours(monkeypatch):
    """Nobody has moved any worktree's HEAD for two hours."""
    import time as _time

    monkeypatch.setattr(repo_mod, "worktree_last_moved", lambda path: _time.time() - 7200)


def test_a_clean_idle_worktree_holding_main_is_removable_even_at_the_tip(repo: Path, _idle_two_hours):
    _worktree_on_main(repo, "merged-here")
    w = _finished(repo)["merged-here"]
    assert w["branch"] == "main" and w["behind"] == 0
    assert w["holds_default"] is True
    assert w["removable"] is True, w["why_kept"]


def test_a_worktree_holding_main_that_was_just_used_is_kept(repo: Path):
    # The real reflog: `worktree add` wrote its entry seconds ago — a session may be in it.
    _worktree_on_main(repo, "in-use")
    w = _finished(repo)["in-use"]
    assert w["holds_default"] is True and w["removable"] is False
    assert "min ago" in w["why_kept"] and "session may still be in it" in w["why_kept"]


def test_a_worktree_holding_main_with_an_unreadable_reflog_is_kept(repo: Path, monkeypatch):
    monkeypatch.setattr(repo_mod, "worktree_last_moved", lambda path: None)
    _worktree_on_main(repo, "unknown")
    w = _finished(repo)["unknown"]
    assert w["removable"] is False and "cannot tell" in w["why_kept"]


def test_worktree_last_moved_reads_the_worktrees_own_head_reflog(repo: Path):
    import time as _time

    wt = _worktree_on_main(repo, "probe")
    moved = repo_mod.worktree_last_moved(wt)
    assert moved is not None and abs(_time.time() - moved) < 300


def test_a_dirty_worktree_holding_main_is_still_kept(repo: Path):
    wt = _worktree_on_main(repo, "busy")
    (wt / "wip.txt").write_text("x", encoding="utf-8")
    w = _finished(repo)["busy"]
    assert w["holds_default"] is True and w["removable"] is False
    assert "uncommitted" in w["why_kept"]


def test_an_ordinary_fresh_worktree_is_not_called_holds_default(repo: Path):
    _worktree(repo, "fresh")
    w = _finished(repo)["fresh"]
    assert w["holds_default"] is False and w["removable"] is False


def test_sweep_yes_removes_the_worktree_holding_main_and_keeps_main(repo: Path, _idle_two_hours):
    wt = _worktree_on_main(repo, "merged-here")
    result = runner.invoke(repo_app, ["sweep", "--repo", str(repo), "--no-fetch", "--yes", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    removed = {w["slug"]: w for w in payload["worktrees_removed"]}
    assert "merged-here" in removed, payload
    assert removed["merged-here"]["error"] is None
    assert not wt.exists()
    assert "main" in _branches(repo), "the default branch must survive the worktree's removal"
    # ...and the defect is gone: the main checkout can take main again.
    _git("checkout", "-q", "main", cwd=repo)


def test_the_shared_remover_refuses_the_default_branch_whatever_the_proof(repo: Path):
    # main is not checked out anywhere here, so git itself would happily delete it.
    _git("checkout", "-q", "-b", "feat/primary", cwd=repo)
    for proof in ("ancestor", "tree", "merged-pr"):
        ok, err = repo_mod.delete_proven_branch(repo, "main", proof, "origin/main")
        assert ok is False and "default branch" in err, (proof, err)
    assert "main" in _branches(repo)


def test_stale_flags_a_worktree_holding_main(repo: Path):
    _worktree_on_main(repo, "merged-here")
    _worktree(repo, "normal")
    by_slug = {Path(w["path"]).name: w for w in collect_stale(repo)["worktrees"]}
    assert by_slug["merged-here"]["holds_default"] is True
    assert by_slug["normal"]["holds_default"] is False
    out = runner.invoke(repo_app, ["stale", "--repo", str(repo)]).output
    assert "holds main" in out, out


# -- a worktree whose tip GitHub records as a MERGED PR's head -----------------
# Squash-merging is the house default, so this is the COMMON finished shape. It was kept
# as "only N behind — may be a fresh worktree", which cannot be true of it: a fresh
# worktree sits at the base tip and was never any PR's head. It waited for 50 merges.


def _merged_via_pr(root: Path, slug: str, monkeypatch, *, number: int = 42) -> tuple[Path, str]:
    """A worktree with its own commit, squash-merged upstream (so NOT an ancestor)."""
    wt = _worktree(root, slug)
    tip = _commit(wt, f"{slug}.txt")
    _advance_main(root, 1)  # the squash commit: main moves on without this tip
    monkeypatch.setattr(
        repo_mod, "_github_pr_index",
        lambda r: ({f"feat/{slug}": {"number": number, "state": "MERGED", "head": tip}}, None),
    )
    return wt, tip


def _finished_gh(root: Path) -> dict:
    data = collect_sweep(root, github=True, fetch=False)
    return {w["slug"]: w for w in data["finished_worktrees"]}


def test_an_idle_squash_merged_worktree_is_removable_long_before_50_behind(
    repo: Path, monkeypatch, _idle_two_hours
):
    _merged_via_pr(repo, "squashed", monkeypatch)
    w = _finished_gh(repo)["squashed"]
    assert w["proof"] == "merged-pr" and w["merged_pr_tip"] is True
    assert w["behind"] < repo_mod.WORKTREE_BEHIND_WARN
    assert w["removable"] is True, w["why_kept"]


def test_a_squash_merged_worktree_that_was_just_used_is_kept(repo: Path, monkeypatch):
    # Real reflog: its commit was seconds ago — the session that merged may still be in it.
    _merged_via_pr(repo, "just-merged", monkeypatch)
    w = _finished_gh(repo)["just-merged"]
    assert w["removable"] is False
    assert "merged as #42 MERGED" in w["why_kept"] and "min ago" in w["why_kept"]
    assert "may be a fresh worktree" not in w["why_kept"]


def test_a_fast_forward_merged_worktree_with_its_pr_record_uses_the_idle_gate(
    repo: Path, monkeypatch, _idle_two_hours
):
    # Ancestry wins the proof, but the PR record still says this is finished work.
    wt = _worktree(repo, "ffwd")
    tip = _commit(wt, "ffwd.txt")
    _git("merge", "-q", "--ff-only", "feat/ffwd", cwd=repo)
    _git("push", "-q", "origin", "main", cwd=repo)
    monkeypatch.setattr(
        repo_mod, "_github_pr_index",
        lambda r: ({"feat/ffwd": {"number": 7, "state": "MERGED", "head": tip}}, None),
    )
    w = _finished_gh(repo)["ffwd"]
    assert w["proof"] == "ancestor" and w["merged_pr_tip"] is True
    assert w["removable"] is True, w["why_kept"]


def test_without_a_pr_record_a_merged_worktree_keeps_the_behind_rule(repo: Path, _idle_two_hours):
    wt = _worktree(repo, "no-gh")
    _commit(wt, "no-gh.txt")
    _git("merge", "-q", "--ff-only", "feat/no-gh", cwd=repo)
    _git("push", "-q", "origin", "main", cwd=repo)
    w = _finished_gh(repo)["no-gh"]  # autouse fixture: no GitHub index
    assert w["merged_pr_tip"] is False
    assert w["removable"] is False and "may be a fresh worktree" in w["why_kept"]


def test_sweep_yes_removes_an_idle_squash_merged_worktree_and_its_branch(
    repo: Path, monkeypatch, _idle_two_hours
):
    wt, _tip = _merged_via_pr(repo, "squashed", monkeypatch)
    result = runner.invoke(repo_app, ["sweep", "--repo", str(repo), "--no-fetch", "--yes", "--json"])
    assert result.exit_code == 0, result.output
    removed = {w["slug"]: w for w in json.loads(result.output)["worktrees_removed"]}
    assert removed["squashed"]["branch_deleted"] is True, removed["squashed"]
    assert not wt.exists()
    assert "feat/squashed" not in _branches(repo)


def test_a_fresh_worktree_reusing_a_branch_name_with_an_old_merged_pr_is_not_finished(
    repo: Path, monkeypatch, _idle_two_hours
):
    # `navig repo new foo` after an earlier feat/foo was merged: ancestry holds trivially,
    # GitHub says MERGED — but for an OLD head. Only head == tip proves THIS work landed.
    _worktree(repo, "reused")
    monkeypatch.setattr(
        repo_mod, "_github_pr_index",
        lambda r: ({"feat/reused": {"number": 9, "state": "MERGED", "head": "0" * 40}}, None),
    )
    w = _finished_gh(repo)["reused"]
    assert w["merged_pr_tip"] is False
    assert w["removable"] is False and "merged as" not in w["why_kept"], w["why_kept"]
