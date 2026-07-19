"""Shared access to the LIVE scheduler job store for habit/cron consumers.

Historically, habit surfaces (``navig habit``, the life dashboard, the
Telegram habit commands, and the deck Life app) read and wrote
``~/.navig/daemon/cron_jobs.json`` — a file the running :class:`CronService`
**never executes** (the engine's store is ``<config_dir>/scheduler/
cron_jobs.json``). Habits were created but never fired.

This module is the single home for the corrected access pattern:

- **In the gateway process** use the live service directly
  (``gateway.cron_service``) — file-level writes would be clobbered by the
  running scheduler's next save.
- **Out of process** (CLI): mutations go **HTTP-first** through the deck
  schedule routes (``/api/deck/schedule/crons*``) when the gateway is up, and
  fall back to a *detached* :class:`CronService` over the live store when it
  is down (safe: nobody else is writing, and the daemon reloads the store on
  its next start — the contract ``navig habit`` always documented).
- **Legacy migration**: jobs stranded in the dead ``daemon/`` store are
  merged into the live store once, then the legacy file is renamed to
  ``cron_jobs.json.migrated``. Migration runs only when the daemon is not
  holding the store (CLI fallback path / engine load).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_HABIT_NAME_PREFIX = "habit:"
_STORE_LOCK = threading.Lock()

# Deck routes used by the HTTP-first path (loopback, discovered via gateway.json).
_CRONS_ROUTE = "/api/deck/schedule/crons"
_STATUS_ROUTE = "/api/deck/status"
_HTTP_TIMEOUT = 2.5


# ── Paths ──────────────────────────────────────────────────────────────────────

def live_store_dir() -> Path:
    """The directory of the store the running CronService actually executes."""
    from navig.platform import paths  # noqa: PLC0415 — lazy: not on the help path

    return paths.config_dir() / "scheduler"


def live_store_path() -> Path:
    return live_store_dir() / "cron_jobs.json"


def legacy_store_path() -> Path:
    """The dead pre-fix store habit surfaces used to write (never executed)."""
    return Path.home() / ".navig" / "daemon" / "cron_jobs.json"


# ── Gateway HTTP (out-of-process consumers) ────────────────────────────────────

def _gateway_url() -> str | None:
    """The live gateway base URL from the discovery handshake, or None."""
    try:
        from navig.platform.paths import config_dir

        gw = config_dir() / "gateway.json"
        if not gw.exists():
            return None
        data = json.loads(gw.read_text(encoding="utf-8"))
        url = data.get("url")
        return str(url).rstrip("/") if url else None
    except Exception:
        return None


def _gateway_json(method: str, path: str, body: dict | None = None) -> dict | None:
    """One JSON round-trip to the gateway. Returns None on ANY failure."""
    base = _gateway_url()
    if not base:
        return None
    try:
        import urllib.request  # noqa: PLC0415 — lazy: not on the help path

        payload = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            f"{base}{path}",
            data=payload,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:  # noqa: S310 — loopback URL from our own handshake file
            data = json.loads(resp.read().decode("utf-8"))
        if isinstance(data, dict) and data.get("ok"):
            inner = data.get("data")
            return inner if isinstance(inner, dict) else {"data": inner}
        return None
    except Exception:
        return None


def daemon_reachable() -> bool:
    return _gateway_json("GET", _STATUS_ROUTE) is not None


# ── Legacy migration ───────────────────────────────────────────────────────────

def migrate_legacy_store() -> int:
    """Merge jobs stranded in the dead ``daemon/`` store into the live store.

    Merge is by job ``name`` (existing live names win); migrated jobs get
    fresh sequential ids from the live counter. The legacy file is renamed to
    ``cron_jobs.json.migrated`` so every old reader stops seeing stale data.

    Only call when the daemon is NOT holding the store (CLI fallback path or
    engine startup) — otherwise the scheduler's next save would clobber the
    merge. Never raises; returns the number of migrated jobs.
    """
    legacy = legacy_store_path()
    if not legacy.exists():
        return 0
    try:
        legacy_data = json.loads(legacy.read_text(encoding="utf-8"))
        legacy_jobs = [j for j in legacy_data.get("jobs", []) if isinstance(j, dict)]

        live = live_store_path()
        if live.exists():
            live_data = json.loads(live.read_text(encoding="utf-8"))
        else:
            live_data = {"counter": 0, "jobs": []}
        live_jobs: list[dict] = [j for j in live_data.get("jobs", []) if isinstance(j, dict)]
        counter = int(live_data.get("counter", 0))
        live_names = {j.get("name") for j in live_jobs}

        migrated = 0
        for job in legacy_jobs:
            if job.get("name") in live_names:
                continue
            counter += 1
            job = dict(job)
            job["id"] = f"job_{counter}"
            live_jobs.append(job)
            migrated += 1

        if migrated:
            _atomic_write(live, {"counter": counter, "jobs": live_jobs})
        legacy.rename(legacy.with_suffix(".json.migrated"))
        if migrated:
            logger.warning(
                "Migrated %s stranded job(s) from the dead %s store into the live scheduler "
                "store — they will start firing once the daemon (re)loads.",
                migrated,
                legacy,
            )
        return migrated
    except Exception as exc:  # pragma: no cover — best-effort by design
        logger.error("Legacy cron store migration failed: %s", exc)
        return 0


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _STORE_LOCK:
        tmp_fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(data, indent=2))
            os.replace(tmp_name, path)
        except Exception:
            Path(tmp_name).unlink(missing_ok=True)
            raise


# ── Detached-service fallback (daemon off) ─────────────────────────────────────

def _detached_service():
    """A CronService over the live store — for use only when the daemon is down."""
    migrate_legacy_store()
    from navig.scheduler.cron_service import CronService  # noqa: PLC0415

    return CronService(None, live_store_dir())


# ── High-level operations ──────────────────────────────────────────────────────
# Resolution order: in-process live service (gateway process) → gateway HTTP
# (CLI while the daemon runs) → detached service over the live store (daemon
# off; safe to write, adopted on next daemon start).

def _live_service():
    from navig.scheduler.cron_service import get_live_service  # noqa: PLC0415

    return get_live_service()


def list_jobs() -> list[dict[str, Any]]:
    """All cron jobs as dicts, from the live engine or the live store."""
    svc = _live_service()
    if svc is not None:
        return [j.to_dict() for j in svc.jobs.values()]
    resp = _gateway_json("GET", _CRONS_ROUTE)
    if resp is not None:
        jobs = resp.get("jobs")
        if isinstance(jobs, list):
            return jobs
    return [j.to_dict() for j in _detached_service().jobs.values()]


def list_habit_jobs() -> list[dict[str, Any]]:
    return [j for j in list_jobs() if str(j.get("name", "")).startswith(_HABIT_NAME_PREFIX)]


def create_job(
    name: str,
    schedule: str,
    command: str,
    *,
    enabled: bool = True,
    timeout_seconds: int = 30,
    replace: bool = False,
) -> dict[str, Any]:
    """Create a job in the LIVE store (via the running daemon when up).

    With ``replace=True`` any existing job with the same name is removed first
    (the ``navig habit add`` overwrite flow).
    """
    if replace:
        delete_jobs(name)
    svc = _live_service()
    if svc is None:
        body = {
            "name": name,
            "schedule": schedule,
            "command": command,
            "enabled": enabled,
            "timeout_seconds": timeout_seconds,
        }
        resp = _gateway_json("POST", _CRONS_ROUTE, body)
        if resp is not None:
            job = resp.get("job") or resp
            if isinstance(job, dict) and job.get("id"):
                return job
        svc = _detached_service()
    job_obj = svc.add_job(
        name=name,
        schedule=schedule,
        command=command,
        enabled=enabled,
        timeout_seconds=timeout_seconds,
    )
    return job_obj.to_dict()


def delete_jobs(key: str) -> int:
    """Delete every job whose ``name`` or ``id`` equals ``key``. Returns count."""
    removed = 0
    svc = _live_service()
    if svc is None:
        resp = _gateway_json("GET", _CRONS_ROUTE)
        if resp is not None:
            for job in resp.get("jobs") or []:
                if (job.get("name") == key or job.get("id") == key) and _gateway_delete(str(job["id"])):
                    removed += 1
            return removed
        svc = _detached_service()
    for job_id, job in list(svc.jobs.items()):
        if job.name == key or job.id == key:
            if svc.remove_job(job_id):
                removed += 1
    return removed


def _gateway_delete(job_id: str) -> bool:
    """DELETE /crons/{id} (urllib DELETE round-trip)."""
    return _gateway_json("DELETE", f"{_CRONS_ROUTE}/{job_id}") is not None
