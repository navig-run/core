"""RuntimeStore SQLite cache: expires_at is written in the canonical '…Z' shape.

cache_set stored expires_at via `_utc_now_dt().isoformat()` → a '+00:00' offset, while the
reads (cache_get / cleanup) compare it lexicographically against `_utcnow()` (which is '…Z').
That is a cross-format string comparison — the same class as the reminder remind_at bug (#635)
— which mis-sorts at the sub-second boundary. It is now written via `_to_utc_iso` so both sides
share one canonical shape. (Short-TTL cache rows self-heal to the new shape; no migration.)
"""

from __future__ import annotations

import re

from navig.store.base import _utcnow
from navig.store.runtime import RuntimeStore

_CANONICAL = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")


def test_cache_expires_at_is_canonical_z(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.db")
    store.cache_set("k", {"v": 1}, ttl_seconds=60)
    row = store._read_one("SELECT expires_at FROM cache WHERE key = ?", ("k",))
    # Pre-fix this was '…+00:00' (isoformat) — a cross-format compare vs _utcnow()'s '…Z'.
    assert _CANONICAL.match(row["expires_at"]), row["expires_at"]
    assert row["expires_at"].endswith("Z") and "+" not in row["expires_at"]
    # Same byte-shape as _utcnow, so the string comparisons the reads use are homogeneous.
    assert _CANONICAL.match(_utcnow())


def test_cache_set_get_roundtrip_and_ttl(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.db")
    store.cache_set("fresh", {"v": 2}, ttl_seconds=3600)
    assert store.cache_get("fresh") == {"v": 2}  # not expired

    store.cache_set("stale", {"v": 3}, ttl_seconds=-1)  # already expired
    assert store.cache_get("stale") is None
