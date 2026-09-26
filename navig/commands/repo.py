"""``navig repo`` — multi-agent repository coordination (conflict radar + agent lock).

Several agents (Claude Code sessions, humans) may work on one repo in
parallel. Two hazards follow:

1. **Cross-worktree merge conflicts** surface only at merge time — after
   both agents already built on top of colliding edits.
2. **A shared main checkout** can eat uncommitted work when one agent moves
   HEAD (checkout / rebase / reset) under another agent's feet.

``navig repo`` makes both visible early — read-only, nothing is modified:

* ``navig repo conflicts`` — simulates a three-way merge between every pair
  of worktrees **in memory** (``git merge-tree --write-tree``, git >= 2.38),
  *including uncommitted changes* (captured via ``git stash create``, which
  writes objects only and never touches the working tree).
* ``navig repo stale`` — everything an agent may have left behind: extra
  worktrees, branches not merged into the default branch, stashes, sibling
  checkouts outside the repo, and the current agent-lock holder.
* ``navig repo sweep`` — the one mutating verb here besides ``prune``/``remove``:
  deletes local branches whose work is PROVABLY already on the remote default
  (ancestor tip, identical tree, or a MERGED PR that landed exactly that tip).
  Dry-run by default; everything unproven is kept and listed for a human.
* ``navig repo lock`` — inspect / release the main-checkout agent lock
  written by ``.claude/hooks/agent_lock.py`` (Claude Code PreToolUse hook).

The lock file is ``.dev/agent.lock`` at the repo root — JSON with
``session_id`` / ``claimed_at`` / ``updated_at`` / ``branch``. A lock older
than ``LOCK_TTL_MINUTES`` (no refresh) counts as stale and may be taken
over. **Keep the schema + TTL in sync with .claude/hooks/agent_lock.py.**
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

import typer

repo_app = typer.Typer(
    help="Multi-agent repo coordination: cross-worktree conflict radar, "
    "stale-work report, main-checkout agent lock",
    no_args_is_help=True,
)

LOCK_TTL_MINUTES = 60  # keep in sync with .claude/hooks/agent_lock.py
LOCK_RELPATH = Path(".dev") / "agent.lock"
WORKTREES_RELDIR = Path(".dev") / "worktrees"  # sanctioned worktrees home
_GIT_TIMEOUT = 15  # seconds; local plumbing (queries) should be instant
# `git worktree remove` is not a query — it physically deletes the checkout. A
# JS worktree carries node_modules + build output (tens of thousands of files),
# which blows far past the query budget on Windows, where an AV scanner walks
# every one. Sized for a real project tree, not for plumbing.
_GIT_DELETE_TIMEOUT = 300
# `git worktree add` is not a query either — it CHECKS OUT the whole tree (13.6k files
# here). Measured 7.6-8.2 s on this machine with other sessions' gates running, and it
# went past 15 s under heavier load: the query budget killed it mid-checkout, `navig repo
# new` printed "timed out after 15s", and the worktree then finished populating anyway
# (the checkout is git's CHILD process, which a bare timeout kill never reached) — with
# git's own "initializing" lock left behind, so `navig repo remove` refused it hours
# later. Sized like the delete: for a real tree.
_GIT_CHECKOUT_TIMEOUT = 300
# The executable `_git` runs. A seam for the timeout test: a batch/shell shim that
# spawns a child holding the inherited pipes is the exact shape of `worktree add`'s
# checkout child, and the only way to exercise it without a 13k-file checkout.
_GIT_EXE = "git"


# ── git plumbing ─────────────────────────────────────────────────────────────


def _git(
    args: list[str], cwd: Path | str, timeout: int | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a git command; never raises — failure is a non-zero result (callers check).

    A timeout is reported as a normal failed result (exit 124, the conventional
    timeout code) rather than an exception: these commands are cleanup plumbing,
    and a raised TimeoutExpired aborts mid-flight — which is precisely how
    ``repo remove`` used to leave a worktree unregistered but its directory on
    disk, the orphan it exists to prevent. Degrading lets the caller fall through
    to its own recovery (``_rmtree_force``).

    A ``cwd`` that is not a real directory is degraded the same way (exit 125).
    ``subprocess`` cannot start a child in a missing/invalid cwd — on Windows
    ``CreateProcess`` raises ``NotADirectoryError`` (WinError 267), and it does so
    BEFORE the command runs, so the timeout guard never sees it. This bites the
    ``repo`` commands two ways: probing a worktree whose folder is already gone
    (``stale`` reading a dangling worktree, ``prune`` re-checking an orphan dir
    that vanished), and a mangled ``--repo`` (a shell that stripped the
    backslashes off ``E:\\projects\\...`` → ``E:projects...``, an invalid
    drive-relative path). Both used to crash the whole CLI with WinError 267.

    We degrade rather than silently retry in the process cwd: for ``repo_root``
    that would resolve a bad ``--repo`` to whatever repo navig happens to sit in
    and operate on the WRONG one. A failed result instead surfaces as a clean
    "not inside a git repository" error, or a graceful skip at each caller.

    A timeout kills the process TREE, not just ``git``. Several git verbs do their
    real work in a child (``worktree add`` checks out via a child ``git``; a ``!``
    alias runs a shell), and ``subprocess.run(timeout=)`` kills only the pid it
    holds — so a timed-out ``worktree add`` reported failure while its orphaned
    child kept writing the checkout to completion, and left git's "initializing"
    lock behind for ``repo remove`` to trip over. Same class as the shell-spawn
    orphan (``navig.core.aio_subprocess``); same helper.
    """
    if cwd is None or not os.path.isdir(cwd):
        return subprocess.CompletedProcess(
            args=["git", *args],
            returncode=125,
            stdout="",
            stderr=f"cwd is not a directory: {cwd!r}",
        )
    limit = timeout or _GIT_TIMEOUT
    try:
        proc = subprocess.Popen(
            [_GIT_EXE, *args],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",  # git porcelain is UTF-8; locale decoding mojibakes it
            errors="replace",
        )
    except OSError as exc:
        # Defence-in-depth for the microscopic isdir→CreateProcess race and for a
        # path is_dir() accepts but CreateProcess rejects (a broken junction, a
        # freshly-unlinked worktree): a cwd problem must never crash the CLI.
        return subprocess.CompletedProcess(
            args=["git", *args],
            returncode=125,
            stdout="",
            stderr=f"git {' '.join(args)} failed to start: {exc}",
        )
    try:
        out, err = proc.communicate(timeout=limit)
    except subprocess.TimeoutExpired:
        from navig.core.aio_subprocess import terminate_process_tree_sync  # noqa: PLC0415

        terminate_process_tree_sync(proc, grace=5.0)
        return subprocess.CompletedProcess(
            args=["git", *args],
            returncode=124,
            stdout="",
            stderr=f"git {' '.join(args)} timed out after {limit}s (process tree killed)",
        )
    except OSError as exc:
        # A pipe that broke mid-read. "Never raises" still holds; don't leave the child.
        from navig.core.aio_subprocess import terminate_process_tree_sync  # noqa: PLC0415

        terminate_process_tree_sync(proc, grace=5.0)
        return subprocess.CompletedProcess(
            args=["git", *args],
            returncode=125,
            stdout="",
            stderr=f"git {' '.join(args)} failed while running: {exc}",
        )
    return subprocess.CompletedProcess(
        args=["git", *args], returncode=proc.returncode, stdout=out, stderr=err
    )


def repo_root(cwd: Path | None = None) -> Path | None:
    """The MAIN working tree's root, even when ``cwd`` is inside a linked worktree.

    ``rev-parse --show-toplevel`` answers the worktree's OWN root from inside one, and
    every ``navig repo`` verb keys on the main tree's ``.dev/`` — its worktrees, its
    ``agent.lock``, its orphan pile. Anchored on the worktree, ``navig repo new b`` run
    from ``.dev/worktrees/a`` created ``.dev/worktrees/a/.dev/worktrees/b``: a worktree
    nested inside a worktree, invisible to ``navig repo stale`` at the main root, outside
    the lock's reach, and left behind when ``a`` is removed. ``--git-common-dir`` is the
    main repository's ``.git`` from anywhere; its parent is the main tree.

    The fallback keeps the old answer for shapes where the common dir is not a plain
    ``.git`` directory (a submodule's ``.git`` file points into the superproject's
    ``modules/``, and ``--path-format`` needs git 2.31+). The orphan classifier calls
    ``--show-toplevel`` directly on purpose — it WANTS the dead-vs-live distinction.
    """
    start = cwd or Path.cwd()
    res = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], start)
    if res.returncode == 0:
        common = Path(res.stdout.strip())
        if common.name == ".git" and common.is_dir():
            return common.parent
    res = _git(["rev-parse", "--show-toplevel"], start)
    if res.returncode != 0:
        return None
    return Path(res.stdout.strip())


# Env hints consulted only when the process cwd is NOT inside a git repo. A
# launched ``navig.exe`` does not always inherit the shell's directory: on
# Windows, PowerShell's Set-Location / Push-Location moves the *shell* location
# but leaves ``[Environment]::CurrentDirectory`` (what a child process inherits)
# untouched, and navig may be spawned from a daemon/workspace dir instead. An
# agent driving these commands from a subshell can point them at the repo via
# either var — Claude Code exports CLAUDE_PROJECT_DIR into tool/hook envs.
_REPO_ENV_HINTS = ("NAVIG_REPO", "CLAUDE_PROJECT_DIR")


def invocation_cwd() -> Path:
    """The directory the operator actually ran ``navig`` from.

    ``main.py`` chdir's into the active space before any command runs, stashing
    the real invocation directory in ``NAVIG_INVOCATION_CWD`` first. Falls back
    to the process cwd when the variable is absent (a direct ``repo_app`` call
    in tests, or an embedding caller).

    Kept as a re-export so existing importers (``commands/sync.py``) keep working;
    the canonical implementation is :func:`navig.platform.paths.invocation_cwd`.
    """
    from navig.platform.paths import invocation_cwd as _canonical  # noqa: PLC0415

    return _canonical()


def resolve_repo_root(repo: str | None = None) -> Path | None:
    """Resolve the target repo root, robust to an unreliable process cwd.

    Precedence:

    1. an explicit ``--repo`` path (the operator said so — if it is not a repo
       that is an error, never a reason to guess elsewhere). A RELATIVE one is
       resolved against the invocation dir, for the same reason as step 2;
    2. the directory the CLI was INVOKED from, when it is inside a git repo;
    3. the process cwd, when it is inside a git repo;
    4. the ``NAVIG_REPO`` / ``CLAUDE_PROJECT_DIR`` env hints, in that order.

    Returns None only when none of these lands inside a git repo.

    ⚠ Step 2 is not redundant with step 3, and leaving it out was a live bug.
    ``main.py`` chdir's to the ACTIVE SPACE during startup, so by the time a
    command runs, "the process cwd" is the space — not where the operator is
    standing. With a space that is not a repo (the default
    ``~/.navig-os/workspaces/my-workspace``), every ``navig repo`` command
    answered "Not inside a git repository" while the operator stood in one, and
    named the space as what it tried. With a space that IS a repo it is worse:
    the command silently operates on the SPACE's repo — precisely the
    "operator standing in repo B" case this precedence exists to prevent.
    ``main.py`` records the pre-chdir directory in ``NAVIG_INVOCATION_CWD``.
    """
    if repo:
        given = Path(repo)
        # A relative --repo means "relative to where I am typing" — the
        # invocation dir, not the space. Resolving it against the process cwd
        # was the same trap one line down, left behind when that was fixed:
        # `navig repo lock --repo .` still answered "Not inside a git
        # repository. tried ." to an operator standing in the repo.
        return repo_root(given if given.is_absolute() else invocation_cwd() / given)
    if (root := repo_root(invocation_cwd())) is not None:
        return root
    root = repo_root()  # process cwd (the active space, once main.py has chdir'd)
    if root is not None:
        return root
    for name in _REPO_ENV_HINTS:
        hint = os.environ.get(name)
        if hint and (found := repo_root(Path(hint))) is not None:
            return found
    return None


def _is_within(child: Path, parent: Path) -> bool:
    """Case-insensitive containment check (Windows-safe)."""
    c = os.path.normcase(str(child.resolve()))
    p = os.path.normcase(str(parent.resolve()))
    return c == p or c.startswith(p + os.sep)


def _display_path(path: Path | str, root: Path) -> str:
    """Root-relative path for tables (keeps columns narrow); absolute otherwise."""
    p = Path(path)
    try:
        rel = p.resolve().relative_to(root.resolve())
        return str(rel)
    except ValueError:
        return str(p)


def list_worktrees(root: Path) -> list[dict]:
    """All worktrees of the repo, parsed from ``git worktree list --porcelain``.

    Each entry: ``path`` (Path), ``head`` (sha), ``branch`` (short name or
    None when detached), ``is_primary`` (the main checkout), ``is_sibling``
    (lives outside the primary checkout — forbidden by house rules), ``locked``
    (the lock reason — ``""`` for a lock with no reason — or None when unlocked).
    """
    res = _git(["worktree", "list", "--porcelain"], root)
    if res.returncode != 0:
        return []
    worktrees: list[dict] = []
    current: dict = {}
    for line in res.stdout.splitlines():
        if line.startswith("worktree "):
            if current:
                worktrees.append(current)
            current = {"path": Path(line[len("worktree ") :].strip())}
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD ") :].strip()
        elif line.startswith("branch "):
            ref = line[len("branch ") :].strip()
            current["branch"] = ref.removeprefix("refs/heads/")
        elif line.strip() == "detached":
            current["branch"] = None
        elif line == "locked" or line.startswith("locked "):
            current["locked"] = line[len("locked") :].strip()
    if current:
        worktrees.append(current)
    for wt in worktrees:
        wt.setdefault("branch", None)
        wt.setdefault("locked", None)
        wt["is_primary"] = _is_within(wt["path"], root) and _is_within(root, wt["path"])
        wt["is_sibling"] = not wt["is_primary"] and not _is_within(wt["path"], root)
        wt["label"] = (
            wt["path"].name if not wt["is_primary"] else f"{wt['path'].name} (main checkout)"
        )
    return worktrees


