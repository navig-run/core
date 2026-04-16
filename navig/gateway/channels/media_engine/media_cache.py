"""
SHA-256 file-level cache with 24-hour TTL for the Media Context Engine.

Results (dicts) are stored as JSON files under::

    ~/.navig/cache/media/<sha256>.json

Expiry is checked on every read via mtime.

Usage::

    from navig.gateway.channels.media_engine.media_cache import MediaCache

    cache = MediaCache()
    key = cache.key(file_bytes)
    result = cache.get(key)
    if result is None:
        result = run_expensive_analysis(file_bytes)
        cache.put(key, result)
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from navig.platform.paths import cache_dir

logger = logging.getLogger(__name__)

_TTL_SECONDS = 86_400  # 24 hours


class MediaCache:
    """Disk-backed JSON cache keyed by SHA-256 hash of raw file bytes."""

    def __init__(
        self,
        namespace: str = "media",
        ttl_seconds: int = _TTL_SECONDS,
        cache_root: Path | None = None,
    ) -> None:
        if cache_root is None:
            cache_root = cache_dir()
        self._dir = cache_root / namespace
        self._dir.mkdir(parents=True, exist_ok=True)
        self._ttl = ttl_seconds

    # ── Key derivation ────────────────────────────────────────────────────────

    @staticmethod
    def key(data: bytes) -> str:
        """Return the full SHA-256 hex digest for *data*."""
        return hashlib.sha256(data).hexdigest()

    # ── Cache operations ──────────────────────────────────────────────────────

    def get(self, key: str) -> dict[str, Any] | None:
        """Return cached result dict, or None if missing / expired."""
        path = self._dir / f"{key}.json"
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > self._ttl:
            try:
                path.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("MediaCache: corrupt entry %s: %s", key[:12], exc)
            return None

    def put(self, key: str, value: dict[str, Any]) -> None:
        """Write *value* to cache under *key*.  Silently ignores write errors."""
        path = self._dir / f"{key}.json"
        tmp = path.with_suffix(".tmp")
        try:
            atomic_write_text(tmp, json.dumps(value, ensure_ascii=False))
            tmp.replace(path)
        except Exception as exc:
            logger.debug("MediaCache: write failed for %s: %s", key[:12], exc)
            try:
                tmp.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

    def invalidate(self, key: str) -> None:
        """Remove a cache entry (no-op if not present)."""
        try:
            (self._dir / f"{key}.json").unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

    def evict_expired(self) -> int:
        """Remove all expired entries.  Returns count removed."""
        removed = 0
        now = time.time()
        for p in self._dir.glob("*.json"):
            try:
                if (now - p.stat().st_mtime) > self._ttl:
                    p.unlink(missing_ok=True)
                    removed += 1
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
        return removed
