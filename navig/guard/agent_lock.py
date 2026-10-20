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
* Unpushed commits are protected for EVERY session (independent of the lock): a
  ``git reset --hard|--keep|--merge``, ``checkout -B``, ``switch -C`` or
  ``branch -f`` that would leave commits held by no other ref is blocked, with the
  one-line way to keep them (``unpushed_commits_dropped``).
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

# A shell command is dangerous when it runs a git subcommand that mutates the
# working tree / index / HEAD of this checkout. The SUBCOMMAND is parsed the way git
# reads it -- the first verb-shaped token after `git` and its global options -- never
# a word anywhere after `git`: the word-scan read messages and search patterns as
# verbs (`git branch --list "*reset*"` blocked a read-only sweep, 2026-10-17; `git
# log --grep=reset` would block every log search), and it needed carve-outs for
# `merge-base`/`merge-tree` (a hyphen is a word boundary) and `stash@{n}` that token
# parsing gets for free. `git commit -m "reset the board"` still counts: `commit` IS
# the verb.
_DANGEROUS_VERBS = frozenset({
    "checkout", "switch", "rebase", "reset", "merge", "pull", "cherry-pick",
    "revert", "clean", "restore", "commit", "add", "rm", "mv", "am", "apply", "stash",
})
#: Global options whose NEXT token is their value (`git -C "my dir" reset`).
_GIT_VALUE_FLAGS = frozenset({"-C", "-c", "--exec-path", "--namespace", "--config-env"})
_GIT_INVOCATION = re.compile(r"(?<![\w.-])git(?:\.exe)?\s+")
#: What a subcommand looks like: lowercase, no dots/quotes/slashes -- a path
#: fragment or a quoted message never reads as the verb.
_VERB_TOKEN = re.compile(r"[a-z][a-z0-9_-]*\Z")
#: Shell operators: a command's arguments end at one.
_SEGMENT = re.compile(r"[^;&|\n]+")
#: The read-only stash subcommands (the old carve-out, kept).
_STASH_READONLY = ("list", "show")


def _read_token(text: str, pos: int, limit: int) -> tuple[str, int, int] | None:
    """(token, start, end) at/after `pos`, never past `limit`; a quoted token runs
    through its closing quote (or to `limit` when unterminated)."""
    n = min(len(text), limit)
    while pos < n and text[pos].isspace():
        pos += 1
    if pos >= n:
        return None
    if text[pos] in "\"'":
        quote = text[pos]
        close = text.find(quote, pos + 1, n)
        end = n if close < 0 else close + 1
        return text[pos:end], pos, end
    end = pos
    while end < n and not text[end].isspace():
        end += 1
    return text[pos:end], pos, end


def _dangerous_git_at(cmd: str) -> int | None:
    """Position of the `git` token where a DANGEROUS subcommand runs, else None.

    Scanned per shell segment (a command's arguments end at `&&`, `;`, `|`), so
    nothing past a shell operator is read as this git's verb -- `git log && reset`
    exempts like the word-scan did -- while a quoted message or a grep pattern
    before/at the verb can never be the verb anyway: the FIRST verb-shaped
    non-option token is. Fail-open like the rest of this hook: an invocation with
    no verb-shaped token mutates nothing.
    """
    for seg in _SEGMENT.finditer(cmd):
        seg_start, seg_end = seg.start(), seg.end()
        for inv in _GIT_INVOCATION.finditer(seg.group()):
            offset = seg_start + inv.start()
            pos = seg_start + inv.end()
            while (tok := _read_token(cmd, pos, seg_end)) is not None:
                token, _start, nxt = tok
                if token[0] in "\"'":
                    pos = nxt  # an argument, never the verb
                    continue
                if token in _GIT_VALUE_FLAGS:
                    value = _read_token(cmd, nxt, seg_end)
                    pos = value[2] if value else nxt
                    continue
                if token.startswith("-"):
                    pos = nxt
                    continue
                if not _VERB_TOKEN.match(token):
                    pos = nxt  # a path fragment, junk, a glued `dir"` tail
                    continue
                if token in _DANGEROUS_VERBS:
                    if token == "stash":
                        sub = _read_token(cmd, nxt, seg_end)
                        if sub and sub[0] in _STASH_READONLY:
                            break  # `git stash list` / `show`: read-only
                    return offset
                break  # a safe subcommand: this invocation is judged
    return None


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
    """Convert a git-bash / MSYS drive path (``/c/foo``) to a native Windows path
    (``C:\\foo``). No-op on POSIX and for already-native paths.

    On Windows, git-bash renders ``C:\\`` as ``/c/`` — but ``Path("/c/foo")`` is NOT
    absolute there (no drive), so ``git -C /c/…/worktrees/x`` was mis-resolved and a
    legitimate worktree command got blocked. Only a single-letter ``/c/`` prefix converts,
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
    git-bash/MSYS form (``C:\\…`` ↔ ``/c/…``), since agents drive git from bash too.
    """
    try:
        wt_abs = (root / ".dev" / "worktrees").resolve()
    except OSError:  # pragma: no cover
        return False
    # normcase folds separators (/ -> \) and case on Windows, so /c/… and C:\… collapse
    # onto the same haystack; only the NEEDLE differs between the two path forms.
    hay = os.path.normcase(cmd.replace("/", os.sep))
    if (os.path.normcase(str(wt_abs)) + os.sep) in hay:  # native drive-colon form: C:\…
        return True
    # git-bash/MSYS form on Windows: C:\projects\… is written /c/projects/… — which
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


