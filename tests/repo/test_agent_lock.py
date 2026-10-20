"""Tests for the main-checkout agent-lock hook (scripts/agent-hooks/agent_lock.py).

The hook is stdlib-only and lives outside the ``navig`` package, so it is
loaded by file path. Covers tool classification (what is locked vs exempt)
and the pure lock decision (claim / refresh / steal / block).
"""

from __future__ import annotations

import importlib.util
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_HOOK = _REPO_ROOT / "scripts" / "agent-hooks" / "agent_lock.py"


@pytest.fixture(scope="module")
def hook():
    spec = importlib.util.spec_from_file_location("agent_lock_hook", _HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    return tmp_path


def _edit(path: str) -> dict:
    return {"tool_name": "Edit", "tool_input": {"file_path": path}}


def _bash(command: str, cwd: str | None = None) -> dict:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    if cwd:
        payload["cwd"] = cwd
    return payload


# ── classify_tool ────────────────────────────────────────────────────────────


def test_edit_inside_main_checkout_is_enforced(hook, root: Path) -> None:
    assert hook.classify_tool(_edit(str(root / "core" / "x.py")), root) == "enforce"


def test_edit_in_worktree_is_exempt(hook, root: Path) -> None:
    target = root / ".dev" / "worktrees" / "slug" / "core" / "x.py"
    assert hook.classify_tool(_edit(str(target)), root) == "exempt"


def test_edit_outside_repo_is_exempt(hook, root: Path, tmp_path: Path) -> None:
    other = tmp_path.parent / "elsewhere" / "x.py"
    assert hook.classify_tool(_edit(str(other)), root) == "exempt"


def test_readonly_tools_are_exempt(hook, root: Path) -> None:
    assert hook.classify_tool({"tool_name": "Read", "tool_input": {}}, root) == "exempt"
    assert hook.classify_tool({"tool_name": "Grep", "tool_input": {}}, root) == "exempt"


@pytest.mark.parametrize(
    "command",
    [
        "git checkout main",
        "git switch -c feat/x",
        "git rebase origin/main",
        "git reset --hard HEAD~1",
        "git merge feat/x",
        "git pull --rebase",
        "git commit -m msg",
        "git add -- core/x.py",
        "git stash",
        "git stash pop",
        "cd sub && git cherry-pick abc123",
    ],
)
def test_mutating_git_is_enforced(hook, root: Path, command: str) -> None:
    assert hook.classify_tool(_bash(command), root) == "enforce"


@pytest.mark.parametrize(
    "command",
    [
        "git status --short",
        "git log --oneline --merges",
        "git diff main...feat/x",
        "git branch -vv",
        "git stash list",
        "git stash show -p stash@{0}",
        # Read-only plumbing whose name STARTS with a mutating verb. The hyphen is a
        # word boundary, so a bare `merge` matched these — blocking the exact command
        # you run to check whether a detached HEAD or stray branch holds unmerged work,
        # i.e. the careful look you take BEFORE deciding to touch anything.
        "git merge-base --is-ancestor HEAD origin/main",
        "git merge-tree main feat/x",
        "git worktree add .dev/worktrees/slug -b feat/slug",
        "git worktree list --porcelain",
        "cd .dev/worktrees/slug && git rebase origin/main",
        "npm run ci:fast",
        "python -m pytest tests/repo -q",
    ],
)
def test_safe_commands_are_exempt(hook, root: Path, command: str) -> None:
    assert hook.classify_tool(_bash(command), root) == "exempt"


def test_sibling_worktree_add_is_blocked(hook, root: Path) -> None:
    """Hard rule: `git worktree add` outside the repo is blocked for EVERYONE."""
    blocked = [
        _bash("git worktree add ../navig-x -b feat/x", cwd=str(root)),
        _bash("git worktree add -b feat/x ../navig-x", cwd=str(root)),
        _bash(f"git worktree add {root.parent / 'elsewhere'} main", cwd=str(root)),
    ]
    for payload in blocked:
        assert hook.classify_tool(payload, root) == "block-sibling"

    allowed = [
        _bash("git worktree add .dev/worktrees/slug -b feat/slug", cwd=str(root)),
        _bash(f"git worktree add {root / '.dev' / 'worktrees' / 'slug'}", cwd=str(root)),
        _bash("git worktree remove ../navig-x", cwd=str(root)),  # only add is gated
        _bash("git worktree list --porcelain", cwd=str(root)),
    ]
    for payload in allowed:
        assert hook.classify_tool(payload, root) == "exempt"


def test_sibling_block_survives_malformed_cwd(hook, root: Path) -> None:
    """A foreign-style cwd (e.g. POSIX path on Windows) must not fabricate siblings."""
    payload = _bash("git worktree add .dev/worktrees/x -b feat/x", cwd="/c/not/a/real/windows/path")
    assert hook.classify_tool(payload, root) == "exempt"  # root-relative resolution rescues it
    payload = _bash("git worktree add ../still-sibling -b feat/x", cwd="/c/not/a/real/windows/path")
    assert hook.classify_tool(payload, root) == "block-sibling"  # outside vs BOTH bases


def test_worktree_add_target_parsing(hook) -> None:
    assert hook.worktree_add_target("../x -b feat/x") == "../x"
    assert hook.worktree_add_target("-b feat/x ../x") == "../x"
    assert hook.worktree_add_target("-B feat/x --detach ../x main") == "../x"
    assert hook.worktree_add_target('"..\\my path\\wt"') == "..\\my path\\wt"
    assert hook.worktree_add_target("-b feat/x") is None


def test_powershell_is_classified_like_bash(hook, root: Path) -> None:
    """PowerShell is a second shell tool — leaving it unmatched is a lock hole."""
    ps_mutate = {"tool_name": "PowerShell", "tool_input": {"command": "git checkout main"}}
    ps_read = {"tool_name": "PowerShell", "tool_input": {"command": "git status"}}
    assert hook.classify_tool(ps_mutate, root) == "enforce"
    assert hook.classify_tool(ps_read, root) == "exempt"


def test_git_c_targeting_another_repo_is_exempt(hook, root: Path, tmp_path: Path) -> None:
    other = tmp_path.parent / "other-repo"
    assert hook.classify_tool(_bash(f"git -C {other} checkout main"), root) == "exempt"
    # -C pointing INSIDE the repo is still enforced
    assert hook.classify_tool(_bash(f"git -C {root} checkout main"), root) == "enforce"


def test_merely_mentioning_worktrees_does_not_exempt_a_main_checkout_mutation(
    hook, root: Path
) -> None:
    """The verdict must come from WHERE the command acts — never from the command text.

    `classify_tool` used to do `if ".dev/worktrees" in cmd: return "exempt"`, so any git
    command that merely MENTIONED that string — a pathspec, a filename, even a commit
    message — silently bypassed the lock while mutating the MAIN checkout. That is
    exactly the concurrent-checkout clobber this guard exists to prevent.
    """
    holes = [
        _bash("git checkout main -- .dev/worktrees/notes.txt", cwd=str(root)),
        _bash('git commit -m "work in .dev/worktrees"', cwd=str(root)),
        _bash("git reset --hard  # tidying .dev/worktrees", cwd=str(root)),
        # A `cd` AFTER the verb cannot move where that verb ran — closing the hole on
        # one side must not reopen it on the other.
        _bash("git checkout main && cd .dev/worktrees/slug", cwd=str(root)),
    ]
    for payload in holes:
        assert hook.classify_tool(payload, root) == "enforce"


def test_git_c_via_an_unexpandable_shell_variable(hook, root: Path) -> None:
    """`git -C $wt ...` — the hook sees raw text and cannot expand shell variables.

    Agents write exactly this (`$wt = "<abs worktree>"; git -C $wt commit ...`), so
    enforcing on it would block the sanctioned parallel path and get the hook ripped out.
    We accept it ONLY when the block also carries an ABSOLUTE path under
    <root>/.dev/worktrees (the assignment that defines the variable). With nothing to
    prove it is a worktree, we fail SAFE and enforce.
    """
    wt = root / ".dev" / "worktrees" / "slug"
    ok = _bash(f'$wt = "{wt}"; git -C $wt commit -m x', cwd=str(root))
    assert hook.classify_tool(ok, root) == "exempt"

    # No absolute worktree path anywhere -> unprovable -> fail safe.
    blind = _bash("git -C $wt checkout main", cwd=str(root))
    assert hook.classify_tool(blind, root) == "enforce"

    # A bare mention must still NOT rescue it — that was the original bypass.
    bare = _bash('git -C $wt checkout main  # see .dev/worktrees', cwd=str(root))
    assert hook.classify_tool(bare, root) == "enforce"


def test_from_msys_converts_only_bash_drive_paths(hook, monkeypatch) -> None:
    """git-bash renders `C:\\foo` as `/c/foo`; convert that to native, nothing else."""
    monkeypatch.setattr(hook.os, "name", "nt")
    assert hook._from_msys("/c/projects/x") == "C:\\projects\\x"
    assert hook._from_msys("/c/Users/a/b") == "C:\\Users\\a\\b"
    # already-native, non-drive POSIX, and short forms are all untouched
    assert hook._from_msys("C:\\projects\\x") == "C:\\projects\\x"
    assert hook._from_msys("/tmp/foo") == "/tmp/foo"  # multi-char first segment is not a drive
    assert hook._from_msys("/e") == "/e"
    # POSIX: always a no-op (there are no drive-letter paths)
    monkeypatch.setattr(hook.os, "name", "posix")
    assert hook._from_msys("/c/projects/x") == "/c/projects/x"


@pytest.mark.skipif(os.name != "nt", reason="MSYS /c/ drive paths are a Windows git-bash form")
def test_git_c_via_msys_bash_worktree_path(hook, root: Path) -> None:
    """Regression: `git -C /c/…/worktrees/x` (git-bash drive form) blocked real work.

    Path("/c/…") is not absolute on Windows, so the worktree target was mis-resolved and
    a legitimate command enforced. Both the literal MSYS path and the `$WT`-variable form
    (with the MSYS path in the assignment) must be exempt.
    """
    wt = root / ".dev" / "worktrees" / "slug"
    drive, rest = os.path.splitdrive(str(wt))
    msys = "/" + drive[0].lower() + rest.replace("\\", "/")

    assert hook.classify_tool(_bash(f'git -C "{msys}" commit -m x', cwd=str(root)), root) == "exempt"
    assert hook.classify_tool(
        _bash(f'WT="{msys}"; git -C "$WT" commit -m x', cwd=str(root)), root
    ) == "exempt"

    # SAFETY: an MSYS path to the MAIN checkout must still ENFORCE (no over-exempt).
    main_rest = os.path.splitdrive(str(root))[1].replace("\\", "/")
    main_msys = "/" + drive[0].lower() + main_rest
    assert hook.classify_tool(
        _bash(f'git -C "{main_msys}" checkout main', cwd=str(root)), root
    ) == "enforce"

    # An MSYS path OUTSIDE the repo is another checkout → exempt, not our lock's concern.
    other = "/" + drive[0].lower() + "/somewhere/else/repo"
    assert hook.classify_tool(
        _bash(f'git -C "{other}" checkout main', cwd=str(root)), root
    ) == "exempt"


def test_git_actually_inside_a_worktree_stays_exempt(hook, root: Path) -> None:
    """The sanctioned parallel path must keep working: cd-into, -C-into, or cwd-inside."""
    wt = root / ".dev" / "worktrees" / "slug"
    exempt = [
        _bash("cd .dev/worktrees/slug && git rebase origin/main", cwd=str(root)),
        _bash(f"git -C {wt} commit -m x", cwd=str(root)),
        _bash("git rebase origin/main", cwd=str(wt)),
    ]
    for payload in exempt:
        assert hook.classify_tool(payload, root) == "exempt"


# ── lock_decision ────────────────────────────────────────────────────────────


def _lock(session: str, updated: datetime) -> dict:
    return {
        "session_id": session,
        "claimed_at": updated.isoformat(),
        "updated_at": updated.isoformat(),
    }


def test_no_lock_claims(hook) -> None:
    assert hook.lock_decision(None, "s1")[0] == "claim"


def test_same_session_refreshes(hook) -> None:
    now = datetime.now(timezone.utc)
    assert hook.lock_decision(_lock("s1", now), "s1")[0] == "refresh"


def test_other_fresh_session_blocks(hook) -> None:
    now = datetime.now(timezone.utc)
    assert hook.lock_decision(_lock("s1", now), "s2", now)[0] == "block"


def test_other_stale_session_is_stolen(hook) -> None:
    now = datetime.now(timezone.utc)
    old = now - timedelta(minutes=hook.TTL_MINUTES + 5)
    assert hook.lock_decision(_lock("s1", old), "s2", now)[0] == "steal"


def test_corrupt_timestamp_claims(hook) -> None:
    lock = {"session_id": "s1", "updated_at": "not-a-date"}
    assert hook.lock_decision(lock, "s2")[0] == "claim"


# ── write/read roundtrip ─────────────────────────────────────────────────────


def test_write_lock_roundtrip_preserves_claimed_at(hook, root: Path) -> None:
    hook.write_lock(root, "s1", None)
    first = hook.read_lock(root)
    assert first["session_id"] == "s1"

    hook.write_lock(root, "s1", first)  # refresh
    second = hook.read_lock(root)
    assert second["claimed_at"] == first["claimed_at"]
    assert second["updated_at"] >= first["updated_at"]

    hook.write_lock(root, "s2", second)  # takeover resets claimed_at
    third = hook.read_lock(root)
    assert third["session_id"] == "s2"
    assert third["claimed_at"] >= second["claimed_at"]


def test_read_lock_corrupt_json_is_none(hook, root: Path) -> None:
    path = hook.lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert hook.read_lock(root) is None


# ── pathspec commit vs a staged `rm --cached` ────────────────────────────────
#
# A pathspec commit is built from the WORKING TREE for the named paths, not the index, so
# a staged deletion whose file is still on disk can be discarded -- and if the file is
# modified, the modification is committed INSTEAD. #1423 merged half-done that way. These
# drive the real function against a real scratch repo; nothing is mocked.


def _scratch_repo(tmp_path: Path) -> Path:
    import subprocess

    repo = tmp_path / "scratch"
    repo.mkdir()
    g = ["git", "-C", str(repo)]
    subprocess.run([*g, "init", "-q"], check=True)
    subprocess.run([*g, "config", "user.email", "t@t"], check=True)
    subprocess.run([*g, "config", "user.name", "t"], check=True)
    (repo / "gen.txt").write_text("generated\n", encoding="utf-8")
    (repo / "other.txt").write_text("other\n", encoding="utf-8")
    subprocess.run([*g, "add", "."], check=True)
    subprocess.run([*g, "commit", "-qm", "init"], check=True)
    return repo


def _rm_cached(repo: Path, name: str) -> None:
    import subprocess

    subprocess.run(["git", "-C", str(repo), "rm", "--cached", "-q", name], check=True)


def test_pathspec_commit_naming_a_staged_rm_cached_is_blocked(hook, tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path)
    _rm_cached(repo, "gen.txt")  # staged as deleted, still on disk
    payload = _bash('git commit -m "untrack" -- gen.txt', cwd=str(repo))
    assert hook.pathspec_commit_discards(payload, repo) == ["gen.txt"]


def test_the_block_names_the_file_and_the_fix(hook) -> None:
    msg = hook.pathspec_message(["gen.txt"])
    assert "gen.txt" in msg
    assert "without a pathspec" in msg
    assert "#1423" in msg


def test_pathspec_commit_is_fine_when_the_deletion_is_not_named(hook, tmp_path: Path) -> None:
    """A staged deletion OUTSIDE the pathspec is simply left staged -- normal git."""
    repo = _scratch_repo(tmp_path)
    _rm_cached(repo, "gen.txt")
    payload = _bash('git commit -m "x" -- other.txt', cwd=str(repo))
    assert hook.pathspec_commit_discards(payload, repo) == []


def test_commit_without_a_pathspec_is_never_blocked(hook, tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path)
    _rm_cached(repo, "gen.txt")
    for cmd in ('git commit -m "untrack"', "git commit -F msg.txt", 'git commit --amend -m "x"'):
        assert hook.pathspec_commit_discards(_bash(cmd, cwd=str(repo)), repo) == [], cmd


def test_a_real_git_rm_is_not_blocked(hook, tmp_path: Path) -> None:
    """File gone from disk: the working tree agrees with the index, the deletion commits."""
    import subprocess

    repo = _scratch_repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "rm", "-q", "gen.txt"], check=True)
    payload = _bash('git commit -m "delete" -- gen.txt', cwd=str(repo))
    assert hook.pathspec_commit_discards(payload, repo) == []


