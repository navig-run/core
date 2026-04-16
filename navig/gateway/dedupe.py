"""Two-level idempotency cache for Telegram update intake (and webhook calls).

Prevents double-execution when Telegram retries an update or a webhook is
delivered more than once due to network hiccups or service restarts.

Layer 1 — In-memory bounded dict (fast hot-path check).
Layer 2 — Disk JSON store (cold-start recovery, survives process restart).

Both layers are bounded; the disk store is atomically written (write-to-temp
+ rename) to avoid corrupt state on crash or abrupt shutdown.

Usage
-----
from navig.gateway.dedupe import UpdateDedupe

_dedup = UpdateDedupe()           # uses defaults from config/defaults.yaml
                                   # or pass memory_max / file_max directly

async def on_update(update_id: int) -> None:
    if _dedup.check_and_record(str(update_id)):
        return  # already processed — discard
    ...
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from collections import OrderedDict
from pathlib import Path

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level defaults — override via UpdateDedupe(...) constructor args or
# config/defaults.yaml  gateway.dedupe.*
# ---------------------------------------------------------------------------
_DEFAULT_MEMORY_MAX: int = 1_000    # max entries held in memory LRU
_DEFAULT_FILE_MAX: int = 5_000      # max entries persisted to disk
_DEFAULT_STORE_PATH: str = os.path.join(
    os.path.expanduser("~"), ".navig", "dedup_updates.json"
)


class UpdateDedupe:
    """Thread-safe (within a single asyncio event loop) two-level dedupe store.

    Parameters
    ----------
    memory_max:
        Maximum number of update IDs held in the in-memory LRU.
    file_max:
        Maximum number of update IDs persisted to the JSON store on disk.
    store_path:
        Path for the disk store file.  Parent directories are created if missing.
    """

    def __init__(
        self,
        memory_max: int = _DEFAULT_MEMORY_MAX,
        file_max: int = _DEFAULT_FILE_MAX,
        store_path: str | Path = _DEFAULT_STORE_PATH,
    ) -> None:
        self._memory_max = memory_max
        self._file_max = file_max
        self._store_path = Path(store_path)

        # Ordered dict used as LRU (oldest at front)
        self._cache: OrderedDict[str, float] = OrderedDict()
        self._loaded = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_and_record(self, key: str) -> bool:
        """Return ``True`` if *key* was already seen; record it if not.

        Checks memory first (O(1)), then the disk store on the first call
        after a process restart.  If the key is new, it is recorded in both
        layers.
        """
        if not self._loaded:
            self._load_from_disk()

        if key in self._cache:
            # Move to end (most-recently-used)
            self._cache.move_to_end(key)
            return True

        # Not in memory — this is a new key; record it
        self._record(key)
        return False

    def clear(self) -> None:
        """Wipe memory and disk store (useful in tests)."""
        self._cache.clear()
        if self._store_path.exists():
            try:
                self._store_path.unlink()
            except OSError:  # noqa: BLE001
                pass
        self._loaded = False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _record(self, key: str) -> None:
        self._cache[key] = time.time()
        # Evict oldest entries if over memory cap
        while len(self._cache) > self._memory_max:
            self._cache.popitem(last=False)
        # Persist to disk (best-effort; never raise into caller)
        self._flush_to_disk()

    def _load_from_disk(self) -> None:
        self._loaded = True
        if not self._store_path.exists():
            return
        try:
            data = json.loads(self._store_path.read_text(encoding="utf-8"))
            entries: dict[str, float] = data if isinstance(data, dict) else {}
            # Load newest first so we don't overfill the memory cap
            for key, ts in sorted(entries.items(), key=lambda x: -x[1]):
                if len(self._cache) >= self._memory_max:
                    break
                self._cache[key] = ts
        except Exception as exc:  # noqa: BLE001
            _log.debug("dedupe: failed to load disk store: %r", exc)

    def _flush_to_disk(self) -> None:
        """Atomically write the current cache to disk, pruning to *file_max*."""
        try:
            self._store_path.parent.mkdir(parents=True, exist_ok=True)

            # Build pruned snapshot: keep the *file_max* most-recently-seen
            all_items = list(self._cache.items())
            if len(all_items) > self._file_max:
                all_items = sorted(all_items, key=lambda x: -x[1])[: self._file_max]
            snapshot = dict(all_items)

            # Atomic write: temp file in same directory → rename
            fd, tmp = tempfile.mkstemp(
                dir=self._store_path.parent, suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(snapshot, f)
                os.replace(tmp, self._store_path)
            except Exception:  # noqa: BLE001
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except Exception as exc:  # noqa: BLE001
            _log.debug("dedupe: failed to flush disk store: %r", exc)
