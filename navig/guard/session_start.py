#!/usr/bin/env python3
"""Claude Code SessionStart hook — stale-work + lock briefing for this checkout.

Prints a compact repo-guard briefing to stdout (which Claude Code adds to the
session's context): current branch, agent-lock holder, leftover worktrees
(flagging forbidden sibling checkouts outside the repo), a cross-worktree
merge radar (which worktree pairs would conflict, dirty state included),
branches not merged into the REMOTE default branch, a count of branches
PROVABLY already on it (ancestor tip or identical tree — delete them with
``navig repo sweep --yes``), and stashes. Every session starts by KNOWING
what other agents left behind and where collisions are brewing, instead of
discovering it at merge time.

Stdlib + git only (never REQUIRES navig — must work even when the venv is
broken; navig's spawn flags are used when importable and skipped when not).
Fail-open: any error prints nothing and exits 0. Output is pure
ASCII — the Windows hook pipe garbles non-ASCII. Rich human version:
``navig repo stale``. Wiring: see scripts/agent-hooks/README.md.

Bounded: the hook returns within FOREGROUND_BUDGET_S. The briefing runs in a
detached ``--refresh`` child that writes .dev/cache/session-briefing.txt; the
hook prints it if the child finishes in time, else the last cached copy with
its age. Measured 2026-10-03: on a loaded box with 16 worktrees a full briefing
took 366 s, so the hook hit its 30 s ceiling on EVERY session start — no
briefing, plus 30 s added to a startup the VS Code extension caps at 60 s
("Subprocess initialization did not complete within 60000ms").
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def repo_root() -> Path:
    """The MAIN working tree's root — even when this hook runs from a linked worktree.

    A worktree's `.git` is a FILE (a gitdir pointer), and `.exists()` is true for a file,
    so the walk below stopped at the worktree and called it the repo. Every session opened
    in `.dev/worktrees/<x>` then got a briefing listing the main checkout and every sibling
    worktree as "SIBLING checkout OUTSIDE the repo (forbidden)" — the one line meant to
    catch a real rule break, crying wolf on every legitimate layout. `navig repo` fixed the
    same thing in #1443 with `--git-common-dir`. Here the `.git` FILE itself is read — it
    says `gitdir: <main>/.git/worktrees/<name>` — so no subprocess is needed, and the shape
    is identical to `agent_lock.repo_root`, which runs on every tool call.
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
                if not gitdir.is_absolute():
                    # Relative to the `.git` file, not to wherever the hook's cwd drifted.
                    gitdir = (dot.parent / gitdir).resolve()
                # <main>/.git/worktrees/<name>  ->  <main>
                if gitdir.parent.name == "worktrees" and gitdir.parent.parent.name == ".git":
                    return gitdir.parent.parent.parent
            return parent  # some other gitdir shape (a submodule): this tree is the root
    return here.parents[1]


def _spawn_kwargs() -> dict:
    """navig's console-suppressing spawn flags — OPTIONAL, because this hook must not need navig.

    The contract at the top of this file is "stdlib + git only, must work even
    when the venv is broken", and it was broken: this import sat unguarded, so
    a checkout whose navig would not import raised ImportError past the
    ``except`` below, ``briefing()`` died, and ``main()`` swallowed it — the hook
    exited 0 having printed NOTHING. Measured: 0 bytes of output with navig
    un-importable. A briefing that vanishes in exactly the broken-environment
    case it exists for is the worst shape of failure this file can have.
    Without navig the only cost is a possible console flash on Windows.
    """
    try:
        from navig.platform.process import spawn_kwargs  # noqa: PLC0415

        return spawn_kwargs()
    except Exception:  # noqa: BLE001 — a broken venv must degrade, never silence the briefing
        return {}


#: A worktree this many commits behind origin/<default> has missed days of merges (main
#: moves ~20 a day here). Mirrors WORKTREE_BEHIND_WARN in navig/commands/repo.py — this hook
#: must not import navig, so the number is repeated and a test pins the two together.
WORKTREE_BEHIND_WARN = 50


def _git_rc(args: list[str], cwd: Path | str) -> tuple[int, str]:
    try:
        res = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace", **_spawn_kwargs()
        )
    except (OSError, subprocess.SubprocessError):
        return -1, ""
    return res.returncode, res.stdout


def _git(args: list[str], cwd: Path) -> str | None:
    rc, out = _git_rc(args, cwd)
    return out if rc == 0 else None


def _is_within(child: str, parent: Path) -> bool:
    c = os.path.normcase(str(Path(child)))
    p = os.path.normcase(str(parent))
    return c == p or c.startswith(p + os.sep)