def test_pathspec_covers_directories_and_globs(hook) -> None:
    assert hook._pathspec_covers("web/www", "web/www/next-env.d.ts")
    assert hook._pathspec_covers("web/www/", "web/www/next-env.d.ts")
    assert hook._pathspec_covers("*.d.ts", "next-env.d.ts")
    assert hook._pathspec_covers(".", "anything/at/all")
    assert not hook._pathspec_covers("web/www", "web/wwwx/file")
    assert not hook._pathspec_covers("apps/deck", "web/www/next-env.d.ts")


def test_honours_a_dash_C_target_and_fails_open_on_a_variable(hook, tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path)
    _rm_cached(repo, "gen.txt")
    # -C names the scratch repo explicitly, from an unrelated cwd.
    payload = _bash(f'git -C "{repo}" commit -m "x" -- gen.txt', cwd=str(tmp_path))
    assert hook.pathspec_commit_discards(payload, tmp_path) == ["gen.txt"]
    # -C $var cannot be expanded: fail open rather than inspect the wrong tree.
    payload = _bash('git -C $WT commit -m "x" -- gen.txt', cwd=str(repo))
    assert hook.pathspec_commit_discards(payload, repo) == []


def test_the_full_hook_returns_2_for_the_footgun(hook, tmp_path: Path, monkeypatch) -> None:
    """End to end through main(): stdin payload in, exit 2 and the message on stderr."""
    import io
    import json

    repo = _scratch_repo(tmp_path)
    _rm_cached(repo, "gen.txt")
    payload = _bash('git commit -m "untrack" -- gen.txt', cwd=str(repo))
    payload["session_id"] = "s-test"
    payload["hook_event_name"] = "PreToolUse"
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(hook, "repo_root", lambda: repo)
    err = io.StringIO()
    monkeypatch.setattr("sys.stderr", err)
    assert hook.main() == 2
    assert "DISCARD" in err.getvalue() and "gen.txt" in err.getvalue()


