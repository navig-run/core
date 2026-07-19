#!/usr/bin/env python3
"""Claude Code agent-lock hook — one session at a time may mutate the MAIN checkout.

Why: several Claude Code sessions can run against this repo at once. A branch
does NOT isolate files — one folder has one HEAD — so a second session's
``git checkout`` / ``rebase`` can destroy the first session's uncommitted
edits (this happened; see memory ``navig-shared-tree-rebase-hazard``).

What this does (stdlib-only, fail-open — any internal error allows the call):

* PreToolUse (Edit|Write|MultiEdit|NotebookEdit|Bash|PowerShell): the first session to
  edit claims ``.dev/agent.lock``; every later edit refreshes it. A DIFFERENT
  live session gets **exit 2** (blocked) with instructions to work in a
  worktree under ``.dev/worktrees/`` instead. Locks unrefreshed for
  ``TTL_MINUTES`` count as dead and are taken over silently.
* Sibling worktrees are blocked for EVERY session (independent of the lock):
  ``git worktree add`` targeting a path OUTSIDE the repo violates the house
  hard rule — worktrees live inside ``.dev/worktrees/``.
* SessionEnd: releases the lock if this session holds it.

Always allowed (never locked):
* edits outside this repo, and edits under ``.dev/worktrees/**``;
* read-only Bash (anything without a mutating ``git`` verb);
* ``git worktree ...`` management (except sibling ``add``, above),
  ``git stash list/show``, and git commands explicitly targeting another
  checkout via ``git -C <path-outside-repo>``.

Wiring (``.claude/`` may be gitignored, so each machine wires once): run
``navig repo guard install``, or copy the snippet from
``scripts/agent-hooks/README.md`` into ``.claude/settings.json`` using
ABSOLUTE script paths. Hook stderr/stdout must stay pure ASCII — the Windows
hook pipe garbles non-ASCII. Inspect / release the lock: ``navig repo lock``
(or delete ``.dev/agent.lock``). Keep TTL + lock schema in sync with
``core/navig/commands/repo.py``.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

TTL_MINUTES = 60  # keep in sync with core/navig/commands/repo.py
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}

# A shell command is dangerous when it runs a git verb that mutates the
# working tree / index / HEAD of this checkout.
_DANGEROUS_GIT = re.compile(
    r"\bgit\b[^\n|&;]*?\b(checkout|switch|rebase|reset|merge|pull|cherry-pick"
    r"|revert|clean|restore|commit|add|rm|mv|am|apply"
    r"|stash(?!\s+(?:list|show)|@))\b"  # stash@{n} is a ref, not the verb
)
_GIT_C_TARGET = re.compile(r"\bgit\b\s+(?:[^\s]+\s+)*?-C\s+([^\s\"']+|\"[^\"]+\"|'[^']+')")
# A `cd`/`pushd` at the start of the command or of a chained segment — it moves the
# directory a following git verb runs in (`cd .dev/worktrees/x && git rebase ...`).
# Anchored to a segment boundary so it cannot match inside a quoted message.
# A `-C` target we cannot expand: `$wt`, `${wt}`, `$env:wt`, `%WT%`.
_SHELL_VAR = re.compile(r"[$%]")
_CD_TARGET = re.compile(
    r"(?:^|[;&|]\s*)(?:cd|pushd|Push-Location)\s+([^\s;&|\"']+|\"[^\"]+\"|'[^']+')",
    re.IGNORECASE,
)
_WORKTREE_ADD = re.compile(r"\bgit\b[^\n|&;]*?\bworktree\s+add\s+([^\n|&;]+)")

# `git worktree add` flags that consume the following token as their value.
_WT_VALUE_FLAGS = {"-b", "-B", "--reason", "--orphan"}


def repo_root() -> Path:
    """This checkout's root — nearest ancestor of this file containing .git."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists():
            return parent
    return here.parents[1]  # fallback: <root>/<dir>/agent_lock.py -> root


def lock_path(root: Path) -> Path:
    return root / ".dev" / "agent.lock"


def _from_msys(path_str: str) -> str:
    """Convert a git-bash / MSYS drive path (``/e/foo``) to a native Windows path
    (``E:\\foo``). No-op on POSIX and for already-native paths.

    On Windows, git-bash renders ``E:\\`` as ``/e/`` — but ``Path("/e/foo")`` is NOT
    absolute there (no drive), so ``git -C /e/…/worktrees/x`` was mis-resolved and a
    legitimate worktree command got blocked. Only a single-letter ``/x/`` prefix converts,
    so real POSIX paths like ``/tmp/foo`` are untouched.
    """
    if os.name == "nt":
        m = re.match(r"^/([A-Za-z])/(.*)$", path_str)
        if m:
            return m.group(1).upper() + ":\\" + m.group(2).replace("/", "\\")
    return path_str


def _is_within(child: str, parent: Path, base: Path | None = None) -> bool:
    try:
        p_child = Path(_from_msys(child))
        if not p_child.is_absolute() and base is not None:
            p_child = base / p_child
        c = os.path.normcase(str(p_child.resolve()))
    except OSError:
        return False
    p = os.path.normcase(str(parent.resolve()))
    return c == p or c.startswith(p + os.sep)


