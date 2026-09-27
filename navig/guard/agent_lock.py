#!/usr/bin/env python3
"""Claude Code agent-lock hook — one session at a time may mutate the MAIN checkout.

Why: several Claude Code sessions can run against this repo at once. A branch
does NOT isolate files — one folder has one HEAD — so a second session's
``git checkout`` / ``rebase`` can destroy the first session's uncommitted
edits (this happened; see memory ``navig-shared-tree-rebase-hazard``).

What this does (stdlib-only, fail-open — any internal error allows the call):

* PreToolUse (Edit|Write|MultiEdit|NotebookEdit|Bash|PowerShell): the first session to
  edit claims ``.dev/agent.lock``; every later edit refreshes it, and so does
  ANY tool call by the holder (a read, a test run, a patch script) — activity
  is activity. A DIFFERENT live session gets **exit 2** (blocked) with
  instructions to work in a worktree under ``.dev/worktrees/`` instead. Locks
  unrefreshed for ``TTL_MINUTES`` count as dead and are taken over silently.
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
    # `merge(?!-)`: `merge-base` and `merge-tree` are READ-ONLY plumbing, but the
    # hyphen is a word boundary so a bare `merge` matched them. That blocked the very
    # command you reach for to check whether a detached HEAD or a stray branch holds
    # unmerged work — the careful inspection you do BEFORE touching anything — and a
    # guard that blocks the careful path pushes people toward `lock release --force`
    # instead. Same shape as the `stash(?!…)` carve-out below; plain `merge` still matches.
    r"\bgit\b[^\n|&;]*?\b(checkout|switch|rebase|reset|merge(?!-)|pull|cherry-pick"
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
    # PowerShell's own verb is `Set-Location` (`cd`/`chdir` are its aliases); without it a
    # `Set-Location <main>; git checkout main` from a worktree was judged by the worktree.
    r"(?:^|[;&|]\s*)(?:cd|chdir|pushd|Push-Location|Set-Location)\s+"
    r"([^\s;&|\"']+|\"[^\"]+\"|'[^']+')",
    re.IGNORECASE,
)
_WORKTREE_ADD = re.compile(r"\bgit\b[^\n|&;]*?\bworktree\s+add\s+([^\n|&;]+)")

# A `git commit` that names paths after a standalone `--`. Everything after it is the
# pathspec; `--amend`/`--no-verify` do not match because the `--` must stand alone.
_PATHSPEC_COMMIT = re.compile(r"\bgit\b[^\n|&;]*?\bcommit\b[^\n|&;]*?\s--\s+([^\n|&;]+)")

# `git worktree add` flags that consume the following token as their value.
_WT_VALUE_FLAGS = {"-b", "-B", "--reason", "--orphan"}


def repo_root() -> Path:
    """The MAIN working tree's root — even when this hook runs from a linked worktree.

    A linked worktree's ``.git`` is a FILE (``gitdir: <main>/.git/worktrees/<name>``), and
    ``.exists()`` is true for a file, so the old walk stopped at the worktree and treated
    it as the repo. Measured 2026-09-19 from a session opened in ``.dev/worktrees/x``: the
    exact command ``navig repo new y`` runs — a worktree add with the ABSOLUTE main-tree
    path — was BLOCKED as "OUTSIDE this repo (a sibling folder)", because outside worktree
    x it is; and the block message recommended the relative form, which would have nested
    a worktree inside the worktree. ``navig repo`` has anchored on the main tree since
    #1443; the hook that gates it must resolve the same root.

    Stdlib only, no subprocess: this hook runs on EVERY tool call, and the ``.git`` file
    already says where the main tree is.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        dot = parent / ".git"
        if dot.is_dir():
            return parent  # the main working tree
        if dot.is_file():
            try:
                text = dot.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                return parent
            if text.startswith("gitdir:"):
                gitdir = Path(text[len("gitdir:"):].strip())
                # <main>/.git/worktrees/<name>  ->  <main>
                if gitdir.parent.name == "worktrees" and gitdir.parent.parent.name == ".git":
                    return gitdir.parent.parent.parent
            return parent  # some other gitdir shape (a submodule): this tree is the root
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


