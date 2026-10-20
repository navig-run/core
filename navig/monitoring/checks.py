"""Structured health-check API — the data layer under every monitoring surface.

``navig.commands.monitoring`` *renders* these checks for the CLI; this module owns
the data: the read-only shell recipes, their parsers, and the :class:`Sample` rows
the Gate 1 alert pipeline consumes. Nothing here prints, and nothing here reaches
for "the active server" — the caller passes the host it wants.

Two layers, one data path
-------------------------
1. **Command constants + parsers** (``RESOURCES_COMMAND`` / ``parse_resources`` and
   friends). The CLI printers and :func:`collect` both run these, so a shell recipe
   exists exactly once and a parser change cannot drift between the two surfaces.
2. **Sample builders** — turn a parsed dict (SSH) or a ``commands/monitor.py``
   psutil snapshot (local) into :class:`Sample` rows.

Targets
-------
* ``host=None`` — this machine, sampled with the real psutil collectors in
  ``navig.commands.monitor`` (no SSH, no config read).
* a local name (``localhost`` / ``127.0.0.1`` / ``::1`` / ``local`` / this
  machine's hostname) — same local collectors, samples labelled with that name.
* any other name — looked up **explicitly** with ``config.load_host_config(host)``
  (or the ``config=`` you pass) and sampled over the existing read-only SSH
  batches. No hidden global "active server" state.

Checks
------
``resources`` · ``disk`` · ``services`` · ``network`` (the four the CLI has), plus
``http`` / ``cert`` which run :class:`navig.tools.site_check.SiteCheckTool` against
``urls=`` (or a URL-bearing mapping passed as ``config=``).

Metric catalogue (stable names for the alert pipeline)::

    resources   cpu_pct · mem_pct · mem_used_kb · mem_total_kb · disk_pct
                load_avg_1m · load_avg_5m · load_avg_15m · tcp_connections · uptime
    disk        disk.<mount>.usage_pct          e.g. disk./.usage_pct, disk.C:.usage_pct
    services    service.<name>.status           e.g. service.nginx.status  (state word)
    network     listening_ports · established_connections
    http        http.status_code · http.latency_ms · http.online ·
                http.redirects · http.cert_expiry

Multi-line / list fields of a parsed check (``connection_summary``,
``interfaces``) are **not** scalar readings and stay on the parsed dict — call the
``parse_*`` functions directly when you need them.

Failure policy — a failed fetch is never a reading
--------------------------------------------------
* Transport failure (non-zero SSH exit, exception, unresolvable host) → **no
  samples** for that check plus a :class:`CheckFailure`.
* A malformed field → no sample for that field, with a logged warning.
* :func:`collect` never raises on any of the above; it is the samples-only wrapper
  over :func:`collect_with_errors` and logs every dropped check at
  ``logger.warning``. The one exception is an unknown check *name*, which is a
  caller contract violation and raises ``ValueError``.

SSH work runs in a worker thread (``asyncio.to_thread``) so an async caller is not
blocked for the length of a round trip.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

from navig.remote import RemoteOperations
from navig.remote import is_local_host as _server_config_is_local

logger = logging.getLogger(__name__)

#: Where a reading came from: an SSH batch, this machine's psutil, or an HTTP probe.
Source = Literal["ssh", "local", "http"]

#: Every check name :func:`collect` accepts.
VALID_CHECKS: tuple[str, ...] = ("resources", "disk", "services", "network", "http", "cert")
#: The four host checks — everything else is a URL check (``http`` / ``cert``).
HOST_CHECKS: tuple[str, ...] = ("resources", "disk", "services", "network")
#: Sensible default when a caller wants "the host is healthy".
DEFAULT_CHECKS: tuple[str, ...] = HOST_CHECKS

# Hostnames that mean "this machine" without a config lookup.
_LOCAL_NAMES = frozenset({"localhost", "127.0.0.1", "::1", "local"})

# Windows service states → the systemd vocabulary the rest of the API speaks.
_LOCAL_STATUS_ALIASES = {"running": "active", "stopped": "inactive"}

# Positional slots of a `load average` line (uptime always prints all three).
_LOAD_METRICS = ("load_avg_1m", "load_avg_5m", "load_avg_15m")

# /dev/sda1  20G  15G  4.2G  79% /
_DF_ROW = re.compile(r"(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\d+)%\s+(\S+)")


# ── Rows ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Sample:
    """One reading: *what* was measured, *where*, *when*, and *how* it was taken.

    ``value`` is a scalar — a number for a thresholdable metric, a word for a
    state (``service.nginx.status``). Anything non-scalar stays on the parsed dict
    returned by the ``parse_*`` functions.
    """

    host: str
    metric: str
    value: float | int | str
    unit: str
    ts: float  # epoch seconds
    source: Source


@dataclass(frozen=True)
class CheckFailure:
    """A check that produced **no** samples — the explicit error channel.

    ``collect_with_errors`` returns these next to the samples; ``collect`` logs
    them (``logger.warning``) and drops them, because its contract is
    ``list[Sample]``.
    """

    host: str
    check: str
    reason: str


class _CheckFailed(Exception):
    """A check produced no data at all (transport-level failure)."""


# ── Read-only shell recipes (one copy, shared by CLI + collect) ────────────────

#: The individual recipes inside ``RESOURCES_COMMAND`` — also run one-by-one by
#: ``generate_report``, so they live here once rather than twice in the CLI.
CPU_COMMAND = "top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1"
MEM_COMMAND = "free | grep Mem | awk '{print ($3/$2) * 100.0, $3, $2}'"
ROOT_DISK_COMMAND = "df -h / | tail -1 | awk '{print $5, $3, $2, $4}' | sed 's/%//'"
LOAD_COMMAND = "uptime | awk -F'load average:' '{print $2}' | xargs"
TCP_COUNT_COMMAND = "ss -s 2>/dev/null | grep 'TCP:' | awk '{print $2}'"
UPTIME_COMMAND = "uptime -p"

#: CPU · MEM · root DISK · LOAD · TCP connections · UPTIME in ONE SSH round-trip.
#: (Previously six separate calls — see the QUANTUM VELOCITY E1 note in the CLI.)
RESOURCES_COMMAND = (
    f"echo '===CPU==='; {CPU_COMMAND}; "
    f"echo '===MEM==='; {MEM_COMMAND}; "
    f"echo '===DISK==='; {ROOT_DISK_COMMAND}; "
    f"echo '===LOAD==='; {LOAD_COMMAND}; "
    f"echo '===CONN==='; {TCP_COUNT_COMMAND}; "
    f"echo '===UPTIME==='; {UPTIME_COMMAND}"
)

#: Every mounted filesystem, ``df``-style rows.
DISK_COMMAND = "df -h | grep -E '^/dev/'"

#: Connection summary · listening · established · interfaces in ONE round-trip.
NETWORK_COMMAND = (
    "echo '===CONN==='; ss -s; "
    "echo '===LISTEN==='; ss -tuln 2>/dev/null | grep -c LISTEN || echo 0; "
    "echo '===ESTAB==='; ss -tn 2>/dev/null | grep -c ESTAB || echo 0; "
    "echo '===IFACE==='; ip -s link show | grep -E '^[0-9]+:' | awk '{print $2}' "
    "| sed 's/:$//' | head -5"
)

#: The services ``monitor_services`` probes (systemd units on a Linux host).
CRITICAL_SERVICES: tuple[str, ...] = (
    "nginx",
    "apache2",
    "mysql",
    "mariadb",
    "postgresql",
    "php8.3-fpm",
    "php8.2-fpm",
    "php8.1-fpm",
    "php-fpm",
    "hestia",
    "fail2ban",
    "ufw",
    "ssh",
    "sshd",
    "redis-server",
    "memcached",
)


def service_status_command(service: str) -> str:
    """Read-only status of ONE unit (the per-service recipe ``generate_report`` runs)."""
    return f"systemctl is-active {service} 2>/dev/null || echo 'not-installed'"


def services_command(services: Sequence[str] = CRITICAL_SERVICES) -> str:
    """Every unit's status in ONE SSH round-trip (delimited ``===SVC:name===`` blocks)."""
    return "; ".join(
        f"echo '===SVC:{name}==='; {service_status_command(name)}" for name in services
    )