def conflict_lines(root: Path) -> list[str]:
    """Cross-worktree merge radar: one line when clean, one per colliding pair.

    Same in-memory simulation as ``navig repo conflicts`` (git merge-tree,
    git >= 2.38), including each worktree's uncommitted tracked changes via
    ``git stash create`` (writes objects only; never touches any tree).
    Empty when the repo has fewer than two worktrees.
    """
    from itertools import combinations

    rc, out = _git_rc(["worktree", "list", "--porcelain"], root)
    if rc != 0:
        return []
    entries: list[tuple[str, str]] = []  # (path, HEAD sha)
    path = head = None
    for line in [*out.splitlines(), ""]:
        if line.startswith("worktree "):
            path = line[len("worktree "):].strip()
        elif line.startswith("HEAD "):
            head = line[len("HEAD "):].strip()
        elif not line.strip():
            if path and head:
                entries.append((path, head))
            path = head = None
    if len(entries) < 2:
        return []

    sides: list[tuple[str, str]] = []  # (label, committish incl. dirty state)
    for p, h in entries:
        rc2, sha = _git_rc(["stash", "create"], p)
        sides.append((Path(p).name, sha.strip() if rc2 == 0 and sha.strip() else h))

    lines: list[str] = []
    pairs = list(combinations(sides, 2))
    for (label_a, ref_a), (label_b, ref_b) in pairs:
        rc3, _ = _git_rc(["merge-base", ref_a, ref_b], root)
        if rc3 != 0:
            continue  # unrelated histories - nothing meaningful to simulate
        rc4, merged = _git_rc(
            ["merge-tree", "--write-tree", "--name-only", "--no-messages", ref_a, ref_b], root
        )
        if rc4 != 1:
            continue  # 0 = clean; anything else = simulation unavailable
        files = list(dict.fromkeys(ln.strip() for ln in merged.splitlines()[1:] if ln.strip()))
        shown = ", ".join(files[:4]) + (" ..." if len(files) > 4 else "")
        lines.append(
            f"[!] merge conflict brewing: {label_a} <-> {label_b} ({len(files)} file(s): {shown})"
        )
    if not lines:
        return [f"* cross-worktree merge check: {len(pairs)} pair(s), all clean"]
    return lines


# Keep in sync with agent_lock.py / core/navig/commands/repo.py — the one number that
# decides whether a present lock means "go to a worktree" or "the holder is gone".
LOCK_TTL_MINUTES = 60


#: How far back the lost-work radar looks. Unreachable reflog entries expire after
#: gc.reflogExpireUnreachable (30 days by default); a week leaves time to act.
LOST_WORK_DAYS = 7
#: Lines the radar prints at most; the count says how many more there are.
LOST_WORK_SHOWN = 5
#: Candidates judged per briefing -- each costs a handful of batched git calls.
LOST_WORK_JUDGED = 12
#: Same-subject reachable commits compared per candidate (by patch id).
LOST_WORK_TWINS = 20
#: The subjects git gives a stash's commits -- a dropped stash, not lost work.
_STASH_SHAPED = re.compile(r"(?:WIP on|index on|untracked files on) |On [^\s:]+: ")
_SQUASH_SUFFIX = re.compile(r" \(#\d+\)$")


def _ascii(text: str) -> str:
    return text.encode("ascii", "replace").decode("ascii")


#: Basenames too common to prove a move: an unrelated ``index.ts`` elsewhere on the ref
#: says nothing about the commit's file. Losing a generically-named file stays reported.
_GENERIC_NAMES = frozenset({
    "index.ts", "index.tsx", "index.js", "index.mjs", "index.md",
    "types.ts", "types.tsx", "utils.ts", "constants.ts", "helpers.ts",
    "config.ts", "entries.ts", "styles.css", "package.json", "readme.md",
})

#: ref -> basename -> paths, cached for the hook's one run. Keyed by root AND ref:
#: tests create many scratch repos that all name their branch ``main``.
_NAME_CACHE: dict[tuple[str, str], dict[str, list[str]]] = {}


def _paths_by_basename(root: Path, ref: str) -> dict[str, list[str]]:
    key = (str(root), ref)
    if key not in _NAME_CACHE:
        by_name: dict[str, list[str]] = {}
        for p in (_git(["ls-tree", "-r", "--name-only", ref], root) or "").splitlines():
            if p:
                by_name.setdefault(p.rsplit("/", 1)[-1].lower(), []).append(p)
        _NAME_CACHE[key] = by_name
    return _NAME_CACHE[key]


def _row_superseded(
    status: str,
    path: str,
    *,
    on_ref: dict[str, str],
    since: set[str],
    in_commit: dict[str, str],
    twin_of: dict[str, list[str]],
    twin_blobs: dict[str, str],
    touched_twins: set[str],
    hunks_on_ref: bool,
) -> bool:
    """One row of the supersession test (see ``_superseded``). Keyword-only so the
    caller's loop variables are bound by value -- a closure over them is the classic
    loop-capture bug (ruff B023)."""
    if status == "D":
        return path not in on_ref
    if path in on_ref:
        if on_ref[path] == in_commit.get(path):
            return True
        if status == "A":
            return path in since  # the file it created came into being again: a redo
        # A file that already existed: any later edit of it proves nothing -- an
        # unrelated comment fix hid a lost function (review F2952). Its hunks must be.
        return hunks_on_ref
    mine = in_commit.get(path)
    return any(twin_blobs.get(t) == mine or t in touched_twins for t in twin_of.get(path, []))