def _pathspec_covers(spec: str, path: str) -> bool:
    """Does one pathspec token name ``path`` -- exactly, as a directory prefix, or as a glob."""
    spec = spec.strip("\"'").replace("\\", "/").rstrip("/")
    path = path.replace("\\", "/")
    if spec in ("", "."):
        return True
    if path == spec or path.startswith(spec + "/"):
        return True
    from fnmatch import fnmatch

    return fnmatch(path, spec)


def pathspec_commit_discards(payload: dict, root: Path) -> list[str]:
    """Staged deletions a `git commit -- <paths>` would silently DISCARD.

    A pathspec commit builds the commit from the WORKING TREE for the named paths, not
    from the index. Measured in a scratch repo:

        staged D, file on disk, unmodified  ->  deletion committed
        staged D, file on disk, ignored     ->  deletion committed
        staged D, file on disk, MODIFIED    ->  the modification is committed and the
                                                deletion discarded (still tracked)

    That third row is how #1423 merged half-done: `git rm --cached` on two generated
    files, a pathspec commit naming both, one deletion landed and the other quietly
    turned into "still tracked, now also gitignored" -- dirty in every checkout and
    refused by `git add`. The house style here IS the pathspec commit, so every session
    is one `rm --cached` away from it.

    Conservative on purpose: any staged deletion that the pathspec covers and whose file
    is still on disk is reported, whether or not it is modified -- the unmodified case
    happened to work in the scratch repo but did not in #1423, and the cost of the false
    block is one re-run without a pathspec. A file removed from disk (a real `git rm`) is
    not reported: the working tree agrees with the index there.
    """
    if payload.get("tool_name", "") not in SHELL_TOOLS:
        return []
    cmd = str((payload.get("tool_input") or {}).get("command", ""))
    m = _PATHSPEC_COMMIT.search(cmd)
    if not m:
        return []
    specs = [t for t in m.group(1).split() if not t.startswith("-")]
    if not specs:
        return []

    # Which repo? Honour `-C`, else the shell's cwd. Unexpandable `-C $var`: fail open --
    # we cannot inspect a repo we cannot name, and a wrong guess would block the wrong tree.
    repo = Path(payload.get("cwd") or root)
    c = _GIT_C_TARGET.search(cmd)
    if c:
        target = c.group(1).strip("\"'")
        if _SHELL_VAR.search(target):
            return []
        t = Path(target)
        repo = t if t.is_absolute() else repo / t

    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "diff", "--cached", "--diff-filter=D", "--name-only", "-z"],
            capture_output=True, timeout=10, check=True,
        ).stdout
        top = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
            capture_output=True, timeout=10, check=True, text=True, encoding="utf-8", errors="replace",
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return []
    staged = [p for p in out.decode("utf-8", "replace").split("\0") if p]
    return sorted(
        p for p in staged
        if any(_pathspec_covers(s, p) for s in specs) and (Path(top) / p).exists()
    )


def pathspec_message(discarded: list[str]) -> str:
    return (
        "BLOCKED: this `git commit -- <paths>` would silently DISCARD a staged deletion.\n"
        "A pathspec commit is built from the WORKING TREE for the named paths, not the\n"
        "index -- and these are `git rm --cached` (staged as deleted, still on disk):\n"
        + "".join(f"  {p}\n" for p in discarded)
        + "Measured: if the file is modified on disk, the MODIFICATION is committed instead\n"
        "and the file stays tracked (#1423 merged half-done exactly this way).\n"
        "Do ONE of:\n"
        "  1. Commit without a pathspec, after `git diff --cached --name-only` shows only\n"
        "     what you mean to commit.\n"
        "  2. If the file should go from disk too, `git rm <path>` (not --cached) first.\n"
        "Then verify with `git show --stat HEAD`.\n"
    )