# ── Parsers (pure: text in, plain data out — the transport stays outside) ──────


def split_sections(stdout: str) -> dict[str, list[str]]:
    """Split delimited ``===KEY===`` output into ``{key: [lines...]}``.

    Blank lines are kept: ``parse_resources`` treats an *empty* section (header
    with no lines) differently from an *absent* one, and that distinction is part
    of the CLI's existing behaviour.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in stdout.splitlines():
        if line.startswith("===") and line.endswith("==="):
            current = line.strip("=").strip()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


def _split_service_sections(stdout: str) -> dict[str, list[str]]:
    """``===SVC:name===`` blocks → ``{name: [status lines...]}`` (blanks dropped)."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in stdout.splitlines():
        if line.startswith("===SVC:") and line.endswith("==="):
            current = line[7:-3].strip()
            sections[current] = []
        elif current is not None and line.strip():
            sections[current].append(line.strip())
    return sections


def _first(sections: Mapping[str, list[str]], key: str, default: str) -> str | None:
    """First line of *key*; *default* when the section is absent, ``None`` when empty."""
    if key not in sections:
        return default
    try:
        return sections[key][0]
    except IndexError:
        return None


def parse_cpu(text: str) -> float | None:
    """CPU % out of ``top`` output (unrounded — alerts compare the raw value)."""
    try:
        return float(str(text).strip())
    except ValueError:
        return None