# ── heartbeat: an exempt call by the holder keeps the claim alive ───────────


def _run_main(hook, monkeypatch, repo: Path, payload: dict) -> int:
    import io
    import json

    payload.setdefault("hook_event_name", "PreToolUse")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(hook, "repo_root", lambda: repo)
    monkeypatch.setattr("sys.stderr", io.StringIO())
    return hook.main()


def test_an_exempt_call_by_the_holder_refreshes_the_lock(hook, tmp_path: Path, monkeypatch) -> None:
    """A Bash-only hour is still an hour of work: the holder's read-only call
    bumps updated_at, so the claim cannot go stale under a live session."""
    repo = _scratch_repo(tmp_path)
    old = datetime.now(timezone.utc) - timedelta(minutes=hook.TTL_MINUTES - 5)
    hook.lock_path(repo).parent.mkdir(parents=True, exist_ok=True)
    hook.lock_path(repo).write_text(
        __import__("json").dumps(_lock("s-holder", old)), encoding="utf-8"
    )
    payload = _bash("ls -la", cwd=str(repo))
    payload["session_id"] = "s-holder"
    assert _run_main(hook, monkeypatch, repo, payload) == 0
    refreshed = hook.read_lock(repo)
    assert refreshed["session_id"] == "s-holder"
    assert datetime.fromisoformat(refreshed["updated_at"]) > old


