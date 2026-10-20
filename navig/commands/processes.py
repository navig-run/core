"""
Cross-platform process management — find and kill processes by name, group,
or special selector (Chrome tab renderers, etc.).

Pure psutil-based — no shell-outs, no platform branches in the caller.
Returns plain dicts so the same module is usable from Telegram handlers,
the CLI, Deck REST routes, and tests.

`monitor.py` stays read-only by contract; destructive operations live here.
"""

from __future__ import annotations

import functools
import logging
import os
import posixpath
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import psutil  # type: ignore[import-untyped]
except ImportError:
    psutil = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ─── Built-in process groups ────────────────────────────────────────────────
# group name → list of substring patterns matched (case-insensitive) against
# the psutil process name. "node" matches both "node" (Linux/Mac) and
# "node.exe" (Windows) because we use substring match.
KNOWN_GROUPS: dict[str, list[str]] = {
    "node":       ["node"],
    "python":     ["python", "pythonw"],
    "powershell": ["powershell", "pwsh"],
    "pwsh":       ["pwsh"],
    "conhost":    ["conhost"],
    "windows-terminal": ["windowsterminal"],
    "esbuild":    ["esbuild"],
    "git":        ["git"],
    # Special selectors handled by dedicated finders, not name match:
    #   "chrome-tabs"           → Chrome renderer procs excluding extension hosts
    #   "chrome-all-renderers"  → all Chrome renderer procs (tabs + extensions)
    #   "chrome"                → every chrome.exe (browser + GPU + renderers)
    #   "claude-idle"           → Claude Code sessions proven idle (see classify_claude_sessions)
}

# Groups requiring extra caution — these may include the running daemon
# or system-critical processes. Caller should warn the user.
SELF_RISKY_GROUPS: set[str] = {"python", "powershell", "pwsh"}


# ─── Types ──────────────────────────────────────────────────────────────────
ProcInfo = dict[str, Any]   # {pid, name, cmdline, username, created_at}
KillReport = dict[str, Any] # {killed: [...], failed: [...], skipped: [...], group, dry_run, ts}


def _psutil_available() -> bool:
    return psutil is not None


def _to_info(p: "psutil.Process") -> ProcInfo:
    try:
        cmdline = p.cmdline()
    except Exception:
        cmdline = []
    try:
        username = p.username()
    except Exception:
        username = ""
    try:
        created = datetime.fromtimestamp(p.create_time(), tz=timezone.utc).isoformat()
    except Exception:
        created = ""
    try:
        name = p.name()
    except Exception:
        name = ""
    return {
        "pid": p.pid,
        "name": name,
        "cmdline": cmdline,
        "username": username,
        "created_at": created,
    }


# ─── Finders ────────────────────────────────────────────────────────────────

def find_processes_by_name(patterns: list[str]) -> list[ProcInfo]:
    """Return processes whose `name()` contains any of the substring patterns
    (case-insensitive). Substring match handles `node` ↔ `node.exe` and
    Windows/Linux variants uniformly."""
    if not _psutil_available() or not patterns:
        return []
    needles = [p.lower() for p in patterns]
    out: list[ProcInfo] = []
    for p in psutil.process_iter(["name"]):
        try:
            n = (p.info.get("name") or "").lower()
            if not n:
                continue
            if any(needle in n for needle in needles):
                out.append(_to_info(p))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


def find_chrome_renderers(
    include_extensions: bool = False, include_automation: bool = False
) -> list[ProcInfo]:
    """Find Chrome renderer processes (each browser tab is one renderer).
    `include_extensions=False` excludes extension hosts so killing only kills tabs.

    Automation browsers are spared unless `include_automation=True`: a test
    harness, `navig cdp` or any other driver launches Chrome with an explicit
    --user-data-dir, and its renderers carry it; the operator's own Chrome /
    Beta / Dev run their default profile and never do. Crashing a harness
    browser's renderer silently fails someone's live test run.

    Works the same way on Win/Linux/Mac because Chrome uses the same
    command-line flags everywhere — psutil exposes cmdline uniformly.
    """
    if not _psutil_available():
        return []
    out: list[ProcInfo] = []
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            name = (p.info.get("name") or "").lower()
            if "chrome" not in name and "chromium" not in name:
                continue
            cmd_parts = p.info.get("cmdline") or []
            cmd = " ".join(cmd_parts)
            if "--type=renderer" not in cmd:
                continue
            if not include_automation and "--user-data-dir=" in cmd:
                continue
            is_extension = "--extension-process" in cmd
            if is_extension and not include_extensions:
                continue
            out.append(_to_info(p))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