def parse_memory_usage(text: str) -> tuple[float, int, int] | None:
    """``free`` MEM line → ``(usage_percent, used_kb, total_kb)``; ``None`` if malformed."""
    try:
        parts = str(text).split()
        return float(parts[0]), int(parts[1]), int(parts[2])
    except (ValueError, IndexError):
        return None


def memory_metrics(usage_percent: float, used_kb: int, total_kb: int) -> dict[str, Any]:
    """The rounded memory dict the CLI renders (key order is part of the JSON shape)."""
    return {
        "usage_percent": round(usage_percent, 1),
        "used_kb": used_kb,
        "total_kb": total_kb,
        "used_mb": round(used_kb / 1024, 1),
        "total_mb": round(total_kb / 1024, 1),
    }


def parse_disk_output(stdout: str, returncode: int = 0) -> list[dict[str, Any]] | None:
    """``df -h | grep '^/dev/'`` rows → disk dicts; ``None`` when the fetch failed.

    ``None`` (fetch failed) and ``[]`` (fetched, nothing matched) are different
    answers — the CLI prints "Failed to retrieve disk information" only for the
    first, exactly as before.
    """
    if returncode != 0:
        return None
    disks: list[dict[str, Any]] = []
    for line in stdout.strip().split("\n"):
        match = _DF_ROW.match(line)
        if match:
            device, size, used, available, usage, mount = match.groups()
            disks.append(
                {
                    "device": device,
                    "size": size,
                    "used": used,
                    "available": available,
                    "usage_percent": int(usage),
                    "mount": mount,
                }
            )
    return disks


