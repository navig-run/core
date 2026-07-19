#!/usr/bin/env python3
"""Claude Code SessionStart hook — stale-work + lock briefing for this checkout.

Prints a compact repo-guard briefing to stdout (which Claude Code adds to the
session's context): current branch, agent-lock holder, leftover worktrees
(flagging forbidden sibling checkouts outside the repo), a cross-worktree
merge radar (which worktree pairs would conflict, dirty state included),
branches not merged into the default branch, and stashes. Every session
starts by KNOWING what other agents left behind and where collisions are
brewing, instead of discovering it at merge time.

Stdlib + git only (never imports navig — must work even when the venv is
broken). Fail-open: any error prints nothing and exits 0. Output is pure
ASCII — the Windows hook pipe garbles non-ASCII. Rich human version:
``navig repo stale``. Wiring: see scripts/agent-hooks/README.md.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists():
            return parent
    return here.parents[1]


def _git_rc(args: list[str], cwd: Path | str) -> tuple[int, str]:
    try:
        res = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=10
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

    lock_file = root / ".dev" / "agent.lock"
    if lock_file.exists():
        try:
            lock = json.loads(lock_file.read_text(encoding="utf-8"))
            sid = str(lock.get("session_id", "?"))[:8]
            lines.append(
                f"[!] agent lock present (session {sid}..., branch {lock.get('branch', '?')}) - "
                f"another session may be active in this folder. Details: navig repo lock"
            )
        except (OSError, ValueError):
            lines.append("[!] agent lock file present but unreadable - check: navig repo lock")

    out = _git(["worktree", "list", "--porcelain"], root)
    if out:
        paths = [
            line[len("worktree "):].strip()
            for line in out.splitlines()
            if line.startswith("worktree ")
        ]
        for p in paths:
            if os.path.normcase(p) == os.path.normcase(str(root)):
                continue
            marker = (
                " - [!] SIBLING checkout OUTSIDE the repo (forbidden; should live in .dev/worktrees/)"
                if not _is_within(p, root)
                else ""
            )
            lines.append(f"* extra worktree: {p}{marker}")

    lines.extend(conflict_lines(root))

    out = _git(
        ["branch", "--no-merged", default, "--format=%(refname:short)|%(upstream:track)"], root
    )
    if out:
        for line in out.splitlines():
            if not line.strip():
                continue
            name, _, track = line.partition("|")
            gone = " (remote gone - merged remotely? verify then delete)" if "gone" in track else ""
            lines.append(f"* branch not merged into {default}: {name}{gone}")

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
