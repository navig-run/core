"""
System monitoring — pure psutil-based data collection.

Returns plain dicts so both the Telegram command handlers (in
gateway/channels/telegram_commands.py) and the Deck REST routes (in
gateway/deck/routes/monitor.py) share one source of truth.

No formatting — callers are responsible for presentation.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from typing import Any, NamedTuple

try:
    import psutil  # type: ignore[import-untyped]
except ImportError:
    psutil = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT = 3.0

# Shared pool for disk probes. Module-level (not per-call) so a slow/cold drive's
# probe can keep running in the background after we've already timed out and
# returned — without a per-call ``ThreadPoolExecutor`` context manager blocking on
# exit until every probe finishes (which would defeat the timeout entirely).
_DISK_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="disk-probe")


def _psutil_available() -> bool:
    return psutil is not None


# ── Non-blocking CPU sampler ─────────────────────────────────────────────────
# psutil.cpu_percent(interval=0.3) BLOCKS for 300ms per call, which stalls the
# /api/deck/monitor endpoint (called on every dashboard poll). Instead we sample
# with interval=None, which returns the busy % since the *previous* call (no
# sleep). We prime the counters at import so the first request returns a real
# value, and keep the last good reading to smooth over transient 0.0s.
_last_cpu_overall: float = 0.0
_cpu_primed: bool = False

# Only ever paid once, when there is no prior reading at all (see
# _cpu_percent_nonblocking) — enough of a window for a real number, short enough
# that no caller notices. Every later read is a free delta sample.
_CPU_FIRST_SAMPLE_S = 0.12


def _prime_cpu() -> None:
    global _cpu_primed
    if _cpu_primed or not _psutil_available():
        return
    try:
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(percpu=True)
    except Exception:
        pass
    _cpu_primed = True


# Prime at import time — the gateway imports this well before the first request,
# so the delta window is already seconds wide by the time the deck polls.
_prime_cpu()


def _cpu_percent_nonblocking() -> float:
    """Overall CPU % since the last call — effectively never blocks."""
    global _last_cpu_overall
    if not _psutil_available():
        return 0.0
    _prime_cpu()
    try:
        val = round(float(psutil.cpu_percent(interval=None)), 1)
        if val > 0:
            _last_cpu_overall = val
            return _last_cpu_overall
        # A delta sample taken right after priming has no window to measure and
        # returns a flat 0.0 — which is a LIE, not a reading, and it was the
        # first thing the dashboard showed after a daemon start. With no prior
        # reading to fall back on, pay for one short bounded sample instead.
        if _last_cpu_overall == 0.0:
            val = round(float(psutil.cpu_percent(interval=_CPU_FIRST_SAMPLE_S)), 1)
            if val > 0:
                _last_cpu_overall = val
        return _last_cpu_overall
    except Exception:
        return _last_cpu_overall


def _system_mountpoint() -> str:
    """Normalized mountpoint of the OS/system drive (``C:`` on Windows, ``/`` on POSIX)."""
    if os.name == "nt":
        # SystemDrive is like "C:"; normalize to a bare upper drive letter.
        return os.environ.get("SystemDrive", "C:").rstrip("\\").upper()
    return "/"


def _system_mount_path() -> str:
    """Path form of the system drive for ``disk_usage`` (``C:\\`` on Windows, ``/``)."""
    if os.name == "nt":
        return os.environ.get("SystemDrive", "C:").rstrip("\\") + "\\"
    return "/"


def get_system_disk() -> list[dict[str, Any]]:
    """Just the OS/system drive — fast.

    Skips ``disk_partitions`` (which queries fstype per volume and can block for
    seconds on machines with cold/network drives). Used by the one-shot snapshot,
    where only the single headline "Disk" vital is shown — there's no need to
    enumerate every mounted volume to render it.
    """
    if not _psutil_available():
        return []
    mount = _system_mount_path()
    try:
        usage = psutil.disk_usage(mount)
    except (OSError, PermissionError):
        return []
    return [{
        "mountpoint": mount,
        "fstype": "",
        "total_gb": round(usage.total / (1024 ** 3), 2),
        "used_gb": round(usage.used / (1024 ** 3), 2),
        "free_gb": round(usage.free / (1024 ** 3), 2),
        "percent": round(usage.percent, 1),
        "is_system": True,
    }]


# ── Background-refreshed partition list ──────────────────────────────────────
# ``psutil.disk_partitions`` can block for seconds — and on Windows with a cold
# mapped network drive it blocks FOREVER (proven on the operator's machine: it
# never returned, and because it doesn't release the GIL it froze the whole
# gateway). It is never called inline: the list is cached and refreshed in the
# disk pool.
_PARTITIONS_TTL = 300.0
_partitions_cache: tuple[float, list[Any]] | None = None
_partitions_future: Any = None

# Windows GetDriveType codes. REMOTE (network) and CDROM are the ones whose
# volume-info / usage probes hang; FIXED and REMOVABLE answer instantly.
_DRIVE_REMOVABLE = 2
_DRIVE_FIXED = 3


class _Part(NamedTuple):
    """Duck-types psutil's ``sdiskpart`` for the fields the probe uses."""

    mountpoint: str
    fstype: str = ""


