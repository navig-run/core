"""Small JSON cache helper for NAVIG.

Caches live under the global NAVIG cache directory (~/.navig/cache) and are
meant for speeding up discovery-style commands.

Cache file format:
{
  "cached_at": "2025-12-26T12:34:56Z",
  "data": { ... }
}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(dt_str: str) -> datetime | None:
    try:
        s = dt_str.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except Exception:
        return None


def global_cache_dir() -> Path:
    return Path.home() / ".navig" / "cache"


@dataclass(frozen=True)
class CacheReadResult:
    hit: bool
    expired: bool
    data: Any | None
    cached_at: str | None


def read_json_cache(
    filename: str,
    *,
    ttl_seconds: int,
    no_cache: bool = False,
) -> CacheReadResult:
    """Read a cache file if present and not expired."""

    if no_cache:
        return CacheReadResult(hit=False, expired=False, data=None, cached_at=None)

    cache_path = global_cache_dir() / filename
    if not cache_path.exists():
        return CacheReadResult(hit=False, expired=False, data=None, cached_at=None)

    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        cached_at_raw = str(payload.get("cached_at", ""))
        cached_at_dt = _parse_iso(cached_at_raw)
        if cached_at_dt is None:
            return CacheReadResult(hit=False, expired=False, data=None, cached_at=None)

        age_seconds = int(
            (_utc_now() - cached_at_dt.astimezone(timezone.utc)).total_seconds()
        )
        if ttl_seconds >= 0 and age_seconds > ttl_seconds:
            return CacheReadResult(
                hit=True, expired=True, data=None, cached_at=cached_at_raw
            )

        return CacheReadResult(
            hit=True, expired=False, data=payload.get("data"), cached_at=cached_at_raw
        )
    except Exception:
        return CacheReadResult(hit=False, expired=False, data=None, cached_at=None)


def write_json_cache(filename: str, data: Any) -> Path:
    """Write a cache file atomically (best effort)."""

    cache_dir = global_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)

    cache_path = cache_dir / filename
    tmp_path = cache_dir / (filename + ".tmp")

    payload = {"cached_at": _to_iso_z(_utc_now()), "data": data}

    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(cache_path)
    return cache_path