def parse_resources(stdout: str, returncode: int = 0) -> tuple[dict[str, Any], list[str]]:
    """The ``RESOURCES_COMMAND`` batch → ``(metrics, alerts)`` — the CLI's own shape.

    ``returncode != 0`` yields empty results (the CLI printed an empty table for
    that before, and still does); a malformed section is skipped with a logged
    warning rather than raising.
    """
    if returncode != 0:
        return {}, []

    sections = split_sections(stdout)
    metrics: dict[str, Any] = {}
    alerts: list[str] = []

    # CPU — an ABSENT section still defaults to 0 (existing CLI behaviour);
    # an empty or malformed one skips the key entirely.
    cpu_usage = parse_cpu(_first(sections, "CPU", "0") or "")
    if cpu_usage is not None:
        metrics["cpu"] = round(cpu_usage, 1)
        if cpu_usage > 80:
            alerts.append(f"High CPU usage: {cpu_usage}%")

    # Memory
    mem_raw = parse_memory_usage(_first(sections, "MEM", "0 0 0") or "0 0 0")
    if mem_raw is not None:
        usage, used_kb, total_kb = mem_raw
        metrics["memory"] = memory_metrics(usage, used_kb, total_kb)
        if usage > 80:
            alerts.append(f"High memory usage: {usage}%")

    # Root disk
    disk_values = (_first(sections, "DISK", "0 0 0 0") or "").split()
    try:
        disk_usage = int(disk_values[0])
        metrics["disk"] = {
            "usage_percent": disk_usage,
            "used": disk_values[1],
            "total": disk_values[2],
            "available": disk_values[3],
        }
        if disk_usage > 80:
            alerts.append(f"High disk usage: {disk_usage}%")
    except (ValueError, IndexError):
        logger.warning("monitoring: malformed DISK section: %r", disk_values)

    # Scalar metrics
    if sections.get("LOAD"):
        metrics["load_average"] = sections["LOAD"][0].strip()
    if sections.get("CONN"):
        metrics["tcp_connections"] = sections["CONN"][0].strip()
    if sections.get("UPTIME"):
        metrics["uptime"] = sections["UPTIME"][0].strip()

    return metrics, alerts


def parse_services(
    stdout: str,
    returncode: int = 0,
    services: Sequence[str] = CRITICAL_SERVICES,
) -> tuple[list[dict[str, Any]], list[str]]:
    """``services_command()`` output → ``(service_status, inactive_services)``.

    ``service_status`` rows are ``{"name", "status", "health"}`` — the exact dicts
    the CLI renders, including the ``unknown`` fill when the fetch failed.
    """
    service_map: dict[str, list[str]] = (
        _split_service_sections(stdout) if returncode == 0 else {}
    )

    service_status: list[dict[str, Any]] = []
    inactive_services: list[str] = []
    for service in services:
        status = str((service_map.get(service) or ["unknown"])[0] or "unknown").strip() or "unknown"
        service_info: dict[str, Any] = {"name": service, "status": status}
        if status == "active":
            service_info["health"] = "healthy"
        elif status == "inactive":
            service_info["health"] = "stopped"
            inactive_services.append(service)
        elif status == "not-installed":
            service_info["health"] = "not-installed"
        else:
            service_info["health"] = "unknown"
        service_status.append(service_info)
    return service_status, inactive_services


def parse_network(stdout: str, returncode: int = 0) -> dict[str, Any]:
    """``NETWORK_COMMAND`` output → the CLI's network metrics dict."""
    if returncode != 0:
        return {}

    sections = split_sections(stdout)
    metrics: dict[str, Any] = {}

    if sections.get("CONN"):
        metrics["connection_summary"] = "\n".join(sections["CONN"]).strip()
    for key, name in (("LISTEN", "listening_ports"), ("ESTAB", "established_connections")):
        try:
            metrics[name] = int((_first(sections, key, "0") or "0").strip())
        except ValueError:
            logger.warning("monitoring: malformed %s section: %r", key, sections.get(key))
    if sections.get("IFACE"):
        metrics["interfaces"] = [ln for ln in sections["IFACE"] if ln.strip()]
    return metrics


# ── Scalar coercion ────────────────────────────────────────────────────────────


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _as_int(value: Any) -> int | None:
    number = _as_float(value)
    if number is None:
        return None
    return int(number)


def _load_values(raw: Any) -> list[tuple[str, float]]:
    """``"0.10, 0.20, 0.30"`` → three positional load samples (unparseable → skipped)."""
    parts = [part.strip() for part in str(raw).split(",")]
    if len(parts) != len(_LOAD_METRICS):
        logger.warning("monitoring: unexpected load-average shape %r", raw)
        return []
    out: list[tuple[str, float]] = []
    for name, part in zip(_LOAD_METRICS, parts, strict=True):
        value = _as_float(part)
        if value is None:
            logger.warning("monitoring: unparseable load average %r", raw)
            continue
        out.append((name, value))
    return out