def _superseded(root: Path, refs: list[str], sha: str, when: str) -> bool:
    """Is this commit's work on one of ``refs`` by another road (a squash, a redo, a move)?

    True when, on some ref, every file it touched is: the identical blob, a file it
    CREATED that ref has changed since the commit (a redo of the same work), a file it
    edited whose hunks ref already carries (``_hunks_on_ref`` -- a later edit of the
    same file is not evidence by itself), or -- for a deletion -- already gone. For a file the commit ADDED that ref lacks, the ref may simply
    have received it at a new path: this repo renames paths as a workflow (a manual
    moved blackbook/ -> cookbook/ with edits -- 59 of its paths went absent and a path
    check alone called the work "lost"; 14 of the 59 are provable as this move). What a
    reader must judge stays reported on purpose: a test whose RULE a renamed module
    re-asserted is provable by neither path, name nor blob, and guessing there would
    hide a real loss the next time. An absent added file counts as
    superseded when a same-named file exists on ref AND that path was created or
    changed there after this commit (a rename arriving later) or carries our exact
    blob (a verbatim move). A generically-named file never counts: an unrelated
    ``index.ts`` must not mask a real loss.

    The commit's file list is read once; each ref costs a handful of batched calls
    whatever the commit's size (a 267-file rename was measured).
    """
    rows = [
        r.split("\t", 1)
        for r in (_git(["diff", "--name-status", "--no-renames", f"{sha}^", sha], root) or "").splitlines()
        if "\t" in r
    ]
    if not when or not rows:
        return False
    paths = [p for _, p in rows]

    def blob_map(ref: str, wanted: list[str]) -> dict[str, str]:
        if not wanted:
            return {}
        out = _git(["ls-tree", "-r", ref, "--", *wanted], root) or ""
        found: dict[str, str] = {}
        for line in out.splitlines():
            meta, _, path = line.partition("\t")
            parts = meta.split()
            if len(parts) == 3:
                found[path] = parts[2]
        return found

    in_commit = blob_map(sha, paths)
    added = [p for s, p in rows if s != "D"]
    for ref in refs:
        on_ref = blob_map(ref, paths)
        since = set(
            (_git(["log", f"--since={when}", "--format=", "--name-only", ref, "--", *paths], root) or "")
            .split("\n")
        )

        # Rename twins: absent added files, matched by a distinctive basename on ref.
        absent = [p for p in added if p not in on_ref]
        by_name = _paths_by_basename(root, ref) if absent else {}
        twin_of: dict[str, list[str]] = {}
        for p in absent:
            name = p.rsplit("/", 1)[-1].lower()
            if name not in _GENERIC_NAMES:
                twin_of[p] = by_name.get(name, [])
        all_twins = [t for twins in twin_of.values() for t in twins]
        twin_blobs = blob_map(ref, all_twins)
        touched_twins = (
            set(
                (_git(["log", f"--since={when}", "--format=", "--name-only", ref, "--", *all_twins], root) or "")
                .split("\n")
            )
            if all_twins
            else set()
        )

        edited = [p for s, p in rows if s == "M" and p in on_ref and on_ref[p] != in_commit.get(p)]
        hunks_on_ref = bool(edited) and _hunks_on_ref(root, ref, sha, edited)

        if all(
            _row_superseded(
                s,
                p,
                on_ref=on_ref,
                since=since,
                in_commit=in_commit,
                twin_of=twin_of,
                twin_blobs=twin_blobs,
                touched_twins=touched_twins,
                hunks_on_ref=hunks_on_ref,
            )
            for s, p in rows
        ):
            return True
    return False


