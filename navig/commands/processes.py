"""
Cross-platform process management — find and kill processes by name, group,
or special selector (Chrome tab renderers, etc.).

Pure psutil-based — no shell-outs, no platform branches in the caller.
Returns plain dicts so the same module is usable from Telegram handlers,
the CLI, Deck REST routes, and tests.

`monitor.py` stays read-only by contract; destructive operations live here.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
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


def find_chrome_renderers(include_extensions: bool = False) -> list[ProcInfo]:
    """Find Chrome renderer processes (each browser tab is one renderer).
    `include_extensions=False` excludes extension hosts so killing only kills tabs.

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
            is_extension = "--extension-process" in cmd
            if is_extension and not include_extensions:
                continue
            out.append(_to_info(p))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


def find_processes_by_group(group: str) -> list[ProcInfo]:
    """Find processes belonging to a built-in group or a special selector."""
    g = group.strip().lower()
    if g == "chrome-tabs":
        return find_chrome_renderers(include_extensions=False)
    if g in ("chrome-all-renderers", "chrome-renderers"):
        return find_chrome_renderers(include_extensions=True)
    if g == "chrome":
        return find_processes_by_name(["chrome", "chromium"])
    if g in KNOWN_GROUPS:
        return find_processes_by_name(KNOWN_GROUPS[g])
    # Fallback: treat as a name pattern itself
    return find_processes_by_name([g])


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

    # Fire off the terminate/kill call
    for p in to_kill:
        try:
            if force:
                p.kill()
            else:
                p.terminate()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied as exc:
            report["failed"].append({"pid": p.pid, "name": p.name(), "reason": f"access denied: {exc}"})
        except Exception as exc:
            report["failed"].append({"pid": p.pid, "name": "", "reason": str(exc)})

    # Wait, then escalate any survivors to kill()
    gone, alive = psutil.wait_procs(to_kill, timeout=timeout)
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
    return sorted(KNOWN_GROUPS.keys()) + ["chrome", "chrome-tabs", "chrome-all-renderers"]
