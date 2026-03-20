"""Update history storage for navig update.

Subclasses DeployHistory to reuse the JSONL pattern, but writes to
``update_history.jsonl`` and understands node_id-based filtering.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from navig.platform.paths import cache_dir as _cache_dir


class UpdateHistory:
    """JSONL-based history store for navig update operations."""

    _FILENAME = "update_history.jsonl"
    _DEFAULT_KEEP = 50

    def __init__(self, cache_dir: Optional[str] = None, keep: int = _DEFAULT_KEEP):
        if cache_dir is None:
            try:
                from navig.config import get_config_manager
                cm = get_config_manager()
                base = Path(cm.get("cache_dir", str(_cache_dir())))
            except Exception:
                base = _cache_dir()
            cache_dir = str(base)

        self._path = Path(cache_dir) / self._FILENAME
        self._keep = keep

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def append(self, record: Dict[str, Any]) -> None:
        """Append a single record to history, pruning to ``_keep`` entries."""
        import json
        self._path.parent.mkdir(parents=True, exist_ok=True)

        existing: List[str] = []
        if self._path.exists():
            try:
                existing = self._path.read_text(encoding="utf-8").splitlines()
            except Exception:
                existing = []

        existing.append(json.dumps(record, ensure_ascii=False))
        # Keep the most recent N entries
        if len(existing) > self._keep:
            existing = existing[-self._keep:]
        self._path.write_text("\n".join(existing) + "\n", encoding="utf-8")

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read(
        self,
        limit: int = 20,
        node_id: Optional[str] = None,
        host: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return history entries, optionally filtered by node_id / host."""
        import json
        if not self._path.exists():
            return []
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return []

        entries: List[Dict] = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        target = node_id or host
        if target:
            entries = [e for e in entries if e.get("node_id") == target]

        return entries[:limit]

    # ------------------------------------------------------------------
    # Clear
    # ------------------------------------------------------------------

    def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()
