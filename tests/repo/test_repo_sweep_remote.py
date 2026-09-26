"""``navig repo sweep --remote`` — the refs ``gh pr merge --delete-branch`` leaves on origin.

Run from a linked worktree, ``gh pr merge --delete-branch`` merges the PR and
then fails at its local checkout-main step — BEFORE deleting the remote branch
— with a message that reads like harmless noise. One session accumulated 13
merged refs on origin this way while reporting each as cleaned; locally
nothing was left to see, so the local sweep could not help.

Same three proofs as the local sweep, same asymmetry: a remote ref is deleted
only under a proof that would hold in a fresh clone, and every failure mode
keeps MORE. The extra rule here: a remote ref whose NAME is an unmerged local
branch is someone's pushed, in-flight work — protected.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands import repo as repo_mod
from navig.commands.repo import collect_remote_sweep, repo_app

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


def _remote_heads(root: Path) -> set[str]:
    out = _git("ls-remote", "--heads", "origin", cwd=root)
    return {ln.split("refs/heads/", 1)[1] for ln in out.splitlines() if "refs/heads/" in ln}


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
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


def _push_branch_then_forget_it(root: Path, name: str, *, land_on_main: bool) -> str:
    """Create ``name``, push it, delete the LOCAL branch — the leaked shape.

    With ``land_on_main`` the branch's commit is merged into main and pushed,
    so ``origin/<name>`` is an ancestor of ``origin/main``; without it the ref
    carries real unmerged work.
    """
    _git("checkout", "-q", "-b", name, cwd=root)
    sha = _commit(root, f"{name.replace('/', '-')}.txt")
    _git("push", "-q", "-u", "origin", name, cwd=root)
    _git("checkout", "-q", "main", cwd=root)
    if land_on_main:
        _git("merge", "-q", "--ff-only", name, cwd=root)
        _git("push", "-q", "origin", "main", cwd=root)
    _git("branch", "-D", name, cwd=root)
    return sha


# -- classification ------------------------------------------------------------


def test_a_leaked_merged_ref_is_redundant_and_a_live_one_is_kept(repo: Path):
    _push_branch_then_forget_it(repo, "feat/landed", land_on_main=True)
    _push_branch_then_forget_it(repo, "feat/in-flight", land_on_main=False)

    data = collect_remote_sweep(repo)

    assert [(b["name"], b["proof"]) for b in data["redundant"]] == [("feat/landed", "ancestor")]
    assert [(b["name"], b["ahead"]) for b in data["kept"]] == [("feat/in-flight", 1)]
    assert "main" in data["protected"]


def test_the_default_branch_is_never_classified(repo: Path):
    data = collect_remote_sweep(repo)
    assert data["redundant"] == [] and data["kept"] == []
    assert data["protected"] == ["main"]


def test_a_remote_ref_backed_by_an_unmerged_local_branch_is_protected(repo: Path):
    # Pushed, still checked out somewhere, not merged: a colleague's live work.
    _git("checkout", "-q", "-b", "feat/mine", cwd=repo)
    _commit(repo, "mine.txt")
    _git("push", "-q", "-u", "origin", "feat/mine", cwd=repo)
    _git("checkout", "-q", "main", cwd=repo)

    data = collect_remote_sweep(repo)

    assert "feat/mine" in data["protected"]
    assert data["redundant"] == [] and data["kept"] == []


def test_a_merged_pr_that_landed_this_exact_tip_is_redundant(repo: Path, monkeypatch):
    # Squash-merged: main gets a NEW commit with the branch's tree; the branch
    # tip is not an ancestor. Only the PR record proves it.
    _git("checkout", "-q", "-b", "feat/squashed", cwd=repo)
    tip = _commit(repo, "sq.txt")
    _git("push", "-q", "-u", "origin", "feat/squashed", cwd=repo)
    _git("checkout", "-q", "main", cwd=repo)
    _commit(repo, "unrelated.txt")  # main moved on, trees differ
    _git("push", "-q", "origin", "main", cwd=repo)
    _git("branch", "-D", "feat/squashed", cwd=repo)

    monkeypatch.setattr(
        repo_mod, "_github_pr_index",
        lambda root: ({"feat/squashed": {"number": 42, "state": "MERGED", "head": tip}}, None),
    )
    data = collect_remote_sweep(repo)
    assert [(b["name"], b["proof"], b["pr"]) for b in data["redundant"]] == [
        ("feat/squashed", "merged-pr", "#42 MERGED")
    ]


def test_a_ref_that_moved_after_its_pr_merged_is_kept_and_says_why(repo: Path, monkeypatch):
    _git("checkout", "-q", "-b", "feat/reused", cwd=repo)
    old_tip = _commit(repo, "r1.txt")
    _commit(repo, "r2.txt")  # a commit AFTER the PR's merged sha
    _git("push", "-q", "-u", "origin", "feat/reused", cwd=repo)
    _git("checkout", "-q", "main", cwd=repo)
    _git("branch", "-D", "feat/reused", cwd=repo)

    monkeypatch.setattr(
        repo_mod, "_github_pr_index",
        lambda root: ({"feat/reused": {"number": 7, "state": "MERGED", "head": old_tip}}, None),
    )
    data = collect_remote_sweep(repo)
    assert data["redundant"] == []
    assert data["kept"][0]["name"] == "feat/reused"
    assert data["kept"][0]["note"] == "commits after its merged PR"


# -- the command ----------------------------------------------------------------


def test_dry_run_lists_remote_refs_and_deletes_nothing(repo: Path):
    _push_branch_then_forget_it(repo, "feat/landed", land_on_main=True)
    res = runner.invoke(repo_app, ["sweep", "--remote", "--repo", str(repo)])
    assert res.exit_code == 0, res.output
    assert "feat/landed" in res.output
    assert "Dry run" in res.output
    assert "--remote --yes" in res.output
    assert _remote_heads(repo) == {"main", "feat/landed"}


def test_yes_deletes_the_leaked_ref_on_origin_and_keeps_the_live_one(repo: Path):
    _push_branch_then_forget_it(repo, "feat/landed", land_on_main=True)
    _push_branch_then_forget_it(repo, "feat/in-flight", land_on_main=False)

    res = runner.invoke(repo_app, ["sweep", "--remote", "--yes", "--json", "--repo", str(repo)])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert [d["name"] for d in payload["remote"]["deleted"]] == ["feat/landed"]
    assert payload["remote"]["failed"] == []
    assert _remote_heads(repo) == {"main", "feat/in-flight"}


def test_without_remote_flag_origin_is_untouched(repo: Path):
    _push_branch_then_forget_it(repo, "feat/landed", land_on_main=True)
    res = runner.invoke(repo_app, ["sweep", "--yes", "--json", "--repo", str(repo)])
    assert res.exit_code == 0, res.output
    assert "remote" not in json.loads(res.output)
    assert _remote_heads(repo) == {"main", "feat/landed"}


# -- one push per batch ------------------------------------------------------


def test_remote_deletes_go_out_in_batches_not_one_push_per_branch(repo: Path, monkeypatch):
    """194 redundant origin/* refs meant 194 pushes and 194 hook starts. One ref-update."""
    import json as _json

    for i in range(5):
        _git("branch", f"gone{i}", cwd=repo)
        _git("push", "-q", "origin", f"gone{i}", cwd=repo)   # all at main's tip → ancestors
    pushes: list[list[str]] = []
    real = repo_mod._git

    def _spy(args, cwd, **kw):
        if args[:3] == ["push", "origin", "--delete"]:
            pushes.append(args[3:])
        return real(args, cwd, **kw)

    monkeypatch.setattr(repo_mod, "_git", _spy)
    result = runner.invoke(repo_app, ["sweep", "--repo", str(repo), "--no-fetch", "--remote", "--yes", "--json"])
    assert result.exit_code == 0, result.output
    payload = _json.loads(result.output)
    deleted = {d["name"] for d in payload["remote"]["deleted"]}
    assert {f"gone{i}" for i in range(5)} <= deleted, payload["remote"]
    assert len(pushes) == 1 and len(pushes[0]) >= 5, f"expected one batched push, got {pushes}"


def test_a_failing_batch_falls_back_to_one_by_one_so_the_rest_still_go(repo: Path, monkeypatch):
    import json as _json

    for i in range(3):
        _git("branch", f"ok{i}", cwd=repo)
        _git("push", "-q", "origin", f"ok{i}", cwd=repo)
    real = repo_mod._git
    calls = {"batch": 0}

    def _flaky(args, cwd, **kw):
        if args[:3] == ["push", "origin", "--delete"] and len(args) > 4:
            calls["batch"] += 1
            return subprocess.CompletedProcess(args, 1, "", "error: remote ref does not exist")  # the batch "fails"
        return real(args, cwd, **kw)

    monkeypatch.setattr(repo_mod, "_git", _flaky)
    result = runner.invoke(repo_app, ["sweep", "--repo", str(repo), "--no-fetch", "--remote", "--yes", "--json"])
    payload = _json.loads(result.output)
    assert calls["batch"] == 1
    assert {f"ok{i}" for i in range(3)} <= {d["name"] for d in payload["remote"]["deleted"]}, payload["remote"]