# ── Sample builders (parsed dict → rows) ───────────────────────────────────────


def _resource_samples(
    host: str, metrics: Mapping[str, Any], ts: float, source: Source
) -> list[Sample]:
    out: list[Sample] = []

    def add(metric: str, value: float | int | str | None, unit: str) -> None:
        if value is not None:
            out.append(
                Sample(host=host, metric=metric, value=value, unit=unit, ts=ts, source=source)
            )

    if "cpu" in metrics:
        add("cpu_pct", _as_float(metrics.get("cpu")), "%")

    memory = metrics.get("memory")
    if isinstance(memory, Mapping):
        if "usage_percent" in memory:
            add("mem_pct", _as_float(memory.get("usage_percent")), "%")
        if "used_kb" in memory:
            add("mem_used_kb", _as_int(memory.get("used_kb")), "kB")
        if "total_kb" in memory:
            add("mem_total_kb", _as_int(memory.get("total_kb")), "kB")

    disk = metrics.get("disk")
    if isinstance(disk, Mapping) and "usage_percent" in disk:
        add("disk_pct", _as_float(disk.get("usage_percent")), "%")

    if metrics.get("load_average") is not None:
        for name, value in _load_values(metrics.get("load_average")):
            add(name, value, "load")

    if metrics.get("tcp_connections") is not None:
        add("tcp_connections", _as_int(metrics.get("tcp_connections")), "count")

    if metrics.get("uptime"):
        add("uptime", str(metrics["uptime"]), "")

    return out


def _disk_samples(
    host: str, rows: Sequence[Mapping[str, Any]], ts: float, source: Source
) -> list[Sample]:
    """One ``disk.<mount>.usage_pct`` per mount — the mount lives in the metric name."""
    out: list[Sample] = []
    for row in rows:
        percent = row.get("usage_percent")
        value = _as_float(percent)
        if value is None:
            logger.warning("monitoring: disk row without a usable usage percent: %r", dict(row))
            continue
        mount = str(row.get("mount") or "").replace("\\", "/").rstrip("/") or "/"
        out.append(
            Sample(
                host=host,
                metric=f"disk.{mount}.usage_pct",
                value=value,
                unit="%",
                ts=ts,
                source=source,
            )
        )
    return out


def _service_samples(
    host: str,
    services: Sequence[Mapping[str, Any]],
    ts: float,
    source: Source,
    *,
    normalize: bool = False,
) -> list[Sample]:
    out: list[Sample] = []
    for row in services:
        name = row.get("name")
        status = row.get("status")
        if not name or status is None:
            continue
        text = str(status).strip() or "unknown"
        if normalize:
            text = _LOCAL_STATUS_ALIASES.get(text.lower(), text)
        out.append(
            Sample(
                host=host,
                metric=f"service.{name}.status",
                value=text,
                unit="state",
                ts=ts,
                source=source,
            )
        )
    return out


def _network_samples(
    host: str, metrics: Mapping[str, Any], ts: float, source: Source
) -> list[Sample]:
    """Scalar network readings only — ``connection_summary`` / ``interfaces`` stay dict-side."""
    out: list[Sample] = []
    for key in ("listening_ports", "established_connections"):
        if metrics.get(key) is None:
            continue
        value = _as_int(metrics.get(key))
        if value is None:
            logger.warning("monitoring: non-numeric %s: %r", key, metrics.get(key))
            continue
        out.append(Sample(host=host, metric=key, value=value, unit="count", ts=ts, source=source))
    return out


# ── Local (psutil) collection ──────────────────────────────────────────────────


