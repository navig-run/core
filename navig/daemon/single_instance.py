"""Single-instance enforcement for NAVIG daemon/gateway processes.

A long-running process imports its modules once and caches them — so a *stale*
instance keeps serving the OLD code even after you edit the source. The fix is to
guarantee that starting a role (gateway / daemon) supersedes every other instance
of that role: kill the others, then run.

Safety: we NEVER kill the current process or any of its ancestors (the supervisor
that spawned this gateway, NSSM/Task Scheduler, the launching shell). psutil-first
with a subprocess fallback so it works on a bare install.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

from navig.core.proc_text import console_encoding

logger = logging.getLogger(__name__)

# Process cmdline fragments identifying a NAVIG service process.
# GATEWAY_PATTERNS is deliberately gateway-ONLY: a separate telegram_worker (the
# bot host) is a different role and may legitimately run alongside; a stale worker
# that holds the gateway port is already reaped by gateway._free_port().
GATEWAY_PATTERNS: tuple[str, ...] = (
    "navig gateway start",
    "-m navig gateway",
)
DAEMON_PATTERNS: tuple[str, ...] = (
    "navig.daemon.entry",
    "-m navig.daemon",
)

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def ancestor_pids() -> set[int]:
    """``{self} ∪ {all ancestor pids}`` — the process tree we must never kill."""
    keep: set[int] = {os.getpid()}
    try:
        import psutil  # type: ignore[import-untyped]

        for parent in psutil.Process(os.getpid()).parents():
            keep.add(parent.pid)
    except Exception:  # noqa: BLE001
        pass  # psutil missing / access denied — self-exclusion still holds
    return keep


def process_table() -> list[tuple[int, str]]:
    """Return ``[(pid, cmdline)]`` for running processes (best-effort)."""
    rows: list[tuple[int, str]] = []
    try:
        import psutil  # type: ignore[import-untyped]

        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmd = " ".join(proc.info.get("cmdline") or [])
                if cmd:
                    rows.append((int(proc.info["pid"]), cmd))
            except Exception:  # noqa: BLE001
                continue
        return rows
    except Exception:  # noqa: BLE001
        pass  # fall back to a shell enumeration

    try:
        if sys.platform == "win32":
            out = subprocess.run(
                [
                    "powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine } "
                    "| ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }",
                ],
                capture_output=True, timeout=10, creationflags=_CREATE_NO_WINDOW,
                encoding=console_encoding(), errors="replace",
            ).stdout
            for line in out.splitlines():
                pid_s, tab, cmd = line.partition("\t")
                if tab:
                    try:
                        rows.append((int(pid_s.strip()), cmd))
                    except ValueError:
                        continue
        else:
            out = subprocess.run(
                ["ps", "-eo", "pid=,args="], capture_output=True, text=True, timeout=10
            ).stdout
            for line in out.splitlines():
                line = line.strip()
                pid_s, _, cmd = line.partition(" ")
                try:
                    rows.append((int(pid_s), cmd))
                except ValueError:
                    continue
    except Exception:  # noqa: BLE001
        pass
    return rows


def _force_kill(pid: int) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, creationflags=_CREATE_NO_WINDOW,
            )
        else:
            os.kill(pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001
        pass  # best-effort; never crash the start path


def config_dir_of(pid: int) -> Path | None:
    """The NAVIG config dir a running process is using, or None if unreadable.

    A navig process is bound to a config dir (``NAVIG_CONFIG_DIR``, else the platform
    default) — that dir *is* the brain: its vault, data, spaces, gateway.json and
    gateway.pid all live there. Two gateways on the same config dir are the same brain
    and must not coexist; two on *different* config dirs are different brains.

    Returns None when the environment can't be read (another user's process, psutil
    missing, permission denied). Callers must treat None as "not mine" and leave it
    alone — never kill a process you cannot identify.
    """
    try:
        import psutil  # type: ignore[import-untyped]

        env = psutil.Process(pid).environ()
    except Exception:  # noqa: BLE001 — NoSuchProcess / AccessDenied / no psutil
        return None
    raw = env.get("NAVIG_CONFIG_DIR")
    try:
        if raw:
            return Path(raw).expanduser().resolve()
        from navig.platform.paths import config_dir  # default for a process with no override

        return config_dir().resolve()
    except Exception:  # noqa: BLE001
        return None


# A process writes its pidfile just AFTER it starts, so a genuine owner's create_time is at or
# before the file's mtime. This only absorbs clock skew, not a real elapsed window.
_PIDFILE_SKEW_SECONDS = 60.0


def pid_from_pidfile(path: Path, *, cmdline_contains: str | None = None) -> int | None:
    """The PID recorded in *path* — but only if it is STILL the process that wrote it.

    A pidfile records a *number*, and a number is not an identity. When the process it named
    exits, the OS is free to hand that number to anything, so every consumer that treats the
    file as proof is one reboot away from acting on a stranger: ``navig agent stop`` sent
    ``taskkill /F`` to it, and ``agent status`` / ``tray`` reported "running" because *something*
    answered to the number. The same mistake in ``navig cdp stop`` could take out an unrelated
    process tree (fixed in cac2c1d40).

    The decisive test needs no new file format: the owner writes the pidfile just after starting,
    so its ``create_time`` is at or before the file's mtime — while a process that recycled the
    number necessarily started *after* the file was written. That is both necessary and
    sufficient, because only the owner held that number at the moment it wrote the file.
    *cmdline_contains* is optional defence in depth for callers that know a stable marker.

    Returns ``None`` for missing, unparseable, dead, unreadable, or recycled — every one of which
    means "not running", which is the safe answer: a caller that gets ``None`` reports nothing
    running and kills nothing, whereas a wrong PID gets a live process killed.
    """
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
        recorded_at = path.stat().st_mtime
    except (OSError, ValueError):
        return None
    if pid <= 0:
        return None

    try:
        import psutil  # type: ignore[import-untyped]

        proc = psutil.Process(pid)
        created = proc.create_time()
        cmdline = " ".join(proc.cmdline() or [])
    except Exception:  # noqa: BLE001 — NoSuchProcess / AccessDenied / no psutil: not ours
        return None

    if created > recorded_at + _PIDFILE_SKEW_SECONDS:
        logger.warning(
            "single-instance: pid %d in %s was recycled (started %ds after the file was "
            "written) — treating as not running rather than acting on a stranger",
            pid, path.name, int(created - recorded_at),
        )
        return None

    if cmdline_contains and cmdline_contains.lower() not in cmdline.lower():
        logger.warning(
            "single-instance: pid %d no longer looks like %s — treating as not running",
            pid, cmdline_contains,
        )
        return None

    return pid


def kill_other_instances(
    patterns: tuple[str, ...],
    *,
    table: list[tuple[int, str]] | None = None,
    keep: set[int] | None = None,
    killer=None,
    config_dir: Path | None = None,
    config_dir_reader=None,
) -> list[int]:
    """Force-kill every process whose cmdline contains ANY of *patterns*, except the
    current process and its ancestors. Returns the PIDs that were killed.

    **Pass ``config_dir`` to scope the sweep to your own brain.** Without it this kills
    matching processes *machine-wide*, which is how a gateway started from ANY other
    navig — a second venv, a CI job, a temp-config smoke test — silently force-killed
    the operator's live production daemon. It is not a hypothetical: the operator's
    gateway (pid …, ``-m navig gateway start``) is exactly what that sweep matches, and
    "never boot a second gateway locally" became a standing rule *because* of this.

    Scoped, the brain model is unchanged — one gateway per config dir, and a stale
    instance of *your* gateway is still superseded — but a process belonging to a
    different config dir is a different brain and is left alone. A process whose config
    dir cannot be read is never killed: you must not kill what you cannot identify.

    *table*, *keep*, *killer* and *config_dir_reader* are injectable for testing.
    """
    rows = table if table is not None else process_table()
    protected = keep if keep is not None else ancestor_pids()
    do_kill = killer if killer is not None else _force_kill
    read_cfg = config_dir_reader if config_dir_reader is not None else config_dir_of
    pats = tuple(p.lower() for p in patterns)
    mine = config_dir.resolve() if config_dir is not None else None

    killed: list[int] = []
    for pid, cmd in rows:
        if pid in protected:
            continue
        low = cmd.lower()
        if not any(p in low for p in pats):
            continue
        if mine is not None:
            theirs = read_cfg(pid)
            # Normalize BOTH sides — a caller (or a reader) may hand back an
            # unresolved path, and a near-miss here would silently widen the sweep
            # back out to machine-wide, which is the bug this scoping exists to kill.
            try:
                theirs = Path(theirs).resolve() if theirs is not None else None
            except (OSError, ValueError):  # pragma: no cover — unresolvable path
                theirs = None
            if theirs is None or theirs != mine:
                logger.debug(
                    "single-instance: leaving pid %d alone (config dir %s != %s)",
                    pid, theirs, mine)
                continue
        killed.append(pid)
        do_kill(pid)
    return killed