def _relative_age(mtime: float, now: float) -> str:
    """Coarse human age (``3d ago``) from a mtime — no git call needed."""
    secs = max(0.0, now - mtime)
    for label, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{int(secs // size)}{label} ago"
    return "just now"


def orphan_worktree_dirs(root: Path, registered: list[dict] | None = None) -> list[dict]:
    """Physical dirs under ``.dev/worktrees/`` that git no longer tracks.

    On Windows ``git worktree remove`` frequently cannot delete the physical
    folder (a live process — antivirus, a file watcher, the navig daemon —
    holds a handle), so git unregisters the worktree but the directory is left
    behind. These are invisible to ``git worktree list`` — and therefore to the
    rest of ``stale`` — yet each can be a full multi-thousand-file checkout, so
    the pile silently grows across parallel sessions. ``navig repo prune``
    removes them.

    One entry per orphan: ``path``, ``name``, ``entries`` (count of top-level
    items — a full checkout has ~15, an empty shell 0; this is the honest disk
    signal, since ``git worktree remove`` often strips the ``.git`` marker but
    leaves a heavy ``core/``/``apps/`` tree behind), ``checkout`` (still has a
    ``.git`` marker), ``age`` (relative, from the folder mtime — git plumbing is
    unreliable on a dangling worktree). Registered worktree dirs are excluded.
    Sorted by name.
    """
    base = root / WORKTREES_RELDIR
    if not base.is_dir():
        return []
    if registered is None:
        registered = list_worktrees(root)
    reg = {os.path.normcase(str(Path(wt["path"]).resolve())) for wt in registered}
    now = datetime.now().timestamp()
    orphans: list[dict] = []
    try:
        children = sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name)
    except OSError:
        return []
    for child in children:
        try:
            if os.path.normcase(str(child.resolve())) in reg:
                continue
            mtime = child.stat().st_mtime
            with os.scandir(child) as it:  # one syscall; NOT a recursive walk
                entries = sum(1 for _ in it)
        except OSError:
            continue
        orphans.append(
            {
                "path": str(child),
                "name": child.name,
                "entries": entries,
                "checkout": (child / ".git").exists(),
                "age": _relative_age(mtime, now),
            }
        )
    return orphans


def _orphan_kind(od: dict) -> str:
    """Rich-styled honesty label for an orphan dir, from its top-level entry count."""
    n = od.get("entries", 0)
    if n == 0:
        return "[dim]empty[/dim]"
    if n > 3:
        return f"[yellow]checkout (~{n} top-level)[/yellow]"
    return "[dim]leftover[/dim]"


def dirty_ref(wt_path: Path) -> tuple[str | None, bool]:
    """Capture a worktree's uncommitted state without touching anything.

    Returns ``(commit_ish_or_None, is_dirty)``. ``git stash create`` writes
    a dangling commit of the tracked dirty state (staged + unstaged) and
    leaves the working tree untouched; untracked files are not captured.
    """
    status = _git(["status", "--porcelain"], wt_path)
    is_dirty = bool(status.stdout.strip()) if status.returncode == 0 else False
    if not is_dirty:
        return None, False
    created = _git(["stash", "create"], wt_path)
    sha = created.stdout.strip() if created.returncode == 0 else ""
    return (sha or None), True


def pair_conflicts(root: Path, ref_a: str, ref_b: str) -> dict:
    """Simulate merging ``ref_a`` + ``ref_b`` in memory; report conflicts.

    Returns ``{"status": "clean"|"conflict"|"no-base"|"error", "files": [...]}``.
    """
    base = _git(["merge-base", ref_a, ref_b], root)
    if base.returncode != 0:
        return {"status": "no-base", "files": []}
    res = _git(
        ["merge-tree", "--write-tree", "--name-only", "--no-messages", ref_a, ref_b],
        root,
    )
    if res.returncode == 0:
        return {"status": "clean", "files": []}
    if res.returncode == 1:
        lines = [ln.strip() for ln in res.stdout.splitlines()[1:] if ln.strip()]
        # de-dupe, preserve order
        files = list(dict.fromkeys(lines))
        return {"status": "conflict", "files": files}
    return {"status": "error", "files": [], "detail": res.stderr.strip()[:300]}


def collect_conflicts(root: Path, include_dirty: bool = True) -> dict:
    """Pairwise merge simulation across all worktrees (optionally + dirty state)."""
    worktrees = list_worktrees(root)
    sides: list[dict] = []
    for wt in worktrees:
        ref = wt["branch"] or wt.get("head")
        if not ref:
            continue
        entry = {**wt, "ref": ref, "dirty": False}
        if include_dirty:
            sha, is_dirty = dirty_ref(wt["path"])
            entry["dirty"] = is_dirty
            if sha:
                entry["ref"] = sha  # branch tip + uncommitted tracked changes
        sides.append(entry)
    pairs = []
    for a, b in combinations(sides, 2):
        result = pair_conflicts(root, a["ref"], b["ref"])
        pairs.append(
            {
                "a": a["label"],
                "b": b["label"],
                "branch_a": (a["branch"] or "detached") + (" +dirty" if a["dirty"] else ""),
                "branch_b": (b["branch"] or "detached") + (" +dirty" if b["dirty"] else ""),
                **result,
            }
        )
    return {"worktrees": _wt_json(worktrees), "pairs": pairs}


def default_branch(root: Path) -> str:
    """The repo's default branch (origin/HEAD), falling back to ``main``."""
    res = _git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], root)
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip().split("/", 1)[-1]
    return "main"


def merge_base_ref(root: Path, default: str) -> str:
    """The ref "merged into main" is judged against — the REMOTE default when one exists.

    ``collect_stale`` judged against the LOCAL default branch, and in a shared
    checkout that is precisely the ref that goes stale: measured twice in one
    week, local ``main`` sat 5 and then 107 PRs behind ``origin/main`` while
    agents merged through GitHub. Against a stale base every recently merged
    branch reads as "not merged" — harmless for a report, and fatal for the
    sweep built on it, which would keep debris forever. Merges land on the
    remote ref, so it is the only honest base. Falls back to the local branch
    when there is no remote at all (a fresh ``git init``).
    """
    remote = f"origin/{default}"
    res = _git(["rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}"], root)
    return remote if res.returncode == 0 else default


#: A worktree this many commits behind the default branch has missed days of merges —
#: on this repo main moves ~20 commits a day — and should rebase before any more work
#: lands on it. Above it the stale report and the session briefing say so.
WORKTREE_BEHIND_WARN = 50


#: A worktree whose branch was created this long ago and has NEVER received a commit is
#: unused, whatever the behind-count says. Behind-count alone misses this during a quiet
#: week: main may move only 30 commits in 14 days, and a worktree someone created and
#: never touched reads as "fresh" the whole time.
WORKTREE_UNUSED_DAYS = 14


def branch_never_committed(root: Path, name: str) -> tuple[bool, float | None]:
    """``(True, created_at)`` when the branch's reflog holds exactly its creation entry —
    no commit, rebase, reset or merge has ever moved it — else ``(False, None)``.

    The reflog is the only record of "was this branch ever worked on"; refs alone cannot
    tell a never-used branch from one whose commits were fast-forwarded into main. An
    EMPTY reflog (expired, or the branch was made by a path that wrote none) is unknown,
    and unknown must not read as "never committed" — measured: an eight-week-old finished
    worktree had zero reflog entries.
    """
    res = _git(
        ["reflog", "show", "--date=unix", "--format=%gd|%gs|%cd", f"refs/heads/{name}"], root
    )
    if res.returncode != 0:
        return False, None
    entries = [ln for ln in (res.stdout or "").splitlines() if ln.strip()]
    if len(entries) != 1:
        return False, None
    _sel, subject, date = (entries[0].split("|", 2) + ["", ""])[:3]
    if not subject.startswith("branch: Created"):
        return False, None
    try:
        return True, float(date.strip())
    except ValueError:
        return True, None


def worktree_last_moved(path: Path) -> float | None:
    """Unix time of the newest entry in the worktree's OWN HEAD reflog, or ``None``.

    Every checkout, pull, reset and commit in that worktree writes one, so it is the
    closest thing git records to "someone was working here". ``None`` (no reflog, git
    failed) is unknown — a caller must treat it as possibly-in-use, never as idle.
    """
    res = _git(["reflog", "-1", "--date=unix", "--format=%gd"], Path(path))
    sel = (res.stdout or "").strip() if res.returncode == 0 else ""
    if not (sel.endswith("}") and "@{" in sel):
        return None
    try:
        return float(sel[sel.rindex("@{") + 2 : -1])
    except ValueError:
        return None


def _idle_verdict(path: Path, what: str) -> str | None:
    """``None`` (removable) once the worktree's HEAD has not moved for ``LOCK_TTL_MINUTES``,
    else why it is kept. Used where the behind-count caution does not apply — the worktree
    is provably not fresh — but a live session may still be sitting in it.
    """
    moved = worktree_last_moved(path)
    if moved is None:
        return f"{what}; cannot tell when it was last used — remove by hand if finished"
    idle_min = (time.time() - moved) / 60
    if idle_min < LOCK_TTL_MINUTES:
        return (
            f"{what}, used {idle_min:.0f} min ago — a session may still be in it; "
            f"removable after {LOCK_TTL_MINUTES} idle min"
        )
    return None


def worktree_behind(root: Path, head_sha: str | None, base: str) -> int | None:
    """Commits on *base* that the worktree at *head_sha* has never seen.

    ``rev-list --count merge-base(head, base)..base``. ``None`` when it cannot be
    computed (no sha, no merge base) — the caller must show "?" rather than 0, because
    "0 behind" is a claim and this would be a guess.
    """
    if not head_sha:
        return None
    mb = _git(["merge-base", head_sha, base], root)
    if mb.returncode != 0 or not mb.stdout.strip():
        return None
    cnt = _git(["rev-list", "--count", f"{mb.stdout.strip()}..{base}"], root)
    if cnt.returncode != 0 or not cnt.stdout.strip().isdigit():
        return None
    return int(cnt.stdout.strip())


def collect_stale(root: Path) -> dict:
    """Stale-work report: worktrees, unmerged branches, stashes, lock state."""
    default = default_branch(root)
    base = merge_base_ref(root, default)
    worktrees = list_worktrees(root)
    wt_branches = {wt["branch"] for wt in worktrees if wt["branch"]}
    extra_wts = []
    for wt in worktrees:
        if wt["is_primary"]:
            continue
        _sha, is_dirty = dirty_ref(wt["path"])
        age = _git(["log", "-1", "--format=%cr"], wt["path"])
        never_committed, created_at = (
            branch_never_committed(root, wt["branch"]) if wt.get("branch") else (False, None)
        )
        age_days = (
            round((time.time() - created_at) / 86400, 1)
            if never_committed and created_at else None
        )
        extra_wts.append(
            {
                "path": str(wt["path"]),
                "branch": wt["branch"],
                "dirty": is_dirty,
                "last_commit": age.stdout.strip() if age.returncode == 0 else "?",
                # How many commits on the default branch this worktree's base does not have.
                # "Last commit 5 days ago" says when someone last touched it; this says how
                # much of main it has never seen — including every safety fix merged since.
                # Measured 2026-09-19: two worktrees sat 900 and 1,178 commits behind, still
                # running the pre-#1447/#1459 scripts that killed other sessions' processes.
                # ⚠ Not dirty_ref's sha: that is a `stash create` commit, produced only when
                # the tree is dirty — a clean worktree gets None and would show "?". The
                # porcelain listing already carries the real HEAD.
                "behind": worktree_behind(root, wt.get("head"), base),
                # Whether this worktree's branch has ever received a commit. "Last commit
                # 5 days ago" is the branch's TIP date — for a never-worked branch that is
                # when it was cut from main, which reads as recent activity. The reflog is
                # the only record of "was this ever worked on": a branch with only its
                # creation entry, cut weeks ago, is an unused worktree even in a quiet week
                # when behind-count stays low. Surfaced so `stale` shows it without a sweep.
                "never_committed": never_committed,
                "age_days": age_days,
                # A worktree on the DEFAULT branch blocks the main checkout from ever
                # checking it out — `gh pr merge --delete-branch` inside a worktree leaves
                # one behind. `navig repo sweep --yes` removes it when clean.
                "holds_default": wt["branch"] == default,
                "sibling": wt["is_sibling"],
                "locked": wt.get("locked"),
                # git locks a worktree "initializing" for the length of `worktree add` and
                # clears it on the way out; one still wearing it belongs to an add that
                # DIED (killed at a timeout, a crashed session) - a partial checkout that
                # `git worktree list` shows as a normal worktree. Not a person's lock.
                "dead_add": wt.get("locked") == "initializing",
            }
        )

    branches: list[dict] = []
    res = _git(
        [
            "branch",
            "--no-merged",
            base,
            "--format=%(refname:short)|%(upstream:short)|%(upstream:track)",
        ],
        root,
    )
    if res.returncode == 0:
        for line in res.stdout.splitlines():
            if not line.strip():
                continue
            name, upstream, track = (line.split("|") + ["", ""])[:3]
            ahead = _git(["rev-list", "--count", f"{base}..{name}"], root)
            branches.append(
                {
                    "name": name,
                    "ahead": int(ahead.stdout.strip() or 0) if ahead.returncode == 0 else None,
                    "upstream": upstream or None,
                    "upstream_gone": "gone" in track,
                    "in_worktree": name in wt_branches,
                    # A changelog fragment is the cheapest honest signal that a branch
                    # carries USER-FACING work: every merged change leaves one, so an
                    # unmerged branch holding one is a release note nobody can read yet.
                    # "3 ahead" says nothing about what; "1 fragment" says it matters.
                    "fragments": branch_fragments(root, base, name),
                }
            )

    stashes: list[str] = []
    res = _git(["stash", "list", "--format=%gd · %cr · %gs"], root)
    if res.returncode == 0:
        stashes = [ln for ln in res.stdout.splitlines() if ln.strip()]

    return {
        "default_branch": default,
        "base_ref": base,
        "worktrees": extra_wts,
        "orphan_dirs": orphan_worktree_dirs(root, worktrees),
        "unmerged_branches": branches,
        "stashes": stashes,
        "lock": read_lock(root),
    }


def branch_fragments(root: Path, base: str, branch: str) -> int:
    """Changelog fragments *branch* adds over its merge-base with *base*.

    Three-dot, added-only, scoped to ``core/changelog.d/``: fragments that main already
    holds (or that the branch deleted by assembling them) are not the branch's work. A
    failed diff (no such ref, no git) counts as none — this is a hint, never a gate.
    """
    res = _git(
        ["diff", "--name-only", "--diff-filter=A", f"{base}...{branch}", "--", "core/changelog.d/"],
        root,
    )
    if res.returncode != 0:
        return 0
    return sum(
        1 for ln in res.stdout.splitlines() if ln.strip() and not ln.strip().endswith("/README.md")
    )


def _wt_json(worktrees: list[dict]) -> list[dict]:
    return [
        {
            "path": str(wt["path"]),
            "branch": wt["branch"],
            "primary": wt["is_primary"],
            "sibling": wt["is_sibling"],
        }
        for wt in worktrees
    ]


# ── agent lock ───────────────────────────────────────────────────────────────


def lock_path(root: Path) -> Path:
    return root / LOCK_RELPATH