# ── unpushed commits a branch move would drop ───────────────────────────────
#
# `git reset --hard <ref>` moves the branch tip, and every commit above <ref> that no
# other ref holds becomes reachable from the reflog ONLY -- which expires, which no
# other session reads, and which `git log` never shows. On 2026-10-13 a session in the
# Schema checkout aborted a rebase, then ran `git reset` to origin/main and dropped
# seven commits another session had made and not yet pushed. They were recovered by
# hand from the reflog a day later. The lock never saw it: the session that reset WAS
# the lock holder. So, like the pathspec check, this runs for every session.

# The verbs that move an EXISTING branch off its commits AND throw the content away:
# reset --hard/--keep/--merge (a --soft or mixed reset leaves the content in the tree --
# the normal squash flow -- so it is not judged), checkout -B, switch -C, branch -f.
# Parsed in git's own grammar -- `git [-C dir] [-c k=v] <subcommand>`, the shape the
# branch-moved check below uses -- never as a word anywhere in the line: a path naming a
# `reset-guard` worktree held the word "reset" and hid the real `reset --hard` after it,
# and `switch -C <branch>` is not git's `-C <dir>`.
_GIT_PREFIX = r"\bgit\b(?:\s+-C\s+(?:\"[^\"]*\"|'[^']*'|\S+)|\s+-c\s+\S+)*\s+"
_GIT_GLOBAL_C = re.compile(r"\bgit\b(?:\s+-c\s+\S+)*\s+-C\s+(\"[^\"]*\"|'[^']*'|\S+)")
_RESET = re.compile(_GIT_PREFIX + r"reset\b([^\n|&;]*)")
_RESET_DESTRUCTIVE = re.compile(r"(?:^|\s)--(?:hard|keep|merge)\b")
_FORCE_BRANCH = re.compile(
    _GIT_PREFIX
    + r"(?:checkout\s+-B|switch\s+(?:-C|--force-create)|branch\s+(?:-f|--force))"
    + r"\s+([^\s\"';&|]+)(?:\s+([^\s\"';&|-][^\s\"';&|]*))?"
)


def _git(repo: Path, *args: str) -> str | None:
    """`git -C repo <args>` stdout, or None on any failure (the guard fails open)."""
    import subprocess

    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, timeout=10, check=True,
            text=True, encoding="utf-8", errors="replace",
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _command_repo(payload: dict, cmd: str, root: Path) -> Path | None:
    """The repo a git command runs in: `-C`, else a leading `cd`, else the shell cwd.

    An unexpandable target ($var, %VAR%) returns None -- we cannot inspect a repo we
    cannot name, and a wrong guess would judge the wrong tree.
    """
    repo = Path(payload.get("cwd") or root)
    for pattern in (_CD_TARGET, _GIT_GLOBAL_C):
        m = pattern.search(cmd)
        if m:
            target = _from_msys(m.group(1).strip("\"'"))
            if _SHELL_VAR.search(target):
                return None
            t = Path(target)
            repo = t if t.is_absolute() else repo / t
    return repo