#: Install-folder name -> channel label. Every channel runs as chrome.exe; the
#: folder (…\Google\<folder>\Application\chrome.exe) is the only tell.
_CHROME_CHANNELS = {"chrome": "stable", "chrome beta": "beta", "chrome dev": "dev", "chrome sxs": "canary"}


def chrome_channel(cmdline: list[str]) -> str:
    """stable / beta / dev / canary from the executable path, else 'chromium'."""
    exe = (cmdline[0] if cmdline else "").replace("\\", "/").lower()
    parts = exe.split("/")
    if "application" in parts:
        folder = parts[parts.index("application") - 1]
        return _CHROME_CHANNELS.get(folder, folder or "chromium")
    return "chromium" if "chromium" in exe else "stable"


def find_chrome_extensions(
    channels: set[str] | None = None, include_automation: bool = False
) -> list[ProcInfo]:
    """Only Chrome's extension processes (renderers launched with
    --extension-process), optionally limited to some channels. Tabs untouched;
    automation browsers spared (see find_chrome_renderers)."""
    out = []
    for info in find_chrome_renderers(include_extensions=True, include_automation=include_automation):
        cmd = info.get("cmdline") or []
        if "--extension-process" not in cmd and "--extension-process" not in " ".join(cmd):
            continue
        info["channel"] = chrome_channel(cmd)
        if channels and info["channel"] not in channels:
            continue
        out.append(info)
    return out


def find_processes_by_group(group: str) -> list[ProcInfo]:
    """Find processes belonging to a built-in group or a special selector."""
    g = group.strip().lower()
    if g == "chrome-tabs":
        return find_chrome_renderers(include_extensions=False)
    if g in ("chrome-all-renderers", "chrome-renderers"):
        return find_chrome_renderers(include_extensions=True)
    if g == "chrome-extensions":
        return find_chrome_extensions()
    if g == "chrome":
        return find_processes_by_name(["chrome", "chromium"])
    if g == "claude-idle":
        return find_idle_claude_sessions()
    if g in KNOWN_GROUPS:
        return find_processes_by_name(KNOWN_GROUPS[g])
    # Fallback: treat as a name pattern itself
    return find_processes_by_name([g])


def available_ram_bytes() -> int | None:
    """System RAM available right now (psutil `virtual_memory().available`), or
    None when psutil is missing or the read fails — callers must treat None as
    "unknown", never as zero."""
    if not _psutil_available():
        return None
    try:
        return int(psutil.virtual_memory().available)
    except Exception:
        return None


# ─── Killers ────────────────────────────────────────────────────────────────

def _self_pids() -> set[int]:
    """PIDs to protect when exclude_self=True: this process + its parent."""
    out = {os.getpid()}
    if _psutil_available():
        try:
            out.add(psutil.Process(os.getpid()).ppid())
        except Exception:
            pass
    return out


def _safe_name(p: "psutil.Process") -> str:
    """Process name for a report line — a denied process may refuse even this."""
    try:
        return p.name()
    except Exception:  # noqa: BLE001
        return ""