def test_an_exempt_call_by_a_stranger_never_claims_or_touches(hook, tmp_path: Path, monkeypatch) -> None:
    """Reading is not claiming: another session's read-only call leaves the
    holder's lock byte-identical, and with no lock at all none appears."""
    repo = _scratch_repo(tmp_path)
    payload = _bash("git status", cwd=str(repo))
    payload["session_id"] = "s-reader"
    assert _run_main(hook, monkeypatch, repo, payload) == 0
    assert hook.read_lock(repo) is None

    now = datetime.now(timezone.utc)
    hook.lock_path(repo).parent.mkdir(parents=True, exist_ok=True)
    hook.lock_path(repo).write_text(
        __import__("json").dumps(_lock("s-holder", now)), encoding="utf-8"
    )
    before = hook.lock_path(repo).read_text(encoding="utf-8")
    assert _run_main(hook, monkeypatch, repo, dict(payload)) == 0
    assert hook.lock_path(repo).read_text(encoding="utf-8") == before


# ── repo_root from inside a linked worktree ──────────────────────────────────


def _git(*args: str, cwd: Path) -> None:
    import subprocess
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def _real_repo_with_worktree(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "t@navig.local", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "f.txt").write_text("x", encoding="utf-8")
    _git("add", "f.txt", cwd=repo)
    _git("commit", "-q", "-m", "base", cwd=repo)
    wt = repo / ".dev" / "worktrees" / "x"
    wt.parent.mkdir(parents=True)
    _git("worktree", "add", "-q", str(wt), "-b", "feat/x", cwd=repo)
    return repo, wt