def _mentions_worktree_path(cmd: str, root: Path) -> bool:
    """True if the command text contains an ABSOLUTE path under <root>/.dev/worktrees.

    Only used when a ``git -C`` target is a shell variable we cannot expand. Requiring an
    absolute path (not a bare ``.dev/worktrees`` mention) is what keeps this from being
    the old text-substring bypass all over again. Recognises BOTH the native path and its
    git-bash/MSYS form (``E:\\…`` ↔ ``/e/…``), since agents drive git from bash too.
    """
    try:
        wt_abs = (root / ".dev" / "worktrees").resolve()
    except OSError:  # pragma: no cover
        return False
    # normcase folds separators (/ -> \) and case on Windows, so /e/… and E:\… collapse
    # onto the same haystack; only the NEEDLE differs between the two path forms.
    hay = os.path.normcase(cmd.replace("/", os.sep))
    if (os.path.normcase(str(wt_abs)) + os.sep) in hay:  # native drive-colon form: C:\…
        return True
    # git-bash/MSYS form on Windows: E:\projects\… is written /e/projects/… — which
    # normalises to \e\projects\… (no drive colon), a needle distinct from the native one.
    drive, rest = os.path.splitdrive(str(wt_abs))
    if drive.endswith(":"):
        msys = os.path.normcase(os.sep + drive[0] + rest)
        if (msys + os.sep) in hay:
            return True
    return False


def worktree_add_target(args_text: str) -> str | None:
    """First non-flag token of ``git worktree add <args>`` = the target path."""
    tokens = re.findall(r'"[^"]*"|\'[^\']*\'|\S+', args_text.strip())
    skip_next = False
    for tok in tokens:
        if skip_next:
            skip_next = False
            continue
        if tok.startswith("-"):
            if tok in _WT_VALUE_FLAGS:
                skip_next = True
            continue
        return tok.strip("\"'")
    return None


def classify_tool(payload: dict, root: Path) -> str:
    """``"enforce"`` (lock applies), ``"block-sibling"`` (hard rule), or ``"exempt"``."""
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}

    if tool in EDIT_TOOLS:
        target = tool_input.get("file_path") or tool_input.get("notebook_path")
        if not target:
            return "enforce"  # unknown target inside an edit tool -> be safe
        if not _is_within(target, root):
            return "exempt"  # outside this repo (scratchpad, other projects)
        if _is_within(target, root / ".dev" / "worktrees"):
            return "exempt"  # isolated worktree — the sanctioned parallel path
        return "enforce"

    if tool in SHELL_TOOLS:
        cmd = str(tool_input.get("command", ""))
        wt_add = _WORKTREE_ADD.search(cmd)
        if wt_add:
            target = worktree_add_target(wt_add.group(1))
            # Resolve relative targets against BOTH the session cwd and the repo
            # root; block only when clearly outside (a malformed/foreign-style
            # cwd must not turn an in-repo path into a false sibling).
            bases = [Path(payload.get("cwd") or root), root]
            if target and not any(_is_within(target, root, base=b) for b in bases):
                return "block-sibling"  # hard rule: no worktrees outside the repo
            return "exempt"  # in-repo worktree add never moves this HEAD
        danger = _DANGEROUS_GIT.search(cmd)
        if not danger:
            return "exempt"
        if re.search(r"\bgit\s+worktree\b", cmd):
            return "exempt"  # worktree management never moves this HEAD

        # WHERE does this command actually act? Decide from the `-C` target, else from
        # the directory the git verb runs in — never from a substring of the text.
        #
        # This used to be `if ".dev/worktrees" in cmd: return "exempt"`, which exempted
        # any git command that merely MENTIONED that string anywhere — a pathspec, a
        # filename, even a commit message. `git checkout main -- .dev/worktrees/x` and
        # `git commit -m "work in .dev/worktrees"` both bypassed the lock while mutating
        # the MAIN checkout — precisely the concurrent-checkout clobber this guard
        # exists to prevent.
        base = Path(payload.get("cwd") or root)
        # Honour a `cd`/`pushd` that happens BEFORE the git verb (e.g.
        # `cd .dev/worktrees/slug && git rebase origin/main`). A `cd` AFTER it cannot
        # move where that verb ran — treating it as if it could would just reopen the
        # hole from the other side (`git checkout main && cd .dev/worktrees/x`).
        for cd in _CD_TARGET.finditer(cmd):
            if cd.start() > danger.start():
                break
            hop = Path(cd.group(1).strip("\"'"))
            base = hop if hop.is_absolute() else base / hop

        m = _GIT_C_TARGET.search(cmd)
        if m:
            target = m.group(1).strip("\"'")
            if _SHELL_VAR.search(target):
                # `git -C $wt ...` / `%WT%` — a shell variable we cannot expand. The
                # assignment is normally in the same block, so look for an ABSOLUTE path
                # under <root>/.dev/worktrees in the command text. Absolute-only on
                # purpose: a bare ".dev/worktrees" mention in a pathspec or a commit
                # message must NOT exempt — that was the original bypass, and those
                # commands carry no `-C`, so they never reach this branch.
                if _mentions_worktree_path(cmd, root):
                    return "exempt"
                return "enforce"  # cannot prove it is a worktree — fail safe
            if not _is_within(target, root, base=base):
                return "exempt"  # explicitly targets another checkout
            if _is_within(target, root / ".dev" / "worktrees", base=base):
                return "exempt"  # explicitly targets an isolated worktree
            return "enforce"  # -C targets THIS checkout — the lock applies

        if not _is_within(str(base), root):
            return "exempt"  # the shell is sitting in a different checkout entirely
        if _is_within(str(base), root / ".dev" / "worktrees"):
            return "exempt"  # running inside an isolated worktree — the sanctioned path
        return "enforce"

    return "exempt"