def _unresolved_location(cmd: str, root: Path) -> str:
    """Verdict when a mutating git verb runs somewhere we cannot resolve — a shell variable
    in a `-C` target or a `cd`, or `cd -`.

    The assignment is normally in the same block, so an ABSOLUTE path under
    <root>/.dev/worktrees in the command text is taken as the sanctioned worktree path.
    Absolute-only on purpose: a bare ".dev/worktrees" mention in a pathspec or a commit
    message must NOT exempt — that was the original substring bypass. Anything else cannot
    be proven to be a worktree, so the lock applies (fail safe).
    """
    return "exempt" if _mentions_worktree_path(cmd, root) else "enforce"


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
        # None = "we cannot tell where the shell is" (see the loop below).
        base: Path | None = Path(payload.get("cwd") or root)
        # Honour a `cd`/`pushd` that happens BEFORE the git verb (e.g.
        # `cd .dev/worktrees/slug && git rebase origin/main`). A `cd` AFTER it cannot
        # move where that verb ran — treating it as if it could would just reopen the
        # hole from the other side (`git checkout main && cd .dev/worktrees/x`).
        for cd in _CD_TARGET.finditer(cmd):
            if cd.start() > danger.start():
                break
            raw = cd.group(1).strip("\"'")
            if raw.startswith("~"):
                raw = os.path.expanduser(raw)  # resolvable — not a variable
            if raw == "-" or _SHELL_VAR.search(raw):
                # `cd "$ROOT"`, `cd $env:ROOT`, `cd -`: where the shell went is unknown.
                # Judging the verb by the directory it LEFT made `cd "$ROOT" && git checkout
                # -b ...` from a worktree exempt — the same hole as the MSYS `cd` below.
                base = None
                continue
            # `_from_msys` as for `-C`: git-bash writes E:\x as /e/x, and Path("/e/x") has a
            # root but no drive, so `base / hop` kept only the drive -> E:\e\x -> "outside
            # the repo" -> EXEMPT. That let `cd /e/<repo> && git checkout -b ...` through a
            # live lock (2026-09-26) and moved HEAD under the lock holder's next commit.
            hop = Path(_from_msys(raw))
            if hop.is_absolute():
                base = hop  # an absolute hop makes the location known again
            elif base is not None:
                base = base / hop

        m = _GIT_C_TARGET.search(cmd)
        if m:
            target = m.group(1).strip("\"'")
            if _SHELL_VAR.search(target):
                # `git -C $wt ...` / `%WT%` — a shell variable we cannot expand.
                return _unresolved_location(cmd, root)
            if base is None and not Path(_from_msys(target)).is_absolute():
                return _unresolved_location(cmd, root)  # relative to an unknown dir
            if not _is_within(target, root, base=base):
                return "exempt"  # explicitly targets another checkout
            if _is_within(target, root / ".dev" / "worktrees", base=base):
                return "exempt"  # explicitly targets an isolated worktree
            return "enforce"  # -C targets THIS checkout — the lock applies

        if base is None:
            return _unresolved_location(cmd, root)
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


def heartbeat(root: Path, session_id: str) -> None:
    """Refresh the lock if THIS session already holds it — never claim.

    An exempt call (read-only Bash, a python patch script, a jest run) is still a
    live session at work. Before this, a session that edited through Bash for an
    hour never touched ``updated_at``, the claim went stale, a second session
    took it over mid-round, and the first session's own ``git commit`` was then
    blocked by a lock it had held all along.
    """
    lock = read_lock(root)
    if lock and lock.get("session_id") == session_id:
        write_lock(root, session_id, lock)


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

        # Checked BEFORE the lock and regardless of it: the footgun is identical in a
        # worktree, and it is a correctness rule, not a coordination one.
        discarded = pathspec_commit_discards(payload, root)
        if discarded:
            print(pathspec_message(discarded), file=sys.stderr)
            return 2

        verdict = classify_tool(payload, root)
        if verdict == "exempt":
            heartbeat(root, session_id)
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
