"""Harbor Bay catalog — the cloud marketplace index, cached for the Store.

The one source of truth for purchasable/openable items (webapps, apps, lenses,
skills, spaces…) is the cloud catalog at ``api.navig.run/api/marketplace/list``.
This module fetches it, caches it to ``~/.navig/cache/bay-catalog.json`` with a
TTL, and is offline-safe (a fetch failure falls back to the cache, then to an
empty list). The Store aggregator surfaces its ``webapp`` / ``app`` entries so
Photopea, Design Mode, etc. appear in ``navig store`` and the deck/os — the same
catalog every surface reads. Never fetches on the CLI fast path — only when the
Store is actually collected (and even then, only past the TTL or with --refresh).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_BAY_URL = "https://api.navig.run/api/marketplace/list"
_CACHE_FILE = "bay-catalog.json"
_TTL_SECONDS = 6 * 60 * 60  # 6h — the catalog changes rarely; refresh forces it
_FETCH_TIMEOUT = 4.0


def _cache_path():
    from navig.platform.paths import cache_dir

    return cache_dir() / _CACHE_FILE


def _read_cache() -> tuple[list[dict[str, Any]] | None, float]:
    """Return (entries, mtime) from the disk cache, or (None, 0) if absent/bad."""
    try:
        p = _cache_path()
        if not p.exists():
            return None, 0.0
        data = json.loads(p.read_text(encoding="utf-8"))
        entries = data.get("entries") if isinstance(data, dict) else data
        return (entries if isinstance(entries, list) else None), p.stat().st_mtime
    except Exception as exc:  # noqa: BLE001
        logger.debug("bay cache unreadable: %s", exc)
        return None, 0.0


def _write_cache(entries: list[dict[str, Any]]) -> None:
    try:
        from navig.core.yaml_io import atomic_write_text

        atomic_write_text(_cache_path(), json.dumps({"entries": entries}, indent=1))
    except Exception as exc:  # noqa: BLE001
        logger.debug("bay cache write failed: %s", exc)


def _fetch() -> list[dict[str, Any]] | None:
    """Fetch the live catalog. Returns None on any network/parse failure."""
    import urllib.request

    try:
        req = urllib.request.Request(_BAY_URL, headers={"User-Agent": "navig-store"})
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310 — fixed https host
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — offline / DNS / timeout / 5xx
        logger.debug("bay catalog fetch failed: %s", exc)
        return None
    # Accept either a bare list or {entries|catalog|items: [...]}.
    if isinstance(payload, list):
        return payload
    for key in ("entries", "catalog", "items"):
        val = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(val, list):
            return val
    return None


def fetch_bay_catalog(*, refresh: bool = False) -> list[dict[str, Any]]:
    """Bay catalog entries, cached. Fetches when the cache is missing, older than
    the TTL, or ``refresh`` is set; always falls back to the cache, then []."""
    cached, mtime = _read_cache()
    fresh = cached is not None and (time.time() - mtime) < _TTL_SECONDS
    if fresh and not refresh:
        return cached  # type: ignore[return-value]

    fetched = _fetch()
    if fetched is not None:
        _write_cache(fetched)
        return fetched
    return cached or []  # offline → last-known catalog, else empty