def _hook_loaded_from(dir_: Path):
    """The hook as a checkout would carry it: a copy living INSIDE `dir_`."""
    import shutil
    dest = dir_ / "scripts" / "agent-hooks"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_HOOK, dest / "agent_lock.py")
    spec = importlib.util.spec_from_file_location(f"agent_lock_from_{dir_.name}", dest / "agent_lock.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_repo_root_from_a_linked_worktree_is_the_main_tree(tmp_path: Path) -> None:
    """A linked worktree's `.git` is a FILE; `.exists()` accepted it and the walk stopped
    there, so a hook running from a worktree session treated the worktree as the repo."""
    repo, wt = _real_repo_with_worktree(tmp_path)
    assert _hook_loaded_from(wt).repo_root().resolve() == repo.resolve()
    assert _hook_loaded_from(repo).repo_root().resolve() == repo.resolve()


def test_from_a_worktree_session_a_sibling_worktree_add_in_the_main_tree_is_not_a_sibling(tmp_path: Path) -> None:
    """THE regression. `navig repo new y` runs a worktree add with the ABSOLUTE main-tree
    path. From a session in `.dev/worktrees/x` that path is outside worktree x — and the
    old root made the hook BLOCK it as 'a worktree OUTSIDE this repo', while its message
    recommended the relative form that would have nested a worktree inside the worktree."""
    repo, wt = _real_repo_with_worktree(tmp_path)
    mod = _hook_loaded_from(wt)
    root = mod.repo_root()
    target = (repo / ".dev" / "worktrees" / "y").as_posix()
    verdict = mod.classify_tool(_bash(f"git worktree add {target} -b feat/y", cwd=str(wt)), root)
    assert verdict != "block-sibling", verdict
    # and a genuinely outside path is still caught, from the same session
    outside = (tmp_path / "elsewhere").as_posix()
    assert mod.classify_tool(_bash(f"git worktree add {outside} -b feat/z", cwd=str(wt)), root) == "block-sibling"


# -- a `cd` hop in git-bash / PowerShell form ----------------------------------
# The `-C` target was converted from MSYS form (regression test above), but the `cd`
# hop that decides the same thing was not: Path("/c/...") has a root and no drive, so
# `base / hop` kept only the drive and produced C:\e\projects\..., "outside the repo",
# and the command was EXEMPT. Live incident 2026-09-26: another session ran
#   cd /c/projects/apps/navig && git checkout -b fix/deck-initdata-signature-field
# from the repo's core/ dir while this session held a fresh lock; the checkout went
# through, and this session's next commit landed on that foreign branch.


def _msys(p: Path) -> str:
    drive, rest = os.path.splitdrive(str(p))
    return "/" + drive[0].lower() + rest.replace("\\", "/")


@pytest.mark.skipif(os.name != "nt", reason="MSYS /c/ drive paths are a Windows git-bash form")
def test_cd_into_the_main_checkout_via_msys_path_is_enforced(hook, root: Path) -> None:
    (root / "core").mkdir()
    cmd = f"cd {_msys(root)} && git checkout -b fix/x 2>&1 | tail -2"
    # The exact incident shape: from a subdirectory, and from inside a worktree.
    assert hook.classify_tool(_bash(cmd, cwd=str(root / "core")), root) == "enforce"
    wt = root / ".dev" / "worktrees" / "slug"
    wt.mkdir(parents=True)
    assert hook.classify_tool(_bash(cmd, cwd=str(wt)), root) == "enforce"


@pytest.mark.skipif(os.name != "nt", reason="MSYS /c/ drive paths are a Windows git-bash form")
def test_cd_into_a_worktree_via_msys_path_stays_exempt(hook, root: Path) -> None:
    wt = root / ".dev" / "worktrees" / "slug"
    wt.mkdir(parents=True)
    cmd = f"cd {_msys(wt)} && git rebase origin/main"
    assert hook.classify_tool(_bash(cmd, cwd=str(root)), root) == "exempt"


@pytest.mark.skipif(os.name != "nt", reason="MSYS /c/ drive paths are a Windows git-bash form")
def test_cd_via_msys_path_outside_the_repo_stays_exempt(hook, root: Path) -> None:
    other = root.parent / "elsewhere"
    other.mkdir(exist_ok=True)
    cmd = f"cd {_msys(other)} && git checkout main"
    assert hook.classify_tool(_bash(cmd, cwd=str(root)), root) == "exempt"


def test_powershell_set_location_into_the_main_checkout_is_enforced(hook, root: Path) -> None:
    wt = root / ".dev" / "worktrees" / "slug"
    wt.mkdir(parents=True)
    payload = {
        "tool_name": "PowerShell",
        "tool_input": {"command": f"Set-Location '{root}'; git checkout main"},
        "cwd": str(wt),
    }
    assert hook.classify_tool(payload, root) == "enforce"


def test_powershell_set_location_into_a_worktree_stays_exempt(hook, root: Path) -> None:
    wt = root / ".dev" / "worktrees" / "slug"
    wt.mkdir(parents=True)
    payload = {
        "tool_name": "PowerShell",
        "tool_input": {"command": f"Set-Location '{wt}'; git rebase origin/main"},
        "cwd": str(root),
    }
    assert hook.classify_tool(payload, root) == "exempt"


# -- a `cd` whose target cannot be resolved ------------------------------------
# `cd "$ROOT"`, `cd $env:ROOT`, `cd -` leave the shell somewhere the hook cannot know,
# yet the verb was judged by the directory it LEFT — so from inside a worktree,
# `cd "$ROOT" && git checkout -b x` was EXEMPT (it can move the main checkout's HEAD under
# a live lock). Now: the same rule as a `-C` target that is a shell variable — exempt only
# when the command itself carries an absolute .dev/worktrees path, else the lock applies.


def _wt(root: Path) -> Path:
    wt = root / ".dev" / "worktrees" / "slug"
    wt.mkdir(parents=True, exist_ok=True)
    return wt


def test_cd_to_a_variable_from_a_worktree_is_enforced(hook, root: Path) -> None:
    wt = _wt(root)
    cmd = f'ROOT="{root}"; cd "$ROOT" && git checkout -b fix/y'
    assert hook.classify_tool(_bash(cmd, cwd=str(wt)), root) == "enforce"


def test_powershell_cd_to_an_env_variable_is_enforced(hook, root: Path) -> None:
    wt = _wt(root)
    payload = {
        "tool_name": "PowerShell",
        "tool_input": {"command": "cd $env:ROOT; git checkout main"},
        "cwd": str(wt),
    }
    assert hook.classify_tool(payload, root) == "enforce"


def test_cd_dash_is_an_unknown_location(hook, root: Path) -> None:
    wt = _wt(root)
    assert hook.classify_tool(_bash("cd - && git checkout main", cwd=str(wt)), root) == "enforce"


def test_cd_to_a_variable_naming_an_absolute_worktree_is_exempt(hook, root: Path) -> None:
    # Parity with a `-C "$WT"` target: the assignment names the sanctioned path.
    wt = _wt(root)
    cmd = f'WT="{wt}"; cd "$WT" && git rebase origin/main'
    assert hook.classify_tool(_bash(cmd, cwd=str(root)), root) == "exempt"


def test_an_absolute_cd_after_an_unknown_one_makes_the_location_known(hook, root: Path) -> None:
    wt = _wt(root)
    cmd = f'cd "$X" && cd "{wt}" && git rebase origin/main'
    assert hook.classify_tool(_bash(cmd, cwd=str(root)), root) == "exempt"
    cmd = f'cd "$X" && cd "{root}" && git checkout main'
    assert hook.classify_tool(_bash(cmd, cwd=str(wt)), root) == "enforce"


def test_a_relative_dash_c_after_an_unknown_cd_is_enforced(hook, root: Path) -> None:
    wt = _wt(root)
    cmd = 'cd "$X" && git -C sub checkout main'
    assert hook.classify_tool(_bash(cmd, cwd=str(wt)), root) == "enforce"


def test_cd_tilde_is_expanded_not_treated_as_unknown(hook, root: Path, monkeypatch) -> None:
    wt = _wt(root)
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(root))
    assert hook.classify_tool(_bash("cd ~ && git checkout main", cwd=str(wt)), root) == "enforce"
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(wt))
    assert hook.classify_tool(_bash("cd ~ && git rebase origin/main", cwd=str(root)), root) == "exempt"