def _local_resource_metrics() -> dict[str, Any]:
    """The collectors' answer, shaped exactly like ``parse_resources``' output."""
    from navig.commands import monitor  # noqa: PLC0415 — psutil stack stays off the import path

    if not monitor._psutil_available():
        raise _CheckFailed("psutil is not installed")

    metrics: dict[str, Any] = {}

    cpu = monitor.get_cpu_info()
    if cpu.get("overall_percent") is not None:
        metrics["cpu"] = cpu["overall_percent"]

    ram = (monitor.get_memory_info() or {}).get("ram")
    if ram:
        used_kb = int(round(float(ram.get("used_gb") or 0) * 1024**2))
        total_kb = int(round(float(ram.get("total_gb") or 0) * 1024**2))
        metrics["memory"] = memory_metrics(float(ram.get("percent") or 0.0), used_kb, total_kb)

    disks = monitor.get_system_disk()
    if disks:
        metrics["disk"] = {"usage_percent": disks[0].get("percent")}

    load = cpu.get("load_avg")
    if load:
        metrics["load_average"] = ", ".join(str(value) for value in load)

    uptime = (monitor.get_uptime_info() or {}).get("uptime_human")
    if uptime:
        metrics["uptime"] = uptime

    if not metrics:
        raise _CheckFailed("no local resource data (psutil unavailable?)")
    return metrics


def _local_check(check: str, host: str, ts: float) -> list[Sample]:
    from navig.commands import monitor  # noqa: PLC0415

    if check == "resources":
        return _resource_samples(host, _local_resource_metrics(), ts, "local")

    if check == "disk":
        rows = [
            {"mount": str(disk.get("mountpoint") or ""), "usage_percent": disk.get("percent")}
            for disk in monitor.get_disk_info()
        ]
        if not rows:
            raise _CheckFailed("no local disk data (every probe skipped or timed out)")
        return _disk_samples(host, rows, ts, "local")

    if check == "services":
        info = monitor.get_services_info(max_services=1000, include_stopped=True) or {}
        services = info.get("services") or []
        if not services:
            raise _CheckFailed("no local service data (psutil/systemctl unavailable?)")
        return _service_samples(host, services, ts, "local", normalize=True)

    if check == "network":
        # Relays the collector's own count. It reports 0 both when nothing listens
        # and when the OS denies the enumeration (get_ports_info cannot tell) — the
        # same number every other surface of this machine already shows.
        return [
            Sample(
                host=host,
                metric="listening_ports",
                value=len(monitor.get_ports_info()),
                unit="count",
                ts=ts,
                source="local",
            )
        ]

    raise ValueError(f"unknown check: {check!r}")


# ── SSH collection ─────────────────────────────────────────────────────────────


def _ssh_runner(config: Any, server_config: Mapping[str, Any]) -> Callable[[str], Any]:
    """One ``RemoteOperations`` per collect; the runner hides the server_config arg."""
    remote = RemoteOperations(config)

    def run(command: str) -> Any:
        return remote.execute_command(command, dict(server_config))

    return run


def _failure_reason(result: Any) -> str:
    stderr = str(getattr(result, "stderr", "") or "").strip()
    detail = f": {stderr[:200]}" if stderr else ""
    return f"remote command exited {getattr(result, 'returncode', '?')}{detail}"


def _ssh_check(check: str, run: Callable[[str], Any], host: str, ts: float) -> list[Sample]:
    if check == "resources":
        result = run(RESOURCES_COMMAND)
        if result.returncode != 0:
            raise _CheckFailed(_failure_reason(result))
        metrics, _alerts = parse_resources(result.stdout, result.returncode)
        return _resource_samples(host, metrics, ts, "ssh")

    if check == "disk":
        result = run(DISK_COMMAND)
        if result.returncode != 0:
            raise _CheckFailed(_failure_reason(result))
        rows = parse_disk_output(result.stdout, result.returncode)
        if not rows:
            raise _CheckFailed("remote df returned no filesystems")
        return _disk_samples(host, rows, ts, "ssh")

    if check == "services":
        result = run(services_command())
        if result.returncode != 0:
            raise _CheckFailed(_failure_reason(result))
        statuses, _inactive = parse_services(result.stdout, result.returncode)
        return _service_samples(host, statuses, ts, "ssh")

    if check == "network":
        result = run(NETWORK_COMMAND)
        if result.returncode != 0:
            raise _CheckFailed(_failure_reason(result))
        metrics = parse_network(result.stdout, result.returncode)
        if not metrics:
            raise _CheckFailed("remote network probe returned no data")
        return _network_samples(host, metrics, ts, "ssh")

    raise ValueError(f"unknown check: {check!r}")


