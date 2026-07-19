"""
In-flight operation registry + reaper — ledger honesty under hard kills.

The operation ledger (``navig.operation_recorder``) writes a line ONLY at
completion: ``start_operation()`` builds an in-memory ``PENDING`` record and the
line is appended by ``complete_operation()`` at process exit (an ``atexit``
handler — ``navig.cli.middleware``). A process hard-killed (SIGKILL, or a
SIGTERM that never reaches ``atexit``) therefore leaves **no ledger line at
all** — the operation vanishes silently. That is the honesty gap this module
closes.

Mechanism: a tiny per-invocation MARKER file written OUTSIDE the hash-chained
ledger (``<history>/inflight/<op_id>.json``) the moment an operation starts, and
deleted when it completes. A marker that outlives its process is an operation
that was interrupted. The reaper appends exactly ONE terminal ``interrupted``
record for it — a **chain-safe APPEND** via ``OperationRecorder.record()``,
never a rewrite of a chained line (T-067) — then removes the marker
(idempotent).

Why a sidecar rather than a PENDING ledger line: the chained ledger's invariant
is exactly one line per operation (T-068, ``claim_cli_operation``). Writing a
PENDING line at start and a terminal line at completion would either double
every line or require rewriting a chained line (breaking the chain). The sidecar
keeps one-line-per-op: a completed op writes its single terminal line and its
marker is deleted; an interrupted op writes its single terminal line when
reaped.

Safety — never reap a live op:

- the marker records the owning PID and its ``psutil`` ``create_time``; a marker
  whose PID is still alive (with a matching create_time) is NEVER reaped — a
  long-running op is protected by process liveness, not by any age guess;
- liveness that cannot be determined is treated as ALIVE (do not reap what you
  cannot identify — the ``navig doctor`` honesty rule);
- a secondary age threshold guards against PID recycling and clock skew;
- if the op id already has a ledger line (a completion that raced the marker
  delete), the marker is just removed — no duplicate, no false ``interrupted``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

#: Default grace before a dead-PID marker is reaped. PID-liveness is the real
#: guard (a running op has a live PID); this window only covers PID-recycle and
#: clock-skew races between a completion write and its marker delete.
DEFAULT_REAP_MAX_AGE_SECONDS: float = 120.0

_MARKER_SUFFIX = ".json"


def inflight_dir(history_dir: Path | str) -> Path:
    """The directory holding per-operation in-flight markers."""
    return Path(history_dir) / "inflight"


@dataclass
class InflightMarker:
    """A parsed in-flight marker — one operation that started and may still be
    running (live PID) or may have been interrupted (dead PID)."""

    op_id: str
    command: str
    pid: int | None
    create_time: float | None
    started_at: str  # ISO-8601 (UTC)
    host: str | None
    operation_type: str
    working_dir: str
    path: Path

    def age_seconds(self, now: float | None = None) -> float:
        """Seconds since the operation started, from ``started_at`` (falling
        back to the marker file's mtime when the timestamp is unparseable)."""
        now = time.time() if now is None else now
        ts = _parse_iso(self.started_at)
        if ts is None:
            try:
                return max(0.0, now - self.path.stat().st_mtime)
            except OSError:
                return 0.0
        return max(0.0, now - ts)


def _parse_iso(ts: str) -> float | None:
    try:
        return datetime.fromisoformat(ts).timestamp()
    except (ValueError, TypeError):
        return None


def _process_create_time(pid: int) -> float | None:
    """The process's creation time (for PID-recycle detection), or ``None``."""
    try:
        import psutil

        return psutil.Process(pid).create_time()
    except Exception:  # noqa: BLE001 — best-effort identity hint, never fatal
        return None


def write_marker(
    history_dir: Path | str,
    *,
    op_id: str,
    command: str,
    host: str | None,
    operation_type: str,
    working_dir: str,
    pid: int | None = None,
    started_at: str | None = None,
) -> Path | None:
    """Write an in-flight marker for one operation. Best-effort; never raises.

    Atomic (tmp + ``os.replace``) so a concurrent reader never sees a
    half-written marker. Returns the marker path, or ``None`` on any failure.
    """
    if not op_id:
        return None
    pid = os.getpid() if pid is None else pid
    started_at = started_at or datetime.now(timezone.utc).isoformat()
    payload = {
        "op_id": op_id,
        "command": command,
        "pid": pid,
        "create_time": _process_create_time(pid),
        "started_at": started_at,
        "host": host,
        "operation_type": operation_type,
        "working_dir": working_dir,
    }
    try:
        d = inflight_dir(history_dir)
        d.mkdir(parents=True, exist_ok=True)
        final = d / f"{op_id}{_MARKER_SUFFIX}"
        tmp = d / f".{op_id}{_MARKER_SUFFIX}.tmp"
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, final)
        return final
    except OSError as exc:
        logger.debug("inflight marker write skipped: %s", exc)
        return None