def _enumerate_partitions() -> list[Any]:
    """Volumes worth probing. On Windows this never touches ``disk_partitions``.

    ``psutil.disk_partitions()`` asks each volume for its filesystem type, which
    hangs on a disconnected network drive. Enumerating the drive letters
    ourselves is a mount-table lookup instead — ``os.listdrives()`` +
    ``GetDriveTypeW``, both measured at 0.000s — and lets us skip the drive types
    that hang (REMOTE, CDROM) before anything touches them.
    """
    if os.name != "nt" or not hasattr(os, "listdrives"):
        return list(psutil.disk_partitions(all=False))
    try:
        import ctypes

        get_drive_type = ctypes.windll.kernel32.GetDriveTypeW
    except Exception:  # noqa: BLE001 — no ctypes/kernel32: fall back
        return list(psutil.disk_partitions(all=False))

    out: list[Any] = []
    for drive in os.listdrives():
        try:
            kind = get_drive_type(str(drive))
        except Exception:  # noqa: BLE001
            continue
        if kind in (_DRIVE_FIXED, _DRIVE_REMOVABLE):
            out.append(_Part(mountpoint=str(drive)))
        else:
            logger.debug("skipping drive %s (type %s: hangs on probe)", drive, kind)
    return out


def _cached_partitions(max_partitions: int, first_scan_timeout: float = 1.0) -> list[Any]:
    """The partition list, refreshed in the pool — never blocking the caller.

    On the FIRST call there is nothing cached, so we wait up to
    ``first_scan_timeout`` for the scan rather than returning an empty list and
    making the caller show "no disks" until something asks again. The wait is
    bounded and the scan stays in the pool, so even a pathological enumeration
    can only cost that timeout, never a hang.
    """
    global _partitions_cache, _partitions_future
    now = time.monotonic()
    if _partitions_future is not None and _partitions_future.done():
        try:
            _partitions_cache = (now, list(_partitions_future.result()))
        except Exception:  # noqa: BLE001
            pass
        _partitions_future = None
    fresh = _partitions_cache is not None and (now - _partitions_cache[0] < _PARTITIONS_TTL)
    if not fresh and _partitions_future is None:
        _partitions_future = _DISK_POOL.submit(_enumerate_partitions)

    if _partitions_cache is None and _partitions_future is not None:
        # Nothing to serve yet — give the scan a bounded moment to land.
        done, _ = wait([_partitions_future], timeout=first_scan_timeout)
        if done:
            try:
                _partitions_cache = (time.monotonic(), list(_partitions_future.result()))
            except Exception:  # noqa: BLE001
                pass
            _partitions_future = None

    parts = _partitions_cache[1] if _partitions_cache else []
    return parts[:max_partitions]


def get_disk_info(max_partitions: int = 20, scan_timeout: float = 2.0) -> list[dict[str, Any]]:
    """List mounted partitions with usage. Skips inaccessible/permission-denied ones.

    Each entry carries ``is_system`` so the UI can headline the OS drive (e.g. C:)
    rather than guessing — otherwise it tends to surface whichever unrelated
    partition happens to be fullest.

    Drives are probed **concurrently** with an overall ``scan_timeout``: a single
    cold/spun-down or network drive can otherwise block ``disk_usage`` for many
    seconds. Drives that don't answer in time are skipped this round (they
    reappear once warm) rather than stalling the whole scan.
    """
    if not _psutil_available():
        return []
    partitions = _cached_partitions(max_partitions)
    if not partitions:
        return []
    system_mount = _system_mountpoint()

    def _probe(part: Any) -> dict[str, Any] | None:
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            return None
        return {
            "mountpoint": part.mountpoint,
            "fstype": part.fstype or "",
            "total_gb": round(usage.total / (1024 ** 3), 2),
            "used_gb": round(usage.used / (1024 ** 3), 2),
            "free_gb": round(usage.free / (1024 ** 3), 2),
            "percent": round(usage.percent, 1),
            "is_system": part.mountpoint.rstrip("\\").upper() == system_mount,
        }

    out: list[dict[str, Any]] = []
    futures = {_DISK_POOL.submit(_probe, p): p for p in partitions}
    done, not_done = wait(futures, timeout=scan_timeout)
    for fut in done:
        try:
            row = fut.result()
        except Exception:  # noqa: BLE001
            row = None
        if row is not None:
            out.append(row)
    for part in (futures[f] for f in not_done):
        # Cold/spun-down or network drive — skipped this round (reappears warm).
        # Its probe keeps running in the pool and is discarded when it finishes.
        logger.debug("disk_usage slow/unresponsive, skipped: %s", part.mountpoint)
    # Stable order (system drive first, then by mountpoint) regardless of which
    # probe finished first.
    out.sort(key=lambda d: (not d["is_system"], d["mountpoint"]))
    return out