def read_lock(root: Path) -> dict | None:
    """The parsed lock file, or None when absent/corrupt."""
    try:
        return json.loads(lock_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def current_session_id() -> str | None:
    """This Claude Code session's id, as the lock file records it.

    The agent-lock hook takes ``session_id`` from Claude Code's hook payload;
    the CLI has no payload, but the same id is exported into the tool
    environment. Any other caller — a human shell, CI, cron — has no session and
    gets None, which keeps them on the strict ``--force`` path.
    """
    return os.environ.get("CLAUDE_CODE_SESSION_ID") or None


def lock_is_ours(lock: dict | None) -> bool:
    """True when *lock* was claimed by THIS session.

    Releasing your own lock must not require ``--force``. Keying only on
    freshness meant the commonest release — you claimed it, you finished, the
    protocol asks you to leave nothing held — pushed the operator to ``--force``,
    the one flag whose entire purpose is overriding ANOTHER agent's live claim.
    Making the dangerous flag part of the routine path is how a foreign lock
    eventually gets destroyed, which is the failure the guard exists to prevent.
    """
    if not lock:
        return False
    ours = current_session_id()
    return bool(ours) and str(lock.get("session_id") or "") == ours


def lock_state(lock: dict | None, now: datetime | None = None) -> dict:
    """Classify a lock: ``{"state": "free"|"held"|"stale", "age_minutes": float|None, ...}``."""
    if not lock:
        return {"state": "free", "age_minutes": None}
    now = now or datetime.now(timezone.utc)
    try:
        updated = datetime.fromisoformat(lock.get("updated_at", ""))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        age = (now - updated).total_seconds() / 60
    except ValueError:
        return {"state": "stale", "age_minutes": None, **_lock_meta(lock)}
    state = "stale" if age > LOCK_TTL_MINUTES else "held"
    return {"state": state, "age_minutes": round(age, 1), **_lock_meta(lock)}


def _lock_meta(lock: dict) -> dict:
    return {
        "session": str(lock.get("session_id", "?"))[:8],
        "branch": lock.get("branch"),
    }


# ── CLI commands ─────────────────────────────────────────────────────────────


def _resolution_hint(repo: str | None) -> str:
    """Name the path actually tried — never echo the flag back at the operator.

    "tried . — pass --repo <path>" was the whole message for someone who had
    just passed --repo, and a bare "." tells them nothing about where it went.
    """
    here = invocation_cwd()
    if repo:
        given = Path(repo)
        if given.is_absolute():
            return f"tried {given} — not a git repository (nor inside one)."
        return (
            f"tried {repo!r} → {here / given} (relative to where you ran navig) "
            "— not a git repository. Pass an absolute path, or set NAVIG_REPO / "
            "CLAUDE_PROJECT_DIR."
        )
    return (
        f"tried {here}, then {Path.cwd()} — neither is inside a git repository. "
        "Pass --repo <path>, or set NAVIG_REPO / CLAUDE_PROJECT_DIR."
    )


def _require_root(repo: str | None = None) -> Path:
    root = resolve_repo_root(repo)
    if root is None:
        from navig import console_helper as ch

        ch.error("Not inside a git repository.", _resolution_hint(repo))
        raise typer.Exit(1)
    return root


@repo_app.command("conflicts")
def conflicts_cmd(
    repo: str = typer.Option(None, "--repo", help="Target repo (default: current directory)"),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
    include_dirty: bool = typer.Option(
        True,
        "--dirty/--no-dirty",
        help="Include each worktree's uncommitted (tracked) changes in the simulation",
    ),
) -> None:
    """Simulate merges between every pair of worktrees; list conflicting files.

    Read-only (in-memory ``git merge-tree``). Exit code 2 when any pair
    conflicts — same convention as clash — so scripts and hooks can gate on it.
    """
    root = _require_root(repo)
    data = collect_conflicts(root, include_dirty=include_dirty)
    any_conflict = any(p["status"] == "conflict" for p in data["pairs"])

    if json_out:
        typer.echo(json.dumps(data, indent=2))
        raise typer.Exit(2 if any_conflict else 0)

    from navig import console_helper as ch

    if len(data["worktrees"]) < 2:
        ch.info(
            "Single worktree — nothing to cross-check.",
            "Parallel agents should work in worktrees: "
            "git worktree add .dev/worktrees/<slug> -b <type>/<slug>",
        )
        raise typer.Exit(0)

    table = ch.create_table(
        "Cross-worktree merge simulation",
        [
            {"name": "Pair", "style": "cyan"},
            {"name": "Branches", "style": "white"},
            {"name": "Status", "style": "white"},
            {"name": "Conflicting files", "style": "red"},
        ],
    )
    for p in data["pairs"]:
        if p["status"] == "clean":
            status = "[green]● clean[/green]"
        elif p["status"] == "conflict":
            status = f"[red]✗ {len(p['files'])} conflict(s)[/red]"
        elif p["status"] == "no-base":
            status = "[yellow]no common history[/yellow]"
        else:
            status = "[yellow]error[/yellow]"
        table.add_row(
            f"{p['a']} ↔ {p['b']}",
            f"{p['branch_a']} ↔ {p['branch_b']}",
            status,
            ", ".join(p["files"][:6]) + ("…" if len(p["files"]) > 6 else ""),
        )
    ch.print_table(table)
    if any_conflict:
        ch.warning(
            "Conflicts brewing — coordinate before merge time.",
            "Rebase one side early, or split the overlapping files between agents.",
        )
        raise typer.Exit(2)
    ch.dim("All worktree pairs merge cleanly · re-check anytime with: navig repo conflicts")


def sync_plan(root: Path) -> dict:
    """What `repo sync` would do, and why it may refuse. Pure inspection — no writes.

    Refusals, in order: the lock is held by a LIVE session (its uncommitted work is on
    that branch), the tree is dirty, or the branch is not the default one and carries
    commits that are not on the remote (switching away would strand them).
    """
    default = default_branch(root)
    lock = read_lock(root)
    st = lock_state(lock)
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], root).stdout.strip() or "?"
    dirty_res = _git(["status", "--porcelain", "--untracked-files=no"], root)
    dirty = bool(dirty_res.stdout.strip())

    plan: dict = {
        "root": str(root),
        "branch": branch,
        "default_branch": default,
        "dirty": dirty,
        "lock": st,
        "blocked": None,
        "actions": [],
    }
    if st["state"] == "held" and not lock_is_ours(lock):
        plan["blocked"] = (
            f"the main checkout is locked by session {st.get('session', '?')} on "
            f"{st.get('branch') or '?'} — that agent may be mid-edit"
        )
        return plan
    if dirty:
        plan["blocked"] = f"{branch} has uncommitted changes — commit or stash them first"
        return plan
    if branch != default:
        unpushed = _git(["log", "--oneline", f"origin/{branch}..{branch}"], root)
        if unpushed.returncode != 0 or unpushed.stdout.strip():
            n = len([x for x in unpushed.stdout.splitlines() if x.strip()])
            plan["blocked"] = (
                f"{branch} has {n or 'unpushed'} commit(s) not on origin — push or land it first"
            )
            return plan
        plan["actions"].append(f"checkout {default}")
    plan["actions"].append("pull --ff-only")
    return plan


