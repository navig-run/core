"""
bridge_grid_reader.py — Read ~/.navig/bridge-grid.json written by navig-bridge.

navig-bridge (the VS Code extension) writes this heartbeat file every 8 seconds
when it holds the "primary" role.  Schema:

    {
        "pid":          <int>,          # OS process ID of the VS Code window
        "ts":           <ISO-8601>,     # Last heartbeat timestamp (UTC)
        "slot":         <int>,          # Window slot (0=VS Code, 1=Insiders, etc.)
        "app":          <str>,          # "vscode" | "vscodium" | "cursor" | …
        "role":         "primary",
        "llm_port":     <int>,          # LlmServer WebSocket port
        "bridge_port":  <int>           # CopilotBridgeProvider HTTP port (/v1)
    }

We consider the entry valid if:
  - File exists and parses as JSON
  - `ts` is within PRIMARY_TTL_SECONDS of now
  - Process `pid` is still alive (best-effort; skipped on platforms that
    cannot probe other processes)
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

# Must match PRIMARY_TTL_MS in navig-bridge extension.ts (15 000 ms)
PRIMARY_TTL_SECONDS: float = 15.0

# Default WebSocket port for LlmServer in navig-bridge
BRIDGE_DEFAULT_PORT: int = 42070

# Debounce: don't hammer the filesystem from hot paths
_PROBE_INTERVAL: float = 5.0

_bridge_grid_path: Path = Path.home() / ".navig" / "bridge-grid.json"
_last_read_ts: float = 0.0
_cached_result: dict | None = None


def read_bridge_grid(*, force: bool = False) -> dict | None:
    """Return parsed bridge-grid.json data if valid, else ``None``.

    Results are cached for ``_PROBE_INTERVAL`` seconds to avoid constant
    disk I/O from hot paths such as :meth:`BridgeRegistry.best`.
    Pass ``force=True`` to bypass the cache.
    """
    global _last_read_ts, _cached_result

    now = time.monotonic()
    if not force and (now - _last_read_ts) < _PROBE_INTERVAL:
        return _cached_result

    _last_read_ts = now
    _cached_result = _read_and_validate()
    return _cached_result


def _read_and_validate() -> dict | None:
    try:
        text = _bridge_grid_path.read_text(encoding="utf-8")
        data = json.loads(text)
    except Exception:
        return None

    # ── TTL check ─────────────────────────────────────────────────────────────
    ts_raw = data.get("ts")
    if ts_raw:
        try:
            ts = datetime.fromisoformat(ts_raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - ts).total_seconds()
            if age > PRIMARY_TTL_SECONDS:
                return None
        except Exception:
            return None

    # ── PID liveness ──────────────────────────────────────────────────────────
    pid = data.get("pid")
    if pid:
        try:
            if not _is_pid_alive(int(pid)):
                return None
        except Exception:
            pass  # Cannot check; assume alive

    return data


def is_bridge_grid_alive(*, force: bool = False) -> bool:
    """Return ``True`` if a live navig-bridge primary window is running."""
    return read_bridge_grid(force=force) is not None


def get_llm_port(*, force: bool = False) -> int | None:
    """Return the live LlmServer WebSocket port, or ``None``."""
    data = read_bridge_grid(force=force)
    if data:
        return data.get("llm_port")
    return None


def get_bridge_port(*, force: bool = False) -> int | None:
    """Return the live CopilotBridgeProvider HTTP port, or ``None``."""
    data = read_bridge_grid(force=force)
    if data:
        return data.get("bridge_port")
    return None


def invalidate_cache() -> None:
    """Force the next :func:`read_bridge_grid` call to re-read from disk."""
    global _last_read_ts
    _last_read_ts = 0.0


# ── Platform-safe PID probe ───────────────────────────────────────────────────


def _is_pid_alive(pid: int) -> bool:
    """Cross-platform best-effort PID liveness check."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)  # Signal 0 = probe; raises OSError if dead/no perms
        return True
    except OSError:
        return False
    except Exception:
        return True  # Unknown platform; assume alive