def _hunks_on_ref(root: Path, ref: str, sha: str, paths: list[str]) -> bool:
    """Are the changes ``sha`` made to ``paths`` already in ``ref``'s copies of them?

    Proven the way git would: the commit's patch, reversed, applies to ref's blobs in a
    throwaway index that holds only those paths -- no work tree is touched and the real
    index is never read. False on any doubt (a reported commit is a look, not a loss).
    """
    import tempfile

    def run(args: list[str], data: bytes | None = None, env: dict | None = None) -> bytes | None:
        # Bytes, not text: a text pipe writes LF as CRLF on Windows and reads CRLF back
        # as LF, and a patch changed on the way never applies.
        try:
            res = subprocess.run(
                ["git", *args], cwd=str(root), input=data, capture_output=True, timeout=10,
                env=env, **_spawn_kwargs()
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return res.stdout if res.returncode == 0 else None

    patch = run(["diff", "--no-renames", "--full-index", "--binary", f"{sha}^", sha, "--", *paths])
    listing = run(["ls-tree", "-r", ref, "--", *paths])
    if not patch or not listing:
        return False
    with tempfile.TemporaryDirectory(prefix="navig-briefing-") as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        if run(["update-index", "--index-info"], listing, env) is None:
            return False
        return run(["apply", "--cached", "--check", "-R"], patch, env) is not None


def _git_stdin(args: list[str], cwd: Path, text: str) -> str | None:
    """`git <args>` fed ``text`` on stdin -- for revision lists longer than a Windows
    command line (32 767 chars) takes. None on any failure."""
    try:
        res = subprocess.run(
            ["git", *args], cwd=str(cwd), input=text, capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace", **_spawn_kwargs()
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return res.stdout if res.returncode == 0 else None


def _patch_ids(root: Path, shas: list[str]) -> dict[str, str]:
    """sha -> stable patch id, for commits that have a patch (an empty one has none)."""
    if not shas:
        return {}
    patches = _git(["log", "-p", "--no-walk=unsorted", "--no-color", "--no-renames", *dict.fromkeys(shas)], root)
    out = _git_stdin(["patch-id", "--stable"], root, patches) if patches else None
    ids: dict[str, str] = {}
    for line in (out or "").splitlines():
        pid, _, sha = line.partition(" ")
        if pid and sha:
            ids[sha] = pid
    return ids


def lost_work_lines(root: Path, base: str) -> list[str]:
    """Commits from the last LOST_WORK_DAYS that NO ref holds -- kept only by a reflog.

    On 2026-10-13 a reset dropped seven unpushed Schema commits; they were found a day
    later only because someone read the reflog by hand. The same reflog held two more
    nobody had noticed (a lost test file among them) until this radar read it three
    days on. The agent-lock hook now refuses such resets; this shows what was already
    dropped, before gc expires it.

    Filters, measured in a week of real reflogs (Schema 177 raw orphans -> 2, navig
    479 -> 3): merges are skipped; a commit whose SUBJECT is on a ref (squash suffix
    "(#123)" ignored) with the identical patch is a rebased copy (a same-subject commit
    with other content is judged like any other); a stash-shaped commit is a dropped
    stash; and a commit whose files are all on ``base`` -- at their path, at a RENAMED
    path (``_superseded``), or on the current tip of the ref whose reflog held it, a
    branch its own session rewrote -- is superseded.

    Cost: the walk starts from the last LOST_WORK_DAYS of reflog TIPS, not every reflog
    entry ever (``rev-list --reflog`` walked 90 days of entries: 4.3 s vs 1.6 s on
    navig, identical result).
    """
    # One pass over every reflog (worktrees included): the recent tips, and which ref
    # last held each ("feat/x@{1}", "worktrees/w/HEAD@{8}").
    homes: dict[str, str] = {}
    for line in (
        _git(["log", "-g", "--all", f"--since={LOST_WORK_DAYS}.days.ago", "--format=%H %gD"], root) or ""
    ).splitlines():
        full, _, selector = line.partition(" ")
        ref = selector.split("@{", 1)[0]
        if full and ref and ref != "refs/stash":
            homes.setdefault(full, ref)
    if not homes:
        return []
    out = _git_stdin(
        ["rev-list", "--no-merges", f"--since={LOST_WORK_DAYS}.days.ago", "--stdin", "--not", "--all"],
        root,
        "\n".join(homes) + "\n",
    )
    shas = (out or "").split()
    if not shas:
        return []
    # A squash-merge renames the subject to "<subject> (#123)": compare without it.
    held: dict[str, list[str]] = {}
    for line in (
        _git(["log", "--all", f"--since={3 * LOST_WORK_DAYS}.days.ago", "--no-merges", "--format=%H\t%s"], root)
        or ""
    ).splitlines():
        sha, _, subject = line.partition("\t")
        held.setdefault(_SQUASH_SUFFIX.sub("", subject), []).append(sha)
    shown = _git(["log", "--no-walk=sorted", "--format=%H\t%h\t%cI\t%cr\t%s", *shas[:300]], root) or ""
    rows = [(line.split("\t", 4) + [""] * 4)[:5] for line in shown.splitlines()]
    rows = [r for r in rows if not _STASH_SHAPED.match(r[4])]
    # A shared subject only NOMINATES a rebased/amended copy: `fix tests` or `work` names
    # unrelated changes too, and the subject alone hid a real loss (review F2953). The
    # copy is confirmed by an identical patch; anything else is judged on its content.
    nominated = {r[0]: held[r[4]][:LOST_WORK_TWINS] for r in rows if r[4] in held}
    pids = _patch_ids(root, [*nominated, *(t for ts in nominated.values() for t in ts)])
    candidates = []
    for full, short, when, age, subject in rows:
        if full in nominated and full in pids and any(
            pids.get(t) == pids[full] for t in nominated[full]
        ):
            continue
        candidates.append((short, when, age, subject, homes.get(full)))
    lost = [
        (short, age, subject)
        for short, when, age, subject, home in candidates[:LOST_WORK_JUDGED]
        if not _superseded(root, [base, *([home] if home else [])], short, when)
    ]
    if not lost:
        return []
    lines = [
        f"[!] {len(lost)}{'+' if len(candidates) > LOST_WORK_JUDGED else ''} commit(s) from the "
        f"last {LOST_WORK_DAYS} days are held by NO branch, tag or remote and are not on {base} "
        "- only the reflog keeps them, and it expires. Look, then rescue or let go:"
    ]
    for short, age, subject in lost[:LOST_WORK_SHOWN]:
        lines.append(f"    {short} ({age}) {_ascii(subject)[:70]} - rescue: git branch rescue/{short} {short}")
    if len(lost) > LOST_WORK_SHOWN:
        lines.append(f"    ... and {len(lost) - LOST_WORK_SHOWN} more: git rev-list --reflog --no-merges --not --all")
    return lines


def _lock_line(lock: dict, sid: str, now: datetime | None = None) -> str:
    """One line that says what to DO about a present lock, not just that it exists.

    "another session may be active" was the whole message before, for a lock touched
    seven minutes ago and one abandoned three hours ago alike — so the reader had to run
    `navig repo lock status` to learn which. The age and the TTL verdict are what an
    agent needs before its first edit: LIVE means work in a worktree; EXPIRED means the
    holder is gone and the next edit takes over. Same rule agent_lock.py enforces.
    """
    branch = lock.get("branch", "?")
    try:
        updated = datetime.fromisoformat(str(lock.get("updated_at", "")))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        age_min = ((now or datetime.now(timezone.utc)) - updated).total_seconds() / 60
    except ValueError:
        return (
            f"[!] agent lock present (session {sid}..., branch {branch}) with an unreadable "
            f"timestamp - treat it as dead; the next edit takes over. Details: navig repo lock"
        )
    age = f"{age_min / 60:.1f}h" if age_min >= 90 else f"{age_min:.0f}m"
    if age_min > LOCK_TTL_MINUTES:
        return (
            f"[!] agent lock EXPIRED (session {sid}..., branch {branch}, last touched {age} ago; "
            f"TTL {LOCK_TTL_MINUTES}m) - the holder is gone; your first edit takes the lock over."
        )
    return (
        f"[!] agent lock LIVE (session {sid}..., branch {branch}, touched {age} ago) - another "
        f"session is working in this folder: edit ONLY in a worktree (navig repo new <slug>), "
        f"or wait (expires after {LOCK_TTL_MINUTES}m idle). Details: navig repo lock status"
    )


# Branches an agent makes to SET WORK ASIDE before resetting a checkout
# ("kept aside before it is brought current"). Unlike a feature branch, nobody
# is going to open a PR for one: if the session that made it ends, the work is
# simply gone from main. Measured 2026-09-28 (Schema): ~240 files of passport,
# tablet and zone work sat on wip/main-checkout-2026-09-28 for a day, listed
# as one quiet "* branch not merged" line among routine ones, while every
# session built on a main that lacked it. So this one is loud and sized.
SET_ASIDE_PREFIXES = ("wip/", "backup/", "stash/")


def _set_aside_line(root: Path, base: str, name: str) -> str:
    """A loud, sized line for a set-aside branch the base does not contain."""
    ahead = (_git(["rev-list", "--count", f"{base}..{name}"], root) or "?").strip()
    files_out = _git(["diff", "--name-only", f"{base}...{name}"], root) or ""
    n_files = len([f for f in files_out.splitlines() if f.strip()])
    when = (_git(["log", "-1", "--format=%cr", name], root) or "").strip()
    return (
        f"[!] SET-ASIDE WORK not restored: {name} ({ahead} commit(s), {n_files} file(s)"
        f"{', ' + when if when else ''}) - {base} does NOT have it. Restore it "
        f"(git cherry-pick --no-commit {base}..{name}, resolve, commit) before building on "
        f"{base}, or delete it deliberately once proven redundant."
    )


def briefing(root: Path) -> str:
    lines: list[str] = []

    branch = (_git(["branch", "--show-current"], root) or "").strip()
    default = "main"
    head_ref = (_git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], root) or "").strip()
    if head_ref:
        default = head_ref.split("/", 1)[-1]
    if branch and branch != default:
        lines.append(
            f"[!] main checkout is on branch '{branch}' (not {default}) - a previous "
            f"session may not have finished its merge-and-delete contract."
        )

    # Local default behind the remote — the staleness at the root of this whole class.
    # Agents merge through GitHub, so origin/<default> advances while the local branch
    # sits still; a session started here then runs stale code and judges "merged" against
    # a ref 100+ commits old (measured). Uses the tracking ref already on disk — the hook
    # fetches nothing, so this is free and offline (a stale count is better than none).
    if _git(["rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{default}"], root) is not None:
        rc, out = _git_rc(["rev-list", "--count", f"{default}..origin/{default}"], root)
        behind = out.strip() if rc == 0 else ""
        if behind.isdigit() and int(behind) > 0:
            lines.append(
                f"[!] local {default} is {behind} commit(s) behind origin/{default} - "
                f"a session started here runs stale code; `git -C . checkout {default} && "
                f"git pull --ff-only` (or work from a fresh `navig repo new`)."
            )

    lock_file = root / ".dev" / "agent.lock"
    if lock_file.exists():
        try:
            lock = json.loads(lock_file.read_text(encoding="utf-8"))
            sid = str(lock.get("session_id", "?"))[:8]
            lines.append(_lock_line(lock, sid))
        except (OSError, ValueError):
            lines.append("[!] agent lock file present but unreadable - check: navig repo lock")

    finished_wts: list[str] = []
    out = _git(["worktree", "list", "--porcelain"], root)
    if out:
        paths: list[str] = []
        # git locks a worktree "initializing" for the length of `worktree add` and clears
        # it on the way out, so one still wearing that lock belongs to an add that DIED -
        # a partial checkout `git worktree list` shows as a normal worktree. An agent
        # handed that path would edit inside a tree missing half its files.
        dead_adds: set[str] = set()
        heads: dict[str, str] = {}
        branches: dict[str, str] = {}
        for line in out.splitlines():
            if line.startswith("worktree "):
                paths.append(line[len("worktree "):].strip())
            elif line.startswith("HEAD ") and paths:
                heads[paths[-1]] = line[len("HEAD "):].strip()
            elif line.startswith("branch refs/heads/") and paths:
                branches[paths[-1]] = line[len("branch refs/heads/"):].strip()
            elif line == "locked initializing" and paths:
                dead_adds.add(paths[-1])
        # How far behind origin/<default> each worktree's base is. "Last commit 5 days
        # ago" says when it was touched; this says how much of main it has never seen —
        # every safety fix merged since included. Measured 2026-09-19: two worktrees sat
        # 900 and 1,178 commits behind, still running the pre-#1447/#1459 scripts that
        # killed other sessions' processes. Free and offline: the tracking ref is on disk.
        remote_default = f"refs/remotes/origin/{default}"
        has_remote = _git(["rev-parse", "--verify", "--quiet", remote_default], root) is not None
        for p in paths:
            if os.path.normcase(p) == os.path.normcase(str(root)):
                continue
            behind_note = ""
            head = heads.get(p)
            if has_remote and head:
                rc_mb, mb = _git_rc(["merge-base", head, remote_default], root)
                if rc_mb == 0 and mb.strip():
                    rc_n, n = _git_rc(["rev-list", "--count", f"{mb.strip()}..{remote_default}"], root)
                    if rc_n == 0 and n.strip().isdigit() and int(n.strip()) >= WORKTREE_BEHIND_WARN:
                        # Finished, or merely stale? A worktree whose HEAD is already an
                        # ancestor of origin/<default> AND whose tree is clean has no work
                        # that is not on main — `navig repo sweep --yes` removes exactly
                        # those (same proof, same threshold). Anything else just needs a
                        # rebase. Measured before this line existed: one such worktree sat
                        # 904 commits behind for eight weeks, listed every session as
                        # "extra worktree" and nothing more.
                        rc_anc, _ = _git_rc(["merge-base", "--is-ancestor", head, remote_default], root)
                        rc_st, st = _git_rc(["status", "--porcelain"], Path(p))
                        if rc_anc == 0 and rc_st == 0 and not st.strip():
                            finished_wts.append(os.path.basename(p))
                            behind_note = (
                                f" - {n.strip()} behind origin/{default}, every commit already on it, clean: "
                                f"FINISHED - remove: navig repo sweep --yes"
                            )
                        else:
                            behind_note = (
                                f" - [!] {n.strip()} commit(s) behind origin/{default}; rebase before "
                                f"more work lands here (`git -C \"{p}\" rebase origin/{default}`)"
                            )
            marker = (
                " - [!] SIBLING checkout OUTSIDE the repo (forbidden; should live in .dev/worktrees/)"
                if not _is_within(p, root)
                else ""
            )
            if branches.get(p) == default:
                # `gh pr merge --delete-branch` run inside a worktree switches it to the
                # default branch; the main checkout then cannot `checkout <default>` at all
                # ("already used by worktree"). Measured twice in one afternoon.
                marker += (
                    f" - [!] holds `{default}`, so the main checkout cannot check it out"
                    f" - if finished: navig repo sweep --yes"
                )
            if p in dead_adds:
                marker += (
                    " - [!] a `worktree add` DIED here (locked: initializing; partial checkout)"
                    f" - clear: navig repo remove {os.path.basename(p)} --force"
                )
            lines.append(f"* extra worktree: {p}{marker}{behind_note}")

        # Orphaned worktree dirs: physical dirs under .dev/worktrees that git no
        # longer tracks (git worktree remove often can't delete them on Windows),
        # so they pile up unseen by `git worktree list`. Surface a count so the
        # pile does not grow silently; clean it with: navig repo prune.
        registered = {os.path.normcase(pp) for pp in paths}
        try:
            orphans = [
                d
                for d in (root / ".dev" / "worktrees").iterdir()
                if d.is_dir() and os.path.normcase(str(d)) not in registered
            ]
        except OSError:
            orphans = []
        if orphans:
            lines.append(
                f"* {len(orphans)} orphaned worktree dir(s) in .dev/worktrees "
                "(untracked by git) - clean: navig repo prune"
            )

    lines.extend(conflict_lines(root))

    # Changelog fragments waiting to be folded in (core/changelog.d/, this repo only —
    # elsewhere the dir does not exist and nothing is said). A pile that nobody sees
    # grows until a release; one line keeps it visible and names the command.
    try:
        frag_dir = root / "core" / "changelog.d"
        pending = [
            p.name for p in frag_dir.iterdir()
            if p.is_file() and p.suffix == ".md" and p.name != "README.md"
        ] if frag_dir.is_dir() else []
    except OSError:
        pending = []
    if pending:
        lines.append(
            f"* {len(pending)} changelog fragment(s) awaiting assembly in core/changelog.d "
            "- fold in: npm run changelog:assemble"
        )

    # "Merged" is judged against the REMOTE default when one exists. The local
    # branch is exactly the ref that goes stale in a shared checkout — agents
    # merge through GitHub, so origin/main advances while local main does not
    # (measured 107 PRs behind). Against a stale base, every recently merged
    # branch reads as unmerged and this briefing cries wolf. Mirrors
    # navig.commands.repo.merge_base_ref, which this hook may not import.
    base = default
    rc, _ = _git_rc(["rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{default}"], root)
    if rc == 0:
        base = f"origin/{default}"

    # Branches a worktree holds are protected from the sweep and must not be
    # counted as sweepable — mirrors the sweep's own PROTECTED set.
    held: set[str] = {default}
    wt_out = _git(["worktree", "list", "--porcelain"], root) or ""
    for line in wt_out.splitlines():
        if line.startswith("branch refs/heads/"):
            held.add(line[len("branch refs/heads/"):].strip())

    # Provably redundant: tip already an ancestor of the base. ONE git call.
    sweepable: list[str] = []
    out = _git(["branch", "--merged", base, "--format=%(refname:short)"], root)
    if out:
        sweepable = [n.strip() for n in out.splitlines() if n.strip() and n.strip() not in held]

    base_tree = (_git(["rev-parse", f"{base}^{{tree}}"], root) or "").strip()
    out = _git(
        ["branch", "--no-merged", base, "--format=%(refname:short)|%(upstream:track)"], root
    )
    if out:
        for line in out.splitlines():
            if not line.strip():
                continue
            name, _, track = line.partition("|")
            # The second offline proof: a squash-merged branch has commits the
            # base lacks and a tree the base already has. Ancestry cannot see it.
            if (
                base_tree
                and name not in held
                and (_git(["rev-parse", f"{name}^{{tree}}"], root) or "").strip() == base_tree
            ):
                sweepable.append(name)
                continue
            if name.startswith(SET_ASIDE_PREFIXES):
                lines.append(_set_aside_line(root, base, name))
                continue
            gone = " (remote gone - merged remotely? verify then delete)" if "gone" in track else ""
            lines.append(f"* branch not merged into {base}: {name}{gone}")

    if sweepable:
        # Not a hedge: these are PROVEN on the base by ancestry or identical
        # tree, the two proofs that need no network. The third (a merged PR)
        # needs gh and is the sweep's job, not a session-start hook's.
        lines.append(
            f"* {len(sweepable)} branch(es) provably already on {base} "
            f"({', '.join(sweepable[:4])}{', ...' if len(sweepable) > 4 else ''}) "
            "- delete: navig repo sweep --yes"
        )
    if finished_wts:
        lines.append(
            f"* {len(finished_wts)} finished worktree(s) - branch provably on {base}, clean, "
            f">= {WORKTREE_BEHIND_WARN} behind ({', '.join(finished_wts[:4])}"
            f"{', ...' if len(finished_wts) > 4 else ''}) - remove: navig repo sweep --yes"
        )

    lines.extend(lost_work_lines(root, base))

    out = _git(["stash", "list", "--format=%gd | %cr | %gs"], root)
    if out:
        for line in out.splitlines():
            if line.strip():
                lines.append(f"* stash: {line.strip()}")

    if not lines:
        return (
            f"[repo-guard] clean - on {branch or default} - lock free - "
            f"no stale worktrees/branches/stashes"
        )
    header = (
        "[repo-guard] stale-work briefing "
        "(details: navig repo stale - conflicts: navig repo conflicts)"
    )
    return "\n".join([header, *lines])