@repo_app.command("sync")
def sync_cmd(
    repo: str = typer.Option(None, "--repo", help="Target repo (default: current directory)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Say what would happen; change nothing."),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Return the MAIN checkout to the default branch and fast-forward it.

    The installed navig runs from the main checkout, so a checkout parked on someone's
    feature branch means every `navig` command is older than `main` — the quiet failure
    this exists to end. It refuses (rather than forcing) whenever switching could cost
    work: a live lock, a dirty tree, or unpushed commits.
    """
    from navig import console_helper as ch

    root = _require_root(repo)
    plan = sync_plan(root)

    if plan["blocked"]:
        if json_out:
            typer.echo(json.dumps({**plan, "synced": False}, indent=2))
            raise typer.Exit(1)
        ch.error(f"Not syncing: {plan['blocked']}")
        raise typer.Exit(1)

    if dry_run:
        if json_out:
            typer.echo(json.dumps({**plan, "synced": False, "dry_run": True}, indent=2))
            return
        ch.info(f"Would run in {root}: " + " · ".join(plan["actions"]))
        return

    default = plan["default_branch"]
    if plan["branch"] != default:
        res = _git(["checkout", default], root, timeout=120)
        if res.returncode != 0:
            ch.error(f"checkout {default} failed", res.stderr.strip()[:300])
            raise typer.Exit(1)
    res = _git(["pull", "--ff-only"], root, timeout=300)
    if res.returncode != 0:
        ch.error("pull --ff-only failed", res.stderr.strip()[:300])
        raise typer.Exit(1)
    head = _git(["log", "--oneline", "-1"], root).stdout.strip()

    if json_out:
        typer.echo(json.dumps({**plan, "synced": True, "head": head}, indent=2))
        return
    ch.success(f"{root.name} on {default} — {head}")


@repo_app.command("stale")
def stale_cmd(
    repo: str = typer.Option(None, "--repo", help="Target repo (default: current directory)"),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Report leftover agent work: worktrees, unmerged branches, stashes, lock."""
    root = _require_root(repo)
    data = collect_stale(root)

    if json_out:
        typer.echo(json.dumps(data, indent=2))
        return

    from navig import console_helper as ch

    findings = 0

    if data["worktrees"]:
        table = ch.create_table(
            "Extra worktrees",
            [
                {"name": "Path", "style": "cyan"},
                {"name": "Branch", "style": "white"},
                {"name": "State", "style": "white"},
                {"name": "Last commit", "style": "dim"},
                {"name": f"Behind {data['default_branch']}", "style": "white", "justify": "right"},
            ],
        )
        for wt in data["worktrees"]:
            findings += 1
            state = "[yellow]dirty[/yellow]" if wt["dirty"] else "[green]clean[/green]"
            behind = wt.get("behind")
            if behind is None:
                behind_cell = "[dim]?[/dim]"
            elif behind >= WORKTREE_BEHIND_WARN:
                behind_cell = f"[red]{behind} ⚠ rebase[/red]"
            else:
                behind_cell = f"[dim]{behind}[/dim]" if behind == 0 else str(behind)
            if wt["sibling"]:
                state += " [red]⚠ sibling outside repo[/red]"
            if wt.get("holds_default"):
                state += (
                    f" [red]⚠ holds {data['default_branch']} — the main checkout cannot "
                    f"check it out[/red]"
                )
            if wt.get("dead_add"):
                state += " [red]⚠ dead add (locked: initializing)[/red]"
            elif wt.get("locked") is not None:
                state += f" [dim]locked: {wt['locked'] or 'no reason given'}[/dim]"
            # A never-worked branch's "last commit" is just when it was cut from main, which
            # reads as recent activity. Say so where that value lives, so an unused worktree
            # is visible without running `sweep`.
            if wt.get("never_committed"):
                age = wt.get("age_days")
                activity = (
                    f"[yellow]never worked ({age:.0f}d)[/yellow]" if age is not None
                    else "[yellow]never worked[/yellow]"
                )
            else:
                activity = wt["last_commit"]
            table.add_row(
                _display_path(wt["path"], root),
                wt["branch"] or "detached",
                state,
                activity,
                behind_cell,
            )
        ch.print_table(table)

    if data.get("orphan_dirs"):
        table = ch.create_table(
            "Orphaned worktree dirs (.dev/worktrees — untracked by git)",
            [
                {"name": "Path", "style": "cyan"},
                {"name": "Kind", "style": "white"},
                {"name": "Age", "style": "dim"},
            ],
        )
        for od in data["orphan_dirs"]:
            findings += 1
            table.add_row(_display_path(od["path"], root), _orphan_kind(od), od.get("age") or "?")
        ch.print_table(table)

    if data["unmerged_branches"]:
        table = ch.create_table(
            f"Branches not merged into {data['default_branch']}",
            [
                {"name": "Branch", "style": "cyan"},
                {"name": "Ahead", "style": "white", "justify": "right"},
                {"name": "Upstream", "style": "dim"},
                {"name": "Note", "style": "white"},
            ],
        )
        for br in data["unmerged_branches"]:
            findings += 1
            note = []
            if br["upstream_gone"]:
                note.append("[yellow]remote gone (merged+deleted?)[/yellow]")
            if br["in_worktree"]:
                note.append("[dim]checked out in a worktree[/dim]")
            if br.get("fragments"):
                n = br["fragments"]
                note.append(
                    f"[cyan]{n} changelog fragment{'s' if n != 1 else ''} — user-facing work waiting[/cyan]"
                )
            table.add_row(
                br["name"],
                str(br["ahead"] if br["ahead"] is not None else "?"),
                br["upstream"] or "—",
                " ".join(note) or "—",
            )
        ch.print_table(table)

    if data["stashes"]:
        findings += len(data["stashes"])
        ch.info("Stashes", "\n".join(data["stashes"]))

    lock = lock_state(data["lock"])
    if lock["state"] == "free":
        ch.dim("agent lock: free")
    else:
        colour = "yellow" if lock["state"] == "stale" else "green"
        ch.info(
            f"agent lock: [{colour}]{lock['state']}[/{colour}] "
            f"by session {lock.get('session', '?')} "
            f"(age {lock.get('age_minutes', '?')}m, branch {lock.get('branch') or '?'})"
        )

    if findings == 0:
        ch.success("Nothing stale — no leftover worktrees, branches, or stashes.")
    else:
        nudge = (
            f"{findings} item(s) to review · merge or delete finished branches, "
            "drop obsolete stashes, remove finished worktrees (git worktree remove)"
        )
        if data["unmerged_branches"]:
            nudge += " · delete the provably-merged ones: navig repo sweep"
        if data.get("orphan_dirs"):
            nudge += " · clear orphaned dirs: navig repo prune"
        ch.dim(nudge)
        # Its own line, not a clause in the nudge: the nudge wraps at the console width
        # and a command split across two lines is a command the operator pastes broken.
        for wt in data["worktrees"]:
            if wt.get("dead_add"):
                name = Path(wt["path"]).name
                ch.warning(
                    f"A worktree add died mid-checkout at {name} (partial tree, git lock left).",
                    f"Clear it: navig repo remove {name} --force",
                )


#: The retry budget of ``_rmtree_force``. Six tries with capped exponential
#: backoff (0.5 · 1 · 2 · 4 · 5 s) wait ~12.5 s in the worst case. The old budget
#: was three tries / 1.5 s, and it lost, repeatedly: ``navig repo remove`` on a
#: worktree checked out minutes earlier reported a leftover, and ``navig repo
#: prune --yes`` run ~10 s later deleted the same dir cleanly (Schema,
#: 2026-09-20, twice). A scanner's handle outlives 1.5 s; it does not outlive
#: 10 s. A successful delete still returns at once — the budget is only spent
#: while something actually holds the tree.
_RMTREE_ATTEMPTS = 6
_RMTREE_BASE_DELAY = 0.5
_RMTREE_MAX_DELAY = 5.0


def _rmtree_backoff(
    attempts: int = _RMTREE_ATTEMPTS,
    base_delay: float = _RMTREE_BASE_DELAY,
    max_delay: float = _RMTREE_MAX_DELAY,
) -> list[float]:
    """The sleeps between the tries of ``_rmtree_force`` — one fewer than the tries."""
    return [min(base_delay * (2**i), max_delay) for i in range(max(0, attempts - 1))]


def _rmtree_force(
    path: Path,
    attempts: int = _RMTREE_ATTEMPTS,
    base_delay: float = _RMTREE_BASE_DELAY,
    max_delay: float = _RMTREE_MAX_DELAY,
) -> str | None:
    """Delete a directory tree, clearing read-only bits and retrying transient locks.

    On Windows an antivirus / search indexer briefly holds a handle on a
    freshly checked-out worktree, so the first delete fails with "in use" even
    though nothing owns the dir for long (verified: dirs undeletable right after
    checkout delete cleanly minutes later, daemon still running). We clear
    read-only bits (git objects are RO) and retry with capped exponential
    backoff — see ``_RMTREE_ATTEMPTS`` for why the budget is ~12 s, not 1.5 s.
    Returns None on success, or the last error string — the folder stays, to
    be re-tried later once the transient holder is gone.
    """
    import shutil
    import stat
    import time

    def _clear_ro(func, p, *_):  # onexc/onerror hook: chmod +w then retry the op
        os.chmod(p, stat.S_IWRITE)
        func(p)

    sleeps = _rmtree_backoff(attempts, base_delay, max_delay)
    last: str | None = None
    for i in range(max(1, attempts)):
        try:
            try:
                shutil.rmtree(path, onexc=lambda f, p, e: _clear_ro(f, p))  # py >= 3.12
            except TypeError:
                shutil.rmtree(path, onerror=lambda f, p, e: _clear_ro(f, p))  # py < 3.12
            return None
        except OSError as exc:
            last = getattr(exc, "strerror", None) or str(exc)
            if i < len(sleeps):
                time.sleep(sleeps[i])
    return last


def _orphan_live_worktree(orphan: Path) -> dict | None:
    """Whether ``orphan`` is still a LIVE git worktree/repo of its own.

    The common orphan is a DEAD leftover — ``git worktree remove`` stripped its
    gitdir, so ``rev-parse --show-toplevel`` resolves up to the PARENT repo, not
    the dir itself: safe to delete, because any committed work lives on branch
    refs, never in the directory. A dir that IS its own live worktree/repo may
    hold uncommitted or unmerged work git would normally protect — prune refuses
    it without ``--force``.

    Returns ``{"dirty": bool, "branch": str|None}`` when live, else None.
    """
    top = _git(["rev-parse", "--show-toplevel"], orphan)
    if top.returncode != 0:
        return None  # not a git dir at all — a plain leftover folder
    try:
        if Path(top.stdout.strip()).resolve() != orphan.resolve():
            return None  # resolved to the PARENT repo — a dead leftover
    except OSError:
        return None
    st = _git(["status", "--porcelain"], orphan)
    # symbolic-ref reports the branch even on an unborn branch (no commits yet);
    # it fails on a detached HEAD, which correctly yields branch=None.
    br = _git(["symbolic-ref", "--short", "HEAD"], orphan)
    return {
        "dirty": bool(st.stdout.strip()) if st.returncode == 0 else False,
        "branch": br.stdout.strip() if br.returncode == 0 else None,
    }


@repo_app.command("prune")
def prune_cmd(
    repo: str = typer.Option(None, "--repo", help="Target repo (default: current directory)"),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Actually delete (default: dry run — list only)"
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Delete even a live worktree/repo of its own (may hold uncommitted work)",
    ),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Remove orphaned .dev/worktrees dirs that git no longer tracks.

    ``git worktree remove`` often can't delete a worktree's folder on Windows (a
    live handle blocks it), so git unregisters it and the directory lingers —
    invisible to ``git worktree list``, silently piling up across sessions. This
    runs ``git worktree prune`` (metadata) then deletes the leftover physical
    dirs. Dry-run by default; pass ``--yes`` to delete.

    SAFE by default: **committed work is never at risk** (prune deletes
    directories, not branch refs), and a dir that is still a live worktree/repo
    of its own — one that could hold uncommitted work git would protect — is
    SKIPPED unless ``--force``. Never touches a registered worktree, and never
    deletes anything outside ``.dev/worktrees/``.
    """
    root = _require_root(repo)
    _git(["worktree", "prune"], root)  # drop dangling metadata first (safe, idempotent)
    base = (root / WORKTREES_RELDIR).resolve()
    orphans = orphan_worktree_dirs(root)
    for od in orphans:
        od["live"] = _orphan_live_worktree(Path(od["path"]))  # dict when live, else None

    removed: list[str] = []
    skipped: list[dict] = []
    if yes:
        for od in orphans:
            p = Path(od["path"])
            # HARD SAFETY: only ever delete a direct child of .dev/worktrees/.
            if p.resolve().parent != base:
                skipped.append({"name": od["name"], "reason": "refused (outside .dev/worktrees)"})
                continue
            # SAFETY: never delete a live worktree/repo (uncommitted work) without --force.
            if od["live"] is not None and not force:
                detail = od["live"].get("branch") or "detached"
                if od["live"].get("dirty"):
                    detail += ", uncommitted changes"
                skipped.append(
                    {"name": od["name"], "reason": f"live worktree ({detail}) — use --force"}
                )
                continue
            err = _rmtree_force(p)
            (
                removed.append(od["name"])
                if err is None
                else skipped.append({"name": od["name"], "reason": err})
            )

    if json_out:
        typer.echo(
            json.dumps(
                {"orphans": orphans, "removed": removed, "skipped": skipped, "dry_run": not yes},
                indent=2,
            )
        )
        return

    from navig import console_helper as ch

    if not orphans:
        ch.success("No orphaned worktree dirs — .dev/worktrees is clean.")
        return

    live_count = sum(1 for od in orphans if od["live"] is not None)

    if not yes:
        table = ch.create_table(
            f"Orphaned worktree dirs — {len(orphans)} (dry run)",
            [
                {"name": "Path", "style": "cyan"},
                {"name": "Kind", "style": "white"},
                {"name": "Safe?", "style": "white"},
                {"name": "Age", "style": "dim"},
            ],
        )
        for od in orphans:
            safe = (
                "[red]LIVE — needs --force[/red]"
                if od["live"] is not None
                else "[green]dead leftover[/green]"
            )
            table.add_row(
                _display_path(od["path"], root), _orphan_kind(od), safe, od.get("age") or "?"
            )
        ch.print_table(table)
        detail = (
            "Committed work is never at risk — prune deletes directories, not branch refs.\n"
            "Delete them with: navig repo prune --yes"
        )
        if live_count:
            detail += f"\n{live_count} live worktree(s) will be SKIPPED unless you add --force."
        ch.warning("Dry run — nothing deleted.", detail)
        return

    if removed:
        ch.success(f"Removed {len(removed)} orphaned dir(s).", ", ".join(removed))
    if skipped:
        has_live = any("live worktree" in s["reason"] for s in skipped)
        has_locked = any(
            "live worktree" not in s["reason"] and "outside" not in s["reason"] for s in skipped
        )
        hints = []
        if has_locked:
            hints.append(
                "Locked dirs: something still holds a file inside them — most often a "
                "shell/editor/terminal whose current directory is in the worktree (cd out "
                "of it), a dev server or process still running from it, or an "
                "antivirus/indexer briefly walking a fresh checkout. Close or cd out, then "
                "re-run (prune retries transient locks); a reboot clears stubborn ones."
            )
        if has_live:
            hints.append(
                "Live worktrees: re-run with --force only if their uncommitted work is disposable."
            )
        ch.warning(
            f"{len(skipped)} not removed.",
            "\n".join(f"{s['name']}: {s['reason']}" for s in skipped)
            + ("\n" + "\n".join(hints) if hints else ""),
        )
    if not removed and not skipped:
        ch.success("No orphaned worktree dirs — .dev/worktrees is clean.")


# ── merged-branch sweep ──────────────────────────────────────────────────────
#
# Branches accumulate faster than anyone sweeps them: measured in one repo,
# 8 → 20 in eleven days and 37 at the worst, of which 28 were provably already
# on main. Each was deleted by hand after proving it — and the proof is
# mechanical, so it belongs in a command rather than in a person's afternoon.

#: How many MERGED PRs the GitHub index reads. Merged is the only state that PROVES a
#: branch redundant, so this must reach every merged PR the repo has ever had — a branch
#: whose PR falls outside the window is kept as "no PR" forever, which is safe but is
#: exactly the debris sweep exists to clear. The first value here was 500 with the comment
#: "old enough that a branch merged months ago is still recognised"; by the time the repo
#: was landing ~20 PRs a day that was 25 days, and 138 remote refs — every one a merged PR
#: with a tip identical to its PR head — sat unrecognised for ten weeks. Measured: the
#: complete merged index (1,491 PRs) is 192 KB and 7.4 s; the old 500-of-all call was 2.7 s.
#: If a fetch returns exactly this many rows the index is TRUNCATED and the note says so.
_PR_INDEX_LIMIT = 10_000
#: Open PRs are annotation only ("kept — PR #n OPEN"); recent ones are enough.
_PR_OPEN_LIMIT = 300


def _gh_pr_rows(
    gh: str, root: Path, state: str, limit: int
) -> tuple[list[dict] | None, str | None]:
    """One ``gh pr list`` call → rows, or ``(None, why)``."""
    try:
        res = subprocess.run(
            [
                gh,
                "pr",
                "list",
                "--state",
                state,
                "--limit",
                str(limit),
                "--json",
                "headRefName,number,state,headRefOid",
            ],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",  # gh emits UTF-8 JSON; the locale page would mojibake a branch name
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"gh unavailable ({exc.__class__.__name__})"
    if res.returncode != 0:
        tail = (res.stderr.strip().splitlines() or ["gh failed"])[-1]
        return None, f"gh failed: {tail}"
    try:
        return json.loads(res.stdout or "[]"), None
    except json.JSONDecodeError:
        return None, "gh returned unreadable JSON"


def _github_pr_index(root: Path) -> tuple[dict[str, dict], str | None]:
    """``branch -> {number, state, head}`` — EVERY merged PR, plus recent open ones — or
    ``({}, why)`` when unavailable.

    Newest first, so a branch reused across several PRs is judged by its latest.
    ``headRefOid`` is the sha the PR was merged AT — the detail that makes "merged PR" a
    proof rather than a hint: a branch that received commits after its PR merged has a tip
    that no longer matches, and must be kept.

    Absence of ``gh`` is not an error; the ``merged-pr`` class is simply not claimed and
    the caller says so. A truncated merged index is reported the same way: the caller
    prints the note, and the branches beyond the window are kept — the safe direction,
    but a visible one.
    """
    import shutil

    gh = shutil.which("gh")
    if not gh:
        return {}, "gh not installed — squash-merged branches cannot be recognised"
    merged, why = _gh_pr_rows(gh, root, "merged", _PR_INDEX_LIMIT)
    if merged is None:
        return {}, why
    open_rows, _why_open = _gh_pr_rows(gh, root, "open", _PR_OPEN_LIMIT)
    rows = list(merged) + list(open_rows or [])  # merged first: the proof wins over annotation
    index: dict[str, dict] = {}
    for row in rows:
        head = row.get("headRefName")
        if head and head not in index:
            index[head] = {
                "number": row.get("number"),
                "state": row.get("state"),
                "head": row.get("headRefOid"),
            }
    note = None
    if len(merged) >= _PR_INDEX_LIMIT:
        note = (
            f"merged-PR index truncated at {_PR_INDEX_LIMIT} — branches whose PR is older "
            f"are KEPT, not deleted; raise _PR_INDEX_LIMIT"
        )
    return index, note


def _prove_merged(
    root: Path, ref: str, sha: str, base: str, base_tree: str, pr: dict | None
) -> str | None:
    """The proof that *ref* is already on *base*, or ``None`` if none holds.

    The three sufficient proofs, in the order :func:`collect_sweep` and
    :func:`collect_remote_sweep` apply them — this is the single place that
    logic lives, so ``land`` cannot drift from the sweep it must agree with:

    * ``ancestor`` — the tip is an ancestor of the base (every commit is there).
    * ``tree`` — the tip TREE is byte-identical to the base tree (squash-merged
      then rebased onto the merge parent: commits the base lacks, contents it has).
    * ``merged-pr`` — a MERGED PR whose head sha IS this exact tip. The sha is
      what makes it a proof: a branch reused after its PR landed has a tip that
      no longer matches, so this returns ``None`` and the branch is kept.
    """
    if _git(["merge-base", "--is-ancestor", ref, base], root).returncode == 0:
        return "ancestor"
    if base_tree and _git(["rev-parse", f"{ref}^{{tree}}"], root).stdout.strip() == base_tree:
        return "tree"
    if pr and pr.get("state") == "MERGED" and pr.get("head") == sha:
        return "merged-pr"
    return None


def delete_proven_branch(root: Path, name: str, proof: str, base: str) -> tuple[bool, str | None]:
    """Delete a local branch the sweep has PROVEN is already on *base*.

    ``-d`` for a proven ancestor keeps git's own refusal as a second net; the other proofs
    are exactly the cases ``-d`` cannot see, so ``-D``. But ``-d`` has a second check the
    proof does not need: when the branch has an UPSTREAM, git also requires the tip to be
    merged into that upstream — and a stale ``origin/<branch>`` the local tip was rebased
    past makes it refuse a branch it admits is "merged to HEAD" (measured: the first live
    run removed the explore-media worktree and left its branch behind on exactly that
    message). The upstream is debris ``sweep --remote`` clears; the proof is against
    *base*. So on THAT refusal, re-verify the ancestry ourselves and use ``-D``.

    Never the DEFAULT branch, whatever the proof says. Local ``main`` is always "an
    ancestor of origin/main" once it is up to date, so every proof above holds for it —
    and a worktree left holding ``main`` (``gh pr merge --delete-branch`` run inside a
    worktree switches it there) is exactly what the finished-worktree sweep removes,
    after which this is called with the branch that worktree held. Refused here, at the
    one remover every caller shares, rather than at each call site.

    Returns ``(deleted, error)``.
    """
    default = default_branch(root)
    if name == default:
        return False, f"refusing to delete the default branch '{default}'"
    flag = "-d" if proof == "ancestor" else "-D"
    res = _git(["branch", flag, name], root)
    if res.returncode == 0:
        return True, None
    err = (res.stderr or res.stdout).strip()
    if flag == "-d" and "not yet merged to" in err and "refs/remotes/" in err:
        if _git(["merge-base", "--is-ancestor", name, base], root).returncode == 0:
            res2 = _git(["branch", "-D", name], root)
            if res2.returncode == 0:
                return True, None
            err = (res2.stderr or res2.stdout).strip()
    return False, err


def collect_sweep(root: Path, *, github: bool = True, fetch: bool = True) -> dict:
    """Classify every local branch: redundant (provably on the base) or kept.

    Three proofs, each independently sufficient:

    * ``ancestor`` — the tip is an ancestor of the base, so every commit is
      already there. Deleted with ``git branch -d``, git's own safe delete.
    * ``tree`` — the tip TREE is byte-identical to the base tree: a branch
      squash-merged and rebased onto the merge's parent has commits main lacks
      and contents main already has.
    * ``merged-pr`` — GitHub records a MERGED pull request whose head is this
      branch AND whose merged sha is the branch's current tip. A squash merge
      leaves the branch's commits off main's ancestry, so ancestry alone reads
      these as unmerged; the PR record is the proof.

    Everything else is KEPT, carrying its ahead-count and PR state so the
    operator can decide. PROTECTED branches are never classified at all: the
    default branch, and any branch checked out in any worktree, the primary
    included — deleting a checked-out branch is refused by git anyway, but the
    contract should not lean on that.

    The base is the REMOTE default (:func:`merge_base_ref`), refreshed by a
    best-effort fetch first. A fetch that fails leaves the cached ref in
    place, which can only UNDER-classify — a branch merged since the last
    fetch reads as unmerged and is kept. Never the other way round.
    """
    default = default_branch(root)
    fetched = False
    if fetch:
        fetched = _git(["fetch", "origin", default], root, timeout=60).returncode == 0
    base = merge_base_ref(root, default)
    base_tree = _git(["rev-parse", f"{base}^{{tree}}"], root).stdout.strip()

    protected = {default}
    for wt in list_worktrees(root):
        if wt["branch"]:
            protected.add(wt["branch"])

    pr_index: dict[str, dict] = {}
    github_note: str | None = None
    if github:
        pr_index, github_note = _github_pr_index(root)

    res = _git(["branch", "--format=%(refname:short)"], root)
    names = (
        [ln.strip() for ln in res.stdout.splitlines() if ln.strip()] if res.returncode == 0 else []
    )

    redundant: list[dict] = []
    kept: list[dict] = []
    for name in names:
        if name in protected or name.startswith("("):
            continue
        sha = _git(["rev-parse", name], root).stdout.strip()
        short = sha[:9]
        pr = pr_index.get(name)
        pr_label = f"#{pr['number']} {pr['state']}" if pr else None

        if _git(["merge-base", "--is-ancestor", name, base], root).returncode == 0:
            redundant.append({"name": name, "sha": short, "proof": "ancestor", "pr": pr_label})
            continue
        if base_tree and _git(["rev-parse", f"{name}^{{tree}}"], root).stdout.strip() == base_tree:
            redundant.append({"name": name, "sha": short, "proof": "tree", "pr": pr_label})
            continue
        if pr and pr.get("state") == "MERGED" and pr.get("head") == sha:
            redundant.append({"name": name, "sha": short, "proof": "merged-pr", "pr": pr_label})
            continue

        ahead = _git(["rev-list", "--count", f"{base}..{name}"], root)
        kept.append(
            {
                "name": name,
                "sha": short,
                "ahead": int(ahead.stdout.strip() or 0) if ahead.returncode == 0 else None,
                "pr": pr_label,
                # A MERGED PR whose sha moved on: the branch was reused after landing.
                "note": (
                    "commits after its merged PR" if pr and pr.get("state") == "MERGED" else None
                ),
            }
        )

    # Worktrees whose checked-out branch is PROVABLY on the base. Their branches are
    # protected above (deleting a checked-out branch is impossible), which made a
    # worktree left behind after its work landed invisible to this command: measured,
    # one sat 904 commits behind main for eight weeks, provably merged, clean — finished
    # and forgotten. The same three proofs decide; a dirty tree is reported, never
    # removed (uncommitted work on top of a merged branch is still uncommitted work).
    finished: list[dict] = []
    for wt in list_worktrees(root):
        if wt["is_primary"] or not wt.get("branch"):
            continue
        name = wt["branch"]
        sha = wt.get("head") or _git(["rev-parse", name], root).stdout.strip()
        if not sha:
            continue
        pr = pr_index.get(name)
        pr_label = f"#{pr['number']} {pr['state']}" if pr else None
        proof = None
        if _git(["merge-base", "--is-ancestor", sha, base], root).returncode == 0:
            proof = "ancestor"
        elif base_tree and _git(["rev-parse", f"{sha}^{{tree}}"], root).stdout.strip() == base_tree:
            proof = "tree"
        elif pr and pr.get("state") == "MERGED" and pr.get("head") == sha:
            proof = "merged-pr"
        if proof is None:
            continue
        _s, is_dirty = dirty_ref(wt["path"])
        behind = worktree_behind(root, sha, base)
        # ⚠ "Merged" is not "finished". A brand-new worktree sits AT the base tip, so its
        # branch is trivially an ancestor with zero commits of its own — measured: five of
        # seven "finished" worktrees on the first run were other sessions' fresh ones, and
        # --yes would have deleted them. A fresh worktree and a fast-forward-merged one
        # are indistinguishable from refs alone once the reflog ages out; what separates
        # "active" from "abandoned" is whether main has moved past it. So --yes removes
        # only what is proven merged AND clean AND at least WORKTREE_BEHIND_WARN behind —
        # the threshold the briefing already uses for "missed days of merges". Anything
        # younger is listed with the reason and left alone.
        never, created_at = branch_never_committed(root, name)
        age_days = (time.time() - created_at) / 86400 if (never and created_at) else None
        # A worktree holding the DEFAULT branch is never fresh work: `navig repo new`
        # always cuts a feature branch, and committing on the default branch is refused
        # by the pre-commit guard. It is what `gh pr merge --delete-branch` leaves when
        # run inside a worktree (it switches that worktree to main) — measured twice in
        # one afternoon — and while it stands, the main checkout cannot `checkout main`
        # at all ("already used by worktree"). So the behind-count caution does not apply —
        # but a session may still be sitting in it (measured: one reset its main worktree
        # to origin/main minutes before a sweep would have removed it). So it must also be
        # IDLE: no HEAD movement for LOCK_TTL_MINUTES, the same line the agent lock draws
        # between a live session and a gone one. The removal takes the WORKTREE only: the
        # branch is the default one and `delete_proven_branch` refuses it.
        holds_default = name == default
        # The same holds for a tip GitHub records as a MERGED PR's head: a fresh worktree
        # sits at the base tip and was never any PR's head, so "may be fresh" is false of
        # it — yet it was the verdict (measured: #1557's worktree, merged at exactly its
        # tip, clean, kept as "only 5 behind — may be a fresh worktree"). Squash-merging is
        # the house default, so this is the COMMON finished shape, and the behind rule made
        # each one wait for 50 more merges. Same idle gate: the session that just merged may
        # still be in it. Without the GitHub index this never fires, so it can only keep more.
        merged_pr_tip = bool(pr and pr.get("state") == "MERGED" and pr.get("head") == sha)
        if is_dirty:
            why_kept = "uncommitted changes — inspect, then: navig repo remove <slug> --force"
        elif holds_default:
            why_kept = _idle_verdict(Path(wt["path"]), f"holds {name}")
        elif merged_pr_tip:
            why_kept = _idle_verdict(Path(wt["path"]), f"merged as {pr_label}")
        elif behind is None:
            why_kept = "cannot tell how far behind it is"
        elif behind >= WORKTREE_BEHIND_WARN:
            why_kept = None
        elif never and age_days is not None and age_days >= WORKTREE_UNUSED_DAYS:
            # Under the behind threshold, but the reflog says the branch has never moved
            # since it was created weeks ago: unused, not fresh.
            why_kept = None
        elif never and age_days is not None:
            why_kept = (
                f"created {age_days:.0f} day(s) ago and never committed to — "
                f"unused after {WORKTREE_UNUSED_DAYS}; remove by hand if abandoned"
            )
        else:
            why_kept = (
                f"only {behind} behind {base} — may be a fresh worktree; remove by hand if finished"
            )
        finished.append(
            {
                "path": str(wt["path"]),
                "slug": Path(wt["path"]).name,
                "branch": name,
                "sha": sha[:9],
                "proof": proof,
                "pr": pr_label,
                "dirty": is_dirty,
                "behind": behind,
                "never_committed": never,
                "age_days": round(age_days, 1) if age_days is not None else None,
                "holds_default": holds_default,
                "merged_pr_tip": merged_pr_tip,
                "removable": why_kept is None,
                "why_kept": why_kept,
            }
        )

    return {
        "default_branch": default,
        "base_ref": base,
        "fetched": fetched,
        "github": github_note is None and github,
        "github_note": github_note,
        "protected": sorted(protected),
        "redundant": redundant,
        "kept": kept,
        "finished_worktrees": finished,
    }


#: How many remote refs one `git push --delete` carries. Well under any command-line
#: limit at realistic branch-name lengths, and small enough that a failed batch's
#: one-by-one retry stays short.
_REMOTE_DELETE_BATCH = 40


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def collect_remote_sweep(root: Path, *, github: bool = True, fetch: bool = True) -> dict:
    """Classify every ``origin/*`` ref the same way :func:`collect_sweep` classifies locals.

    Why remote refs need their own sweep: ``gh pr merge --delete-branch`` run
    from a linked worktree merges the PR and then fails at its local
    checkout-main step — BEFORE deleting the remote branch — with a message
    that reads like harmless noise. One session accumulated 13 merged refs on
    origin this way while reporting each as cleaned. Locally nothing is left
    to see, so the local sweep cannot help.

    Same three proofs, same asymmetry (every failure keeps MORE). Protected:
    the default branch, and any remote ref whose NAME is a local branch that is
    not itself redundant — that is someone's in-flight work, pushed. A remote
    ref with no local branch at all is exactly the leaked case.

    The fetch prunes, so a ref already deleted on GitHub is not reported.
    """
    default = default_branch(root)
    fetched = False
    if fetch:
        fetched = _git(["fetch", "--prune", "origin"], root, timeout=60).returncode == 0
    base = merge_base_ref(root, default)
    base_tree = _git(["rev-parse", f"{base}^{{tree}}"], root).stdout.strip()

    local_sweep = collect_sweep(root, github=github, fetch=False)
    local_redundant = {br["name"] for br in local_sweep["redundant"]}
    res = _git(["branch", "--format=%(refname:short)"], root)
    local_names = (
        {ln.strip() for ln in res.stdout.splitlines() if ln.strip()}
        if res.returncode == 0
        else set()
    )

    pr_index: dict[str, dict] = {}
    github_note: str | None = None
    if github:
        pr_index, github_note = _github_pr_index(root)

    res = _git(
        ["for-each-ref", "refs/remotes/origin", "--format=%(refname:short) %(objectname)"], root
    )
    rows = (
        [ln.split() for ln in res.stdout.splitlines() if ln.strip()] if res.returncode == 0 else []
    )

    protected = {default}
    redundant: list[dict] = []
    kept: list[dict] = []
    for ref, sha in rows:
        if not ref.startswith("origin/"):
            continue
        name = ref[len("origin/") :]
        if name in ("HEAD", default):
            continue
        if name in local_names and name not in local_redundant:
            protected.add(name)
            continue
        short = sha[:9]
        pr = pr_index.get(name)
        pr_label = f"#{pr['number']} {pr['state']}" if pr else None

        if _git(["merge-base", "--is-ancestor", ref, base], root).returncode == 0:
            redundant.append({"name": name, "sha": short, "proof": "ancestor", "pr": pr_label})
            continue
        if base_tree and _git(["rev-parse", f"{ref}^{{tree}}"], root).stdout.strip() == base_tree:
            redundant.append({"name": name, "sha": short, "proof": "tree", "pr": pr_label})
            continue
        if pr and pr.get("state") == "MERGED" and pr.get("head") == sha:
            redundant.append({"name": name, "sha": short, "proof": "merged-pr", "pr": pr_label})
            continue

        ahead = _git(["rev-list", "--count", f"{base}..{ref}"], root)
        kept.append(
            {
                "name": name,
                "sha": short,
                "ahead": int(ahead.stdout.strip() or 0) if ahead.returncode == 0 else None,
                "pr": pr_label,
                "note": (
                    "commits after its merged PR" if pr and pr.get("state") == "MERGED" else None
                ),
            }
        )

    return {
        "default_branch": default,
        "base_ref": base,
        "fetched": fetched,
        "github": github_note is None and github,
        "github_note": github_note,
        "protected": sorted(protected),
        "redundant": redundant,
        "kept": kept,
    }


@repo_app.command("sweep")
def sweep_cmd(
    repo: str = typer.Option(None, "--repo", help="Target repo (default: current directory)"),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Actually delete (default: dry run — list only)"
    ),
    no_github: bool = typer.Option(
        False, "--no-github", help="Skip the GitHub PR lookup (ancestry and tree proofs only)"
    ),
    no_fetch: bool = typer.Option(
        False, "--no-fetch", help="Judge against the cached remote ref without fetching first"
    ),
    remote: bool = typer.Option(
        False,
        "--remote",
        "-r",
        help="Also sweep origin/* refs (the branches `gh pr merge --delete-branch` leaves behind "
        "when run from a worktree); deleted with `git push origin --delete`",
    ),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Delete local branches whose work is provably already on the default branch.

    Three proofs, any one sufficient: the tip is an ANCESTOR of ``origin/main``;
    the tip TREE is identical to it; or GitHub records a MERGED pull request
    that landed exactly this tip. Anything not proven is KEPT and listed with
    its ahead-count and PR state, so the decision that remains is a human one
    about real work — not a sweep of debris.

    SAFE by default: dry run until ``--yes``; the default branch and every
    branch checked out in a worktree are never touched; each deletion prints
    the sha it removed, and git's reflog keeps the commit reachable. A failed
    fetch or a missing ``gh`` can only make this keep MORE, never delete more.

    ``--remote`` extends the same proofs to ``origin/*``: the refs that
    ``gh pr merge --delete-branch`` leaves on GitHub when run from a linked
    worktree (it merges, then aborts at its local checkout step before the
    remote delete). A remote ref whose name is an unmerged local branch is
    someone's pushed work and is protected. GitHub keeps ``refs/pull/N/head``,
    so a deleted merged ref stays recoverable.
    """
    root = _require_root(repo)
    data = collect_sweep(root, github=not no_github, fetch=not no_fetch)
    remote_data = (
        collect_remote_sweep(root, github=not no_github, fetch=not no_fetch) if remote else None
    )

    deleted: list[dict] = []
    failed: list[dict] = []
    remote_deleted: list[dict] = []
    remote_failed: list[dict] = []
    wt_removed: list[dict] = []
    wt_failed: list[dict] = []
    if yes:
        for br in data["redundant"]:
            ok_del, err = delete_proven_branch(root, br["name"], br["proof"], data["base_ref"])
            (deleted if ok_del else failed).append({**br, "error": err})
        if remote_data:
            # ONE push per batch, not one per branch. The first complete index found 194
            # redundant origin/* refs; deleting them one push at a time is 194 network round
            # trips and 194 pre-push hook invocations (delete-only pushes skip the gate, but
            # each still starts a process). `git push --delete a b c …` takes them all in a
            # single ref-update; a batch that fails is retried one by one so a single bad
            # ref (raced away by another session's merge) cannot hide the rest.
            for chunk in _chunks(remote_data["redundant"], _REMOTE_DELETE_BATCH):
                names = [br["name"] for br in chunk]
                res = _git(["push", "origin", "--delete", *names], root, timeout=180)
                if res.returncode == 0:
                    remote_deleted.extend({**br, "error": None} for br in chunk)
                    continue
                for br in chunk:  # fall back to one at a time to attribute the failure
                    one = _git(["push", "origin", "--delete", br["name"]], root, timeout=60)
                    (remote_deleted if one.returncode == 0 else remote_failed).append(
                        {**br, "error": None if one.returncode == 0 else one.stderr.strip()}
                    )
        # Finished worktrees: only the ones the collector marked removable (proven merged,
        # clean, ≥ WORKTREE_BEHIND_WARN behind). Removal goes through the SAME remover as
        # `navig repo remove` — never --force here, so git's own dirty check is a second net
        # under the collector's — and the branch follows only once the worktree is gone.
        for wt in data["finished_worktrees"]:
            if not wt["removable"]:
                continue
            out = remove_worktree(root, Path(wt["path"]), force=False)
            if out["removed"] and wt.get("holds_default"):
                # The worktree was the defect; the default branch it held stays.
                wt_removed.append({**wt, "branch_deleted": False, "error": None})
            elif out["removed"]:
                ok_del, err = delete_proven_branch(
                    root, wt["branch"], wt["proof"], data["base_ref"]
                )
                wt_removed.append({**wt, "branch_deleted": ok_del, "error": err})
            else:
                wt_failed.append(
                    {**wt, "error": out["refused"] or out["leftover_error"] or "not removed"}
                )

    if json_out:
        payload = {
            **data,
            "deleted": deleted,
            "failed": failed,
            "dry_run": not yes,
            "worktrees_removed": wt_removed,
            "worktrees_failed": wt_failed,
        }
        if remote_data:
            payload["remote"] = {
                **remote_data,
                "deleted": remote_deleted,
                "failed": remote_failed,
            }
        typer.echo(json.dumps(payload, indent=2))
        return

    from navig import console_helper as ch

    if not data["fetched"] and not no_fetch:
        ch.warning(
            "Could not fetch origin — judging against the cached remote ref.",
            "A branch merged since the last fetch will be kept, not deleted.",
        )
    if data["github_note"]:
        ch.dim(f"GitHub: {data['github_note']}")

    if data["redundant"]:
        title = (
            f"Redundant branches — {len(data['redundant'])} "
            f"({'deleted' if yes else 'dry run'}), judged against {data['base_ref']}"
        )
        table = ch.create_table(
            title,
            [
                {"name": "Branch", "style": "cyan"},
                {"name": "Proof", "style": "white"},
                {"name": "Was", "style": "dim"},
                {"name": "PR", "style": "dim"},
            ],
        )
        for br in data["redundant"]:
            table.add_row(br["name"], br["proof"], br["sha"], br["pr"] or "—")
        ch.print_table(table)

    if data["kept"]:
        table = ch.create_table(
            f"Kept — {len(data['kept'])} with work not on {data['base_ref']}",
            [
                {"name": "Branch", "style": "cyan"},
                {"name": "Ahead", "style": "white", "justify": "right"},
                {"name": "PR", "style": "white"},
                {"name": "Note", "style": "yellow"},
            ],
        )
        for br in data["kept"]:
            table.add_row(
                br["name"],
                str(br["ahead"] if br["ahead"] is not None else "?"),
                br["pr"] or "—",
                br["note"] or "",
            )
        ch.print_table(table)

    if data["finished_worktrees"]:
        removable = [w for w in data["finished_worktrees"] if w["removable"]]
        table = ch.create_table(
            f"Finished worktrees — {len(data['finished_worktrees'])} on a branch provably already on "
            f"{data['base_ref']} ({len(removable)} removable{', removed' if yes else ', dry run'})",
            [
                {"name": "Worktree", "style": "cyan"},
                {"name": "Branch", "style": "white"},
                {"name": "Proof", "style": "white"},
                {"name": "Behind", "style": "white", "justify": "right"},
                {"name": "Verdict", "style": "white"},
            ],
        )
        removed_by_slug = {w["slug"]: w for w in wt_removed}
        failed_by_slug = {w["slug"]: w["error"] for w in wt_failed}
        for w in data["finished_worktrees"]:
            if w["slug"] in removed_by_slug:
                r = removed_by_slug[w["slug"]]
                # "removed" must not hide a branch that survived — the first live run did.
                if w.get("holds_default"):
                    verdict = f"[green]removed[/green] [dim](worktree only; {w['branch']} kept)[/dim]"
                elif r["branch_deleted"]:
                    verdict = "[green]removed[/green]"
                else:
                    verdict = f"[green]removed[/green]; [yellow]branch kept: {r['error']}[/yellow]"
            elif w["slug"] in failed_by_slug:
                verdict = f"[red]not removed: {failed_by_slug[w['slug']]}[/red]"
            elif w["removable"] and w.get("holds_default"):
                verdict = (
                    f"[yellow]removable — holds {w['branch']}, so the main checkout "
                    f"cannot check it out[/yellow]"
                )
            elif w["removable"]:
                verdict = "[green]removable[/green]" if not yes else "[yellow]removable[/yellow]"
            else:
                verdict = f"[yellow]kept — {w['why_kept']}[/yellow]"
            table.add_row(
                w["slug"],
                w["branch"],
                w["proof"],
                str(w["behind"]) if w["behind"] is not None else "?",
                verdict,
            )
        ch.print_table(table)

    if remote_data:
        if remote_data["redundant"]:
            table = ch.create_table(
                f"Redundant REMOTE refs — {len(remote_data['redundant'])} "
                f"({'deleted' if yes else 'dry run'}), origin/* judged against {remote_data['base_ref']}",
                [
                    {"name": "origin/…", "style": "cyan"},
                    {"name": "Proof", "style": "white"},
                    {"name": "Was", "style": "dim"},
                    {"name": "PR", "style": "dim"},
                ],
            )
            for br in remote_data["redundant"]:
                table.add_row(br["name"], br["proof"], br["sha"], br["pr"] or "—")
            ch.print_table(table)
        if remote_data["kept"]:
            table = ch.create_table(
                f"Kept on origin — {len(remote_data['kept'])} with work not on {remote_data['base_ref']}",
                [
                    {"name": "origin/…", "style": "cyan"},
                    {"name": "Ahead", "style": "white", "justify": "right"},
                    {"name": "PR", "style": "white"},
                    {"name": "Note", "style": "yellow"},
                ],
            )
            for br in remote_data["kept"]:
                table.add_row(
                    br["name"],
                    str(br["ahead"] if br["ahead"] is not None else "?"),
                    br["pr"] or "—",
                    br["note"] or "",
                )
            ch.print_table(table)

    any_redundant = (
        bool(data["redundant"])
        or bool(remote_data and remote_data["redundant"])
        or any(w["removable"] for w in data["finished_worktrees"])
    )
    any_kept = (
        bool(data["kept"])
        or bool(remote_data and remote_data["kept"])
        or any(not w["removable"] for w in data["finished_worktrees"])
    )
    # A verdict must name what it did NOT judge. Without --remote the origin/* refs were
    # never examined, and "nothing redundant" read as a clean bill while 194 provably-merged
    # remote refs sat on origin (measured). Count them cheaply and say so.
    unjudged = ""
    if not remote:
        refs = _git(["for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/"], root)
        n_remote = sum(
            1
            for ln in (refs.stdout or "").splitlines()
            if ln.strip() and not ln.strip().endswith(("/HEAD", f"/{data['default_branch']}"))
        )
        if n_remote:
            unjudged = f" ({n_remote} origin/* ref(s) not judged — add --remote)"
    if not any_redundant and not any_kept:
        ch.success(f"Nothing to sweep — only protected branches exist.{unjudged}")
        return
    if not any_redundant:
        ch.success(
            "Nothing redundant among local branches — every one carries work not on "
            f"{data['base_ref']}.{unjudged}"
        )
        return
    if not yes:
        ch.warning(
            "Dry run — nothing deleted.",
            "Every listed branch is provably on the base already. Delete them with: "
            f"navig repo sweep{' --remote' if remote else ''} --yes\n"
            "(each deletion prints its sha; the reflog — and GitHub's refs/pull/N/head — keep the commit)",
        )
        return
    if deleted:
        ch.success(
            f"Deleted {len(deleted)} redundant branch(es).",
            "\n".join(f"{d['name']}  (was {d['sha']}, {d['proof']})" for d in deleted),
        )
    if failed:
        ch.warning(
            f"{len(failed)} not deleted.",
            "\n".join(f"{f['name']}: {f['error']}" for f in failed),
        )
    if remote_deleted:
        ch.success(
            f"Deleted {len(remote_deleted)} redundant remote ref(s) on origin.",
            "\n".join(
                f"origin/{d['name']}  (was {d['sha']}, {d['proof']})" for d in remote_deleted
            ),
        )
    if remote_failed:
        ch.warning(
            f"{len(remote_failed)} remote ref(s) not deleted.",
            "\n".join(f"origin/{f['name']}: {f['error']}" for f in remote_failed),
        )


_BRANCH_TYPES = ("feat", "fix", "chore", "docs", "refactor")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _new_base_ref(root: Path, default: str) -> str | None:
    """Base for a new worktree: prefer ``origin/<default>`` (latest), else local
    ``<default>``, else None (HEAD). Basing on ``origin/<default>`` — not the
    possibly-behind local checkout — is the whole point of ``new``."""
    for candidate in (f"origin/{default}", default):
        if _git(["rev-parse", "--verify", "--quiet", candidate], root).returncode == 0:
            return candidate
    return None


@repo_app.command("new")
def new_cmd(
    slug: str = typer.Argument(..., help="Kebab-case name, e.g. 'auth-fix' -> feat/auth-fix"),
    repo: str = typer.Option(None, "--repo", help="Target repo (default: current directory)"),
    branch_type: str = typer.Option(
        "feat", "--type", "-t", help=f"Branch type: {'/'.join(_BRANCH_TYPES)}"
    ),
    from_ref: str = typer.Option(
        None, "--from", help="Base ref (default: latest origin/<default-branch>)"
    ),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Create an isolated worktree under .dev/worktrees/ for a parallel session.

    The sanctioned way to run a second/third Claude session: each gets its own
    worktree (own HEAD), so they never collide with the main checkout. The branch
    is based on the LATEST ``origin/<default-branch>`` (fetched first) — not your
    possibly-behind local checkout — then the folder to open is printed. Never
    creates a sibling folder outside the repo.
    """
    from navig import console_helper as ch

    root = _require_root(repo)

    if not _SLUG_RE.match(slug):
        ch.error(
            f"Invalid slug: {slug!r}",
            "Use kebab-case — lowercase letters, digits, single hyphens (e.g. auth-fix).",
        )
        raise typer.Exit(1)
    if branch_type not in _BRANCH_TYPES:
        ch.error(f"Invalid --type {branch_type!r}.", f"One of: {', '.join(_BRANCH_TYPES)}")
        raise typer.Exit(1)

    branch = f"{branch_type}/{slug}"
    wt_dir = root / WORKTREES_RELDIR / slug
    if wt_dir.exists():
        ch.error(
            f"Path already exists: {_display_path(wt_dir, root)}",
            "Pick another slug, or clear leftovers with: navig repo prune",
        )
        raise typer.Exit(1)
    if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], root).returncode == 0:
        ch.error(f"Branch already exists: {branch}", "Pick another slug or --type.")
        raise typer.Exit(1)

    default = default_branch(root)
    if from_ref is None:
        _git(["fetch", "origin", default], root)  # best-effort; offline is fine
    base = from_ref or _new_base_ref(root, default)

    add_args = ["worktree", "add", str(wt_dir), "-b", branch]
    if base:
        add_args.append(base)
    res = _git(add_args, root, timeout=_GIT_CHECKOUT_TIMEOUT)
    if res.returncode == 124:
        # The tree was killed, so the checkout is partial and git's "initializing" lock
        # is still on it. Say what is on disk and how to clear it — a bare "failed"
        # sent people to `git worktree list`, where it showed as a normal worktree.
        ch.error(
            f"git worktree add did not finish within {_GIT_CHECKOUT_TIMEOUT}s and was killed.",
            f"A PARTIAL checkout is at {wt_dir} (git lists it as locked: initializing).\n"
            f"Clear it with: navig repo remove {slug} --force   then retry.",
        )
        raise typer.Exit(1)
    if res.returncode != 0:
        ch.error("git worktree add failed.", (res.stderr or res.stdout).strip()[:300])
        raise typer.Exit(1)

    if json_out:
        typer.echo(
            json.dumps({"path": str(wt_dir), "branch": branch, "base": base or "HEAD"}, indent=2)
        )
        return

    ch.success(
        f"Worktree ready on {branch} (based on {base or 'HEAD'}).",
        f"Open a Claude Code session in this folder:\n  {wt_dir}\n"
        f"When done: merge {branch} to {default}, then: navig repo remove {slug}",
    )


def live_processes_in(wt_dir: Path, *, skip_pids: set[int] | None = None) -> list[dict]:
    """Processes that are RUNNING FROM *wt_dir* — a dev server, a test runner, a
    shell parked inside it.

    Matched by current directory OR by a command-line argument under the worktree
    (a `next dev` started from `apps/webapp`, a `ts-node src/schema-app.ts` whose
    cwd is the API folder — both live under the worktree). The current process and
    its ancestors are skipped: the shell that runs ``navig repo remove`` is usually
    parked somewhere, and it is not the one being protected.

    Best-effort: psutil is optional and a process can vanish or refuse to be read
    mid-walk; either way the answer is "what could be seen", never an exception.
    """
    try:
        import psutil  # noqa: PLC0415 - optional dependency
    except ImportError:
        return []
    needle = os.path.normcase(str(wt_dir.resolve()))
    inside = lambda p: p and os.path.normcase(p).startswith(needle)  # noqa: E731
    skip = set(skip_pids or ())
    if not skip:
        try:
            me = psutil.Process(os.getpid())
            skip = {me.pid, *(a.pid for a in me.parents())}
        except (psutil.Error, OSError):
            skip = {os.getpid()}
    found: list[dict] = []
    for proc in psutil.process_iter(["pid", "name"]):
        if proc.info["pid"] in skip:
            continue
        try:
            cwd = proc.cwd()
        except (psutil.Error, OSError):
            cwd = None
        try:
            args = proc.cmdline()
        except (psutil.Error, OSError):
            args = []
        if inside(cwd) or any(inside(a) for a in args):
            found.append({"pid": proc.info["pid"], "name": proc.info["name"] or "?",
                          "cwd": cwd, "hint": next((a for a in args if inside(a)), None)})
    return found


def remove_worktree(root: Path, wt_dir: Path, *, force: bool = False) -> dict:
    """Unregister a worktree and delete its folder — the mechanics behind ``navig repo
    remove``, shared with ``navig repo sweep`` so there is ONE remover.

    Returns ``{"removed": bool, "refused": str | None, "leftover_error": str | None,
    "hint": str | None}``. Never raises for a refusal; never removes a dirty worktree
    without *force* (git's own refusal is the guard for uncommitted work).

    A lock git left behind is not a lock anyone holds. ``git worktree add`` locks the
    worktree with the reason "initializing" for the length of the checkout and clears it
    on the way out — so one that is still there belongs to an add that DIED (killed at a
    timeout, a crashed session). Nothing is protected by it: uncommitted work is still
    guarded by the dirty check. Any OTHER reason is a person's deliberate
    ``git worktree lock``, and the honest answer is to name it, not to blame "uncommitted
    changes" for a refusal that has nothing to do with them.
    """
    key = os.path.normcase(str(wt_dir.resolve()))
    by_key = {os.path.normcase(str(Path(w["path"]).resolve())): w for w in list_worktrees(root)}
    if key not in by_key:
        return {
            "removed": False,
            "refused": "not a registered worktree",
            "leftover_error": None,
            "hint": "List them: git worktree list  ·  clean orphaned dirs: navig repo prune",
        }

    lock = by_key[key].get("locked")
    if lock == "initializing":
        _git(["worktree", "unlock", str(wt_dir)], root)
    elif lock is not None:
        return {
            "removed": False,
            "refused": f"locked (reason: {lock or 'none given'}); git refuses to remove it",
            "leftover_error": None,
            "hint": f"If that lock is yours: git worktree unlock {wt_dir}   then re-run.",
        }

    # A worktree with something RUNNING from it is somebody's live stack — a dev
    # server mid-proof, a test runner, a session parked in it. Git's dirty check
    # cannot see that (a committed tree with a server on it is "clean"), and on
    # 2026-09-20 one session's remove took another session's servers and folder
    # out from under a browser run. Name what is running; --force means "I know".
    if not force:
        live = live_processes_in(wt_dir)
        if live:
            named = ", ".join(f"{p['name']}[{p['pid']}]" for p in live[:6])
            more = f" (+{len(live) - 6} more)" if len(live) > 6 else ""
            return {"removed": False,
                    "refused": f"{len(live)} process(es) are running from it: {named}{more}",
                    "leftover_error": None,
                    "hint": ("Another session's dev server or test run is using this worktree. "
                             "Stop them (or let that session finish), then re-run — "
                             "or add --force if you are certain they are yours and disposable.")}

    res = _git(
        ["worktree", "remove", *(["--force"] if force else []), str(wt_dir)],
        root,
        timeout=_GIT_DELETE_TIMEOUT,
    )
    reg_after = {os.path.normcase(str(Path(w["path"]).resolve())) for w in list_worktrees(root)}
    if key in reg_after:  # still registered → git refused
        why = (res.stderr or res.stdout).strip()[:300]
        hint = (
            "Add --force to discard the worktree's uncommitted changes."
            if "modified or untracked" in why
            else None
        )
        return {
            "removed": False,
            "refused": why or "git worktree remove refused",
            "leftover_error": None,
            "hint": hint,
        }

    leftover = _rmtree_force(wt_dir) if wt_dir.exists() else None
    _git(["worktree", "prune"], root)  # tidy dangling metadata
    return {
        "removed": not wt_dir.exists(),
        "refused": None,
        "leftover_error": leftover,
        "hint": None,
    }


@repo_app.command("remove")
def remove_cmd(
    slug: str = typer.Argument(..., help="Worktree slug under .dev/worktrees/"),
    repo: str = typer.Option(None, "--repo", help="Target repo (default: current directory)"),
    force: bool = typer.Option(
        False,
        "--force",
        help="Discard uncommitted changes (git refuses a dirty worktree otherwise)",
    ),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Reliably remove a .dev/worktrees/<slug> worktree — unregister + delete.

    ``git worktree remove`` frequently can't delete the folder on Windows (an
    antivirus / indexer briefly holds the fresh checkout), so it unregisters the
    worktree but leaves an orphaned directory behind — the whole reason the pile
    grows. This unregisters it (git refuses a *dirty* worktree without
    ``--force``, so uncommitted work is protected) then retries the physical
    delete with backoff, so a finished worktree does not leak an orphan.
    """
    from navig import console_helper as ch

    root = _require_root(repo)
    wt_dir = root / WORKTREES_RELDIR / slug
    out = remove_worktree(root, wt_dir, force=force)
    if out["refused"]:
        ch.error(f"{slug}: {out['refused']}.", out["hint"] or "")
        raise typer.Exit(1)
    leftover = out["leftover_error"]

    if json_out:
        typer.echo(
            json.dumps(
                {"slug": slug, "removed": not wt_dir.exists(), "leftover_error": leftover}, indent=2
            )
        )
        return

    if not wt_dir.exists():
        ch.success(f"Removed worktree {slug}.")
    else:
        ch.warning(
            f"Unregistered {slug}, but its folder is still locked.",
            f"{leftover}\nSomething still holds a file inside it — most often a "
            "shell/editor/terminal whose current directory is in the worktree (cd out of "
            "it), a dev server still running from it, or an antivirus/indexer walking a "
            "fresh checkout. Close or cd out, then re-run `navig repo prune` "
            "(it retries transient locks).",
        )


def _worktree_for_branch(root: Path, branch: str) -> Path | None:
    """The worktree checked out on *branch*, or None."""
    for wt in list_worktrees(root):
        if wt.get("branch") == branch and not wt["is_primary"]:
            return Path(wt["path"])
    return None


def collect_land(root: Path, branch: str, *, github: bool = True, fetch: bool = True) -> dict:
    """Plan the teardown of one MERGED branch: proof, remote ref, worktree.

    Returns what ``land`` would do without doing it. ``proof`` is None when the
    branch is NOT provably on the base — the one case ``land`` refuses, because
    the whole point is to finish a *merged* branch, never to delete unmerged
    work. The proof is the same one :func:`collect_sweep` uses (via
    :func:`_prove_merged`), so ``land`` and ``sweep`` can never disagree about
    what "merged" means.
    """
    default = default_branch(root)
    fetched = False
    if fetch:
        # Prune too: a ref deleted on GitHub must not read as a leaked remote branch.
        fetched = _git(["fetch", "--prune", "origin", default], root, timeout=60).returncode == 0
    base = merge_base_ref(root, default)
    base_tree = _git(["rev-parse", f"{base}^{{tree}}"], root).stdout.strip()

    exists = _git(["rev-parse", "--verify", "--quiet", branch], root).returncode == 0
    sha = _git(["rev-parse", branch], root).stdout.strip() if exists else ""

    pr = None
    github_note: str | None = None
    if github:
        index, github_note = _github_pr_index(root)
        pr = index.get(branch)

    proof = _prove_merged(root, branch, sha, base, base_tree, pr) if exists else None
    remote_exists = (
        _git(["rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}"], root).returncode
        == 0
    )
    wt = _worktree_for_branch(root, branch)
    # Uncommitted work on top of a MERGED branch is still uncommitted work — merge, then
    # start the next thing in the same folder, is an ordinary sequence. land used to remove
    # the worktree with `--force` without looking. Fail CLOSED: a status git could not
    # read counts as dirty, because the cost of a wrong "clean" here is someone's work.
    worktree_dirty = False
    if wt:
        st = _git(["status", "--porcelain"], wt)
        worktree_dirty = st.returncode != 0 or bool(st.stdout.strip())
    ahead = _git(["rev-list", "--count", f"{base}..{branch}"], root) if exists else None

    return {
        "branch": branch,
        "default_branch": default,
        "base_ref": base,
        "fetched": fetched,
        "exists": exists,
        "sha": sha[:9],
        "proof": proof,
        "pr": (f"#{pr['number']} {pr['state']}" if pr else None),
        "remote_exists": remote_exists,
        "worktree": str(wt) if wt else None,
        "worktree_dirty": worktree_dirty,
        "ahead":(int(ahead.stdout.strip() or 0) if ahead and ahead.returncode == 0 else None),
        "github_note": github_note,
    }


@repo_app.command("land")
def land_cmd(
    branch: str = typer.Argument(None, help="Branch to finish (default: the current branch)"),
    repo: str = typer.Option(None, "--repo", help="Target repo (default: current directory)"),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Actually tear down (default: dry run — show the plan)"
    ),
    no_github: bool = typer.Option(
        False, "--no-github", help="Skip the GitHub PR lookup (ancestry and tree proofs only)"
    ),
    no_fetch: bool = typer.Option(
        False, "--no-fetch", help="Judge against the cached remote ref without fetching first"
    ),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Finish a MERGED branch completely: delete it locally, on origin, and its worktree.

    The merge-and-delete contract has four steps — merge, delete remote, delete
    local, remove worktree — and only the merge is reliable. ``gh pr merge
    --delete-branch`` run from a linked worktree merges, then FAILS its remote
    delete before it happens (it tries to check out the default branch, which a
    worktree holds); the local branch and worktree then linger. Measured: one
    session left a merged remote ref on four consecutive PRs, and 13 in a week
    elsewhere. This makes the other three steps one verb that always completes.

    It does NOT merge — merging is the reviewed, gated step, and folding it in
    would let an unreviewed branch be deleted. ``land`` REFUSES a branch that is
    not provably already on the base (ancestor tip · identical tree · a MERGED
    PR that landed exactly this tip — the same proofs :func:`collect_sweep` uses),
    so it can never delete unmerged code. Merge first (``gh pr merge`` / the PR UI),
    then ``land`` cleans up. Dry-run by default; the reflog keeps the local
    commit and ``refs/pull/N/head`` keeps the remote one, so ``--yes`` is
    recoverable either way.
    """
    from navig import console_helper as ch

    root = _require_root(repo)
    if not branch:
        branch = (_git(["branch", "--show-current"], root).stdout or "").strip()
        if not branch:
            ch.error("No branch given and HEAD is detached.", "Usage: navig repo land <branch>")
            raise typer.Exit(1)

    if branch == default_branch(root):
        ch.error(f"Refusing to land the default branch ({branch}).")
        raise typer.Exit(1)

    plan = collect_land(root, branch, github=not no_github, fetch=not no_fetch)

    done: dict[str, str] = {}
    failed: dict[str, str] = {}
    # Refused BEFORE step 1, so a dirty worktree never leaves a half-landed branch (remote
    # ref gone, worktree and local branch kept). Checked in dry run too — the plan shown
    # must be the plan that would run.
    refused = (
        f"its worktree ({_display_path(plan['worktree'], root)}) has uncommitted changes"
        if plan["exists"] and plan["proof"] and plan["worktree_dirty"]
        else None
    )
    if refused:
        slug = Path(plan["worktree"]).name
        if json_out:
            typer.echo(json.dumps({**plan, "done": {}, "failed": {}, "dry_run": not yes,
                                   "refused": refused}, indent=2))
        else:
            ch.error(
                f"Refusing to land {branch}: {refused}.",
                "land never discards work. Commit or move it first — or, if it is truly "
                f"disposable: navig repo remove {slug} --force   then re-run land.",
            )
        raise typer.Exit(1)

    if yes and plan["exists"] and plan["proof"]:
        # 1. Remote ref (the step that leaks). refs/pull/N/head keeps it recoverable.
        if plan["remote_exists"]:
            res = _git(["push", "origin", "--delete", branch], root, timeout=60)
            (done if res.returncode == 0 else failed)["remote"] = (
                "deleted" if res.returncode == 0 else (res.stderr or res.stdout).strip()[:200]
            )
        # 2. Worktree (before the local branch — git won't delete a checked-out branch).
        #    Through the SAME remover sweep uses, never forced: the dirty refusal above is
        #    the first net, git's own refusal the second.
        if plan["worktree"]:
            out = remove_worktree(root, Path(plan["worktree"]), force=False)
            if out["removed"]:
                done["worktree"] = "removed"
            else:
                failed["worktree"] = out["refused"] or out["leftover_error"] or "not removed"
        # 3. Local branch — through the SAME remover sweep uses, so land inherits the
        #    stale-upstream fix (#1492): `git branch -d` refuses a proven-ancestor branch
        #    whose origin/<branch> was left behind by a rebase, which is exactly the branch
        #    land is finishing. delete_proven_branch re-verifies ancestry and falls back to
        #    -D on that refusal only. Land used raw -d here and would have leaked the local
        #    branch on the very case it exists to clean up.
        ok_del, del_err = delete_proven_branch(root, branch, plan["proof"], plan["base_ref"])
        (done if ok_del else failed)["local"] = (
            f"deleted (was {plan['sha']})"
            if ok_del
            else (del_err or "").strip()[:200]
        )

    if json_out:
        typer.echo(
            json.dumps({**plan, "done": done, "failed": failed, "dry_run": not yes}, indent=2)
        )
        return

    if not plan["exists"]:
        ch.error(f"No such branch: {branch}.")
        raise typer.Exit(1)
    if plan["github_note"]:
        ch.dim(f"GitHub: {plan['github_note']}")
    if not plan["fetched"] and not no_fetch:
        ch.warning("Could not fetch origin — judging against the cached remote ref.")

    if not plan["proof"]:
        note = "commits after its merged PR" if (plan["pr"] and "MERGED" in plan["pr"]) else ""
        ch.error(
            f"{branch} is NOT provably on {plan['base_ref']} — refusing to land it.",
            f"{plan['ahead']} commit(s) ahead · PR {plan['pr'] or '—'}"
            + (f" · {note}" if note else "")
            + "\nland finishes a MERGED branch; it never deletes unmerged work. "
            "Merge it first (gh pr merge / the PR page), then land.",
        )
        raise typer.Exit(1)

    targets = []
    if plan["remote_exists"]:
        targets.append("origin ref")
    if plan["worktree"]:
        targets.append(f"worktree ({_display_path(plan['worktree'], root)})")
    targets.append(f"local branch (was {plan['sha']})")
    summary = f"{branch} is on {plan['base_ref']} (proof: {plan['proof']}, PR {plan['pr'] or '—'})"

    if not yes:
        ch.info(
            f"Would land {branch}",
            f"{summary}\nWould delete: {', '.join(targets)}.\n"
            "The reflog and refs/pull/N/head keep both recoverable. Run with --yes.",
        )
        return

    if failed:
        ch.warning(
            f"Landed {branch} with {len(failed)} step(s) incomplete.",
            summary
            + "\n"
            + "\n".join(f"{k}: {v}" for k, v in {**done, **failed}.items())
            + (
                "\nA locked worktree clears once its holder cd's out; re-run `navig repo prune`."
                if "worktree" in failed
                else ""
            ),
        )
        raise typer.Exit(1)
    ch.success(
        f"Landed {branch}.",
        summary + "\n" + "\n".join(f"{k}: {v}" for k, v in done.items()),
    )


lock_app = typer.Typer(
    help="Inspect / release the main-checkout agent lock (.dev/agent.lock)",
    invoke_without_command=True,
    no_args_is_help=False,
)
repo_app.add_typer(lock_app, name="lock")


def _ctx_repo(ctx: typer.Context) -> str | None:
    """The group-level ``--repo`` stashed by the lock callback, if any."""
    return (ctx.obj or {}).get("repo") if ctx.obj else None


@lock_app.callback()
def lock_default(
    ctx: typer.Context,
    repo: str = typer.Option(None, "--repo", help="Target repo (default: current directory)"),
) -> None:
    """Inspect / release the main-checkout agent lock (.dev/agent.lock)."""
    ctx.obj = {"repo": repo}
    if ctx.invoked_subcommand is None:
        _print_lock_status(_require_root(repo))


def _print_lock_status(root: Path) -> None:
    from navig import console_helper as ch

    st = lock_state(read_lock(root))
    if st["state"] == "free":
        ch.success("Lock free — no agent holds the main checkout.")
        return
    colour = "yellow" if st["state"] == "stale" else "cyan"
    ch.info(
        f"Lock [{colour}]{st['state']}[/{colour}] — session {st.get('session', '?')} "
        f"(age {st.get('age_minutes', '?')}m, branch {st.get('branch') or '?'})",
        f"file: {lock_path(root)}",
    )
    if st["state"] == "stale":
        ch.dim("Stale (past TTL) — safe to release: navig repo lock release")


@lock_app.command("status")
def lock_status_cmd(
    ctx: typer.Context,
    repo: str = typer.Option(None, "--repo", help="Target repo (default: current directory)"),
) -> None:
    """Show who holds the main-checkout agent lock."""
    _print_lock_status(_require_root(repo or _ctx_repo(ctx)))


@lock_app.command("release")
def lock_release_cmd(
    ctx: typer.Context,
    repo: str = typer.Option(None, "--repo", help="Target repo (default: current directory)"),
    force: bool = typer.Option(
        False,
        "--force",
        help="Release a fresh lock held by ANOTHER session (that agent may be live!)",
    ),
) -> None:
    """Release the agent lock.

    Stale locks and this session's own lock release freely; a fresh lock held by
    a DIFFERENT session needs ``--force``.
    """
    root = _require_root(repo or _ctx_repo(ctx))
    from navig import console_helper as ch

    lock = read_lock(root)
    st = lock_state(lock)
    if st["state"] == "free":
        ch.info("Lock already free.")
        return
    ours = lock_is_ours(lock)
    if st["state"] == "held" and not force and not ours:
        ch.warning(
            f"Lock is fresh (age {st.get('age_minutes', '?')}m) — another agent may be live.",
            "Re-run with --force only if you are sure that session is gone.",
        )
        raise typer.Exit(1)
    try:
        lock_path(root).unlink()
        ch.success("Lock released (it was this session's own)." if ours else "Lock released.")
    except OSError as exc:
        ch.error("Could not remove lock file.", str(exc))
        raise typer.Exit(1) from exc


# ── guard installer ──────────────────────────────────────────────────────────
#
# Installs the multi-agent repo guard (the agent-lock + session-briefing
# Claude Code hooks) into ANY git repo. Hook sources ship inside the wheel as
# the ``navig.guard`` package; this writes machine-local wiring, so it must be
# run once per machine per repo.

_GUARD_HOOKS_SUBDIR = Path(".claude") / "hooks"
_GUARD_SETTINGS_SUBDIR = Path(".claude") / "settings.json"
# Claude Code expands this in hook commands to the project root — it is what makes
# .claude/settings.json portable, and therefore committable.
_GUARD_PROJECT_DIR = "${CLAUDE_PROJECT_DIR}"
_GUARD_MATCHER = "Edit|Write|MultiEdit|NotebookEdit|Bash|PowerShell"


def _guard_interpreter() -> str:
    """Shell name of a Python interpreter available on this machine's PATH."""
    import shutil

    for name in ("python", "python3"):
        if shutil.which(name):
            return name
    return "python"


def _guard_hook_entries(root: Path) -> dict[str, dict]:
    """The three settings.json hook entries for a repo rooted at ``root``.

    Paths use Claude Code's ``${CLAUDE_PROJECT_DIR}`` placeholder (officially supported
    in hook commands; expands to the project root) instead of machine-absolute paths.

    That is the whole point: with absolute paths ``.claude/settings.json`` could not be
    committed, so every clone of a guarded repo silently had NO guard until someone
    remembered to run ``navig repo guard install``. Nobody remembers. A portable file can
    be committed once, and every clone is guarded with zero setup.

    Fails CLOSED if the placeholder ever fails to expand: python exits 2 on a missing
    file, and Claude Code treats a PreToolUse exit 2 as a BLOCK — loud, never silently
    inert.
    """
    py = _guard_interpreter()
    hooks = _GUARD_HOOKS_SUBDIR.as_posix()
    lock_script = f"{_GUARD_PROJECT_DIR}/{hooks}/agent_lock.py"
    start_script = f"{_GUARD_PROJECT_DIR}/{hooks}/session_start.py"

    def entry(command: str, timeout: int, matcher: str | None = None) -> dict:
        e: dict = {"hooks": [{"type": "command", "command": command, "timeout": timeout}]}
        if matcher:
            e = {"matcher": matcher, **e}
        return e

    return {
        "PreToolUse": entry(f'{py} "{lock_script}"', 15, _GUARD_MATCHER),
        "SessionEnd": entry(f'{py} "{lock_script}"', 15),
        "SessionStart": entry(f'{py} "{start_script}"', 30),
    }


_GUARD_MARKERS = {
    "PreToolUse": "agent_lock.py",
    "SessionEnd": "agent_lock.py",
    "SessionStart": "session_start.py",
}


def _guard_event_wired(settings: dict, event: str) -> bool:
    """True when some hook command for ``event`` already references our script."""
    marker = _GUARD_MARKERS[event]
    for entry in settings.get("hooks", {}).get(event, []) or []:
        for h in entry.get("hooks", []) or []:
            if marker in str(h.get("command", "")):
                return True
    return False


def _guard_expand(raw: str, root: Path) -> str:
    """Substitute Claude Code's project-dir placeholder the way Claude Code does."""
    return raw.replace("${CLAUDE_PROJECT_DIR}", root.as_posix()).replace(
        "$CLAUDE_PROJECT_DIR", root.as_posix()
    )


def _guard_script_state(root: Path, settings: dict, name: str) -> tuple[str, str | None]:
    """Resolve a hook script's state: ``(current|outdated|missing, path)``.

    Looks in the standard ``.claude/hooks/`` location first, then wherever the
    settings' hook commands actually point (custom wiring — e.g. the navig
    repo itself wires from ``scripts/agent-hooks/``), so a working non-standard
    install doesn't read as "missing".

    Resolves ``${CLAUDE_PROJECT_DIR}`` against *root* exactly as Claude Code does —
    without this, a portable (committed) wiring would resolve to a literal
    ``${CLAUDE_PROJECT_DIR}/…`` path, and ``guard status`` would report a perfectly
    healthy guard as "missing".
    """
    from navig.guard import template_text

    template = template_text(name)
    candidates: list[Path] = [root / _GUARD_HOOKS_SUBDIR / name]
    pattern = re.compile(r'"([^"]*' + re.escape(name) + r')"|(\S*' + re.escape(name) + r")")
    for entries in (settings.get("hooks", {}) or {}).values():
        for entry in entries or []:
            for h in entry.get("hooks", []) or []:
                m = pattern.search(str(h.get("command", "")))
                if not m:
                    continue
                raw = _guard_expand(m.group(1) or m.group(2), root)
                p = Path(raw)
                if not p.is_absolute():
                    p = root / p
                candidates.append(p)
    for cand in candidates:
        try:
            if cand.exists():
                state = "current" if cand.read_text(encoding="utf-8") == template else "outdated"
                return state, str(cand)
        except OSError:
            continue
    return "missing", None


def _guard_target_root(repo: str | None) -> Path:
    root = resolve_repo_root(repo)
    if root is None:
        from navig import console_helper as ch

        ch.error(
            "Target is not inside a git repository.",
            f"{repo or Path.cwd()} — pass --repo <path>, or set NAVIG_REPO / CLAUDE_PROJECT_DIR.",
        )
        raise typer.Exit(1)
    return root


def _guard_load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        from navig import console_helper as ch

        ch.error(f"{path} is not valid JSON - fix it first (refusing to overwrite).", str(exc))
        raise typer.Exit(1) from exc
    if not isinstance(data, dict):
        from navig import console_helper as ch

        ch.error(f"{path} must contain a JSON object.")
        raise typer.Exit(1)
    return data


def _guard_ensure_gitignore(root: Path) -> bool:
    """Ensure ``.dev/`` (lock + worktrees home) is gitignored. True if changed."""
    gi = root / ".gitignore"
    lines = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
    if any(ln.strip().rstrip("/") == ".dev" for ln in lines):
        return False
    lines += ["", "# navig repo guard (agent lock + worktrees)", ".dev/"]
    gi.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


guard_app = typer.Typer(
    help="Install the multi-agent repo guard (agent-lock + session-briefing hooks) into a repo",
    no_args_is_help=True,
)
repo_app.add_typer(guard_app, name="guard")


@guard_app.command("install")
def guard_install_cmd(
    repo: str = typer.Option(None, "--repo", help="Target repo (default: current directory)"),
) -> None:
    """Install the guard hooks + Claude Code wiring into a git repo.

    Idempotent: re-running refreshes hook scripts to the current templates and leaves
    existing wiring untouched. The wiring is PORTABLE (``${CLAUDE_PROJECT_DIR}``), so
    commit ``.claude/hooks/`` + ``.claude/settings.json`` once and every clone is guarded
    with no per-machine step.
    """
    from navig import console_helper as ch
    from navig.guard import HOOK_FILES, template_text

    root = _guard_target_root(repo)
    hooks_dir = root / _GUARD_HOOKS_SUBDIR
    hooks_dir.mkdir(parents=True, exist_ok=True)

    for name in HOOK_FILES:
        target = hooks_dir / name
        content = template_text(name)
        if target.exists() and target.read_text(encoding="utf-8") == content:
            ch.dim(f"unchanged  {_display_path(target, root)}")
            continue
        state = "updated " if target.exists() else "written "
        target.write_text(content, encoding="utf-8")
        ch.info(f"{state}  {_display_path(target, root)}")

    settings_path = root / _GUARD_SETTINGS_SUBDIR
    settings = _guard_load_settings(settings_path)
    hooks_cfg = settings.setdefault("hooks", {})
    wired_now = []
    for event, entry in _guard_hook_entries(root).items():
        if _guard_event_wired(settings, event):
            continue
        hooks_cfg.setdefault(event, []).append(entry)
        wired_now.append(event)
    if wired_now:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        ch.info(f"wired     {_display_path(settings_path, root)} ({', '.join(wired_now)})")
    else:
        ch.dim(f"unchanged  {_display_path(settings_path, root)} (already wired)")

    if _guard_ensure_gitignore(root):
        ch.info("appended  .dev/ to .gitignore (lock file + worktrees home)")

    # The wiring is portable now, so the useful warning is the OPPOSITE of the old one:
    # a gitignored settings.json means every fresh clone of this repo starts UNGUARDED,
    # which is exactly how a guard ends up protecting nobody.
    ignored = _git(["check-ignore", "-q", str(settings_path)], root).returncode == 0
    if ignored:
        ch.warning(
            ".claude/settings.json is gitignored - every fresh clone will be UNGUARDED.",
            "The wiring is portable (${CLAUDE_PROJECT_DIR}); commit it (and "
            ".claude/hooks/) so the guard travels with the repo.",
        )

    ch.success(
        "Repo guard installed.",
        "Applies to NEW Claude Code sessions in this repo (running ones may need a restart). "
        "Verify anytime: navig repo guard status",
    )


@guard_app.command("status")
def guard_status_cmd(
    repo: str = typer.Option(None, "--repo", help="Target repo (default: current directory)"),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Show whether the guard is installed and wired in a repo."""
    from navig.guard import HOOK_FILES

    root = _guard_target_root(repo)
    settings_path = root / _GUARD_SETTINGS_SUBDIR
    try:
        settings = _guard_load_settings(settings_path)
    except typer.Exit:
        settings = {}

    scripts: dict[str, str] = {}
    script_paths: dict[str, str | None] = {}
    for name in HOOK_FILES:
        state, path = _guard_script_state(root, settings, name)
        scripts[name] = state
        script_paths[name] = path

    events = {ev: _guard_event_wired(settings, ev) for ev in _GUARD_MARKERS}

    data = {
        "root": str(root),
        "scripts": scripts,
        "script_paths": script_paths,
        "wired": events,
        "dev_ignored": _git(["check-ignore", "-q", str(root / ".dev" / "x")], root).returncode == 0,
        "lock": lock_state(read_lock(root)),
    }
    if json_out:
        typer.echo(json.dumps(data, indent=2))
        return

    from navig import console_helper as ch

    table = ch.create_table(
        f"Repo guard - {root.name}",
        [
            {"name": "Component", "style": "cyan"},
            {"name": "State", "style": "white"},
        ],
    )
    glyph = {
        "current": "[green]● current[/green]",
        "outdated": "[yellow]◐ outdated - re-run: navig repo guard install[/yellow]",
        "missing": "[red]○ missing[/red]",
    }
    standard_dir = (root / _GUARD_HOOKS_SUBDIR).resolve()
    for name, state in scripts.items():
        cell = glyph[state]
        path = script_paths.get(name)
        if path and Path(path).resolve().parent != standard_dir:
            cell += f" [dim]({_display_path(path, root)})[/dim]"
        table.add_row(f"hook: {name}", cell)
    for ev, ok in events.items():
        table.add_row(
            f"settings hook: {ev}",
            "[green]● wired[/green]" if ok else "[red]○ not wired[/red]",
        )
    table.add_row(
        ".dev/ gitignored",
        "[green]● yes[/green]" if data["dev_ignored"] else "[yellow]◐ no[/yellow]",
    )
    lk = data["lock"]
    table.add_row("agent lock", lk["state"])
    ch.print_table(table)

    installed = all(s == "current" for s in scripts.values()) and all(events.values())
    if not installed:
        ch.dim(
            "install or refresh with: navig repo guard install"
            + (f" --repo {repo}" if repo else "")
        )


@guard_app.command("uninstall")
def guard_uninstall_cmd(
    repo: str = typer.Option(None, "--repo", help="Target repo (default: current directory)"),
) -> None:
    """Remove the guard wiring (and hook scripts, when unmodified) from a repo."""
    from navig import console_helper as ch
    from navig.guard import HOOK_FILES, template_text

    root = _guard_target_root(repo)
    settings_path = root / _GUARD_SETTINGS_SUBDIR
    settings = _guard_load_settings(settings_path)
    hooks_cfg = settings.get("hooks", {})
    removed = []
    for event, marker in _GUARD_MARKERS.items():
        entries = hooks_cfg.get(event)
        if not entries:
            continue
        kept = [
            e
            for e in entries
            if not any(marker in str(h.get("command", "")) for h in e.get("hooks", []) or [])
        ]
        if len(kept) != len(entries):
            removed.append(event)
            if kept:
                hooks_cfg[event] = kept
            else:
                hooks_cfg.pop(event)
    if removed:
        if not hooks_cfg:
            settings.pop("hooks", None)
        settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        ch.info(f"unwired   {', '.join(removed)} in {_display_path(settings_path, root)}")

    for name in HOOK_FILES:
        target = root / _GUARD_HOOKS_SUBDIR / name
        if not target.exists():
            continue
        if target.read_text(encoding="utf-8") == template_text(name):
            target.unlink()
            ch.info(f"removed   {_display_path(target, root)}")
        else:
            ch.warning(f"kept {_display_path(target, root)} - locally modified, remove manually.")

    lock_file = lock_path(root)
    if lock_file.exists():
        ch.dim(f"note: lock file left in place ({_display_path(lock_file, root)})")
    ch.success("Repo guard uninstalled.", "Applies to new Claude Code sessions.")