def get_memory_info() -> dict[str, Any]:
    """RAM and swap usage."""
    if not _psutil_available():
        return {"ram": None, "swap": None}
    try:
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
    except Exception as exc:
        logger.debug("memory probe failed: %s", exc)
        return {"ram": None, "swap": None}

    ram = {
        "total_gb": round(vm.total / (1024 ** 3), 2),
        "used_gb": round(vm.used / (1024 ** 3), 2),
        "available_gb": round(vm.available / (1024 ** 3), 2),
        "percent": round(vm.percent, 1),
    }
    swap: dict[str, Any] | None = None
    if sw.total > 0:
        swap = {
            "total_gb": round(sw.total / (1024 ** 3), 2),
            "used_gb": round(sw.used / (1024 ** 3), 2),
            "percent": round(sw.percent, 1),
        }
    return {"ram": ram, "swap": swap}


def get_cpu_info(top_n: int = 5) -> dict[str, Any]:
    """CPU overall + per-core + frequency + load + top processes."""
    if not _psutil_available():
        return {
            "overall_percent": 0,
            "physical_cores": 0,
            "logical_cores": 0,
            "freq_mhz": None,
            "freq_max_mhz": None,
            "per_core": [],
            "load_avg": None,
            "top_processes": [],
        }

    overall = _cpu_percent_nonblocking()

    try:
        per_core = [round(p, 1) for p in (psutil.cpu_percent(percpu=True) or [])]
    except Exception:
        per_core = []

    physical = psutil.cpu_count(logical=False) or 0
    logical = psutil.cpu_count(logical=True) or 0

    freq_mhz: float | None = None
    freq_max: float | None = None
    try:
        f = psutil.cpu_freq()
        if f:
            freq_mhz = round(f.current, 0)
            freq_max = round(f.max, 0) if f.max else None
    except Exception:
        pass

    load_avg: list[float] | None = None
    if platform.system() != "Windows":
        try:
            la = psutil.getloadavg()
            load_avg = [round(la[0], 2), round(la[1], 2), round(la[2], 2)]
        except Exception:
            pass

    top: list[dict[str, Any]] = []
    try:
        procs = sorted(
            psutil.process_iter(["pid", "name", "cpu_percent"]),
            key=lambda p: p.info.get("cpu_percent") or 0,
            reverse=True,
        )[:top_n]
        for p in procs:
            top.append({
                "pid": p.info.get("pid"),
                "name": (p.info.get("name") or "?")[:32],
                "cpu_percent": round(p.info.get("cpu_percent") or 0.0, 1),
            })
    except Exception:
        pass

    return {
        "overall_percent": overall,
        "physical_cores": physical,
        "logical_cores": logical,
        "freq_mhz": freq_mhz,
        "freq_max_mhz": freq_max,
        "per_core": per_core,
        "load_avg": load_avg,
        "top_processes": top,
    }


def get_uptime_info() -> dict[str, Any]:
    """System boot time, uptime, logged-in users."""
    if not _psutil_available():
        return {
            "boot_time_iso": "",
            "uptime_seconds": 0,
            "uptime_human": "",
            "users": [],
        }
    try:
        boot_ts = psutil.boot_time()
        boot_dt = datetime.fromtimestamp(boot_ts, tz=timezone.utc)
        now_dt = datetime.now(timezone.utc)
        delta = now_dt - boot_dt
        total_seconds = int(delta.total_seconds())

        days = delta.days
        hours, rem = divmod(delta.seconds, 3600)
        minutes, _ = divmod(rem, 60)
        if days > 0:
            human = f"{days}d {hours}h {minutes}m"
        elif hours > 0:
            human = f"{hours}h {minutes}m"
        else:
            human = f"{minutes}m"
    except Exception:
        boot_dt = datetime.now(timezone.utc)
        total_seconds = 0
        human = ""

    users: list[dict[str, Any]] = []
    try:
        for u in psutil.users() or []:
            users.append({
                "name": u.name,
                "terminal": u.terminal or "",
                "host": u.host or "",
                "started": datetime.fromtimestamp(u.started, tz=timezone.utc).isoformat() if u.started else "",
            })
    except Exception:
        pass

    return {
        "boot_time_iso": boot_dt.isoformat(),
        "uptime_seconds": total_seconds,
        "uptime_human": human,
        "users": users,
    }