def test_a_read_only_command_after_an_unknown_cd_stays_exempt(hook, root: Path) -> None:
    wt = _wt(root)
    assert hook.classify_tool(_bash('cd "$ROOT" && git status', cwd=str(wt)), root) == "exempt"


# -- the commit-branch guard ---------------------------------------------------
# The lock stops another HOOKED session moving HEAD; nothing stopped a terminal or an
# unhooked tool doing it between this session's `checkout -b X` and its `commit`, which
# then landed on their branch (twice on 2026-09-26). The lock now records the branch this
# session switched to and refuses a commit when HEAD has moved to a different named branch.


def _gitdir(root: Path, head: str = "main", branches=("main",), packed=(), remotes=()) -> Path:
    """A minimal on-disk .git the hook can read without spawning git."""
    g = root / ".git"
    (g / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    (g / "HEAD").write_text(
        head if head.startswith(("ref:", "0", "a")) and len(head) >= 40 else f"ref: refs/heads/{head}\n",
        encoding="utf-8",
    )
    for b in branches:
        p = g / "refs" / "heads" / b
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("0" * 40 + "\n", encoding="utf-8")
    for r in remotes:
        p = g / "refs" / "remotes" / "origin" / r
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("0" * 40 + "\n", encoding="utf-8")
    if packed:
        (g / "packed-refs").write_text(
            "# pack-refs with: peeled fully-peeled sorted\n"
            + "".join(f"{'1' * 40} refs/heads/{b}\n" for b in packed),
            encoding="utf-8",
        )
    return g


def _set_head(root: Path, branch: str) -> None:
    (root / ".git" / "HEAD").write_text(f"ref: refs/heads/{branch}\n", encoding="utf-8")


def test_a_commit_after_head_moved_to_another_branch_is_blocked(hook, root: Path) -> None:
    _gitdir(root, head="theirs", branches=("main", "mine", "theirs"))
    msg, expected = hook.commit_branch_check("git commit -m work", root, "mine")
    assert msg and "'theirs'" in msg and "'mine'" in msg
    assert msg.isascii(), "the hook pipe garbles non-ASCII"
    assert expected == "mine"


def test_a_commit_on_the_expected_branch_is_allowed(hook, root: Path) -> None:
    _gitdir(root, head="mine", branches=("main", "mine"))
    assert hook.commit_branch_check("git commit -m work", root, "mine") == (None, "mine")


def test_without_a_recorded_branch_nothing_is_checked(hook, root: Path) -> None:
    _gitdir(root, head="theirs", branches=("main", "theirs"))
    assert hook.commit_branch_check("git commit -m work", root, None) == (None, None)


def test_a_detached_head_is_never_checked(hook, root: Path) -> None:
    _gitdir(root, head="a" * 40, branches=("main", "mine"))  # mid-rebase / bisect
    assert hook.commit_branch_check("git commit -m work", root, "mine")[0] is None


def test_a_switch_then_commit_in_one_command_is_not_a_move(hook, root: Path) -> None:
    _gitdir(root, head="main", branches=("main",))
    for cmd in (
        "git checkout -b feat/x && git add -- a.py && git commit -m x",
        "git switch -c feat/x; git commit -m x",  # PowerShell-style separator
    ):
        msg, expected = hook.commit_branch_check(cmd, root, "old")
        assert msg is None and expected == "feat/x", cmd


def test_switch_forms_that_are_recorded(hook, root: Path) -> None:
    _gitdir(root, head="main", branches=("main", "loose"), packed=("packed",), remotes=("remote",))
    cases = {
        "git checkout -b feat/a": "feat/a",
        "git checkout -B feat/a origin/main": "feat/a",
        "git switch -c feat/a": "feat/a",
        "git switch --create feat/a": "feat/a",
        "git checkout --orphan gh-pages": "gh-pages",
        "git checkout loose": "loose",
        "git checkout packed": "packed",  # only in packed-refs
        "git switch remote": "remote",  # git DWIMs origin/remote into a local branch
        "git -c core.x=y checkout -q loose": "loose",
    }
    for cmd, want in cases.items():
        assert hook.commit_branch_check(cmd, root, None) == (None, want), cmd


def test_moves_the_hook_cannot_name_clear_the_record(hook, root: Path) -> None:
    _gitdir(root, head="main", branches=("main", "mine"))
    for cmd in (
        "git checkout 1234abcd",  # a sha: detached
        "git switch --detach",
        "git checkout -",
        "git branch -m renamed",
        "git bisect start",
        "gh pr merge 12 --squash",
        "gh pr checkout 12",
    ):
        assert hook.commit_branch_check(cmd, root, "mine") == (None, None), cmd


def test_commands_that_do_not_move_head_keep_the_record(hook, root: Path) -> None:
    _gitdir(root, head="mine", branches=("main", "mine"))
    (root / "a.py").write_text("x\n", encoding="utf-8")
    for cmd in (
        "git checkout -- a.py",
        "git checkout a.py",  # a path, not a branch
        "git branch feat/new",
        "git status",
        "git rebase origin/main",
    ):
        assert hook.commit_branch_check(cmd, root, "mine") == (None, "mine"), cmd


def test_a_commit_in_another_checkout_via_dash_c_is_not_checked(hook, root: Path) -> None:
    _gitdir(root, head="theirs", branches=("main", "theirs"))
    cmd = 'git -C "C:/c/.dev/worktrees/y" commit -m x'
    assert hook.commit_branch_check(cmd, root, "mine")[0] is None


# -- the record survives the lock's own refreshes ------------------------------


def test_write_lock_carries_the_record_for_the_same_session_only(hook, root: Path) -> None:
    _gitdir(root, head="mine", branches=("main", "mine"))
    hook.write_lock(root, "me", None, expected="mine")
    hook.heartbeat(root, "me")  # an exempt call refreshes...
    assert hook.read_lock(root)["expected_branch"] == "mine"  # ...and keeps it
    hook.write_lock(root, "me", hook.read_lock(root))  # a plain refresh keeps it too
    assert hook.read_lock(root)["expected_branch"] == "mine"
    hook.write_lock(root, "someone-else", hook.read_lock(root))  # a new holder starts clean
    assert hook.read_lock(root)["expected_branch"] is None


def _drive_hook(hook, root: Path, monkeypatch, session: str, command: str) -> int:
    import io
    import json

    monkeypatch.setattr(hook, "repo_root", lambda: root)
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": session,
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(root),
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    return hook.main()


def test_end_to_end_a_terminal_moving_head_blocks_the_sessions_next_commit(
    hook, root: Path, monkeypatch
) -> None:
    _gitdir(root, head="main", branches=("main",))
    assert _drive_hook(hook, root, monkeypatch, "me", "git checkout -b feat/mine") == 0
    _gitdir(root, head="feat/mine", branches=("main", "feat/mine"))  # git did it
    assert hook.read_lock(root)["expected_branch"] == "feat/mine"

    _set_head(root, "theirs")  # a person at a terminal: git checkout -b theirs
    (root / ".git" / "refs" / "heads" / "theirs").write_text("0" * 40, encoding="utf-8")
    assert _drive_hook(hook, root, monkeypatch, "me", "git commit -m work") == 2

    # Going back is the recovery, and it is recorded as this session's choice.
    assert _drive_hook(hook, root, monkeypatch, "me", "git checkout feat/mine") == 0
    _set_head(root, "feat/mine")
    assert _drive_hook(hook, root, monkeypatch, "me", "git commit -m work") == 0


def test_end_to_end_the_normal_flow_is_never_blocked(hook, root: Path, monkeypatch) -> None:
    _gitdir(root, head="main", branches=("main",))
    assert _drive_hook(hook, root, monkeypatch, "me", "git checkout -b feat/a && git commit -m a") == 0
    _gitdir(root, head="feat/a", branches=("main", "feat/a"))
    assert _drive_hook(hook, root, monkeypatch, "me", "git commit -m b") == 0
    assert _drive_hook(hook, root, monkeypatch, "me", "git checkout main && git pull --ff-only") == 0
    _set_head(root, "main")
    assert _drive_hook(hook, root, monkeypatch, "me", "git checkout -b feat/b") == 0


# ── a branch move that would drop unpushed commits ───────────────────────────
#
# 2026-10-13: a session aborted a rebase in the Schema checkout, then reset main to
# origin/main and dropped seven commits another session had not pushed yet -- recovered
# by hand from the reflog a day later. The resetting session held the lock, so the
# check runs for EVERY session. Real scratch repos with a real remote; nothing mocked.


def _repo_with_remote(tmp_path: Path) -> Path:
    """A clone whose main is in sync with origin/main (one commit)."""
    import subprocess

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    seed = _scratch_repo(tmp_path)
    g = ["git", "-C", str(seed)]
    subprocess.run([*g, "branch", "-M", "main"], check=True)
    subprocess.run([*g, "remote", "add", "origin", str(origin)], check=True)
    subprocess.run([*g, "push", "-q", "origin", "main"], check=True)
    subprocess.run([*g, "fetch", "-q", "origin"], check=True)
    return seed


def _commit(repo: Path, name: str, message: str) -> None:
    import subprocess

    (repo / name).write_text(message + "\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", name], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", message], check=True)


def test_hard_reset_to_the_remote_over_unpushed_commits_is_blocked(hook, tmp_path: Path) -> None:
    """The 2026-10-13 shape: unpushed work on main, then `reset --hard origin/main`."""
    repo = _repo_with_remote(tmp_path)
    _commit(repo, "a.txt", "the sentinel walks")
    _commit(repo, "b.txt", "the handle is sealed")
    lost, branch = hook.unpushed_commits_dropped(
        _bash("git reset --hard origin/main", cwd=str(repo)), repo
    )
    assert branch == "main"
    assert [line.split(" ", 1)[1] for line in lost] == ["the handle is sealed", "the sentinel walks"]
    msg = hook.dropped_message(lost, branch)
    assert "the sentinel walks" in msg
    assert "git branch keep/main main" in msg
    assert msg.isascii()  # the Windows hook pipe garbles anything else


def test_keeping_the_commits_on_a_branch_unblocks_the_same_reset(hook, tmp_path: Path) -> None:
    import subprocess

    repo = _repo_with_remote(tmp_path)
    _commit(repo, "a.txt", "work")
    subprocess.run(["git", "-C", str(repo), "branch", "keep/main", "main"], check=True)
    assert hook.unpushed_commits_dropped(
        _bash("git reset --hard origin/main", cwd=str(repo)), repo
    ) == ([], "main")


def test_pushed_work_resets_freely(hook, tmp_path: Path) -> None:
    import subprocess

    repo = _repo_with_remote(tmp_path)
    _commit(repo, "a.txt", "work")
    subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "main"], check=True)
    lost, _ = hook.unpushed_commits_dropped(_bash("git reset --hard HEAD~1", cwd=str(repo)), repo)
    assert lost == []