# ── Target resolution ──────────────────────────────────────────────────────────


def _local_label(explicit: str | None) -> str:
    return explicit if explicit else (platform.node() or "localhost")


def _is_local_name(host: str) -> bool:
    if host.lower() in _LOCAL_NAMES:
        return True
    node = platform.node()
    return bool(node) and host.lower() == node.lower()


def _resolve_target(host: str | None, config: Any) -> tuple[str, str, Any, Any]:
    """``(mode, label, server_config, config)`` — ``mode`` is ``"local"`` or ``"ssh"``.

    Raises when the named host cannot be resolved: a library must never fall back
    to a different, implicit target.
    """
    if host is None:
        return "local", _local_label(None), None, config
    if _is_local_name(host):
        return "local", host, None, config

    if config is None:
        config = _default_config()
    try:
        server_config = config.load_host_config(host)
    except Exception as exc:  # noqa: BLE001 — an unknown host is data, not a crash
        raise ValueError(f"host {host!r} is not configured: {exc}") from exc
    if not isinstance(server_config, Mapping):
        raise ValueError(f"host {host!r} resolved to a non-mapping config")
    if _server_config_is_local(dict(server_config)):
        return "local", host, None, config
    return "ssh", host, server_config, config


def _default_config() -> Any:
    from navig.config import get_config_manager  # noqa: PLC0415 — only when a named host needs it

    return get_config_manager()


def _reason(exc: BaseException) -> str:
    text = str(exc).strip()
    return text if text else type(exc).__name__


# ── HTTP / cert collection ─────────────────────────────────────────────────────


def _urls_from(config: Any, urls: Sequence[str] | None) -> list[str]:
    if urls:
        return [str(url) for url in urls]
    if isinstance(config, Mapping):
        raw = config.get("urls") or config.get("http_urls") or ()
        if isinstance(raw, (str, bytes)):
            raw = [raw]
        return [str(url) for url in raw]
    return []


def _netloc(url: str) -> str:
    return urlparse(url if "://" in url else f"https://{url}").netloc or str(url)


async def collect_http(
    urls: Sequence[str],
    *,
    host: str | None = None,
    checks: Sequence[str] = ("http", "cert"),
) -> tuple[list[Sample], list[CheckFailure]]:
    """Probe each URL with ``SiteCheckTool`` → ``(samples, failures)``.

    * ``host`` — label for every sample; when ``None`` each sample is labelled with
      the URL's own ``netloc``.
    * ``checks`` — emit the HTTP readings (``http``) and/or the certificate expiry
      (``cert``); an unreachable URL is a :class:`CheckFailure`, never a sample.
    """
    from navig.tools.site_check import SiteCheckTool  # noqa: PLC0415 — httpx stack stays lazy

    want_http = "http" in checks
    want_cert = "cert" in checks
    failure_check = "http" if want_http else (str(checks[0]) if checks else "http")
    tool = SiteCheckTool()
    samples: list[Sample] = []
    errors: list[CheckFailure] = []

    for url in urls:
        label = host if host is not None else _netloc(url)
        ts = time.time()
        try:
            result = await tool.run({"url": url})
        except Exception as exc:  # noqa: BLE001 — a probe must not take the sweep down
            logger.warning("monitoring: site check raised for %s: %s", url, exc)
            errors.append(CheckFailure(host=label, check=failure_check, reason=_reason(exc)))
            continue
        if not result.success:
            errors.append(
                CheckFailure(
                    host=label,
                    check=failure_check,
                    reason=result.error or "site check failed",
                )
            )
            continue

        output = result.output if isinstance(result.output, Mapping) else {}
        if want_http:
            status = _as_int(output.get("status_code"))
            if status is not None:
                samples.append(Sample(label, "http.status_code", status, "", ts, "http"))
            latency = _as_float(output.get("latency_ms"))
            if latency is not None:
                samples.append(Sample(label, "http.latency_ms", latency, "ms", ts, "http"))
            if output.get("online") is not None:
                samples.append(
                    Sample(label, "http.online", int(bool(output.get("online"))), "", ts, "http")
                )
            redirects = _as_int(output.get("redirects"))
            if redirects is not None:
                samples.append(Sample(label, "http.redirects", redirects, "count", ts, "http"))
        if want_cert and output.get("cert_expiry"):
            samples.append(
                Sample(label, "http.cert_expiry", str(output["cert_expiry"]), "date", ts, "http")
            )

    return samples, errors