def clear_marker(history_dir: Path | str, op_id: str) -> None:
    """Delete an operation's in-flight marker (called at completion). No-op when
    absent. Best-effort; never raises."""
    if not op_id:
        return
    try:
        (inflight_dir(history_dir) / f"{op_id}{_MARKER_SUFFIX}").unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("inflight marker clear skipped: %s", exc)


def _read_marker(path: Path) -> InflightMarker | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("op_id"):
        return None
    pid = data.get("pid")
    create_time = data.get("create_time")
    return InflightMarker(
        op_id=str(data.get("op_id")),
        command=str(data.get("command", "")),
        pid=pid if isinstance(pid, int) else None,
        create_time=create_time if isinstance(create_time, (int, float)) else None,
        started_at=str(data.get("started_at", "")),
        host=data.get("host"),
        operation_type=str(data.get("operation_type", "other")),
        working_dir=str(data.get("working_dir", "")),
        path=path,
    )


def iter_markers(history_dir: Path | str) -> list[InflightMarker]:
    """Every parseable in-flight marker. Best-effort; never raises.

    Dot-prefixed files (in-progress ``.tmp`` writes and ``.reaping`` claims) are
    skipped so a scan never trips over a half-written or already-claimed marker.
    """
    d = inflight_dir(history_dir)
    out: list[InflightMarker] = []
    if not d.exists():
        return out
    try:
        paths = sorted(d.glob(f"*{_MARKER_SUFFIX}"))
    except OSError:
        return out
    for p in paths:
        if p.name.startswith("."):
            continue
        marker = _read_marker(p)
        if marker is not None:
            out.append(marker)
    return out


def pid_is_alive(pid: int | None, create_time: float | None = None) -> bool:
    """Is the owning process still alive?

    Indeterminate liveness resolves to ``True`` — never reap what you cannot
    identify (the doctor-honesty rule). A ``None`` pid has no identity to
    protect, so it resolves to ``False`` (reap-eligible by age alone). When a
    ``create_time`` is recorded and does not match the live process holding that
    PID, the PID was recycled → the original process is gone (``False``).
    """
    if pid is None:
        return False

    psutil = None
    try:
        import psutil as _psutil

        psutil = _psutil
    except Exception:  # noqa: BLE001 — psutil is a hard dep; guard defensively
        psutil = None

    if psutil is not None:
        try:
            if not psutil.pid_exists(int(pid)):
                return False
            if create_time is not None:
                try:
                    actual = psutil.Process(int(pid)).create_time()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    return False
                if abs(actual - float(create_time)) > 1.0:
                    return False  # PID recycled — a different process now
            return True
        except Exception:  # noqa: BLE001 — fall through to the os.kill probe
            pass

    if hasattr(os, "kill"):
        try:
            os.kill(int(pid), 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # exists, owned by another user
        except OSError:
            pass

    # Truly indeterminate — treat as alive so a live op is never reaped.
    return True


def claim_marker(marker: InflightMarker) -> Path | None:
    """Atomically claim a marker for reaping (cross-process idempotency).

    Renames the marker to a unique dot-prefixed ``.reaping`` name (skipped by
    :func:`iter_markers`). Returns the claimed path, or ``None`` when another
    reaper already took it (the rename fails because the source is gone).
    """
    claimed = marker.path.with_name(
        f".{marker.path.stem}.reaping.{os.getpid()}{_MARKER_SUFFIX}"
    )
    try:
        marker.path.rename(claimed)
        return claimed
    except (FileNotFoundError, OSError):
        return None


def remove_claimed(claimed: Path) -> None:
    """Remove a claimed marker after its ``interrupted`` record is appended."""
    try:
        Path(claimed).unlink(missing_ok=True)
    except OSError:
        pass
