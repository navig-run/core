"""
NAVIG Gateway — Structured Audit Log

Appends a JSONL record to ``~/.navig/runtime/audit.jsonl`` for every
privileged operation that passes through the gateway.

Record schema::

    {
      "ts":         "2026-02-16T12:34:56.789Z",  # ISO-8601 UTC
      "actor":      "telegram:123456789",         # who initiated
      "action":     "db.query",                   # action slug
      "policy":     "allow",                      # PolicyDecision value
      "status":     "success",                    # success | denied | error | pending_approval
      "input_hash": "sha256:abcdef...",           # sha256 of sanitized input (no secrets)
      "output_len": 128,                          # character length of output
      "metadata":   {}                            # optional extra fields
    }

Thread-safe: uses a threading.Lock so concurrent async handlers can all
emit records without interleaving.

Usage::

    log = AuditLog()
    log.record(
        actor="telegram:user123",
        action="run.shell",
        policy="require_approval",
        status="pending_approval",
        raw_input="ls /home",
    )
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default path — can be overridden via AuditLog(path=...)
_DEFAULT_PATH = Path.home() / ".navig" / "runtime" / "audit.jsonl"


class AuditLog:
    """
    Thread-safe JSONL audit log for privileged gateway operations.

    Each call to :meth:`record` synchronously appends one JSON line to the
    log file.  The file and parent directories are created on first write.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path else _DEFAULT_PATH
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(
        self,
        *,
        actor: str,
        action: str,
        policy: str = "allow",
        status: str = "success",
        raw_input: str | None = None,
        raw_output: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Append one audit record.

        :param actor:      Identifier of the initiating actor.
        :param action:     Dot-notation action slug (e.g. ``"db.query"``).
        :param policy:     PolicyDecision value string.
        :param status:     ``"success" | "denied" | "error" | "pending_approval"``.
        :param raw_input:  Raw input text (will be hashed, not stored verbatim).
        :param raw_output: Raw output text (only length is stored).
        :param metadata:   Optional extra fields to include in the record.
        :returns: The record dict that was written.
        """
        record = self._build_record(
            actor=actor,
            action=action,
            policy=policy,
            status=status,
            raw_input=raw_input,
            raw_output=raw_output,
            metadata=metadata or {},
        )
        self._write(record)
        return record

    def tail(self, n: int = 50) -> list[dict[str, Any]]:
        """Return the last *n* records from the log file."""
        if not self._path.exists():
            return []
        lines: list[str] = []
        try:
            with open(self._path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError as exc:
            logger.warning("AuditLog read error: %s", exc)
            return []

        records = []
        for line in lines[-n:]:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # skip malformed lines
        return records

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_record(
        *,
        actor: str,
        action: str,
        policy: str,
        status: str,
        raw_input: str | None,
        raw_output: str | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "actor": actor,
            "action": action,
            "policy": policy,
            "status": status,
        }

        if raw_input is not None:
            digest = hashlib.sha256(raw_input.encode("utf-8", errors="replace")).hexdigest()[:16]
            record["input_hash"] = f"sha256:{digest}"

        if raw_output is not None:
            record["output_len"] = len(raw_output)

        if metadata:
            record["metadata"] = metadata

        return record

    def _write(self, record: dict[str, Any]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, open(self._path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.error("AuditLog write failed: %s", exc)

    def __repr__(self) -> str:  # pragma: no cover
        return f"AuditLog(path={str(self._path)!r})"