def test_work_already_upstream_as_a_rebased_copy_is_not_lost(hook, tmp_path: Path) -> None:
    """`git cherry` sees the patch upstream under another sha -- a reset after a rebase."""
    import subprocess

    repo = _repo_with_remote(tmp_path)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(tmp_path / "origin.git"), str(other)], check=True)
    for r in (repo, other):
        subprocess.run(["git", "-C", str(r), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(r), "config", "user.name", "t"], check=True)
    _commit(other, "z.txt", "someone else landed first")
    subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"], check=True)
    _commit(repo, "a.txt", "work")
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    # The same patch lands on the remote through another clone (a rebase elsewhere).
    subprocess.run(["git", "-C", str(other), "fetch", "-q", str(repo), sha], check=True)
    subprocess.run(["git", "-C", str(other), "cherry-pick", sha], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"], check=True)
    subprocess.run(["git", "-C", str(repo), "fetch", "-q", "origin"], check=True)
    lost, _ = hook.unpushed_commits_dropped(
        _bash("git reset --hard origin/main", cwd=str(repo)), repo
    )
    assert lost == []


def test_resets_that_keep_the_content_are_not_judged(hook, tmp_path: Path) -> None:
    repo = _repo_with_remote(tmp_path)
    _commit(repo, "a.txt", "work")
    for cmd in ("git reset --soft origin/main", "git reset origin/main", "git reset --hard",
                "git reset HEAD -- a.txt", "git status"):
        assert hook.unpushed_commits_dropped(_bash(cmd, cwd=str(repo)), repo)[0] == [], cmd


def test_force_moving_a_branch_off_unpushed_commits_is_blocked(hook, tmp_path: Path) -> None:
    import subprocess

    repo = _repo_with_remote(tmp_path)
    subprocess.run(["git", "-C", str(repo), "checkout", "-qb", "feat/x"], check=True)
    _commit(repo, "a.txt", "feature work")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)
    for cmd in ("git branch -f feat/x origin/main", "git checkout -B feat/x origin/main",
                "git switch -C feat/x origin/main"):
        lost, branch = hook.unpushed_commits_dropped(_bash(cmd, cwd=str(repo)), repo)
        assert branch == "feat/x", cmd
        assert [line.split(" ", 1)[1] for line in lost] == ["feature work"], cmd
    # Creating a NEW branch moves nothing.
    assert hook.unpushed_commits_dropped(
        _bash("git checkout -B fresh origin/main", cwd=str(repo)), repo
    ) == ([], "")