# ── The public API ─────────────────────────────────────────────────────────────


async def collect_with_errors(
    host: str | None,
    checks: Sequence[str],
    *,
    config: Any | None = None,
    urls: Sequence[str] | None = None,
) -> tuple[list[Sample], list[CheckFailure]]:
    """Run *checks* against *host* → ``(samples, failures)``.

    Raises ``ValueError`` for an unknown check name (caller contract violation);
    every transport / parse / host-resolution failure comes back as a
    :class:`CheckFailure` with no samples for that check. Failures are **not**
    logged here — the caller owns this error channel (``collect`` logs them).
    """
    requested = list(checks)
    unknown = [name for name in requested if name not in VALID_CHECKS]
    if unknown:
        raise ValueError(
            f"unknown check(s): {', '.join(sorted(set(unknown)))} — "
            f"valid: {', '.join(VALID_CHECKS)}"
        )

    samples: list[Sample] = []
    errors: list[CheckFailure] = []

    host_checks = [name for name in requested if name in HOST_CHECKS]
    want_http = "http" in requested
    want_cert = want_http or "cert" in requested

    label = _local_label(host)
    mode: str | None = None
    run: Callable[[str], Any] | None = None
    target_error: str | None = None

    if host_checks:
        try:
            mode, label, server_config, resolved_config = _resolve_target(host, config)
            if mode == "ssh":
                run = _ssh_runner(resolved_config, server_config)
        except Exception as exc:  # noqa: BLE001 — an unresolvable host is data, not a crash
            mode = None
            target_error = _reason(exc)

    for check in host_checks:
        if target_error is not None:
            errors.append(CheckFailure(host=label, check=check, reason=target_error))
            continue
        ts = time.time()
        try:
            if mode == "ssh" and run is not None:
                got = await asyncio.to_thread(_ssh_check, check, run, label, ts)
            else:
                got = await asyncio.to_thread(_local_check, check, label, ts)
        except Exception as exc:  # noqa: BLE001 — never let one dead host kill the sweep
            errors.append(CheckFailure(host=label, check=check, reason=_reason(exc)))
            continue
        samples.extend(got)

    if want_http or want_cert:
        target_urls = _urls_from(config, urls)
        http_checks = tuple(name for name in ("http", "cert") if name in requested)
        if not target_urls:
            errors.append(
                CheckFailure(
                    host=label,
                    check="http" if want_http else "cert",
                    reason="no URLs given — pass urls=[…] or a config mapping with 'urls'",
                )
            )
        else:
            http_samples, http_errors = await collect_http(
                target_urls, host=label, checks=http_checks
            )
            samples.extend(http_samples)
            errors.extend(http_errors)

    return samples, errors


async def collect(
    host: str | None,
    checks: Sequence[str],
    *,
    config: Any | None = None,
    urls: Sequence[str] | None = None,
) -> list[Sample]:
    """Run *checks* against *host* and return only the readings.

    Samples-only view of :func:`collect_with_errors`: every dropped check is
    logged at ``logger.warning``, a dead host yields ``[]``, and nothing raises
    except an unknown check name. Pass ``urls`` (or a URL-bearing mapping as
    ``config``) when ``checks`` contains ``http`` / ``cert``.
    """
    samples, errors = await collect_with_errors(host, checks, config=config, urls=urls)
    for failure in errors:
        logger.warning(
            "monitoring: %s check on %s produced no sample: %s",
            failure.check,
            failure.host,
            failure.reason,
        )
    return samples
