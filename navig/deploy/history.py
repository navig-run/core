"""
navig.deploy.history — Append-only deploy history log.

Stored as JSON-lines at ~/.navig/cache/deploy_history.jsonl
Each line = one DeployResult dict.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_HISTORY_FILE = "deploy_history.jsonl"


class DeployHistory:
    """Manages the append-only deploy history log."""

    def __init__(self, cache_dir: Path, keep: int = 50):
        self._path = cache_dir / _HISTORY_FILE
        self._keep = keep
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, result_dict: dict[str, Any]) -> None:
        """Append one deploy result. Trims log to keep_last entries."""
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(result_dict, ensure_ascii=False) + "\n")
        self._trim()

    def read(
        self, limit: int = 10, app: str | None = None, host: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Read deploy history entries, newest-first.

        Args:
            limit: Max number of entries to return.
            app:   Filter by app name (optional).
            host:  Filter by host name (optional).
        """
        if not self._path.exists():
            return []

        lines = self._path.read_text(encoding="utf-8").splitlines()
        entries: list[dict[str, Any]] = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue  # malformed JSON; skip line
            if app and entry.get("app") != app:
                continue
            if host and entry.get("host") != host:
                continue
            entries.append(entry)
            if len(entries) >= limit:
                break

        return entries

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _trim(self) -> None:
        """Keep only the last N entries in the history file."""
        if not self._path.exists():
            return
        try:
            lines = [
                l
                for l in self._path.read_text(encoding="utf-8").splitlines()
                if l.strip()
            ]
            if len(lines) > self._keep:
                self._path.write_text(
                    "\n".join(lines[-self._keep :]) + "\n",
                    encoding="utf-8",
                )
        except Exception as exc:
            logger.warning("Could not trim deploy history: %s", exc)
