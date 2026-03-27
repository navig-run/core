"""
NAVIG Gateway — Billing Event Emitter

Appends a JSONL record to ``~/.navig/runtime/billing.jsonl`` for every
operation that passes a policy gate (ALLOW decision).

Record schema::

    {
      "ts":         "2026-02-23T12:00:00.000Z",  # ISO-8601 UTC
      "actor":      "telegram:123456789",
      "action":     "mission.create",            # action slug passed to policy_check
      "event_type": "mission.create",            # billing category slug
      "units":      1,                           # metered units (future pricing)
      "metadata":   {}
    }

Thread-safe: uses a threading.Lock for concurrent async handlers.

Usage::

    emitter = BillingEmitter()
    emitter.emit(actor="telegram:user", action="mission.create")
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return (
        datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.")
        + f"{datetime.now(tz=timezone.utc).microsecond // 1000:03d}Z"
    )


# Map action slugs → billing event_type + units
# Unknown actions default to event_type==action, units==1
_ACTION_MAP: dict[str, tuple[str, int]] = {
    "mission.create": ("mission.create", 1),
    "mission.advance.start": ("mission.start", 1),
    "mission.advance.retry": ("mission.retry", 1),
    "mission.complete": ("mission.complete", 1),
    "node.register": ("node.register", 1),
    "formation.start": ("formation.start", 2),
    "daemon.stop": ("daemon.stop", 0),  # ops action, 0 units
    "task.add": ("task.add", 1),
    "mesh.route": ("mesh.route", 1),
    "run.shell": ("run.shell", 1),
}


class BillingEmitter:
    """
    Write structured billing events to ``~/.navig/runtime/billing.jsonl``.

    One instance lives on ``NavigGateway`` as ``gw.billing_emitter``.
    Call ``emit()`` after a successful policy ALLOW to record a chargeable event.
    """

    def __init__(self, log_path: Path | None = None) -> None:
        if log_path is None:
            log_path = Path.home() / ".navig" / "runtime" / "billing.jsonl"
        self._path = log_path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    # ── Public API ────────────────────────────────────────────────────

    def emit(
        self,
        actor: str,
        action: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Write one billing record (non-blocking, thread-safe)."""
        event_type, units = _ACTION_MAP.get(action, (action, 1))
        record: dict[str, Any] = {
            "ts": _now_iso(),
            "actor": actor,
            "action": action,
            "event_type": event_type,
            "units": units,
        }
        if metadata:
            record["metadata"] = metadata

        line = json.dumps(record, ensure_ascii=False)
        with self._lock, open(self._path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def tail(self, n: int = 50) -> list[dict[str, Any]]:
        """Return the last *n* billing records (oldest-first)."""
        if not self._path.exists():
            return []
        with self._lock:
            lines = self._path.read_text(encoding="utf-8", errors="replace").splitlines()
        records = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # malformed JSON; skip line
        return records[-n:]