def read_lock(root: Path) -> dict | None:
    try:
        return json.loads(lock_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def lock_decision(
    lock: dict | None, session_id: str, now: datetime | None = None
) -> tuple[str, dict | None]:
    """Pure decision: ``("claim"|"refresh"|"steal"|"block", lock)``."""
    if not lock or not lock.get("session_id"):
        return "claim", lock
    if lock.get("session_id") == session_id:
        return "refresh", lock
    now = now or datetime.now(timezone.utc)
    try:
        updated = datetime.fromisoformat(str(lock.get("updated_at", "")))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        age_minutes = (now - updated).total_seconds() / 60
    except ValueError:
        return "claim", lock  # corrupt timestamp -> treat as dead
    if age_minutes > TTL_MINUTES:
        return "steal", lock
    return "block", lock


def _current_branch(root: Path) -> str:
    """Best-effort branch name without spawning git."""
    try:
        head = (root / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: refs/heads/"):
            return head[len("ref: refs/heads/"):]
        return head[:12]  # detached
    except OSError:
        return "?"


def write_lock(root: Path, session_id: str, previous: dict | None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    claimed = (
        previous.get("claimed_at", now)
        if previous and previous.get("session_id") == session_id
        else now
    )
    lock = {
        "session_id": session_id,
        "tool": "claude-code",
        "branch": _current_branch(root),
        "claimed_at": claimed,
        "updated_at": now,
        "note": "main-checkout agent lock - inspect with: navig repo lock",
    }
    path = lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".lock.tmp")
    tmp.write_text(json.dumps(lock, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def block_message(lock: dict, root: Path) -> str:
    # ASCII only: the Windows hook pipe garbles non-ASCII characters.
    session = str(lock.get("session_id", "?"))[:8]
    branch = lock.get("branch", "?")
    return (
        f"BLOCKED by the main-checkout agent lock: another live Claude Code session "
        f"({session}..., branch {branch}) is working in this folder right now. "
        f"One folder has ONE HEAD - editing here can destroy that session's uncommitted work.\n"
        f"Do ONE of:\n"
        f"  1. Work in an isolated worktree (sanctioned parallel path):\n"
        f"     git worktree add .dev/worktrees/<slug> -b <type>/<slug>\n"
        f"     ...then edit ONLY under .dev/worktrees/<slug>/ (always allowed).\n"
        f"  2. Wait for the other session to finish (lock auto-expires after "
        f"{TTL_MINUTES} min without activity).\n"
        f"  3. If that git command targets ANOTHER repo, re-run it as: git -C <that-path> ...\n"
        f"  4. Only if you are CERTAIN the other session is gone: navig repo lock release --force\n"
        f"Lock file: {lock_path(root)}"
    )


def sibling_message(root: Path) -> str:
    # ASCII only: the Windows hook pipe garbles non-ASCII characters.
    return (
        "BLOCKED: creating a worktree OUTSIDE this repo (a sibling folder) is forbidden "
        "by the house rules - sibling checkouts scatter work and get orphaned.\n"
        "Create it INSIDE the repo instead (gitignored, scanner-exempt):\n"
        "  git worktree add .dev/worktrees/<slug> -b <type>/<slug>\n"
        f"Repo root: {root}"
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0  # fail-open

    try:
        root = repo_root()
        session_id = str(payload.get("session_id", "")) or "unknown-session"
        event = payload.get("hook_event_name", "")

        if event == "SessionEnd":
            lock = read_lock(root)
            if lock and lock.get("session_id") == session_id:
                try:
                    lock_path(root).unlink()
                except OSError:
                    pass
            return 0

        verdict = classify_tool(payload, root)
        if verdict == "exempt":
            return 0
        if verdict == "block-sibling":
            print(sibling_message(root), file=sys.stderr)
            return 2

        action, lock = lock_decision(read_lock(root), session_id)
        if action == "block":
            print(block_message(lock or {}, root), file=sys.stderr)
            return 2
        write_lock(root, session_id, lock)
        return 0
    except Exception:  # noqa: BLE001 — a broken hook must never block work
        return 0


if __name__ == "__main__":
    sys.exit(main())