def kill_processes(
    procs: list[ProcInfo],
    force: bool = False,
    exclude_self: bool = True,
    timeout: float = 3.0,
) -> KillReport:
    """Terminate (or kill if force=True) the given processes.

    - `force=False` → graceful `terminate()` (SIGTERM on POSIX, TerminateProcess on Win).
      If the process doesn't exit within `timeout`, falls back to `kill()`.
    - `force=True` → immediate `kill()` (SIGKILL on POSIX).
    - `exclude_self=True` → skip our own PID + parent PID (prevents NAVIG daemon
      from terminating itself when targeting "python" or "pwsh").
    """
    report: KillReport = {
        "killed": [],
        "failed": [],
        "skipped": [],
        "force": force,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if not _psutil_available():
        report["failed"].append({"pid": 0, "name": "", "reason": "psutil not installed"})
        return report

    self_pids = _self_pids() if exclude_self else set()
    to_kill: list[psutil.Process] = []

    for info in procs:
        pid = info["pid"]
        if pid in self_pids:
            report["skipped"].append({**info, "reason": "self-protected"})
            continue
        try:
            to_kill.append(psutil.Process(pid))
        except psutil.NoSuchProcess:
            report["skipped"].append({**info, "reason": "already gone"})
        except psutil.AccessDenied:
            report["failed"].append({**info, "reason": "access denied"})

    if not to_kill:
        return report

    # Fire off the terminate/kill call. Only processes whose kill actually went
    # through are kept for the wait: psutil.wait_procs() calls OpenProcess on
    # every pid, and for an elevated target it raises psutil.AccessDenied
    # UNCAUGHT — which crashed `navig kill claude-idle` on 2026-10-15
    # (pid=146080, VS Code-owned) right after successfully killing its
    # sibling. A refused kill must end as a report entry, never an exception.
    waiting: list[psutil.Process] = []
    for p in to_kill:
        try:
            if force:
                p.kill()
            else:
                p.terminate()
            waiting.append(p)
        except psutil.NoSuchProcess:
            report["killed"].append({"pid": p.pid, "name": "", "reason": "already exited"})
        except psutil.AccessDenied as exc:
            report["failed"].append({"pid": p.pid, "name": _safe_name(p), "reason": f"access denied: {exc}"})
        except Exception as exc:
            report["failed"].append({"pid": p.pid, "name": "", "reason": str(exc)})

    # Wait, then escalate any survivors to kill()
    try:
        gone, alive = psutil.wait_procs(waiting, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - a wait failure must degrade, not crash the CLI
        report["failed"].extend(
            {"pid": p.pid, "name": _safe_name(p), "reason": f"wait failed: {exc}"} for p in waiting
        )
        gone, alive = [], []
    for p in gone:
        try:
            report["killed"].append({"pid": p.pid, "name": p.name() if p.is_running() else ""})
        except Exception:
            report["killed"].append({"pid": p.pid, "name": ""})

    for p in alive:
        try:
            p.kill()
            report["killed"].append({"pid": p.pid, "name": p.name(), "escalated": True})
        except psutil.NoSuchProcess:
            report["killed"].append({"pid": p.pid, "name": "", "escalated": True})
        except Exception as exc:
            report["failed"].append({"pid": p.pid, "name": "", "reason": f"escalation failed: {exc}"})

    return report


def kill_group(
    group: str,
    force: bool = False,
    dry_run: bool = False,
    exclude_self: bool = True,
) -> KillReport:
    """Find all processes in a group and kill them (or just enumerate if dry_run)."""
    procs = find_processes_by_group(group)
    if dry_run:
        return {
            "killed": [],
            "failed": [],
            "skipped": [],
            "candidates": procs,
            "group": group,
            "force": force,
            "dry_run": True,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    report = kill_processes(procs, force=force, exclude_self=exclude_self)
    report["group"] = group
    report["dry_run"] = False
    report["candidates"] = procs
    return report


def list_known_groups() -> list[str]:
    """Names callable as group identifiers (for `/kill <group>` autocomplete)."""
    return sorted(KNOWN_GROUPS.keys()) + [
        "chrome", "chrome-tabs", "chrome-all-renderers", "chrome-extensions", "claude-idle",
    ]


# ─── Read-only enrichment: memory, project, owner ───────────────────────────
# Answers "which project is this node.exe from, and is anyone still driving it?"
# Nothing here kills — describe_processes() only reads.

#: Process names (no ".exe", lowercase) that own a dev tree: an agent session,
#: an editor window or a terminal multiplexer. The nearest one up the parent
#: chain is reported as the process's owner.
OWNER_NAMES: frozenset[str] = frozenset(
    {
        "claude",
        "code",
        "vscodium",
        "cursor",
        "windsurf",
        "windowsterminal",
        "tmux",
        "screen",
        "sshd",
        "navig-os",
    }
)

# Interpreter flags whose NEXT argument is a value, not the script being run.
_VALUE_FLAGS = frozenset({"-r", "--require", "--import", "--loader", "--experimental-loader", "-c"})


def _mem_mb(p: "psutil.Process") -> float:
    """Private (commit) bytes on Windows — what actually exhausts the commit limit;
    RSS elsewhere, where psutil exposes no cheap private figure."""
    mi = p.memory_info()
    return (getattr(mi, "private", 0) or mi.rss) / 2**20


def project_of(cwd: str) -> str:
    """Label a working directory by its git repository.

    `<repo>` for a checkout (or any dir inside one), `<repo>:<worktree>` for a
    linked git worktree wherever it lives, and the folder name when no repo
    encloses it.
    """
    if not cwd:
        return ""
    p = Path(cwd)
    return repo_label(cwd) or p.name or str(p)


def project_from_cmdline(cmdline: list[str]) -> str:
    """Infer the project from the first command-line path inside a git repo.

    The fallback when the cwd is unreadable: on Windows psutil can read an
    elevated parent's child's cmdline but not its cwd. Paths outside any repo
    (node.exe itself, a global npx-cli.js) are skipped rather than mislabelled.
    """
    for arg in cmdline[1:]:
        if not arg or arg.startswith("-") or not (os.sep in arg or "/" in arg):
            continue
        path = os.path.normpath(arg.replace("/", os.sep))
        if not os.path.isabs(path):
            continue
        label = repo_label(os.path.dirname(path))
        if label:
            return label
    return ""


@functools.lru_cache(maxsize=1024)
def repo_label(path: str) -> str | None:
    """`repo` / `repo:worktree` for the git repo enclosing `path`, else None."""
    if not path:
        return None
    p = Path(path)
    for d in (p, *p.parents):
        git = d / ".git"
        if git.is_dir():
            return d.name
        if git.is_file():
            # A linked worktree's .git is a file: "gitdir: <main>/.git/worktrees/<slug>".
            try:
                line = git.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                return d.name
            if line.startswith("gitdir:"):
                gd = Path(line[len("gitdir:") :].strip())
                if not gd.is_absolute():
                    gd = d / gd
                if gd.parent.name == "worktrees" and gd.parent.parent.name == ".git":
                    return f"{gd.parent.parent.parent.name}:{d.name}"
            return d.name
    return None


def process_kind(cmdline: list[str]) -> str:
    """Short label for what a process runs: the package for anything under
    node_modules (pnpm's nested layout included), else the script's stem."""
    if not cmdline:
        return ""
    args: list[str] = []
    skip = after_flag = module = False
    for a in cmdline[1:]:
        if not a:
            continue
        if module:  # `python -m pytest`: the module IS what runs
            args.append(a)
            break
        if skip:
            skip = False
            continue
        if a == "-m":
            module = True
            continue
        if a in _VALUE_FLAGS:
            skip = True
            continue
        if a.startswith("-"):
            after_flag = True
            continue
        # A bare word right after an unknown flag is that flag's value
        # (`--output-format stream-json`); a script or path is never bare.
        if after_flag and not any(ch in a for ch in "/\\."):
            after_flag = False
            continue
        args.append(a)
        break
    if not args:
        return Path(cmdline[0]).stem
    # Collapse ".bin/../next" (npm shims) so the package, not ".bin", is reported.
    first = posixpath.normpath(args[0].replace("\\", "/"))
    if "node_modules/" in first:
        tail = first.rsplit("node_modules/", 1)[1].split("/")
        return "/".join(tail[:2]) if tail[0].startswith("@") else tail[0]
    return Path(first).stem or first


def _ppid_map() -> dict[int, int]:
    """pid -> ppid in ONE snapshot.

    On Windows every Process.ppid() call takes a full system snapshot (~90 ms on a
    1,500-process box), so asking each process costs minutes; psutil's platform
    layer exposes the whole map from a single snapshot. Fall back to per-process
    reads where that private helper is missing.
    """
    fn = getattr(getattr(psutil, "_psplatform", None), "ppid_map", None)
    if callable(fn):
        try:
            return dict(fn())
        except Exception:
            pass
    out: dict[int, int] = {}
    for p in psutil.process_iter(["pid", "ppid"]):
        out[p.info["pid"]] = p.info.get("ppid") or 0
    return out


class _CreateTimes(dict):
    """pid -> create_time, read on first use: only ancestors actually walked pay
    for it (an elevated process's create_time costs ~120 ms on Windows)."""

    def __missing__(self, pid: int) -> float:
        try:
            value = psutil.Process(pid).create_time()
        except Exception:
            value = 0.0
        self[pid] = value
        return value


def _process_table() -> dict[int, tuple[str, int]]:
    """pid -> (name without .exe, ppid) for every visible process."""
    table: dict[int, tuple[str, int]] = {}
    if not _psutil_available():
        return table
    ppids = _ppid_map()
    for p in psutil.process_iter(["pid", "name"]):
        name = (p.info.get("name") or "").lower().removesuffix(".exe")
        table[p.info["pid"]] = (name, ppids.get(p.info["pid"], 0))
    return table


def owner_of(
    pid: int,
    table: dict[int, tuple[str, int]],
    create_times: dict[int, float] | None = None,
) -> tuple[str, bool]:
    """Walk up from `pid` to the nearest OWNER_NAMES ancestor.

    Returns (owner, detached). owner is "name[pid]" or "" when none is found.
    detached is True when the chain breaks at a parent that no longer exists —
    including a recycled PID (a "parent" created after its child), which Windows
    hands out freely. A detached tree is either deliberately backgrounded or a
    leftover whose launcher has gone.
    """
    entry = table.get(pid)
    if entry is None:
        return "", False
    ct = create_times if create_times is not None else _CreateTimes()
    child = pid
    cur = entry[1]
    seen: set[int] = {pid}
    while cur and cur not in seen:
        seen.add(cur)
        parent = table.get(cur)
        if parent is None:
            return "", True
        parent_ct, child_ct = ct[cur], ct[child]
        if parent_ct and child_ct and parent_ct > child_ct + 1:
            return "", True
        if parent[0] in OWNER_NAMES:
            return f"{parent[0]}[{cur}]", False
        child = cur
        cur = parent[1]
    return "", False


def describe_processes(procs: list[ProcInfo]) -> list[ProcInfo]:
    """Add mem_mb, cwd, project, kind, owner, detached, age_s and readable to each.

    When the cwd is unreadable (Windows: the process belongs to an elevated
    parent) the project is inferred from repo paths in the cmdline instead;
    readable=False means neither worked — run elevated to see it.
    """
    table = _process_table()
    create_times = _CreateTimes()
    now = time.time()
    out: list[ProcInfo] = []
    for info in procs:
        row: ProcInfo = {
            **info,
            "mem_mb": 0.0,
            "cwd": "",
            "project": "",
            "kind": "",
            "owner": "",
            "detached": False,
            "age_s": 0,
            "readable": True,
        }
        try:
            p = psutil.Process(info["pid"])
            row["mem_mb"] = round(_mem_mb(p), 1)
            row["age_s"] = int(now - create_times[info["pid"]]) if create_times[info["pid"]] else 0
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        cmdline = info.get("cmdline") or []
        try:
            row["cwd"] = p.cwd()
            row["project"] = project_of(row["cwd"])
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            row["project"] = project_from_cmdline(cmdline)
            row["readable"] = bool(row["project"])
        row["kind"] = process_kind(cmdline)
        row["owner"], row["detached"] = owner_of(info["pid"], table, create_times)
        out.append(row)
    return out


def summarize_by_project(rows: list[ProcInfo]) -> list[dict[str, Any]]:
    """Group described rows by project, biggest memory first."""
    groups: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = r["project"] or ("<unreadable>" if not r["readable"] else "<unknown>")
        g = groups.setdefault(
            key,
            {
                "project": key,
                "count": 0,
                "mem_mb": 0.0,
                "kinds": {},
                "owners": set(),
                "detached": 0,
                "pids": [],
            },
        )
        g["count"] += 1
        g["mem_mb"] += r["mem_mb"]
        if r["kind"]:
            g["kinds"][r["kind"]] = g["kinds"].get(r["kind"], 0) + 1
        if r["owner"]:
            g["owners"].add(r["owner"])
        if r["detached"]:
            g["detached"] += 1
        g["pids"].append(r["pid"])
    out = []
    for g in groups.values():
        g["mem_mb"] = round(g["mem_mb"], 1)
        g["owners"] = sorted(g["owners"])
        g["kinds"] = dict(sorted(g["kinds"].items(), key=lambda kv: -kv[1]))
        out.append(g)
    return sorted(out, key=lambda g: -g["mem_mb"])


# ─── Idle Claude Code sessions ──────────────────────────────────────────────
# Each open chat tab in VS Code / VSCodium keeps a claude.exe alive (~0.5-0.9 GB)
# until the tab is closed; on 2026-10-03 56 of them held 35 GB, most 3-5 days
# untouched. Reaping one ends the process only — the conversation stays on disk
# and the tab can resume it. classify_claude_sessions() decides; it never kills.

#: Default idle threshold, in hours.
CLAUDE_IDLE_HOURS = 24.0

#: A session's only child when nothing is running (its console host). Any other
#: child (bash.exe, cmd.exe, …) means a tool call is in flight.
_IDLE_CHILDREN = frozenset({"conhost"})

_SESSION_ID_RE = re.compile(r"--(?:resume|session-id)[= ]([0-9a-fA-F]{8}-[0-9a-fA-F-]{27})")


def session_id_of(cmdline: list[str]) -> str:
    """The transcript id a Claude Code process was started with, or ""."""
    m = _SESSION_ID_RE.search(" ".join(cmdline))
    return m.group(1).lower() if m else ""


def claude_config_dirs() -> list[Path]:
    """Every Claude Code config dir with a projects/ folder: $CLAUDE_CONFIG_DIR,
    ~/.claude and any ~/.claude-* (parallel accounts/installs)."""
    home = Path.home()
    candidates = []
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        candidates.append(Path(env))
    candidates.append(home / ".claude")
    candidates.extend(sorted(p for p in home.glob(".claude-*") if p.is_dir()))
    out: list[Path] = []
    seen: set[str] = set()
    for d in candidates:
        key = str(d).lower()
        if key not in seen and (d / "projects").is_dir():
            seen.add(key)
            out.append(d)
    return out


def encode_project_dir(cwd: str) -> str:
    """Claude Code's projects/<name> for a cwd: every non-alphanumeric becomes '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd).lower()


def transcript_index(
    config_dirs: list[Path],
) -> tuple[dict[str, float], dict[str, list[tuple[str, float]]]]:
    """(session id -> newest transcript mtime, project dir -> [(id, mtime)])."""
    by_sid: dict[str, float] = {}
    by_dir: dict[str, list[tuple[str, float]]] = {}
    for root in config_dirs:
        for f in (root / "projects").glob("*/*.jsonl"):
            try:
                mtime = f.stat().st_mtime
            except OSError:
                continue
            sid = f.stem.lower()
            by_sid[sid] = max(mtime, by_sid.get(sid, 0.0))
            by_dir.setdefault(f.parent.name.lower(), []).append((sid, mtime))
    return by_sid, by_dir


def _ancestors(pid: int, ppids: dict[int, int]) -> set[int]:
    out: set[int] = set()
    cur = pid
    while cur and cur not in out:
        out.add(cur)
        cur = ppids.get(cur, 0)
    return out


def classify_claude_sessions(
    rows: list[ProcInfo],
    idle_hours: float = CLAUDE_IDLE_HOURS,
    *,
    now: float | None = None,
    index: tuple[dict[str, float], dict[str, list[tuple[str, float]]]] | None = None,
    children: dict[int, list[str]] | None = None,
    self_chain: set[int] | None = None,
) -> list[ProcInfo]:
    """Add session_id, idle_h, verdict ('idle' | 'active' | 'young' | 'unknown') and
    reason to each described claude.exe row. Only 'idle' is ever safe to reap.

    'young' = open for less than the threshold, so not judged. It used to be
    reported as 'active' with the reason "opened 19.8h ago": a claim of activity
    resting on age alone, which on 2026-10-10 labelled 79 of 84 sessions active.

    idle needs ALL of: no child but its console host (no tool running), the
    process itself older than the threshold (a just-reopened tab is fresh), and
      * a --resume/--session-id transcript untouched for the threshold, or
      * without an id: no transcript in its project written within the threshold
        that no other live session owns (one would be this session's).
    Anything ambiguous is 'unknown'. The session running this code is 'self'.
    """
    now = now if now is not None else time.time()
    limit_s = idle_hours * 3600
    by_sid, by_dir = index if index is not None else transcript_index(claude_config_dirs())
    if children is None or self_chain is None:
        ppids = _ppid_map() if _psutil_available() else {}
        if children is None:
            names = _process_table()
            children = {}
            for pid, ppid in ppids.items():
                children.setdefault(ppid, []).append(names.get(pid, ("", 0))[0])
        if self_chain is None:
            self_chain = _ancestors(os.getpid(), ppids)

    for r in rows:
        r["session_id"] = session_id_of(r.get("cmdline") or [])
    claimed = {r["session_id"] for r in rows if r["session_id"]}

    for r in rows:
        r["idle_h"] = None
        age_s = r.get("age_s") or 0
        busy = sorted({c for c in children.get(r["pid"], []) if c not in _IDLE_CHILDREN})
        if r["pid"] in self_chain:
            r["verdict"], r["reason"] = "self", "this command runs inside it"
            continue
        if busy:
            r["verdict"], r["reason"] = "active", f"tool running ({', '.join(busy)})"
            continue
        if age_s < limit_s:
            reason = f"opened {age_s / 3600:.1f}h ago (under {idle_hours:g}h)"
            mtime = by_sid.get(r["session_id"]) if r["session_id"] else None
            if mtime is not None:
                r["idle_h"] = round(min(now - mtime, age_s) / 3600, 1)
                reason += f"; transcript written {r['idle_h']:g}h ago"
            r["verdict"], r["reason"] = "young", reason
            continue
        sid = r["session_id"]
        if sid:
            mtime = by_sid.get(sid)
            if mtime is None:
                r["verdict"], r["reason"] = "unknown", "its transcript was not found"
                continue
            idle_s = min(now - mtime, age_s)
            r["idle_h"] = round(idle_s / 3600, 1)
            if idle_s >= limit_s:
                r["verdict"], r["reason"] = "idle", f"transcript untouched {idle_s / 3600:.0f}h"
            else:
                r["verdict"], r["reason"] = "active", f"transcript written {idle_s / 3600:.1f}h ago"
            continue
        if not r.get("cwd"):
            r["verdict"], r["reason"] = "unknown", "no session id and cwd unreadable (run elevated)"
            continue
        recent = [
            s for s, m in by_dir.get(encode_project_dir(r["cwd"]), [])
            if s not in claimed and now - m < limit_s
        ]
        if recent:
            r["verdict"], r["reason"] = "unknown", f"no session id; {len(recent)} recent transcript(s) in its project"
        else:
            r["idle_h"] = round(age_s / 3600, 1)
            r["verdict"], r["reason"] = "idle", f"no session id; nothing written in its project for {idle_hours:g}h"
    return rows


def find_idle_claude_sessions(idle_hours: float = CLAUDE_IDLE_HOURS) -> list[ProcInfo]:
    """Described + classified claude.exe processes whose verdict is 'idle'."""
    rows = describe_processes(find_processes_by_name(["claude"]))
    rows = [r for r in rows if (r.get("name") or "").lower().removesuffix(".exe") == "claude"]
    return [r for r in classify_claude_sessions(rows, idle_hours) if r["verdict"] == "idle"]


# ── roles + machine view (`navig procs --by role`, the header table) ────────────

#: What a process RUNS (``process_kind``) -> the job it does. Ordered: first match
#: wins. A kind matching nothing is reported as itself, so a new tool shows up by
#: name instead of vanishing into "other".
ROLE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("CI gate", ("ci-local", "ci-push", "push-gate", "heavy-slot", "ci-step-spawn", "ci-local-selection")),
    ("test runner", ("jest", "vitest", "pytest", "playwright", "mocha")),
    ("TypeScript", ("typescript", "tsc", "tsserver", "tsc-here", "tsx")),
    ("dev server", ("vite", "next", "wrangler", "workerd", "dev-run", "webpack", "esbuild", "edge-tls")),
    ("package manager", ("npm", "npx", "pnpm", "corepack", "yarn", "bun")),
    ("linter", ("eslint", "ruff", "prettier", "biome")),
    ("MCP server", ("mcp",)),
    ("agent", ("claude",)),
)


def role_of(kind: str) -> str:
    """The role a process kind plays; the kind itself when no rule matches."""
    k = (kind or "").lower()
    if not k:
        return "unknown"
    for role, needles in ROLE_RULES:
        if any(n == k or k.startswith(n + "-") or k.startswith(n + ".") or n in k.split("/") for n in needles):
            return role
    # One process per test FILE (node --test, a jest/vitest worker running `x.test`):
    # dozens of single-file rows otherwise bury the roles that matter. After the
    # rules, so the gate's own `ci-local-selection.test` stays a CI gate.
    if k.endswith((".test", ".spec")) or ".test." in k or ".spec." in k:
        return "test runner"
    return kind


def summarize_by_role(rows: list[ProcInfo]) -> list[dict[str, Any]]:
    """Group described rows by role (CI gate, TypeScript, …), biggest memory first."""
    groups: dict[str, dict[str, Any]] = {}
    for r in rows:
        role = role_of(r.get("kind") or "")
        g = groups.setdefault(
            role, {"role": role, "count": 0, "mem_mb": 0.0, "kinds": {}, "projects": set(), "detached": 0, "pids": []}
        )
        g["count"] += 1
        g["mem_mb"] += r["mem_mb"]
        if r.get("kind"):
            g["kinds"][r["kind"]] = g["kinds"].get(r["kind"], 0) + 1
        if r.get("project"):
            g["projects"].add(r["project"])
        if r.get("detached"):
            g["detached"] += 1
        g["pids"].append(r["pid"])
    out = []
    for g in groups.values():
        g["mem_mb"] = round(g["mem_mb"], 1)
        g["projects"] = sorted(g["projects"])
        g["kinds"] = dict(sorted(g["kinds"].items(), key=lambda kv: -kv[1]))
        out.append(g)
    return sorted(out, key=lambda g: -g["mem_mb"])


def machine_summary(top: int = 8, cpu_interval: float = 0.3) -> dict[str, Any]:
    """CPU, RAM, commit and the biggest process FAMILIES (by private memory).

    The family table is what a per-name view cannot show: 88 node processes
    looked like the problem on 2026-10-10, but they held 6.6 GB while 84 Claude
    Code sessions held 55 GB. Private memory (Windows commit, POSIX USS-ish RSS)
    is used rather than working set because it is what the page file must back.
    Never raises; a field it could not read is ``None``.
    """
    out: dict[str, Any] = {"cpu_percent": None, "ram_total_gb": None, "ram_free_gb": None,
                           "commit_used_gb": None, "commit_total_gb": None, "processes": 0, "families": []}
    if psutil is None:
        return out
    try:
        out["cpu_percent"] = psutil.cpu_percent(interval=cpu_interval)
        vm = psutil.virtual_memory()
        out["ram_total_gb"] = round(vm.total / 1024**3, 1)
        out["ram_free_gb"] = round(vm.available / 1024**3, 1)
        sw = psutil.swap_memory()  # on Windows: the page file, i.e. commit beyond RAM
        out["commit_total_gb"] = round((vm.total + sw.total) / 1024**3, 1)
        out["commit_used_gb"] = round((vm.total - vm.available + sw.used) / 1024**3, 1)
    except Exception as exc:  # noqa: BLE001
        logger.debug("machine_summary: memory read failed: %r", exc)
    fams: dict[str, list[float]] = {}
    for p in psutil.process_iter(["name"]):
        try:
            name = (p.info.get("name") or "?").lower().removesuffix(".exe")
            mi = p.memory_info()
            priv = getattr(mi, "private", None) or mi.rss
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            continue
        out["processes"] += 1
        f = fams.setdefault(name, [0, 0.0])
        f[0] += 1
        f[1] += priv
    ranked = sorted(fams.items(), key=lambda kv: -kv[1][1])[:top]
    out["families"] = [{"name": n, "count": int(c), "private_gb": round(b / 1024**3, 1)} for n, (c, b) in ranked]
    return out
