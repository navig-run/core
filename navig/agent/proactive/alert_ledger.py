"""Which proactive alerts were already pushed — so each one reaches the operator ONCE.

The proactive engine polls every 60 s and sees the same unread email / upcoming event
on every poll until it is read or passes. Pushing on every poll would send the same
Telegram message sixty times an hour; pushing never (the state before this module)
made the whole engine silent. The ledger is the middle: a key per alert, remembered
on disk so a daemon restart does not re-announce everything still unread.

Bounded: entries older than ``RETENTION`` are dropped and at most ``MAX_KEYS`` kept,
so it cannot grow without limit on a busy inbox.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

RETENTION_S = 14 * 24 * 3600
MAX_KEYS = 2000


class AlertLedger:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._seen: dict[str, float] | None = None
        # The file exists but could not be read: remember in memory only, never write —
        # overwriting an unread ledger would re-announce everything it held.
        self._unreadable = False

    def _file(self) -> Path:
        if self._path is None:
            from navig.platform.paths import config_dir

            self._path = config_dir() / "proactive" / "alerted.json"
        return self._path

    def _load(self) -> dict[str, float]:
        if self._seen is None:
            from navig.core.json_io import JsonReadError, load_json_for_update

            try:
                raw = load_json_for_update(self._file(), default={})
            except JsonReadError:
                logger.warning("proactive alert ledger unreadable; not overwriting it")
                self._unreadable = True
                raw = {}
            self._seen = (
                {str(k): float(v) for k, v in raw.items() if isinstance(v, (int, float))}
                if isinstance(raw, dict)
                else {}
            )
        return self._seen

    def claim(self, key: str) -> bool:
        """True the FIRST time ``key`` is seen (and records it); False afterwards."""
        with self._lock:
            seen = self._load()
            if key in seen:
                return False
            now = time.time()
            seen[key] = now
            cutoff = now - RETENTION_S
            for k in [k for k, t in seen.items() if t < cutoff]:
                del seen[k]
            if len(seen) > MAX_KEYS:
                for k, _t in sorted(seen.items(), key=lambda kv: kv[1])[: len(seen) - MAX_KEYS]:
                    del seen[k]
            if self._unreadable:
                return True
            try:
                from navig.core.json_io import atomic_write_json

                self._file().parent.mkdir(parents=True, exist_ok=True)
                atomic_write_json(seen, self._file())
            except Exception:  # noqa: BLE001 — a lost ledger write costs one repeat alert
                logger.debug("proactive alert ledger write failed", exc_info=True)
            return True
