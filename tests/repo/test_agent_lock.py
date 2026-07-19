"""Tests for the main-checkout agent-lock hook (scripts/agent-hooks/agent_lock.py).

The hook is stdlib-only and lives outside the ``navig`` package, so it is
loaded by file path. Covers tool classification (what is locked vs exempt)
and the pure lock decision (claim / refresh / steal / block).
"""

from __future__ import annotations

import importlib.util
import json
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
    """git-bash renders `E:\\foo` as `/e/foo`; convert that to native, nothing else."""
    monkeypatch.setattr(hook.os, "name", "nt")
    assert hook._from_msys("/e/projects/x") == "E:\\projects\\x"
    assert hook._from_msys("/c/Users/a/b") == "C:\\Users\\a\\b"
    # already-native, non-drive POSIX, and short forms are all untouched
    assert hook._from_msys("E:\\projects\\x") == "E:\\projects\\x"
    assert hook._from_msys("/tmp/foo") == "/tmp/foo"  # multi-char first segment is not a drive
    assert hook._from_msys("/e") == "/e"
    # POSIX: always a no-op (there are no drive-letter paths)
    monkeypatch.setattr(hook.os, "name", "posix")
    assert hook._from_msys("/e/projects/x") == "/e/projects/x"


@pytest.mark.skipif(os.name != "nt", reason="MSYS /e/ drive paths are a Windows git-bash form")
def test_git_c_via_msys_bash_worktree_path(hook, root: Path) -> None:
    """Regression: `git -C /e/…/worktrees/x` (git-bash drive form) blocked real work.

    Path("/e/…") is not absolute on Windows, so the worktree target was mis-resolved and
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