#: How long the hook waits for a fresh briefing before falling back to the cache.
FOREGROUND_BUDGET_S = 6.0
#: A refresh lock older than this belongs to a dead refresher and is ignored.
REFRESH_LOCK_STALE_S = 15 * 60
CACHE_NAME = "session-briefing.txt"


def _cache_path(root: Path) -> Path:
    return root / ".dev" / "cache" / CACHE_NAME


def _write_cache(root: Path, text: str) -> None:
    """Atomic: a reader never sees a half-written briefing."""
    path = _cache_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="ascii", errors="replace")
    os.replace(tmp, path)


def _read_cache(root: Path) -> tuple[str, float] | None:
    """(text, mtime) of the cached briefing, or None."""
    path = _cache_path(root)
    try:
        return path.read_text(encoding="ascii", errors="replace"), path.stat().st_mtime
    except OSError:
        return None


def _age(seconds: float) -> str:
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 90 * 60:
        return f"{int(seconds // 60)}m"
    return f"{seconds / 3600:.1f}h"


def _cached_with_age(text: str, mtime: float, now: float) -> str:
    """Mark a cached briefing as such on its first line — it may be out of date."""
    first, _, rest = text.partition("\n")
    note = f" [as of {_age(max(now - mtime, 0))} ago; refreshing in the background]"
    return first + note + ("\n" + rest if rest else "")


