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
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
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
                                f"more work lands here (`git -C {p} rebase origin/{default}`)"
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


def main() -> int:
    try:
        print(briefing(repo_root()))
    except Exception:  # noqa: BLE001 — a broken hook must never break session start
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
