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
_GIT_TIMEOUT = 15  # seconds; local plumbing should be instant


# ── git plumbing ─────────────────────────────────────────────────────────────


def _git(args: list[str], cwd: Path | str) -> subprocess.CompletedProcess[str]:
    """Run a git command; never raises on non-zero exit (callers check)."""
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        encoding="utf-8",  # git porcelain is UTF-8; locale decoding mojibakes it
        errors="replace",
        timeout=_GIT_TIMEOUT,
    )


def repo_root(cwd: Path | None = None) -> Path | None:
    """Resolve the repository root from ``cwd`` (default: process cwd)."""
    res = _git(["rev-parse", "--show-toplevel"], cwd or Path.cwd())
    if res.returncode != 0:
        return None
    return Path(res.stdout.strip())


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
    (lives outside the primary checkout — forbidden by house rules).
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
            current = {"path": Path(line[len("worktree "):].strip())}
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):].strip()
        elif line.startswith("branch "):
            ref = line[len("branch "):].strip()
            current["branch"] = ref.removeprefix("refs/heads/")
        elif line.strip() == "detached":
            current["branch"] = None
    if current:
        worktrees.append(current)
    for wt in worktrees:
        wt.setdefault("branch", None)
        wt["is_primary"] = _is_within(wt["path"], root) and _is_within(
            root, wt["path"]
        )
        wt["is_sibling"] = not wt["is_primary"] and not _is_within(
            wt["path"], root
        )
        wt["label"] = wt["path"].name if not wt["is_primary"] else f"{wt['path'].name} (main checkout)"
    return worktrees


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


def collect_stale(root: Path) -> dict:
    """Stale-work report: worktrees, unmerged branches, stashes, lock state."""
    base = default_branch(root)
    worktrees = list_worktrees(root)
    wt_branches = {wt["branch"] for wt in worktrees if wt["branch"]}
    extra_wts = []
    for wt in worktrees:
        if wt["is_primary"]:
            continue
        _sha, is_dirty = dirty_ref(wt["path"])
        age = _git(["log", "-1", "--format=%cr"], wt["path"])
        extra_wts.append(
            {
                "path": str(wt["path"]),
                "branch": wt["branch"],
                "dirty": is_dirty,
                "last_commit": age.stdout.strip() if age.returncode == 0 else "?",
                "sibling": wt["is_sibling"],
            }
        )

    branches: list[dict] = []
    res = _git(
        ["branch", "--no-merged", base, "--format=%(refname:short)|%(upstream:short)|%(upstream:track)"],
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
                }
            )

    stashes: list[str] = []
    res = _git(["stash", "list", "--format=%gd · %cr · %gs"], root)
    if res.returncode == 0:
        stashes = [ln for ln in res.stdout.splitlines() if ln.strip()]

    return {
        "default_branch": base,
        "worktrees": extra_wts,
        "unmerged_branches": branches,
        "stashes": stashes,
        "lock": read_lock(root),
    }


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


def _require_root() -> Path:
    root = repo_root()
    if root is None:
        from navig import console_helper as ch

        ch.error("Not inside a git repository.")
        raise typer.Exit(1)
    return root


@repo_app.command("conflicts")
def conflicts_cmd(
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
    root = _require_root()
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


@repo_app.command("stale")
def stale_cmd(
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Report leftover agent work: worktrees, unmerged branches, stashes, lock."""
    root = _require_root()
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
            ],
        )
        for wt in data["worktrees"]:
            findings += 1
            state = "[yellow]dirty[/yellow]" if wt["dirty"] else "[green]clean[/green]"
            if wt["sibling"]:
                state += " [red]⚠ sibling outside repo[/red]"
            table.add_row(
                _display_path(wt["path"], root), wt["branch"] or "detached", state, wt["last_commit"]
            )
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
        ch.dim(
            f"{findings} item(s) to review · merge or delete finished branches, "
            "drop obsolete stashes, remove finished worktrees (git worktree remove)"
        )


lock_app = typer.Typer(
    help="Inspect / release the main-checkout agent lock (.dev/agent.lock)",
    invoke_without_command=True,
    no_args_is_help=False,
)
repo_app.add_typer(lock_app, name="lock")


@lock_app.callback()
def lock_default(ctx: typer.Context) -> None:
    """With no subcommand, show lock status."""
    if ctx.invoked_subcommand is None:
        lock_status_cmd()


@lock_app.command("status")
def lock_status_cmd() -> None:
    """Show who holds the main-checkout agent lock."""
    root = _require_root()
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


@lock_app.command("release")
def lock_release_cmd(
    force: bool = typer.Option(
        False, "--force", help="Release even when the lock is fresh (another agent may be live!)"
    ),
) -> None:
    """Release the agent lock (stale locks always; fresh locks need --force)."""
    root = _require_root()
    from navig import console_helper as ch

    st = lock_state(read_lock(root))
    if st["state"] == "free":
        ch.info("Lock already free.")
        return
    if st["state"] == "held" and not force:
        ch.warning(
            f"Lock is fresh (age {st.get('age_minutes', '?')}m) — another agent may be live.",
            "Re-run with --force only if you are sure that session is gone.",
        )
        raise typer.Exit(1)
    try:
        lock_path(root).unlink()
        ch.success("Lock released.")
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
    root = repo_root(Path(repo) if repo else None)
    if root is None:
        from navig import console_helper as ch

        ch.error("Target is not inside a git repository.", repo or str(Path.cwd()))
        raise typer.Exit(1)
    return root


def _guard_load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        from navig import console_helper as ch

        ch.error(
            f"{path} is not valid JSON - fix it first (refusing to overwrite).", str(exc)
        )
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
        "dev_ignored": _git(["check-ignore", "-q", str(root / ".dev" / "x")], root).returncode
        == 0,
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
        ch.dim("install or refresh with: navig repo guard install" + (f" --repo {repo}" if repo else ""))


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