def test_the_reset_guard_follows_git_c_and_fails_open_on_a_variable(hook, tmp_path: Path) -> None:
    repo = _repo_with_remote(tmp_path)
    _commit(repo, "a.txt", "work")
    elsewhere = str(tmp_path)
    lost, _ = hook.unpushed_commits_dropped(
        _bash(f'git -C "{repo}" reset --hard origin/main', cwd=elsewhere), repo
    )
    assert len(lost) == 1
    assert hook.unpushed_commits_dropped(
        _bash("git -C $wt reset --hard origin/main", cwd=elsewhere), repo
    ) == ([], "")


def test_main_blocks_the_drop_for_the_lock_holder_too(hook, tmp_path: Path, monkeypatch) -> None:
    """Exit 2 even with no lock contention -- the 10-13 resetter held the lock."""
    import io
    import json

    repo = _repo_with_remote(tmp_path)
    _commit(repo, "a.txt", "work")
    monkeypatch.setattr(hook, "repo_root", lambda: repo)
    payload = {"hook_event_name": "PreToolUse", "session_id": "me", **_bash(
        "git reset --hard origin/main", cwd=str(repo)
    )}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert hook.main() == 2


def test_the_reset_guard_reads_git_grammar_not_words(hook) -> None:
    """A path holding the word `reset`, and `switch -C <branch>`, are not what they look like."""
    cmd = 'git -C "C:/c/.dev/worktrees/reset-guard/core" reset --hard origin/main'
    m = hook._RESET.search(cmd)
    assert m and m.group(1).strip() == "--hard origin/main"
    assert hook._GIT_GLOBAL_C.search(cmd).group(1) == '"C:/c/.dev/worktrees/reset-guard/core"'
    assert hook._GIT_GLOBAL_C.search("git switch -C feat/x origin/main") is None
    assert hook._RESET.search("git log --grep=reset") is None
    assert hook._RESET.search('git commit -m "reset the board"') is None


# ── a message or a pattern is never read as a verb (word-scan false positives) ─
#
# 2026-10-17 the word-scan blocked `git branch --list "*reset*"` while sweeping a
# checkout: the PATTERN argument held a dangerous word. git reads its subcommand
# from one position; words in arguments are data.


def test_a_message_or_argument_holding_a_verb_word_is_read_only(hook, root: Path) -> None:
    for command in (
        'git branch --list "*reset*"',
        "git log --grep=reset --oneline",
        'git log --grep "checkout stuff"',
        "git diff --name-only my-reset-branch",
        "git show HEAD -- path/with-reset-in-it.txt",
        "git log && reset",
    ):
        assert hook.classify_tool(_bash(command), root) == "exempt", command


def test_a_real_verb_still_counts_with_any_message(hook, root: Path) -> None:
    for command in (
        'git commit -m "reset the board"',
        'git -C "some dir" reset --hard',
        "git stash pop",
        "git rebase origin/main",
        'git worktree add .dev/worktrees/x -b feat/x && git reset --hard origin/main',
    ):
        assert hook.classify_tool(_bash(command), root) == "enforce", command