def _claim_refresh(root: Path) -> str | None:
    """One refresher at a time: ten sessions opening together must not start ten
    full briefings. A lock older than REFRESH_LOCK_STALE_S is a dead refresher's.

    Returns the owner token written into the lock (None when another refresher holds
    it). Only that token releases the lock: a slow refresher whose lock was reclaimed
    as stale must not delete its successor's when it finally ends (review F2485).
    """
    lock = _cache_path(root).with_suffix(".refresh.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        if time.time() - lock.stat().st_mtime > REFRESH_LOCK_STALE_S:
            lock.unlink()
    except OSError:
        pass
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:
        return None
    token = f"{os.getpid()}-{os.urandom(8).hex()}"
    try:
        os.write(fd, token.encode("ascii"))
    finally:
        os.close(fd)
    return token


def _release_refresh(root: Path, token: str | None = None) -> None:
    """Drop the refresh lock -- only while it is still ``token``'s, when one is given."""
    lock = _cache_path(root).with_suffix(".refresh.lock")
    try:
        if token is not None and lock.read_text(encoding="ascii", errors="replace") != token:
            return  # reclaimed as stale: the lock now belongs to another refresher
        lock.unlink()
    except OSError:
        pass


def _spawn_refresh(root: Path, token: str | None = None) -> subprocess.Popen | None:
    """Start `<this file> --refresh` fully detached, so the hook can return while
    it runs and Claude Code's pipe is never held open by it."""
    kwargs: dict = {
        "cwd": str(root),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        detached = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        breakaway = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB: outlive a hook job object
        flag_sets = (detached | breakaway, detached)
    else:
        kwargs["start_new_session"] = True
        flag_sets = (0,)
    for flags in flag_sets:
        try:
            if flags:
                kwargs["creationflags"] = flags
            argv = [sys.executable, os.path.abspath(__file__), "--refresh", *([token] if token else [])]
            return subprocess.Popen(argv, **kwargs)
        except OSError:
            continue  # breakaway refused by the job: retry without it
    return None


def refresh(root: Path, token: str | None = None) -> None:
    """Compute the full briefing and cache it (the detached child's job)."""
    try:
        _write_cache(root, briefing(root))
    finally:
        _release_refresh(root, token)


def bounded_briefing(root: Path, budget_s: float = FOREGROUND_BUDGET_S) -> str:
    """A briefing within budget_s: fresh if the refresh finishes in time, else
    the last cached one marked with its age, else a pointer to the full tool."""
    started = time.time()
    token = _claim_refresh(root)
    proc = _spawn_refresh(root, token) if token else None
    if token and proc is None:
        # No child will ever release it: every later session would wait out the stale
        # window on a refresh that is not running (review F2484).
        _release_refresh(root, token)
    if proc is not None:  # else another session is refreshing: serve the cache now
        try:
            proc.wait(timeout=budget_s)
        except subprocess.TimeoutExpired:
            pass
    cached = _read_cache(root)
    if cached and cached[1] >= started - 1:
        return cached[0]
    if cached:
        return _cached_with_age(cached[0], cached[1], time.time())
    return (
        "[repo-guard] briefing still being computed (first run in this checkout) - "
        "run `navig repo stale` for it now"
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        root = repo_root()
        if "--refresh" in args:
            rest = args[args.index("--refresh") + 1:]
            refresh(root, rest[0] if rest and not rest[0].startswith("-") else None)
        elif "--sync" in args:
            print(briefing(root))  # the old unbounded behaviour, for debugging
        else:
            print(bounded_briefing(root))
    except Exception:  # noqa: BLE001 — a broken hook must never break session start
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