def _orphaned(repo: Path, tip: str, target: str, keep_out: str | None) -> list[str]:
    """Commits on `tip` that would be held by nothing once it moves to `target`.

    Held = reachable from `target`, a remote-tracking ref, a tag, or any OTHER local
    branch (`keep_out` is the branch being moved -- it cannot hold itself). A commit
    already upstream as a patch-equivalent (`git cherry` "-") is not lost either:
    that is a reset after the work was rebased onto the remote, which is fine.
    """
    tip_sha = _git(repo, "rev-parse", "--verify", "--quiet", f"{tip}^{{commit}}")
    target_sha = _git(repo, "rev-parse", "--verify", "--quiet", f"{target}^{{commit}}")
    if not tip_sha or not target_sha:
        return []
    heads = _git(repo, "for-each-ref", "--format=%(refname)", "refs/heads") or ""
    others = [f"^{h}" for h in heads.splitlines() if h and h != f"refs/heads/{keep_out}"]
    listed = _git(
        repo, "rev-list", "--max-count=50", tip_sha, f"^{target_sha}",
        "--not", "--remotes", "--tags", "--not", *others,
    )
    if not listed:
        return []
    shas = listed.split()
    upstream = _git(repo, "cherry", target_sha, tip_sha) or ""
    landed = {line[2:] for line in upstream.splitlines() if line.startswith("- ")}
    lost = [sha for sha in shas if sha not in landed]
    lines = []
    for sha in lost[:10]:
        subject = _git(repo, "log", "-1", "--format=%h %s", sha) or sha[:10]
        lines.append(subject[:100])
    if len(lost) > 10:
        lines.append(f"... and {len(lost) - 10} more")
    return lines


def unpushed_commits_dropped(payload: dict, root: Path) -> tuple[list[str], str]:
    """The unpushed commits this branch move would drop, and the branch it moves.

    Empty when the command moves nothing, keeps the content, or every commit it moves
    off is held by another ref.
    """
    if payload.get("tool_name", "") not in SHELL_TOOLS:
        return [], ""
    cmd = str((payload.get("tool_input") or {}).get("command", ""))
    if "git" not in cmd:
        return [], ""

    m = _RESET.search(cmd)
    if m and _RESET_DESTRUCTIVE.search(m.group(1)):
        repo = _command_repo(payload, cmd, root)
        if repo is None:
            return [], ""
        args = m.group(1).split(" -- ")[0].split()
        refs = [a.strip("\"'") for a in args if not a.startswith("-")]
        target = refs[0] if refs else "HEAD"
        branch = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
        return _orphaned(repo, "HEAD", target, branch), branch or "HEAD"

    f = _FORCE_BRANCH.search(cmd)
    if f:
        repo = _command_repo(payload, cmd, root)
        if repo is None:
            return [], ""
        branch, target = f.group(1), f.group(2) or "HEAD"
        if not _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"):
            return [], ""  # a NEW branch: nothing is moved off anything
        return _orphaned(repo, f"refs/heads/{branch}", target, branch), branch
    return [], ""