def get_services_info(max_services: int = 40, include_stopped: bool = False) -> dict[str, Any]:
    """Services list. Windows: psutil; Linux: systemctl.

    Default = running services only (the dashboard view). ``include_stopped``
    lists every installed service with its real status — the System app's
    "Show all services" setting requests this via ``?services=all``.
    """
    plat = platform.system().lower()
    out_platform = "windows" if plat == "windows" else ("darwin" if plat == "darwin" else "linux")
    services: list[dict[str, Any]] = []

    if plat == "windows":
        if not _psutil_available():
            return {"platform": out_platform, "count": 0, "services": []}
        try:
            matched = []
            for s in psutil.win_service_iter():
                try:
                    status = s.status()
                except Exception:
                    continue
                if include_stopped or status == "running":
                    matched.append((s, status))
            total = len(matched)
            # Running services first, then by name — the interesting ones surface.
            matched.sort(key=lambda t: (t[1] != "running", t[0].name()))
            for svc, status in matched[:max_services]:
                try:
                    services.append({
                        "name": svc.name(),
                        "display_name": svc.display_name(),
                        "status": status,
                    })
                except Exception:
                    continue
            return {"platform": out_platform, "count": total, "services": services}
        except Exception as exc:
            logger.debug("win_service_iter failed: %s", exc)
            return {"platform": out_platform, "count": 0, "services": []}

    # Linux / Darwin via systemctl
    try:
        cmd = ["systemctl", "list-units", "--type=service", "--no-pager", "--no-legend"]
        cmd.insert(3, "--all" if include_stopped else "--state=running")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT,
        )
        lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
        total = len(lines)
        rows = []
        for ln in lines:
            # Columns: UNIT LOAD ACTIVE SUB DESCRIPTION
            parts = ln.split(None, 4)
            if not parts:
                continue
            status = parts[3] if len(parts) > 3 else "unknown"
            rows.append({
                "name": parts[0],
                "display_name": parts[4] if len(parts) > 4 else parts[0],
                "status": status,
            })
        rows.sort(key=lambda r: (r["status"] != "running", r["name"]))
        services = rows[:max_services]
        return {"platform": out_platform, "count": total, "services": services}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"platform": out_platform, "count": 0, "services": []}
    except Exception as exc:
        logger.debug("systemctl probe failed: %s", exc)
        return {"platform": out_platform, "count": 0, "services": []}


def get_ports_info(max_ports: int = 50) -> list[dict[str, Any]]:
    """Listening TCP/UDP ports with process info."""
    if not _psutil_available():
        return []
    out: list[dict[str, Any]] = []
    try:
        conns = psutil.net_connections(kind="inet")
    except (PermissionError, psutil.AccessDenied):
        # On many systems net_connections requires admin
        return []
    except Exception as exc:
        logger.debug("net_connections failed: %s", exc)
        return []

    listening = [c for c in conns if c.status == "LISTEN"]
    listening.sort(key=lambda c: c.laddr.port if c.laddr else 0)

    for c in listening[:max_ports]:
        try:
            port = c.laddr.port if c.laddr else 0
            addr = c.laddr.ip if c.laddr else ""
            if addr in ("0.0.0.0", "::"):
                addr = "*"
            proc_name = ""
            if c.pid:
                try:
                    proc_name = psutil.Process(c.pid).name()[:32]
                except Exception:
                    proc_name = ""
            # SOCK_STREAM=1 is TCP, SOCK_DGRAM=2 is UDP
            protocol = "tcp" if c.type == 1 else "udp"
            out.append({
                "port": port,
                "address": addr,
                "pid": c.pid,
                "process_name": proc_name,
                "protocol": protocol,
            })
        except Exception:
            continue
    return out


def get_all_monitoring(all_services: bool = False) -> dict[str, Any]:
    """One-shot snapshot for the Deck UI — all sections in a single call.

    Uses the fast system-only disk path: the snapshot powers the dashboard's
    single "Disk" vital, so enumerating every mounted volume here (slow on cold/
    network drives) is wasteful. The full multi-drive list is served separately by
    ``get_disk_info`` via ``/api/deck/monitor/disk``.
    """
    return {
        "disk": get_system_disk(),
        "memory": get_memory_info(),
        "cpu": get_cpu_info(),
        "uptime": get_uptime_info(),
        # The all-services view must actually SHOW the stopped tail: with the
        # default 40-row cap and running-first ordering, a machine with >40
        # running services would list an identical page and only the count
        # would change (caught live 2026-07-12: 126 running / 320 total).
        "services": get_services_info(
            max_services=1000 if all_services else 40,
            include_stopped=all_services,
        ),
        "ports": get_ports_info(),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
