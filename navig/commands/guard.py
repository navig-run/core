"""navig guard — the process watchdog: runaway killer, deprioritizer, orphan reaper.

Two halves, both ported from the homelab PowerShell tasks they replace (the
scripts keep working; `guard install` points the scheduled tasks here):

- ``guard run``   ← Homelab-RunawayGuard: every few seconds, log any watched
  process (node/python/esbuild/…) crossing --warn-mb with its ancestry, kill
  its tree past --kill-mb, and hold the watched names at BELOW-NORMAL priority
  so parallel agent builds can no longer starve the desktop, the editor or a
  starting Claude session (2026-10-03: CPU 100%, run queue 37, Claude init 56 s,
  VS Code extension dead at its 60 s limit).

- ``guard sweep`` ← the orphan half of Homelab-ProcCleanup: reap dev-tool
  processes whose parent is gone (bash/sh/git/grep/sleep/node/python/cmd/…),
  sparing the session chain, never-kill names, navig's own pid-file trees and
  every keepalive lease (registered dev stacks) — with the same recycle-pid and
  expiry checks, so a stale lease protects nothing and a live stack is never
  touched.

Killing needs elevation (VS Code-owned processes refuse otherwise); run the
commands from an elevated shell.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import psutil  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - optional at import time
    psutil = None  # type: ignore[assignment]

import typer

from navig.core.proc_text import console_encoding
from navig.platform import paths

guard_app = typer.Typer(
    help="Process watchdog: runaway-memory killer, deprioritizer, orphan reaper.",
    no_args_is_help=True,
)

# Names that may never be terminated: editors, session roots, OS-critical.
NEVER_KILL: frozenset[str] = frozenset({
    "claude", "code", "vscodium", "explorer", "services", "svchost",
    "lsass", "csrss", "wininit", "winlogon", "dwm", "system", "idle",
    "registry", "memcompression", "fontdrvhost", "smss",
})

# The orphan sweep's target set — "dev-tool processes" (port of the PowerShell
# devRx; .exe is optional so POSIX names match too).
DEV_TOOL_RE = re.compile(
    r"^(bash|sh|git|grep|head|tail|sleep|ssh|ssh-agent|node|python|pythonw|navig|"
    r"esbuild|cmd|conhost|OpenConsole|pwsh|powershell|WindowsTerminal|winpty|"
    r"winpty-agent)(\.exe)?$",
    re.IGNORECASE,
)

# What `guard run` watches: big, build-shaped, and safe to hold down in priority.
WATCH_NAMES: tuple[str, ...] = ("node", "python", "py", "pythonw", "esbuild", "tsserver", "Robocopy")

# Interactive apps are deliberately never killed by the guard. Their aggregate
# footprint still matters: Chrome and VS Code split one UI across dozens of
# helpers, so no individual process crosses the runaway threshold even when the
# family consumes tens of GB and makes typing/clipboard actions lag.
INTERACTIVE_PRESSURE_MB: dict[str, int] = {
    "chrome": 16 * 1024,
    "code": 8 * 1024,
    "claude": 8 * 1024,
    "telegram": 8 * 1024,
    "explorer": 4 * 1024,
}

# navig's own process trees (spared by the sweep — each pid file names a tree
# root; a pid that started AFTER the file was written is a recycled pid and is
# NOT spared). Read directly: protection must not depend on navig being runnable.
NAVIG_PID_FILES: tuple[str, ...] = (
    "daemon/supervisor.pid",
    "gateway.pid",
    "agent/agent.pid",
    "worker.pid",
)

def _default_lease_files() -> tuple[str, ...]:
    """Registered dev-stack pid files, from ``NAVIG_LEASE_FILES`` (``os.pathsep``-separated).

    These used to be hardcoded drive paths for one machine. A shipped module cannot name a
    developer's disk layout: it put that path into the public tree and matched nothing on
    any other machine. Globs are expanded at the call site, so this is only the list:

        NAVIG_LEASE_FILES=C:\\work\\myapp\\.dev\\dev-run.pids.json;<other>\\*.dev\\dev-run.pids.json

    Add one for a single run with ``--lease-file``; skip the whole thing with
    ``--no-lease-files``.
    """
    raw = os.environ.get("NAVIG_LEASE_FILES", "")
    return tuple(p.strip() for p in raw.split(os.pathsep) if p.strip())


#: Resolved at import for callers that read the constant; the commands below re-read it so a
#: list set later in the process still applies.
DEFAULT_LEASE_FILES: tuple[str, ...] = _default_lease_files()



def _psutil_available() -> bool:
    return psutil is not None


def log_path() -> Path:
    """Where `guard run` / `guard sweep` append their lines."""
    return paths.log_dir() / "guard.log"


def _log(line: str, *, echo: bool = True) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp}  {line}\n")
    except OSError:
        pass
    if echo:
        print(f"{stamp}  {line}", flush=True)


def is_elevated() -> bool:
    """True when this process can terminate elevated (VS Code-owned) processes."""
    if sys.platform != "win32":
        return os.geteuid() == 0  # type: ignore[attr-defined]
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined,no-any-return]
    except Exception:  # noqa: BLE001 - ctypes failure means "cannot tell", not "admin"
        return False


# ─── Snapshots (pure structures, built once per pass) ───────────────────────

def snapshot_table() -> dict[int, tuple[str, int]]:
    """pid -> (name without .exe, ppid) in one pass — never per-process ppid()
    (on Windows each call re-snapshots the whole system, ~90 ms)."""
    if not _psutil_available():
        return {}
    table: dict[int, tuple[str, int]] = {}
    for p in psutil.process_iter(["pid", "ppid", "name"]):
        name = (p.info.get("name") or "").lower().removesuffix(".exe")
        table[p.info["pid"]] = (name, p.info.get("ppid") or 0)
    return table


class _CreateTimes(dict):
    """pid -> create_time on first use: only the pids actually walked pay for it
    (an elevated process's create_time costs ~120 ms on Windows)."""

    def __missing__(self, pid: int) -> float:
        try:
            value = psutil.Process(pid).create_time() if psutil else 0.0
        except Exception:  # noqa: BLE001 - unknown beats wrong: 0 means "unverifiable"
            value = 0.0
        self[pid] = value
        return value


def _ct(start_of: dict[int, float], pid: int) -> float:
    """create_time for pid, or 0.0 = unknown.

    Must index, not ``.get``: the lazy ``_CreateTimes`` loads through
    ``__missing__``, which ``dict.get`` bypasses — using ``.get`` here made every
    lease look dead and every orphan look ageless (caught by the live selftest).
    """
    try:
        return start_of[pid]
    except KeyError:
        return 0.0


def children_index(table: dict[int, tuple[str, int]]) -> dict[int, list[int]]:
    """ppid -> child pids."""
    kids: dict[int, list[int]] = {}
    for pid, (_name, ppid) in table.items():
        kids.setdefault(ppid, []).append(pid)
    return kids


def ancestors_of(pid: int, table: dict[int, tuple[str, int]], limit: int = 20) -> list[int]:
    """The chain from pid up to `limit` ancestors (pid first), cycle-safe."""
    chain: list[int] = []
    cur = pid
    seen: set[int] = set()
    while cur and cur not in seen and len(chain) < limit:
        seen.add(cur)
        chain.append(cur)
        entry = table.get(cur)
        if not entry:
            break
        cur = entry[1]
    return chain


def subtree_of(root: int, kids: dict[int, list[int]], live: set[int]) -> list[int]:
    """root + every descendant that is still in the snapshot (parents first)."""
    out: list[int] = []
    seen: set[int] = set()
    stack = [root]
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        if pid in live:
            out.append(pid)
        for c in kids.get(pid, ()):
            stack.append(c)
    return out


# ─── Protection sets ────────────────────────────────────────────────────────

def navig_tree_pids(
    table: dict[int, tuple[str, int]],
    start_of: dict[int, float],
    kids: dict[int, list[int]],
    config_dir: Path | None = None,
) -> dict[int, str]:
    """Navig's own pid-file trees: pid -> why it is spared.

    A pid file names a tree ROOT; the root and all descendants are spared — but
    only when the process is OLDER than the file naming it (a recycled pid
    would otherwise spare a stranger).
    """
    base = config_dir if config_dir is not None else paths.config_dir()
    out: dict[int, str] = {}
    live = set(table)
    for rel in NAVIG_PID_FILES:
        f = base / rel
        try:
            text = f.read_text(encoding="utf-8").strip()
            mtime = f.stat().st_mtime
        except OSError:
            continue
        try:
            root = int(text)
        except ValueError:
            continue
        if root <= 0 or root not in table or not _ct(start_of, root):
            continue
        if _ct(start_of, root) > mtime + 5:
            continue  # started after the file: recycled pid, not navig
        why = f"navig process tree ({f.name})"
        for pid in subtree_of(root, kids, live):
            out.setdefault(pid, why)
    return out


def load_leases(
    lease_files: list[Path],
    lease_dir: Path | None,
    table: dict[int, tuple[str, int]],
    start_of: dict[int, float],
    kids: dict[int, list[int]],
    *,
    now: float | None = None,
) -> dict[int, str]:
    """Keepalive leases: pid -> why spared (registered stacks + their launchers).

    Faithful port of the PowerShell loader: unreadable/expired/misowned/recycled
    leases protect NOTHING; only live roots (and their subtree + launcher chain)
    are spared, each ancestor no younger than its child.
    """
    now = now if now is not None else time.time()
    import glob as _glob

    candidates: list[Path] = []
    for pat in lease_files:
        pat = Path(pat)
        # glob support (the worktree pattern is a glob): stdlib glob handles
        # absolute patterns, which Path.glob() refuses.
        if any(ch in str(pat) for ch in "*?["):
            candidates.extend(Path(p) for p in sorted(_glob.glob(str(pat))))
        elif pat.exists():
            candidates.append(Path(pat))
    if lease_dir and lease_dir.is_dir():
        candidates.extend(sorted(lease_dir.glob("*.json")))

    out: dict[int, str] = {}
    for f in candidates:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        label = str(data.get("name") or data.get("repo") or f.stem)
        if data.get("repo"):
            # dev-run format: the file must live in <repo>\.dev\ of the repo it names.
            owner = f.parent.parent
            if str(owner).casefold() != str(data["repo"]).casefold():
                continue
        expires = data.get("expires")
        if expires:
            if isinstance(expires, (int, float)):
                exp = float(expires)
            else:
                exp = _parse_iso(str(expires))
                if exp is None:
                    continue
            if exp < now:
                continue  # expired: protects nothing
        roots: list[int] = []
        children = data.get("children") or []
        if not isinstance(children, (list, tuple)):
            children = [children]
        for v in [data.get("pid"), *children]:
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            if n > 0:
                roots.append(n)
        written = f.stat().st_mtime
        live = [
            r for r in roots
            if r != os.getpid() and r in table and _ct(start_of, r) and _ct(start_of, r) <= written + 5
        ]
        if not live:
            continue  # dead root: stale lease, protects nothing
        live_set = set(table)
        why = f"registered dev stack '{label}' (lease root {live[0]})"
        for root in live:
            for pid in subtree_of(root, kids, live_set):
                out.setdefault(pid, why)
            # Launcher chain: each live ancestor no younger than its child.
            cur = root
            for _ in range(20):
                entry = table.get(cur)
                if not entry:
                    break
                pp = entry[1]
                if pp <= 0 or pp not in table or not _ct(start_of, pp) or not _ct(start_of, cur):
                    break
                if _ct(start_of, pp) > _ct(start_of, cur):
                    break
                out.setdefault(pp, f"launcher of {why}")
                cur = pp
    return out


def _parse_iso(text: str) -> float | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# ─── guard sweep: the orphan reaper ─────────────────────────────────────────

def classify_orphans(
    table: dict[int, tuple[str, int]],
    protected: dict[int, str],
    lease_why: dict[int, str],
    *,
    min_age_min: int,
    start_of: dict[int, float],
    now: float,
) -> tuple[list[int], list[tuple[int, str, str]]]:
    """(kill_pids, skipped[(pid, name, reason)]) for the orphan sweep.

    A candidate is a dev-tool process whose parent is gone; its whole live
    subtree is collected (the parent shell and whatever it spawned die together).
    Spare order matches the script: session chain → never-kill name → lease →
    younger than min age (unknown age counts as old, as in the script).
    """
    kids = children_index(table)
    seen: set[int] = set()
    hits: list[int] = []
    for pid, (name, ppid) in table.items():
        if not DEV_TOOL_RE.match(name):
            continue
        if ppid in table:  # parent alive -> not an orphan
            continue
        stack = [pid]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            if cur in table:
                hits.append(cur)
            stack.extend(kids.get(cur, ()))
    kill: list[int] = []
    skipped: list[tuple[int, str, str]] = []
    for pid in hits:
        name = table[pid][0]
        if pid in protected:
            skipped.append((pid, name, protected[pid]))
            continue
        if name in NEVER_KILL:
            skipped.append((pid, name, f"protected name ({name})"))
            continue
        if pid in lease_why:
            skipped.append((pid, name, lease_why[pid]))
            continue
        age_min = None
        ct = _ct(start_of, pid)
        if ct:
            age_min = (now - ct) / 60
        if age_min is not None and age_min < min_age_min:
            skipped.append(
                (pid, name, f"orphan but only {age_min:.0f} min old (< {min_age_min}): "
                            f"in-flight detached work, spared until the next run")
            )
            continue
        kill.append(pid)
    return kill, skipped


# ─── sweeps 2–5 (ported from homelab scripts/cleanup-procs-and-files.ps1) ───
# The hourly Homelab-ProcCleanup task ran four more passes. They live here now
# so the script can retire; the guarded-task defaults below match its arguments
# (-RunawayMB 6000, leaked browsers 15 min, spin on, -CdpDirs).

#: OS-critical names the spin sweep may never touch — deliberately NOT the same
#: list as NEVER_KILL: editors ARE killable here, but only as leaves (a main
#: editor always has helper children, so only stuck leaf helpers match — that is
#: exactly what the sweep exists to catch: two VSCodium helpers pinned at 100%
#: for 19 and 12 days, ~282 CPU-hours wasted, invisible to the memory sweeps).
SPIN_OS_CRITICAL: frozenset[str] = frozenset({
    "system", "idle", "registry", "memcompression", "csrss", "wininit",
    "winlogon", "lsass", "services", "svchost", "smss", "fontdrvhost",
    "dwm", "explorer", "audiodg", "wudfhost", "sihost",
    # Virtual machines: leaf-shaped, ancient and legitimately burning CPU is
    # what a running VM LOOKS like. Caught by the first live dry-run of this
    # port (2026-10-14): vmmemwsl at 382% for 415h, i.e. the operator's own
    # Ubuntu box — killing it would destroy the VM (the PS original would have).
    "vmmem", "vmmemwsl", "vmwp",
})


def has_editor_ancestor(pid: int, table: dict[int, tuple[str, int]], limit: int = 20) -> bool:
    """True when any ancestor (≤20 hops) is an editor root (Code/VSCodium)."""
    cur = pid
    seen: set[int] = set()
    for _ in range(limit):
        if not cur or cur in seen:
            return False
        seen.add(cur)
        entry = table.get(cur)
        if not entry:
            return False
        if entry[0] in ("code", "vscodium"):
            return True
        cur = entry[1]
    return False


def sweep_runaway(
    table: dict[int, tuple[str, int]],
    protected: set[int],
    lease_why: dict[int, str],
    footprints: dict[int, float],
    *,
    runaway_mb: int,
    hard_mb: int,
    dry_run: bool = False,
) -> dict[str, Any]:
    """node/python over --runaway-mb: report, kill, or hold — per the script.

    A leased dev stack's server is big by nature (Next dev 7–12 GB): HOLD and
    report it — unless it crosses the hard ceiling, which overrides both the
    lease and the editor-descendant exemption.
    """
    report: dict[str, Any] = {"killed": [], "skipped": [], "held": []}
    if runaway_mb <= 0:
        return report
    for pid, (name, _ppid) in sorted(table.items()):
        if name not in ("node", "python", "pythonw"):
            continue
        mb = footprints.get(pid, 0.0)
        if mb < runaway_mb:
            continue
        why_skip: str | None = None
        if pid in protected:
            why_skip = "session chain"
        elif name in NEVER_KILL:
            why_skip = f"protected name ({name})"
        over = hard_mb > 0 and mb >= hard_mb
        reason: str | None = None
        if why_skip is None and pid in lease_why:
            if not over:
                report["held"].append(
                    f"{name} ({pid}) — {mb:.0f} MB, {lease_why[pid]}; reported, not killed "
                    f"(below the {hard_mb} MB hard ceiling)"
                )
                continue
            reason = f"{lease_why[pid]} but OVER {mb:.0f} MB >= {hard_mb} MB ceiling"
        if why_skip is None and has_editor_ancestor(pid, table):
            if not over:
                report["skipped"].append((pid, name, f"editor descendant, {mb:.0f} MB below ceiling"))
                continue
            reason = f"editor descendant but OVER {mb:.0f} MB >= {hard_mb} MB ceiling"
        if why_skip is not None:
            report["skipped"].append((pid, name, why_skip))
            continue
        if dry_run:
            report["killed"].append(pid)
            continue
        if kill_tree(pid):
            report["killed"].append(pid)
            if reason:
                report.setdefault("killed_reasons", []).append(f"{name} ({pid}): {reason}")
    return report


def aggregate_pressure(
    table: dict[int, tuple[str, int]],
    footprints: dict[int, float],
    thresholds: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Report interactive process families whose combined memory is excessive.

    This pass is diagnostic only. A real browser/editor is user state, and an
    hourly guard must not close it merely because many valid tabs or sessions
    are open. The report makes split-process pressure visible so the operator
    can restart the right app instead of hunting for one impossible 20 GB PID.
    """
    limits = thresholds or INTERACTIVE_PRESSURE_MB
    totals: dict[str, float] = dict.fromkeys(limits, 0.0)
    counts: dict[str, int] = dict.fromkeys(limits, 0)
    for pid, (name, _ppid) in table.items():
        if name not in limits:
            continue
        totals[name] += footprints.get(pid, 0.0)
        counts[name] += 1
    return [
        {"name": name, "count": counts[name], "mb": round(totals[name], 1), "threshold_mb": limits[name]}
        for name in limits
        if totals[name] >= limits[name]
    ]


def parse_user_data_dir(cmdline: str) -> str | None:
    m = re.search(r'--user-data-dir=("([^"]*)"|\S+)', cmdline)
    if not m:
        return None
    return m.group(2) if m.group(2) is not None else m.group(1)


def owner_state(udd: str | None, table: dict[int, tuple[str, int]], start_of: dict[int, float]) -> str:
    """Who owns this browser profile: 'alive' | 'owner dead' | 'pid recycled' |
    'no owner.json' | 'unreadable' — the harness's owner.json is the signal
    (on Windows the launcher exits by design, so "parent gone" says nothing)."""
    if not udd:
        return "no owner.json"
    try:
        data = json.loads((Path(udd) / "owner.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "no owner.json"
    except (OSError, ValueError):
        return "unreadable"
    try:
        opid = int(data["pid"])
    except (KeyError, TypeError, ValueError):
        return "unreadable"
    if opid <= 0 or opid not in table:
        return "owner dead"
    started_at = data.get("startedAt")
    if started_at and _ct(start_of, opid) and _ct(start_of, opid) > float(started_at) / 1000 + 5:
        return "pid recycled"
    return "alive"


def sweep_leaked_browsers(
    chrome_cmds: dict[int, str],
    table: dict[int, tuple[str, int]],
    start_of: dict[int, float],
    *,
    min_age_min: int,
    dry_run: bool = False,
    now: float | None = None,
    owner_fn: Any = owner_state,
) -> dict[str, Any]:
    """Headless test browsers whose harness OWNER is gone (≥ min_age_min old).

    chrome_cmds: pid -> cmdline for every chrome.exe (None-worthiness handled by
    the caller). Runs in progress keep a live owner and are never touched;
    navig's own cdp-profiles browsers belong to `navig cdp stop`.
    """
    report: dict[str, Any] = {"killed": [], "skipped": []}
    if min_age_min <= 0:
        return report
    now = now if now is not None else time.time()
    for pid, cmd in sorted(chrome_cmds.items()):
        if "--type=" in cmd:
            continue
        if "--headless" not in cmd or "--remote-debugging-port" not in cmd:
            continue
        udd = parse_user_data_dir(cmd)
        if udd and "cdp-profiles" in udd.replace("\\", "/"):
            report["skipped"].append((pid, "chrome", "navig cdp profile (navig cdp stop owns it)"))
            continue
        ct = _ct(start_of, pid)
        if not ct:
            report["skipped"].append((pid, "chrome", "start time unreadable"))
            continue
        age_min = int((now - ct) // 60)
        if age_min < min_age_min:
            report["skipped"].append((pid, "chrome", f"{age_min} min old (< {min_age_min})"))
            continue
        state = owner_fn(udd, table, start_of)
        if state == "alive":
            report["skipped"].append((pid, "chrome", f"owner is alive ({age_min} min)"))
            continue
        if dry_run:
            report["killed"].append(pid)
            continue
        if kill_tree(pid):
            report["killed"].append(pid)
            report.setdefault("killed_reasons", []).append(f"{pid}: {state}, {age_min} min")
    return report


def sample_spinners(
    *,
    measure: Any,
    child_counts: dict[int, int],
    table: dict[int, tuple[str, int]],
    start_of: dict[int, float],
    protected: set[int],
    min_pct: float,
    min_age_h: float,
    min_cpu_h: float,
    dry_run: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """Leaf processes stuck burning a core forever.

    `measure()` returns {pid: (core_pct_during_window, total_cpu_hours)} — the
    sampler, so this decision logic stays testable. ALL of sustained spin,
    ≥ min_age_h old, ≥ min_cpu_h burned, and ZERO children must hold; the leaf
    rule is what keeps a real editor (always with helpers) safe.
    """
    report: dict[str, Any] = {"killed": [], "skipped": [], "hits": []}
    now = now if now is not None else time.time()
    for pid, (pct, cpu_hours) in measure().items():
        if pct < min_pct:
            continue
        if child_counts.get(pid, 0) > 0:
            continue
        ct = _ct(start_of, pid)
        if not ct or (now - ct) / 3600 < min_age_h:
            continue
        if cpu_hours < min_cpu_h:
            continue
        name = table.get(pid, ("<gone>", 0))[0]
        if pid in protected:
            report["skipped"].append((pid, name, "session chain"))
            continue
        if name in SPIN_OS_CRITICAL:
            report["skipped"].append((pid, name, "OS-critical"))
            continue
        report["hits"].append(f"{name} ({pid}) — {pct:.0f}% of a core, age {(now - ct) / 3600:.1f}h, "
                              f"{cpu_hours:.1f} CPU-hours burned, 0 children")
        if dry_run:
            report["killed"].append(pid)
            continue
        if kill_tree(pid):
            report["killed"].append(pid)
    return report


def chrome_user_data_dirs(chrome_cmds: dict[int, str]) -> set[str]:
    """Lowercased --user-data-dir values of running chromes (live profiles)."""
    out: set[str] = set()
    for cmd in chrome_cmds.values():
        udd = parse_user_data_dir(cmd)
        if udd:
            out.add(udd.strip().lower())
    return out


def sweep_cdp_dirs(
    *,
    dry_run: bool,
    chrome_cmds: dict[int, str],
    stop_fn: Any = None,
    tmp_root: Path | None = None,
    sessions_root: Path | None = None,
) -> dict[str, Any]:
    """Reclaim leftover CDP browser profile directories on disk.

    Graceful first: `navig cdp stop --all` closes navig's own live browsers.
    A profile still used by a running browser is never deleted; if we cannot
    prove which profiles are idle (cmdlines unavailable) we delete nothing.
    """
    import shutil as _shutil

    report: dict[str, Any] = {"deleted": [], "kept": [], "freed_mb": 0, "skipped": ""}
    if chrome_cmds is None:
        report["skipped"] = "chrome cmdlines unavailable — cannot confirm which profiles are idle; skipping"
        return report
    if not dry_run and stop_fn is not None:
        try:
            stop_fn()
        except Exception as exc:  # noqa: BLE001 - best effort, same as the script
            report.setdefault("notes", []).append(f"navig cdp stop failed: {exc}")
    live = chrome_user_data_dirs(chrome_cmds or {})
    roots = [tmp_root if tmp_root is not None else Path(os.environ.get("LOCALAPPDATA", "")) / "Temp",
             sessions_root if sessions_root is not None else paths.config_dir() / "cdp-profiles" / "sessions"]
    candidates: list[Path] = []
    if roots[0].is_dir():
        candidates.extend(sorted(p for p in roots[0].glob("cdp-*") if p.is_dir()))
    if roots[1].is_dir():
        candidates.extend(sorted(p for p in roots[1].iterdir() if p.is_dir()))
    for d in candidates:
        if str(d).lower() in live:
            report["kept"].append(str(d))
            continue
        size_mb = 0.0
        try:
            size_mb = sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / 2**20
        except OSError:
            pass
        if dry_run:
            report["deleted"].append((str(d), round(size_mb)))
            continue
        try:
            _shutil.rmtree(d, ignore_errors=False)
            report["deleted"].append((str(d), round(size_mb)))
            report["freed_mb"] += round(size_mb)
        except OSError as exc:
            report.setdefault("notes", []).append(f"could not remove {d}: {exc}")
    return report


def _chrome_cmdlines() -> tuple[dict[int, str], bool]:
    """pid -> cmdline for every chrome.exe; second value = some unreadable."""
    cmds: dict[int, str] = {}
    denied = False
    if not _psutil_available():
        return cmds, True
    for p in psutil.process_iter(["name"]):
        if (p.info.get("name") or "").lower() != "chrome.exe":
            continue
        try:
            cmds[p.pid] = " ".join(p.cmdline())
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            denied = True
    return cmds, denied


def footprint_mb(p: Any) -> float:
    """Whichever of working set / private is larger — the runaway threshold's
    unit in the script (95 GB WS vs 326 GB commit taught it not to trust WS)."""
    mi = p.memory_info()
    return max(mi.rss, getattr(mi, "private", 0)) / 2**20


def _spinner_measure(window_sec: float) -> dict[int, tuple[float, float]]:
    """Sample every process's CPU across `window_sec` -> {pid: (core_pct, cpu_hours)}."""
    if not _psutil_available():
        return {}
    first: dict[int, tuple[float, float]] = {}
    for p in psutil.process_iter(["pid"]):
        try:
            t = p.cpu_times()
            first[p.pid] = (t.user + t.system, 0.0)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    time.sleep(window_sec)
    out: dict[int, tuple[float, float]] = {}
    for p in psutil.process_iter(["pid"]):
        if p.pid not in first:
            continue
        try:
            t = p.cpu_times()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        total = t.user + t.system
        delta = total - first[p.pid][0]
        if delta <= 0:
            continue
        out[p.pid] = (delta / window_sec * 100.0, total / 3600.0)
    return out


def kill_tree(pid: int) -> int:
    """Force-kill pid and its descendants, children first. Returns kills made."""
    if not _psutil_available():
        return 0
    try:
        root = psutil.Process(pid)
        targets = root.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        targets = []
    killed = 0
    for p in targets:
        try:
            p.kill()
            killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):  # noqa: PERF203 - best effort per proc
            pass
    try:
        root.kill()
        killed += 1
    except (psutil.NoSuchProcess, psutil.AccessDenied, UnboundLocalError):
        pass
    return killed


def _navig_cdp_stop_all() -> None:
    """Let navig close its own live browsers before we delete profile dirs."""
    # encoding named: the child is navig itself and writes the console page,
    # so a bare text=True decodes with the ANSI table (the subprocess-encoding
    # guard in tests/quality names this line). Same remedy as _run_pwsh below.
    subprocess.run(
        [sys.executable, "-m", "navig", "cdp", "stop", "--all"],
        capture_output=True, encoding=console_encoding(), errors="replace",
        timeout=90,
    )


def sweep_once(
    *,
    min_age_min: int = 30,
    dry_run: bool = False,
    lease_files: list[Path] | None = None,
    lease_dir: Path | None = None,
    echo: bool = True,
    runaway_mb: int = 6000,
    hard_mb: int = 32768,
    leaked_min_age: int = 15,
    spin: bool = True,
    spin_pct: float = 80.0,
    spin_min_age_h: float = 48.0,
    spin_min_cpu_h: float = 24.0,
    spin_sample_sec: float = 4.0,
    cdp_dirs: bool = True,
) -> dict[str, Any]:
    """All five hourly sweeps in one pass (the Homelab-ProcCleanup task's
    behaviour, arguments defaulting to exactly what that task passed):
    orphans, runaway node/python, leaked test browsers, CPU-spinners, CDP dirs.

    Returns the original orphan-report keys plus ``passes`` for the others.
    """
    if not _psutil_available():
        return {"error": "psutil not installed"}
    table = snapshot_table()
    start_of = _CreateTimes()
    kids = children_index(table)
    protected: dict[int, str] = {}
    for pid in ancestors_of(os.getpid(), table):
        protected[pid] = "session chain"
    # Force-create times for the session chain (protective, cheap: ≤20 pids).
    for pid in list(protected):
        start_of[pid]
    protected.update(navig_tree_pids(table, start_of, kids))
    files = lease_files if lease_files is not None else [Path(p) for p in _default_lease_files()]
    lease_why = load_leases(files, lease_dir if lease_dir is not None else paths.config_dir() / "leases",
                            table, start_of, kids)
    passes: dict[str, Any] = {}

    # ── pass 1: orphaned dev tools (parent gone) ───────────────────────────
    kill, skipped = classify_orphans(
        table, protected, lease_why, min_age_min=min_age_min, start_of=start_of, now=time.time(),
    )
    killed: list[int] = []
    for pid in kill:
        if dry_run:
            killed.append(pid)
            if echo:
                print(f"  DRY RUN: would kill {table[pid][0]} ({pid}) parent gone", flush=True)
            continue
        if kill_tree(pid):
            killed.append(pid)
    passes["orphan"] = {"killed": len(killed), "skipped": len(skipped)}

    # ── pass 2: runaway node/python over the soft threshold ────────────────
    footprints: dict[int, float] = {}
    wanted_footprints = {"node", "python", "pythonw"} | set(INTERACTIVE_PRESSURE_MB)
    if _psutil_available():
        for p in psutil.process_iter(["name"]):
            base = (p.info.get("name") or "").lower().removesuffix(".exe")
            if base not in wanted_footprints:
                continue
            try:
                footprints[p.pid] = footprint_mb(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    runaway = sweep_runaway(table, set(protected), lease_why, footprints,
                            runaway_mb=runaway_mb, hard_mb=hard_mb, dry_run=dry_run)
    passes["pressure"] = {"hits": aggregate_pressure(table, footprints)}
    passes["runaway"] = {
        "killed": len(runaway["killed"]),
        "skipped": len(runaway["skipped"]),
        "held": runaway["held"],
        "skipped_detail": runaway["skipped"],
        "killed_reasons": runaway.get("killed_reasons", []),
    }

    # ── passes 3 + 5 share the chrome cmdline fetch (one read) ─────────────
    chrome_cmds, denied = _chrome_cmdlines()
    if denied:
        passes["leaked"] = {"killed": 0, "skipped": "chrome cmdlines unreadable — cannot judge safely"}
        passes["cdp_dirs"] = {"deleted": 0, "skipped": "chrome cmdlines unavailable — skipped"}
    else:
        leaked = sweep_leaked_browsers(chrome_cmds, table, start_of,
                                       min_age_min=leaked_min_age, dry_run=dry_run)
        passes["leaked"] = {
            "killed": len(leaked["killed"]),
            "skipped": len(leaked["skipped"]),
            "reasons": [f"{pid}: {why}" for pid, _n, why in leaked["skipped"]] if echo else None,
            "killed_reasons": leaked.get("killed_reasons", []),
        }
        if cdp_dirs:
            dirs = sweep_cdp_dirs(dry_run=dry_run, chrome_cmds=chrome_cmds,
                                  stop_fn=None if dry_run else _navig_cdp_stop_all)
            passes["cdp_dirs"] = dirs

    # ── pass 4: leaf CPU-spinners (sustained burn + multi-day age) ─────────
    if spin:
        child_counts = {pp: len(cs) for pp, cs in kids.items()}
        spin_report = sample_spinners(
            measure=lambda: _spinner_measure(spin_sample_sec),
            child_counts=child_counts, table=table, start_of=start_of,
            protected=set(protected), min_pct=spin_pct, min_age_h=spin_min_age_h,
            min_cpu_h=spin_min_cpu_h, dry_run=dry_run,
        )
        passes["spin"] = {"killed": len(spin_report["killed"]), "skipped": len(spin_report["skipped"]),
                          "hits": spin_report["hits"],
                          "skip_reasons": [f"{n} ({p}) — {w}" for p, n, w in spin_report["skipped"]]}

    report = {
        "killed": len(killed),
        "killed_pids": killed,
        "skipped": len(skipped),
        "skip_reasons": [f"{name} ({pid}) — {why}" for pid, name, why in skipped],
        "leases_spared": len(set(lease_why) & set(table)),
        "dry_run": dry_run,
        "passes": passes,
    }
    parts = [f"orphan killed={passes['orphan']['killed']}"]
    if runaway_mb > 0:
        parts.append(f"runaway killed={passes['runaway']['killed']} held={passes['runaway']['held']}")
    if "leaked" in passes and isinstance(passes["leaked"].get("killed"), int):
        parts.append(f"leaked killed={passes['leaked']['killed']}")
    if spin:
        parts.append(f"spin killed={passes['spin']['killed']}")
    if "cdp_dirs" in passes and not passes["cdp_dirs"].get("skipped"):
        parts.append(f"cdp-dirs freed={passes['cdp_dirs'].get('freed_mb', 0)}MB")
    pressure = passes["pressure"]["hits"]
    if pressure:
        detail = "/".join(f"{p['name']}={p['mb'] / 1024:.1f}GB({p['count']})" for p in pressure)
        parts.append(f"pressure {detail}")
    _log("sweep: " + ", ".join(parts) + (f" [dry run]" if dry_run else ""), echo=echo)
    return report


# ─── guard run: the watchdog loop ──────────────────────────────────────────

def guard_verdict(
    mb: float,
    *,
    warn_mb: int,
    kill_mb: int,
    prev_mb: float,
    protected: bool = False,
    never_kill: bool = False,
) -> str:
    """'kill' | 'warn' | 'keep' for one watched process (pure decision)."""
    if protected or never_kill:
        return "keep"
    if mb >= kill_mb:
        return "kill"
    if mb >= warn_mb and (prev_mb == 0 or mb >= prev_mb * 1.5):
        return "warn"
    return "keep"


def _memory_mb(p: Any) -> float:
    """Private bytes (Windows commit — what exhausts the limit); RSS elsewhere."""
    mi = p.memory_info()
    return (getattr(mi, "private", 0) or mi.rss) / 2**20


def _ancestry_text(pid: int) -> str:
    """name[pid] :: cmd  chain for the warn log (best effort, ≤6 levels)."""
    if not _psutil_available():
        return ""
    parts: list[str] = []
    cur: int | None = pid
    seen: set[int] = set()
    for _ in range(6):
        if cur is None or cur in seen:
            break
        seen.add(cur)
        try:
            p = psutil.Process(cur)
            try:
                cmd = " ".join(p.cmdline())
            except Exception:  # noqa: BLE001 - elevated children refuse cmdline
                cmd = "<unreadable>"
            parts.append(f"{p.name()}[{cur}] {cmd[:180]}")
            cur = p.ppid()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            break
    return " <- ".join(parts)


def deprioritize(names: tuple[str, ...], protected: set[int]) -> int:
    """Hold watched builds at below-normal so the desktop stays responsive."""
    if not _psutil_available() or sys.platform != "win32":
        return 0
    lowered = 0
    needles = {n.lower() for n in names}
    for p in psutil.process_iter(["name"]):
        name = (p.info.get("name") or "").lower().removesuffix(".exe")
        if name not in needles or p.pid in protected:
            continue
        try:
            if p.priority_class == psutil.NORMAL_PRIORITY_CLASS:  # type: ignore[attr-defined]
                p.priority_class = psutil.BELOW_NORMAL_PRIORITY_CLASS  # type: ignore[attr-defined]
                lowered += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError, OSError):
            continue
    return lowered


def run_watch(
    *,
    interval: float = 4.0,
    warn_mb: int = 8000,
    kill_mb: int = 32768,
    names: tuple[str, ...] = WATCH_NAMES,
    deprioritize_builds: bool = True,
) -> None:
    """The watchdog loop (foreground; the scheduled task runs this)."""
    if not _psutil_available():
        print("psutil not installed — guard run cannot watch processes", file=sys.stderr)
        raise typer.Exit(1)
    elevated = is_elevated()
    _log(f"guard run started: interval={interval}s warn={warn_mb}MB kill={kill_mb}MB "
         f"names={','.join(names)} elevated={elevated}")
    if not elevated and sys.platform == "win32":
        _log("WARNING: not elevated — kills of VS Code-owned processes will fail; run elevated")
    warned: dict[int, float] = {}
    needles = {n.lower() for n in names}
    try:
        while True:
            table = snapshot_table()
            protected: set[int] = set(ancestors_of(os.getpid(), table))
            if deprioritize_builds:
                deprioritize(names, protected)
            for p in psutil.process_iter(["name"]):
                name = (p.info.get("name") or "").lower().removesuffix(".exe")
                if name not in needles:
                    continue
                try:
                    mb = _memory_mb(p)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
                verdict = guard_verdict(
                    mb, warn_mb=warn_mb, kill_mb=kill_mb, prev_mb=warned.get(p.pid, 0.0),
                    protected=p.pid in protected, never_kill=name in NEVER_KILL,
                )
                if verdict == "kill":
                    _log(f"KILL {name}.exe pid={p.pid} commit={mb:.0f}MB exceeds {kill_mb}MB — terminating tree")
                    kill_tree(p.pid)
                    warned.pop(p.pid, None)
                elif verdict == "warn":
                    warned[p.pid] = mb
                    _log(f"WARN {name}.exe pid={p.pid} commit={mb:.0f}MB age={_age_text(p)}")
                    _log(f"        {_ancestry_text(p.pid)}")
                elif verdict == "keep" and p.pid in warned and mb < warn_mb:
                    warned.pop(p.pid, None)
            time.sleep(interval)
    except KeyboardInterrupt:
        _log("guard run stopped (Ctrl+C)")


def _age_text(p: Any) -> str:
    try:
        return f"{(time.time() - p.create_time()) / 60:.1f}min"
    except Exception:  # noqa: BLE001
        return "?"


# ─── scheduled tasks (Windows) ─────────────────────────────────────────────

TASK_GUARD = "Navig-Guard"
TASK_SWEEP = "Navig-ProcSweep"
OLD_GUARD = "Homelab-RunawayGuard"
OLD_SWEEP = "Homelab-ProcCleanup"


def _run_pwsh(script: str) -> tuple[int, str]:
    """Run PowerShell with the script on stdin (no quoting pain)."""
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", "-"]
    try:
        # encoding named: powershell writes the CONSOLE code page (chcp), and a bare
        # text=True decodes with the ANSI one — same bytes, two tables (the two
        # subprocess-encoding guards in tests/quality catch exactly this line).
        res = subprocess.run(cmd, input=script, capture_output=True,
                             encoding=console_encoding(), errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return res.returncode, (res.stdout + res.stderr).strip()


def _task_action(exe: str, args: str) -> str:
    """PowerShell literal for the action (single-quoted, escaping ' )."""
    return f"(New-ScheduledTaskAction -Execute '{exe.replace(chr(39), chr(39) * 2)}' " \
           f"-Argument '{args.replace(chr(39), chr(39) * 2)}')"


def _register_task(name: str, action_cmd: str, *, hourly: bool) -> tuple[int, str]:
    principal_user = os.environ.get("USERNAME") or "current-user"
    if hourly:
        script = f"""
$ErrorActionPreference = 'Stop'
$act = {action_cmd}
$trg = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Hours 1)
$prn = New-ScheduledTaskPrincipal -UserId '{principal_user}' -LogonType S4U -RunLevel Highest
Register-ScheduledTask -TaskName '{name}' -Action $act -Trigger $trg -Principal $prn -Force | Out-Null
'ok {name} (hourly)'
"""
    else:
        script = f"""
$ErrorActionPreference = 'Stop'
$act = {action_cmd}
$trg = New-ScheduledTaskTrigger -AtLogOn
$prn = New-ScheduledTaskPrincipal -UserId '{principal_user}' -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName '{name}' -Action $act -Trigger $trg -Principal $prn -Force | Out-Null
'ok {name} (at logon)'
"""
    return _run_pwsh(script)


def _task_exists(name: str) -> bool:
    _rc, out = _run_pwsh(
        f"if (Get-ScheduledTask -TaskName '{name}' -ErrorAction SilentlyContinue) {{ 'yes' }}"
    )
    return "yes" in out


# ─── Commands ──────────────────────────────────────────────────────────────

@guard_app.command("run")
def guard_run(
    interval: float = typer.Option(4.0, "--interval", "-i", help="Seconds between watches."),
    warn_mb: int = typer.Option(8000, "--warn-mb", help="Log (with ancestry) at this commit in MB."),
    kill_mb: int = typer.Option(32768, "--kill-mb", help="Kill the tree at this commit in MB. 0 = never kill."),
    names: str = typer.Option("", "--names", help="Comma list to watch (default: node,python,…)."),
    no_deprioritize: bool = typer.Option(False, "--no-deprioritize", help="Do not hold watched builds at below-normal."),
) -> None:
    """Watch memory hogs: warn with ancestry, kill past the ceiling, hold builds down.

    Run it from an elevated shell (the scheduled task does) — killing VS Code-owned
    processes refuses otherwise. Log: paths.log_dir()/guard.log.
    """
    watch = tuple(n.strip() for n in names.split(",") if n.strip()) if names else WATCH_NAMES
    run_watch(interval=interval, warn_mb=warn_mb, kill_mb=kill_mb, names=watch,
              deprioritize_builds=not no_deprioritize)


@guard_app.command("sweep")
def guard_sweep(
    min_age_min: int = typer.Option(30, "--min-age", help="Spare orphans younger than this (minutes)."),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="List what would be killed."),
    lease_file: list[str] = typer.Option([], "--lease-file", help="Extra lease glob/file (repeatable)."),
    no_lease_files: bool = typer.Option(False, "--no-lease-files", help="Skip the built-in dev-stack lease files."),
    runaway_mb: int = typer.Option(6000, "--runaway-mb", help="Report/kill node/python at this MB (0 = off)."),
    hard_ceiling_mb: int = typer.Option(32768, "--hard-ceiling-mb", help="Leases/editor-exemption do not protect past this."),
    leaked_min_age: int = typer.Option(15, "--leaked-min", help="Reap headless test browsers whose owner is gone, at least this old (minutes, 0 = off)."),
    no_spin: bool = typer.Option(False, "--no-spin", help="Skip the CPU-spin sweep."),
    no_cdp_dirs: bool = typer.Option(False, "--no-cdp-dirs", help="Skip reclaiming leftover CDP profile dirs."),
) -> None:
    """The full hourly sweep: orphans, runaway node/python, leaked test browsers,
    stuck CPU spinners, leftover CDP profile dirs — leases and session chains
    spared throughout. Defaults match the old Homelab-ProcCleanup task."""
    files = [] if no_lease_files else [Path(p) for p in _default_lease_files()]
    files.extend(Path(p) for p in lease_file)
    report = sweep_once(
        min_age_min=min_age_min, dry_run=dry_run, lease_files=files,
        runaway_mb=runaway_mb, hard_mb=hard_ceiling_mb, leaked_min_age=leaked_min_age,
        spin=not no_spin, cdp_dirs=not no_cdp_dirs,
    )
    if "error" in report:
        print(f"error: {report['error']}", file=sys.stderr)
        raise typer.Exit(1)
    for line in report["skip_reasons"]:
        print(f"  SKIP  {line}")
    print(f"  {'would kill' if dry_run else 'killed'} {report['killed']}; skipped {report['skipped']}")
    passes = report.get("passes", {})
    for hit in passes.get("pressure", {}).get("hits", []):
        print(
            f"  PRESSURE  {hit['name']} — {hit['mb'] / 1024:.1f} GB across {hit['count']} processes "
            f"(warn at {hit['threshold_mb'] / 1024:.0f} GB; live app preserved)"
        )
    if passes.get("runaway"):
        r = passes["runaway"]
        for held in r.get("held", []):
            print(f"  HOLD  {held}")
        if r["killed"]:
            print(f"  runaway: {'would kill' if dry_run else 'killed'} {r['killed']} node/python over {runaway_mb} MB")
        for why in r.get("killed_reasons", [])[:10]:
            print(f"    OVER {why}")
        for pid, name, why in r.get("skipped_detail", [])[:10]:
            print(f"  SKIP  {name} ({pid}) — {why}")
    if passes.get("leaked"):
        if passes["leaked"].get("skipped"):
            print(f"  SKIP  leaked browsers: {passes['leaked']['skipped']}")
        elif passes["leaked"]["killed"]:
            print(f"  leaked browsers: {'would kill' if dry_run else 'killed'} {passes['leaked']['killed']}")
            for why in (passes["leaked"].get("killed_reasons") or [])[:10]:
                print(f"    {why}")
        else:
            print("  leaked browsers: none")
    if passes.get("spin"):
        s = passes["spin"]
        for hit in s.get("hits", []):
            print(f"  SPIN  {hit}" + (" (dry run)" if dry_run else ""))
        if not s["hits"] and not s["skipped"]:
            print("  no stuck CPU spinners found")
        for why in s.get("skip_reasons", []):
            print(f"  SKIP  {why}")
    if passes.get("cdp_dirs"):
        d = passes["cdp_dirs"]
        if d.get("skipped"):
            print(f"  SKIP  cdp dirs: {d['skipped']}")
        elif d["deleted"]:
            for path, mb in d["deleted"]:
                print(f"  DEL   {path}  ({mb} MB)")
            print(f"  CDP profile disk reclaimed: ~{d.get('freed_mb', 0)} MB")
        else:
            print("  no leftover CDP profile dirs found")
    if not is_elevated() and sys.platform == "win32" and report["killed"] and not dry_run:
        print("  note: not elevated — VS Code-owned processes may have been refused")


@guard_app.command("install")
def guard_install(
    interval: float = typer.Option(4.0, "--interval", "-i", help="Guard watch interval in seconds."),
    no_hourly: bool = typer.Option(False, "--no-hourly", help="Skip the hourly Navig-ProcSweep task."),
    keep_old_guard: bool = typer.Option(False, "--keep-old-guard", help="Do not disable Homelab-RunawayGuard."),
) -> None:
    """Register Navig-Guard (at logon) and Navig-ProcSweep (hourly) as scheduled tasks.

    Needs elevation. Disables Homelab-RunawayGuard (fully replaced) unless
    --keep-old-guard. Homelab-ProcCleanup keeps running its extra sweeps (CDP
    dirs, 6 GB report, CPU-spin); its orphan pass now overlaps idempotently —
    retire it once those are ported.
    """
    if sys.platform != "win32":
        print("error: guard install is Windows-only (Task Scheduler)", file=sys.stderr)
        raise typer.Exit(1)
    if not is_elevated():
        print("error: run 'navig guard install' from an elevated shell (task registration "
              "and RunLevel Highest require it)", file=sys.stderr)
        raise typer.Exit(1)
    exe = sys.executable
    rc, out = _register_task(
        TASK_GUARD,
        _task_action(exe, f'-m navig guard run --interval {interval:g} --warn-mb 8000 --kill-mb 32768'),
        hourly=False,
    )
    print(out or f"guard task: rc={rc}")
    if rc != 0:
        raise typer.Exit(1)
    if not no_hourly:
        rc, out = _register_task(TASK_SWEEP, _task_action(exe, "-m navig guard sweep"), hourly=True)
        print(out or f"sweep task: rc={rc}")
        if rc != 0:
            raise typer.Exit(1)
    if not keep_old_guard and _task_exists(OLD_GUARD):
        rc, out = _run_pwsh(
            f"Stop-ScheduledTask -TaskName '{OLD_GUARD}' -ErrorAction SilentlyContinue; "
            f"Disable-ScheduledTask -TaskName '{OLD_GUARD}' -ErrorAction SilentlyContinue; 'disabled {OLD_GUARD}'"
        )
        print(out or f"disable rc={rc}")
    print("installed: navig guard status  (log: " + str(log_path()) + ")")


@guard_app.command("uninstall")
def guard_uninstall(
    restore_old: bool = typer.Option(False, "--restore-old", help="Re-enable Homelab-RunawayGuard."),
) -> None:
    """Remove the Navig-Guard / Navig-ProcSweep tasks."""
    if sys.platform != "win32":
        print("error: Windows-only", file=sys.stderr)
        raise typer.Exit(1)
    if not is_elevated():
        print("error: run elevated", file=sys.stderr)
        raise typer.Exit(1)
    rc, out = _run_pwsh(
        f"foreach ($t in @('{TASK_GUARD}','{TASK_SWEEP}')) {{ Unregister-ScheduledTask -TaskName $t "
        f"-Confirm:$false -ErrorAction SilentlyContinue }}; 'removed navig guard tasks'"
    )
    print(out or f"rc={rc}")
    if restore_old and _task_exists(OLD_GUARD):
        _run_pwsh(f"Enable-ScheduledTask -TaskName '{OLD_GUARD}'; 're-enabled {OLD_GUARD}'")
        print(f"re-enabled {OLD_GUARD}")


@guard_app.command("status")
def guard_status() -> None:
    """Show the watchdog tasks and the last guard log lines."""
    if sys.platform == "win32":
        rc, out = _run_pwsh(
            "foreach ($t in @('Navig-Guard','Navig-ProcSweep','Homelab-RunawayGuard','Homelab-ProcCleanup')) { "
            "$s = Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue; "
            "if ($s) { $i = $s | Get-ScheduledTaskInfo; "
            "'{0,-22} {1,-8} last={2} result={3}' -f $t, $s.State, $i.LastRunTime, $i.LastTaskResult } "
            "else { '{0,-22} absent' -f $t } }"
        )
        print(out)
    log = log_path()
    print(f"log: {log}")
    if log.exists():
        try:
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
            print("\n".join(lines[-8:]))
        except OSError as exc:
            print(f"  (unreadable: {exc})")
