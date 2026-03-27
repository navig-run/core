"""NAVIG Blackbox Recorder — append-only JSONL event stream.

Storage: ~/.navig/blackbox/events.jsonl
Format : One JSON object per line (JSONL).
Rotation: File rotated when it exceeds 50 MB (old renamed to events.jsonl.1).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .types import BlackboxEvent, EventType

__all__ = ["BlackboxRecorder", "get_recorder"]

_MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB
_EVENTS_FILE = "events.jsonl"


class BlackboxRecorder:
    """Append-only event recorder using JSONL storage.

    Parameters
    ----------
    blackbox_dir : Path to the blackbox directory.
    """

    def __init__(self, blackbox_dir: Path) -> None:
        self.blackbox_dir = blackbox_dir
        self._events_path = blackbox_dir / _EVENTS_FILE
        self._enabled = True

    # ── Control ──────────────────────────────────────────────────────────────

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    def is_enabled(self) -> bool:
        return self._enabled

    # ── Recording ─────────────────────────────────────────────────────────────

    def record(
        self,
        event_type: EventType,
        payload: dict,
        tags: list[str] | None = None,
        source: str = "navig",
    ) -> BlackboxEvent | None:
        """Append an event to the JSONL stream.

        Returns the event, or None if recording is disabled.
        """
        if not self._enabled:
            return None

        event = BlackboxEvent.create(event_type, payload, tags, source)
        self._write(event)
        return event

    def _write(self, event: BlackboxEvent) -> None:
        self.blackbox_dir.mkdir(parents=True, exist_ok=True)
        self._maybe_rotate()
        with open(self._events_path, "a", encoding="utf-8") as fh:
            fh.write(event.to_json() + "\n")

    def _maybe_rotate(self) -> None:
        if not self._events_path.exists():
            return
        if self._events_path.stat().st_size > _MAX_SIZE_BYTES:
            rotated = self._events_path.with_suffix(".jsonl.1")
            rotated.unlink(missing_ok=True)
            self._events_path.rename(rotated)

    # ── Reading ───────────────────────────────────────────────────────────────

    def read_events(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 500,
        event_type: EventType | None = None,
    ) -> list[BlackboxEvent]:
        """Read and filter events from the JSONL stream.

        Parameters
        ----------
        since      : Only events at or after this timestamp (UTC).
        until      : Only events at or before this timestamp (UTC).
        limit      : Max number of events to return (newest first).
        event_type : Filter to a specific event type.
        """
        if not self._events_path.exists():
            return []

        events: list[BlackboxEvent] = []
        try:
            lines = self._events_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []

        # Parse last ``limit`` lines for efficiency (large files)
        for line in reversed(lines[-limit * 4 :]):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                event = BlackboxEvent.from_dict(data)
            except (json.JSONDecodeError, KeyError, ValueError):
                continue

            if since and event.timestamp < since:
                continue
            if until and event.timestamp > until:
                continue
            if event_type and event.event_type != event_type:
                continue

            events.append(event)
            if len(events) >= limit:
                break

        return events  # already newest-first from reversed iteration

    def tail(self, n: int = 50) -> list[BlackboxEvent]:
        """Return the last *n* events."""
        return self.read_events(limit=n)

    def event_count(self) -> int:
        """Approximate number of events (line count)."""
        if not self._events_path.exists():
            return 0
        try:
            with open(self._events_path, "rb") as fh:
                return sum(1 for _ in fh)
        except OSError:
            return 0

    def last_event_ts(self) -> datetime | None:
        """Timestamp of the most recent event."""
        events = self.tail(1)
        return events[0].timestamp if events else None

    # ── Maintenance ───────────────────────────────────────────────────────────

    def clear(self) -> None:
        """Truncate the events JSONL file."""
        if self._events_path.exists():
            self._events_path.write_bytes(b"")

    def file_size_mb(self) -> float:
        if not self._events_path.exists():
            return 0.0
        return self._events_path.stat().st_size / (1024 * 1024)


# ── Module-level singleton ────────────────────────────────────────────────────

_recorder: BlackboxRecorder | None = None


def get_recorder(blackbox_dir: Path | None = None) -> BlackboxRecorder:
    """Return (or create) the global BlackboxRecorder singleton."""
    global _recorder
    if _recorder is None:
        if blackbox_dir is None:
            from navig.platform.paths import blackbox_dir as _bbdir

            blackbox_dir = _bbdir()
        _recorder = BlackboxRecorder(blackbox_dir)
    return _recorder