def dropped_message(lost: list[str], branch: str) -> str:
    return (
        f"BLOCKED: this would move '{branch}' off commits that NO other ref holds -- no\n"
        "remote, no tag, no other branch. They would survive only in the reflog, which\n"
        "expires and which no other session reads:\n"
        + "".join(f"  {line}\n" for line in lost)
        + "(2026-10-13: seven unpushed commits were dropped exactly this way, by the\n"
        "session that held the lock.)\n"
        "Do ONE of:\n"
        f"  1. Keep them first:  git branch keep/{branch.replace('/', '-')} {branch}\n"
        "     -- then the same command is allowed (the commits are held).\n"
        "  2. Push or land them, then move the branch.\n"
        "  3. If they are truly abandoned, say so to the owner and delete them on purpose\n"
        "     with `git branch -D` on a kept branch -- never as a side effect of a reset.\n"
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
        danger_at = _dangerous_git_at(cmd)
        wt_add = _WORKTREE_ADD.search(cmd)
        if wt_add:
            target = worktree_add_target(wt_add.group(1))
            # Resolve relative targets against BOTH the session cwd and the repo
            # root; block only when clearly outside (a malformed/foreign-style
            # cwd must not turn an in-repo path into a false sibling).
            bases = [Path(payload.get("cwd") or root), root]
            if target and not any(_is_within(target, root, base=b) for b in bases):
                return "block-sibling"  # hard rule: no worktrees outside the repo
            if danger_at is None:
                return "exempt"  # a pure in-repo worktree add never moves this HEAD
            # ...but the command may MUTATE elsewhere too (`... add ... && git
            # reset`): an in-repo worktree add used to exempt the WHOLE line, and
            # the reset behind it moved this checkout's HEAD (caught by this
            # change's own test, 2026-10-17). Fall through and judge by WHERE the
            # dangerous verb runs — the cd/-C logic below.
        if danger_at is None:
            return "exempt"
        # `git worktree add` needs no carve-out here: `worktree` is the parsed
        # subcommand (it was `add` in the old word-scan), and the sibling check
        # above already judged its target. A cmd that merely MENTIONS a worktree
        # no longer exempts a real reset later in it (that hole is gone with it).

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
            if cd.start() > danger_at:
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
            # `_from_msys` as for `-C`: git-bash writes C:\x as /c/x, and Path("/c/x") has a
            # root but no drive, so `base / hop` kept only the drive -> C:\e\x -> "outside
            # the repo" -> EXEMPT. That let `cd /c/<repo> && git checkout -b ...` through a
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


def _head_branch(root: Path) -> str | None:
    """The branch HEAD is on, or None when detached/unreadable (never a guess)."""
    try:
        head = (root / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return head[len("ref: refs/heads/"):] if head.startswith("ref: refs/heads/") else None


def _ref_exists(root: Path, ref: str) -> bool:
    """Is ``refs/...`` present, loose or packed? Read from disk — the hook never spawns git."""
    git_dir = root / ".git"
    if (git_dir / ref).is_file():
        return True
    try:
        packed = (git_dir / "packed-refs").read_text(encoding="utf-8")
    except OSError:
        return False
    return any(line.endswith(" " + ref) for line in packed.splitlines())


# ── the commit-branch guard ──────────────────────────────────────────────────
# The lock stops another HOOKED session from moving this checkout's HEAD. Nothing stops a
# person at a terminal, or any tool without the hook, from doing it between this session's
# `checkout -b X` and its `commit` — and the commit then lands on THEIR branch with no
# error (it happened twice on 2026-09-26, once through a hole in the lock itself). So the
# lock records the branch this session last switched the main checkout to, and a commit is
# refused when HEAD has moved to a different named branch since.
#
# It fails OPEN wherever it is unsure: only an unambiguous switch is recorded; anything
# that moves HEAD in a way the hook cannot follow CLEARS the record (no check until the
# next unambiguous switch); a detached HEAD is never checked (rebase/bisect flows).

_GIT_VERB = re.compile(
    r"\bgit\b(?:\s+-C\s+(?:\"[^\"]*\"|'[^']*'|\S+)|\s+-c\s+\S+)*\s+"
    r"(?P<verb>checkout|switch|commit|branch|bisect)\b(?P<args>[^\n|&;]*)"
)
_GH_MOVES_HEAD = re.compile(r"\bgh\s+pr\s+(?:checkout|merge)\b")
_SWITCH_VALUE_FLAGS = {"-b", "-B", "-c", "-C", "--create", "--force-create", "--orphan"}


def _branch_switch(verb: str, args: str, root: Path) -> tuple[str, str | None] | None:
    """What a checkout/switch/branch/bisect does to HEAD: ``("set", name)``,
    ``("clear", None)`` (moves it somewhere we cannot name), or None (does not move it).
    """
    tokens = [t.strip("\"'") for t in args.split()]
    if verb == "bisect":
        return ("clear", None)
    if verb == "branch":
        # A rename moves HEAD's branch name; creating/listing/deleting does not.
        return ("clear", None) if any(t in ("-m", "-M", "--move") for t in tokens) else None
    if verb == "checkout" and "--" in tokens:
        return None  # `git checkout <rev> -- <paths>` restores files; HEAD stays put
    for i, tok in enumerate(tokens):
        if tok in _SWITCH_VALUE_FLAGS:
            return ("set", tokens[i + 1]) if i + 1 < len(tokens) else ("clear", None)
    if "--detach" in tokens or "-" in tokens:
        return ("clear", None)
    names = [t for t in tokens if t and not t.startswith("-")]
    if not names:
        return None  # bare `git checkout` / `git switch` changes nothing
    name = names[0]
    if _ref_exists(root, f"refs/heads/{name}"):
        return ("set", name)
    if _ref_exists(root, f"refs/remotes/origin/{name}"):
        return ("set", name)  # git creates the local branch from origin/<name>
    if verb == "checkout" and (root / name).exists():
        return None  # `git checkout <file>` — restores a path, HEAD stays put
    return ("clear", None)  # a sha, a tag, a remote ref: detached or unknown


def commit_branch_check(
    cmd: str, root: Path, expected: str | None
) -> tuple[str | None, str | None]:
    """Walk *cmd* in order. Returns ``(block_message_or_None, new_expected)``.

    A switch earlier in the same command moves HEAD before the commit runs, so it counts
    as where HEAD will be — `git checkout -b x && git commit` is not a move.
    """
    head = _head_branch(root)
    events: list[tuple[int, str, str]] = [
        (m.start(), m.group("verb"), m.group("args"))
        for m in _GIT_VERB.finditer(cmd)
        # `git -C <path> ...` acts on another checkout (the classifier already decided
        # whether the command as a whole touches this one) — not this HEAD's business.
        if "-C" not in cmd[m.start() : m.start("verb")].split()
    ]
    events += [(m.start(), "gh", "") for m in _GH_MOVES_HEAD.finditer(cmd)]
    for _pos, verb, args in sorted(events):
        if verb == "gh":
            expected, head = None, None
            continue
        if verb == "commit":
            if expected and head and head != expected:
                return _moved_message(expected, head), expected
            continue
        change = _branch_switch(verb, args, root)
        if change is None:
            continue
        kind, name = change
        expected, head = (name, name) if kind == "set" else (None, None)
    return None, expected


def _moved_message(expected: str, head: str) -> str:
    # ASCII only: the Windows hook pipe garbles non-ASCII characters.
    return (
        f"BLOCKED: this commit would land on '{head}', but this session last switched the "
        f"main checkout to '{expected}'. Something else moved HEAD since (another terminal "
        f"or a tool without this hook) - committing now puts your work on their branch.\n"
        f"  Check: git reflog -5   (who moved it, and when)\n"
        f"  Then:  git checkout {expected}   (uncommitted changes come along if they do not "
        f"conflict) and commit again.\n"
        f"  If '{head}' really is where this belongs: git checkout {head}  (records it), "
        f"then commit."
    )


def _current_branch(root: Path) -> str:
    """Best-effort branch name without spawning git."""
    try:
        head = (root / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: refs/heads/"):
            return head[len("ref: refs/heads/"):]
        return head[:12]  # detached
    except OSError:
        return "?"


_KEEP = object()  # write_lock: carry the previous expected_branch forward


def write_lock(
    root: Path, session_id: str, previous: dict | None, *, expected: object = _KEEP
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    mine = bool(previous and previous.get("session_id") == session_id)
    claimed = previous.get("claimed_at", now) if mine else now
    # `branch` is where HEAD IS (re-read on every write, so it follows any move silently);
    # `expected_branch` is where THIS session last PUT it. Only the second can tell that
    # somebody else moved it. A new holder starts with none: another session's intent is
    # not ours to enforce.
    if expected is _KEEP:
        expected = previous.get("expected_branch") if mine else None
    lock = {
        "session_id": session_id,
        "tool": "claude-code",
        "branch": _current_branch(root),
        "expected_branch": expected,
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
        lost, moved_branch = unpushed_commits_dropped(payload, root)
        if lost:
            print(dropped_message(lost, moved_branch), file=sys.stderr)
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
        # Only a lock this session already held carries an intent of ours to check.
        previous_expected = (lock or {}).get("expected_branch") if action == "refresh" else None
        cmd = str((payload.get("tool_input") or {}).get("command", ""))
        moved, expected = commit_branch_check(cmd, root, previous_expected) if cmd else (
            None,
            previous_expected,
        )
        if moved:
            print(moved, file=sys.stderr)
            return 2
        write_lock(root, session_id, lock, expected=expected)
        return 0
    except Exception:  # noqa: BLE001 — a broken hook must never block work
        return 0


if __name__ == "__main__":
    sys.exit(main())
