"""
Operation Recorder for NAVIG - Command Replay & Time Travel

Records all CLI operations with full context for:
- Replaying successful commands
- Undoing reversible operations
- Auditing command history
- Exporting operation logs

Storage: ~/.navig/history/operations.jsonl (JSON Lines format)

Hash chain (T-067): every appended entry additionally carries ``prev`` (the
previous chained entry's hash) and ``hash`` (sha256 over prev + the entry's
canonical JSON) — see ``navig.ledger_chain``. Tamper-evident, not
tamper-proof; ``navig ledger verify`` re-walks the chain.
"""

import hashlib
import json
import logging
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# "Read everything" sentinel: used by export_json/export_csv/count when no
# row limit is wanted. Caps at a value well above any realistic history size.
_UNBOUNDED_SCAN_LIMIT: int = 100_000


class OperationType(str, Enum):
    """Categories of operations for filtering and undo logic."""

    FILE_CREATE = "file_create"
    FILE_DELETE = "file_delete"
    FILE_MODIFY = "file_modify"
    FILE_UPLOAD = "file_upload"
    FILE_DOWNLOAD = "file_download"
    REMOTE_COMMAND = "remote_command"
    LOCAL_COMMAND = "local_command"
    #: Pure read — `navig config get`, `host list`, `db tables`, ... No side
    #: effect, so reversibility is "none" (navig.reversibility), never a
    #: yellow/red scare label (T-068 follow-up).
    READ_QUERY = "read_query"
    DATABASE_QUERY = "database_query"
    DATABASE_DUMP = "database_dump"
    CONFIG_CHANGE = "config_change"
    HOST_SWITCH = "host_switch"
    TUNNEL_START = "tunnel_start"
    TUNNEL_STOP = "tunnel_stop"
    SERVICE_RESTART = "service_restart"
    DOCKER_COMMAND = "docker_command"
    WORKFLOW_RUN = "workflow_run"
    OTHER = "other"


class OperationStatus(str, Enum):
    """Outcome status of an operation."""

    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"  # Some parts succeeded
    CANCELLED = "cancelled"
    PENDING = "pending"
    #: The owning process died before recording a terminal status (SIGKILL, or a
    #: SIGTERM that never reached the atexit completer). Written only by the
    #: in-flight reaper (navig.operation_inflight) — never a working step and
    #: never a warnable failure (its real outcome is unknown), so `skill distill`
    #: treats it as neither, and `navig undo` (SUCCESS-only) never targets it.
    INTERRUPTED = "interrupted"


@dataclass
class OperationRecord:
    """
    Complete record of a CLI operation.

    Stores enough information to:
    - Replay the command
    - Undo reversible operations
    - Audit who did what when
    """

    # Unique identifier
    id: str = ""

    # When
    timestamp: str = ""
    duration_ms: float = 0

    # What
    command: str = ""  # Full CLI command
    operation_type: OperationType = OperationType.OTHER

    # Context
    host: str | None = None
    app: str | None = None
    working_dir: str = ""

    # Result
    status: OperationStatus = OperationStatus.PENDING
    output: str = ""
    error: str = ""
    exit_code: int = 0

    # For replay
    args: dict[str, Any] = field(default_factory=dict)
    env_vars: dict[str, str] = field(default_factory=dict)

    # For undo (T-068): `reversibility` is the green/yellow/red label
    # (navig.reversibility) computed at record time; `reversible` is kept in
    # sync with it (True iff green) for backward compatibility.
    reversible: bool = False
    undo_data: dict[str, Any] = field(default_factory=dict)
    reversibility: str = ""

    # Metadata
    tags: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        d = asdict(self)
        d["operation_type"] = self.operation_type.value
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OperationRecord":
        """Create from dictionary.

        Keeps only known dataclass fields: chain fields (``prev``/``hash``/
        ``sig`` — navig.ledger_chain) live on the JSONL line, not on the
        record, and any FUTURE field a newer writer adds must not crash an
        older reader (forward compatibility — the ledger is append-only and
        long-lived). Also copies instead of mutating the caller's dict.
        """
        from dataclasses import fields as dc_fields

        known = {f.name for f in dc_fields(cls)}
        data = {k: v for k, v in data.items() if k in known}
        # Unknown enum values (a ledger written by a NEWER navig) must degrade,
        # not raise: one foreign line would otherwise break every history/undo
        # iteration. OTHER classifies red and is never undoable — safe default.
        try:
            data["operation_type"] = OperationType(data.get("operation_type", "other"))
        except ValueError:
            data["operation_type"] = OperationType.OTHER
        try:
            data["status"] = OperationStatus(data.get("status", "pending"))
        except ValueError:
            data["status"] = OperationStatus.PENDING
        return cls(**data)


class OperationRecorder:
    """
    Records CLI operations for replay, undo, and auditing.

    Storage format: JSON Lines (one JSON object per line)
    Location: ~/.navig/history/operations.jsonl

    Features:
    - Append-only for reliability
    - Automatic rotation (configurable max entries)
    - Fast querying with in-memory index
    - Export to various formats
    """

    DEFAULT_MAX_ENTRIES = 10000

    def __init__(
        self,
        history_dir: Path | None = None,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ):
        """
        Initialize the operation recorder.

        Args:
            history_dir: Directory for history files. Defaults to ~/.navig/history/
            max_entries: Maximum entries before rotation
        """
        if history_dir is None:
            from navig.config import get_config_manager

            config = get_config_manager()
            history_dir = config.base_dir / "history"

        self.history_dir = Path(history_dir)
        self.history_file = self.history_dir / "operations.jsonl"
        self.max_entries = max_entries

        # Serializes tail-read + append (+ rotation) so concurrent in-process
        # writers cannot fork the hash chain or interleave lines.
        self._write_lock = threading.Lock()

        # Ensure directory exists
        self.history_dir.mkdir(parents=True, exist_ok=True)

        # In-memory index for fast queries
        self._index_loaded = False
        self._operation_ids: list[str] = []
        self._operations_by_id: dict[str, int] = {}  # id -> line number

    def _generate_id(self) -> str:
        """Generate unique operation ID."""
        timestamp = datetime.now(timezone.utc).isoformat()
        random_bits = hashlib.sha256(f"{timestamp}{id(self)}".encode()).hexdigest()[:8]
        return f"op-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{random_bits}"

    def _get_timestamp(self) -> str:
        """Get current timestamp in ISO 8601 format."""
        return datetime.now(timezone.utc).isoformat()

    def _load_index(self):
        """Load operation IDs into memory for fast lookup."""
        if self._index_loaded:
            return

        self._operation_ids = []
        self._operations_by_id = {}

        if not self.history_file.exists():
            self._index_loaded = True
            return

        try:
            with open(self.history_file, encoding="utf-8") as f:
                for line_num, line in enumerate(f):
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        op_id = data.get("id")
                        if op_id:
                            self._operation_ids.append(op_id)
                            self._operations_by_id[op_id] = line_num
                    except json.JSONDecodeError:
                        continue  # malformed JSON; skip line
        except OSError:
            pass  # best-effort cleanup

        self._index_loaded = True

    def record(self, record: OperationRecord) -> str:
        """
        Record an operation.

        The appended JSONL line carries the record plus the hash-chain fields
        ``prev`` and ``hash`` (navig.ledger_chain): the chain head is re-read
        from the file tail at every append, so it survives rotation, restarts
        cleanly after ``clear_history()``, and stays fresh across processes.

        Args:
            record: The operation record to store

        Returns:
            The operation ID
        """
        # Generate ID if not set
        if not record.id:
            record.id = self._generate_id()

        # Set timestamp if not set
        if not record.timestamp:
            record.timestamp = self._get_timestamp()

        # Reversibility label (T-068): computed here so EVERY appended entry
        # carries one, whatever path built the record. An explicit preset
        # label wins; `reversible` is kept in sync (green ⇔ True) so the two
        # fields can never disagree on a committed line.
        from navig.reversibility import Reversibility, classify

        if not record.reversibility:
            record.reversibility = classify(
                record.operation_type.value, record.undo_data or None, record.tags
            ).value
        record.reversible = record.reversibility == Reversibility.GREEN.value

        # Write to file
        try:
            from navig.ledger_chain import compute_entry_hash, read_tail_hash

            with self._write_lock:
                entry = record.to_dict()
                prev = read_tail_hash(self.history_file)
                entry["prev"] = prev
                entry["hash"] = compute_entry_hash(prev, entry)

                with open(self.history_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")

                # Rebuild index from file so operation_id -> file line mapping stays
                # correct even when history includes blank/malformed lines.
                self._index_loaded = False
                self._load_index()

                # Check for rotation
                if len(self._operation_ids) > self.max_entries:
                    self._rotate()

        except OSError as e:
            from navig import console_helper as ch

            ch.dim(f"Could not record operation: {e}")

        return record.id

    def start_operation(
        self,
        command: str,
        operation_type: OperationType = OperationType.OTHER,
        host: str | None = None,
        app: str | None = None,
        args: dict[str, Any] | None = None,
        reversible: bool = False,
        tags: list[str] | None = None,
    ) -> OperationRecord:
        """
        Create a new operation record at the start of a command.

        Call complete_operation() when the operation finishes.

        Returns:
            The operation record (call complete_operation() when done)
        """
        record = OperationRecord(
            id=self._generate_id(),
            timestamp=self._get_timestamp(),
            command=command,
            operation_type=operation_type,
            host=host,
            app=app,
            working_dir=str(Path.cwd()),
            args=args or {},
            reversible=reversible,
            tags=tags or [],
            status=OperationStatus.PENDING,
        )
        return record

    def complete_operation(
        self,
        record: OperationRecord,
        success: bool,
        output: str = "",
        error: str = "",
        exit_code: int = 0,
        duration_ms: float = 0,
        undo_data: dict[str, Any] | None = None,
        status: OperationStatus | None = None,
    ) -> str:
        """
        Complete and record an operation.

        Args:
            record: The operation record started with start_operation()
            success: Whether the operation succeeded
            output: Command output
            error: Error message if failed
            exit_code: Exit code
            duration_ms: Execution duration in milliseconds
            undo_data: Data needed to undo this operation
            status: Explicit terminal status. When given it WINS over *success*
                — lets callers record CANCELLED / PARTIAL (a Ctrl-C'd command is
                a cancel, not a failure), not just the SUCCESS/FAILED binary.
                When ``None`` (default) the status is ``SUCCESS if success else
                FAILED`` — identical to every existing caller.

        Returns:
            The operation ID
        """
        effective_status = (
            status if status is not None
            else (OperationStatus.SUCCESS if success else OperationStatus.FAILED)
        )
        record.status = effective_status
        record.output = self._truncate_output(output)
        record.error = error
        record.exit_code = exit_code
        record.duration_ms = duration_ms

        if undo_data:
            record.undo_data = undo_data

        # Dual-write: also log to audit.db (best-effort)
        try:
            from navig.store.audit import get_audit_store

            get_audit_store().log_event(
                action=f"{record.operation_type.value}",
                actor="user",
                target=record.host or record.app,
                details={
                    "command": record.command,
                    "exit_code": exit_code,
                    "error": error[:500] if error else "",
                },
                channel="cli",
                host=record.host,
                status=effective_status.value,
                duration_ms=int(duration_ms) if duration_ms else None,
            )
        except Exception as _exc:
            logger.debug("audit log skipped: %s", _exc)  # Never let audit failure block recording

        recorded_id = self.record(record)
        # The op reached a terminal status → drop its in-flight marker (if any).
        # A marker outliving its process is what the reaper turns into an honest
        # `interrupted` line; clearing it here is the normal, non-interrupted path.
        self.clear_inflight(record.id)
        return recorded_id

    # ------------------------------------------------------------------
    # In-flight registry + reaper (navig.operation_inflight)
    #
    # A completion writes the ledger line; a hard kill writes nothing. These
    # sidecar markers make an interrupted operation observable so the reaper can
    # record it honestly as `interrupted` — a chain-safe APPEND, never a rewrite.
    # ------------------------------------------------------------------

    def mark_inflight(self, record: OperationRecord, pid: int | None = None) -> None:
        """Write a per-operation in-flight marker so a hard kill leaves a trace.

        Best-effort — a marker-write hiccup must never break the command.
        Cleared by :meth:`complete_operation` on any terminal status; a marker
        that outlives its process is what :meth:`reap_inflight` ages out.
        """
        try:
            from navig import operation_inflight as _inflight

            _inflight.write_marker(
                self.history_dir,
                op_id=record.id,
                command=record.command,
                host=record.host,
                operation_type=record.operation_type.value,
                working_dir=record.working_dir,
                pid=pid,
            )
        except Exception as exc:  # noqa: BLE001 — recording plumbing must never raise
            logger.debug("mark_inflight skipped: %s", exc)

    def clear_inflight(self, op_id: str) -> None:
        """Remove an operation's in-flight marker (no-op when absent)."""
        try:
            from navig import operation_inflight as _inflight

            _inflight.clear_marker(self.history_dir, op_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("clear_inflight skipped: %s", exc)

    def iter_inflight(self) -> list[Any]:
        """Every parseable in-flight marker (running or interrupted)."""
        try:
            from navig import operation_inflight as _inflight

            return _inflight.iter_markers(self.history_dir)
        except Exception as exc:  # noqa: BLE001
            logger.debug("iter_inflight skipped: %s", exc)
            return []

    def reap_inflight(
        self,
        max_age_seconds: float | None = None,
        now: float | None = None,
        dry_run: bool = False,
    ) -> list[dict[str, Any]]:
        """Age out interrupted operations to a terminal ``interrupted`` status.

        A marker whose owning process is gone AND which is older than
        *max_age_seconds* gets exactly ONE ``interrupted`` ledger line appended
        (chain-safe — never a rewrite), then its marker is removed. Safe and
        idempotent:

        - a marker with a live PID is never reaped (a running op is protected by
          process liveness, not any age guess);
        - a marker whose op id already has a ledger line (a completion that raced
          the marker delete) is just cleared — no duplicate, no false status;
        - reaping twice does not double-write (the marker is gone after the
          first pass; a concurrent reaper loses the atomic claim).

        Returns a summary dict per reaped op (or per would-be-reaped op under
        *dry_run*).
        """
        import time

        from navig import operation_inflight as _inflight

        if max_age_seconds is None:
            max_age_seconds = _inflight.DEFAULT_REAP_MAX_AGE_SECONDS
        now = time.time() if now is None else now

        markers = _inflight.iter_markers(self.history_dir)
        if not markers:
            return []

        # Refresh the id index once so a completion that raced the marker delete
        # is detected and does not produce a duplicate `interrupted` line.
        self._index_loaded = False
        self._load_index()

        reaped: list[dict[str, Any]] = []
        for marker in markers:
            if _inflight.pid_is_alive(marker.pid, marker.create_time):
                continue  # currently running — never reap
            if marker.age_seconds(now) < max_age_seconds:
                continue  # dead but within the grace window
            if marker.op_id in self._operations_by_id:
                # Already recorded — the completion raced the marker delete.
                if not dry_run:
                    _inflight.clear_marker(self.history_dir, marker.op_id)
                continue

            summary = {
                "id": marker.op_id,
                "command": marker.command,
                "host": marker.host,
                "operation_type": marker.operation_type,
                "started_at": marker.started_at,
                "age_seconds": round(marker.age_seconds(now), 1),
                "pid": marker.pid,
            }
            if dry_run:
                reaped.append(summary)
                continue

            claimed = _inflight.claim_marker(marker)
            if claimed is None:
                continue  # another reaper claimed it first
            try:
                self._record_interrupted(marker)
                reaped.append(summary)
            finally:
                _inflight.remove_claimed(claimed)
        return reaped

    def _record_interrupted(self, marker: Any) -> str:
        """Append the single terminal ``interrupted`` ledger line for an op whose
        process died before completing. A chain-safe APPEND via :meth:`record`,
        never a rewrite (T-067)."""
        rec = OperationRecord.from_dict(
            {
                "id": marker.op_id,
                "timestamp": marker.started_at,
                "command": marker.command,
                "operation_type": marker.operation_type,
                "host": marker.host,
                "working_dir": marker.working_dir,
                "status": OperationStatus.INTERRUPTED.value,
                "exit_code": -1,
                "error": "process exited without completing (reaped by navig ledger reap)",
                "tags": ["reaped", "interrupted"],
                "notes": (
                    f"reaped after the owning process (pid {marker.pid}) exited "
                    "before recording a terminal status"
                ),
            }
        )
        return self.record(rec)

    def _truncate_output(self, output: str, max_bytes: int = 10240) -> str:
        """Truncate output to prevent huge history files."""
        if not output:
            return output

        output_bytes = output.encode("utf-8", errors="replace")
        if len(output_bytes) <= max_bytes:
            return output

        truncated = output_bytes[:max_bytes].decode("utf-8", errors="replace")
        return f"{truncated}\n... [TRUNCATED - {len(output_bytes)} bytes total]"

    def _rotate(self):
        """Rotate history file, keeping most recent entries.

        Operates on RAW lines — never re-serializes — so chained entries keep
        their exact bytes and the hash chain survives rotation: the first
        retained entry's ``prev`` still names the hash of a rotated-out entry,
        which ``navig ledger verify`` reports as the chain anchor (wrinkle 1
        of plan-evidence-ledger.md). Called with ``_write_lock`` held (from
        ``record()``); must not re-acquire it.
        """
        try:
            # Read all raw lines (blank lines dropped, content untouched)
            with open(self.history_file, encoding="utf-8") as fh:
                lines = [line for line in fh if line.strip()]

            # Keep only recent entries
            keep_count = self.max_entries // 2  # Keep 50% on rotation
            recent_lines = lines[-keep_count:]

            # Write back
            backup_file = self.history_file.with_suffix(".jsonl.bak")
            self.history_file.rename(backup_file)

            _tmp_path: Path | None = None
            try:
                _fd, _tmp = tempfile.mkstemp(dir=self.history_file.parent, suffix=".tmp")
                _tmp_path = Path(_tmp)
                with os.fdopen(_fd, "w", encoding="utf-8") as _fh:
                    for line in recent_lines:
                        _fh.write(line if line.endswith("\n") else line + "\n")
                os.replace(_tmp_path, self.history_file)
                _tmp_path = None
            finally:
                if _tmp_path is not None:
                    _tmp_path.unlink(missing_ok=True)

            # Update index
            self._index_loaded = False
            self._load_index()

            # Remove backup
            backup_file.unlink()

        except OSError as e:
            from navig import console_helper as ch

            ch.warning(f"Could not rotate history: {e}")

    def get_operation(self, op_id: str) -> OperationRecord | None:
        """Get a specific operation by ID."""
        self._load_index()

        if op_id not in self._operations_by_id:
            return None

        line_num = self._operations_by_id[op_id]

        try:
            with open(self.history_file, encoding="utf-8") as f:
                for i, line in enumerate(f):
                    if i == line_num and line.strip():
                        return OperationRecord.from_dict(json.loads(line))
        except (OSError, json.JSONDecodeError):
            pass  # file unreadable or malformed; skip record

        return None

    def iter_operations(
        self,
        limit: int = 100,
        offset: int = 0,
        operation_type: OperationType | None = None,
        host: str | None = None,
        status: OperationStatus | None = None,
        since: str | None = None,
        search: str | None = None,
        reverse: bool = True,  # Most recent first
    ):
        """
        Iterate over operations with filtering.

        Args:
            limit: Maximum number of results
            offset: Number of results to skip
            operation_type: Filter by operation type
            host: Filter by host
            status: Filter by status
            since: Filter by timestamp (ISO format)
            search: Search in command text
            reverse: If True, most recent first

        Yields:
            OperationRecord objects matching filters
        """
        if not self.history_file.exists():
            return

        try:
            # Read all lines
            with open(self.history_file, encoding="utf-8") as f:
                lines = [line for line in f if line.strip()]

            if reverse:
                lines = reversed(lines)

            count = 0
            skipped = 0

            for line in lines:
                try:
                    data = json.loads(line)
                    record = OperationRecord.from_dict(data)

                    # Apply filters
                    if operation_type and record.operation_type != operation_type:
                        continue
                    if host and record.host != host:
                        continue
                    if status and record.status != status:
                        continue
                    if since and record.timestamp < since:
                        continue
                    if search and search.lower() not in record.command.lower():
                        continue

                    # Handle offset
                    if skipped < offset:
                        skipped += 1
                        continue

                    yield record
                    count += 1

                    if count >= limit:
                        break

                except json.JSONDecodeError:
                    continue  # malformed JSON; skip line

        except OSError:
            pass  # best-effort cleanup

    def get_last_n(self, n: int = 10) -> list[OperationRecord]:
        """Get the last N operations."""
        return list(self.iter_operations(limit=n, reverse=True))

    def get_by_command(self, command_pattern: str, limit: int = 10) -> list[OperationRecord]:
        """Find operations matching a command pattern."""
        return list(self.iter_operations(limit=limit, search=command_pattern))

    def export_json(self, output_file: Path, **filters) -> int:
        """
        Export operations to JSON file.

        Returns:
            Number of operations exported
        """
        operations = list(self.iter_operations(limit=_UNBOUNDED_SCAN_LIMIT, **filters))

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump([op.to_dict() for op in operations], f, indent=2)

        return len(operations)

    def export_csv(self, output_file: Path, **filters) -> int:
        """
        Export operations to CSV file.

        Returns:
            Number of operations exported
        """
        import csv

        operations = list(self.iter_operations(limit=_UNBOUNDED_SCAN_LIMIT, **filters))

        if not operations:
            return 0

        fieldnames = [
            "timestamp",
            "command",
            "operation_type",
            "host",
            "status",
            "duration_ms",
            "exit_code",
        ]

        with open(output_file, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for op in operations:
                writer.writerow(
                    {
                        "timestamp": op.timestamp,
                        "command": op.command,
                        "operation_type": op.operation_type.value,
                        "host": op.host or "",
                        "status": op.status.value,
                        "duration_ms": op.duration_ms,
                        "exit_code": op.exit_code,
                    }
                )

        return len(operations)

    def clear_history(self) -> int:
        """
        Clear all history.

        Legitimately ends the hash chain: the next recorded operation starts
        a fresh chain (``prev: null``), which ``navig ledger verify`` reports
        as a clean restart — never a failure (wrinkle 2 of
        plan-evidence-ledger.md).

        Returns:
            Number of operations cleared
        """
        self._load_index()
        count = len(self._operation_ids)

        if self.history_file.exists():
            self.history_file.unlink()

        self._operation_ids = []
        self._operations_by_id = {}

        return count

    def count(self, **filters) -> int:
        """Count operations matching filters."""
        return sum(1 for _ in self.iter_operations(limit=_UNBOUNDED_SCAN_LIMIT, **filters))


def claim_cli_operation(
    match: tuple[str, ...] = (),
) -> tuple["OperationRecord | None", float | None]:
    """Claim (and detach) the in-flight CLI middleware record for this invocation.

    The middleware (``navig/cli/middleware.py``) starts a coarse record for
    every CLI run and completes it at process exit. A command that knows
    better — precise operation type, captured ``undo_data``, exact
    success/error — claims the record, enriches it, and completes it itself:
    exactly ONE ledger line per invocation, carrying the richest data
    available (T-068).

    Args:
        match: claim only when one of these substrings appears in the
            record's command string. This guards LIBRARY calls: when e.g.
            ``set_config()`` runs inside another command's flow, the outer
            command keeps its own record and the caller falls back to a
            standalone entry.

    Returns:
        ``(record, start_time)`` — or ``(None, None)`` when there is no
        middleware record (library call, tests) or the command doesn't match.
    """
    try:
        import click

        ctx = click.get_current_context(silent=True)
    except Exception:  # noqa: BLE001 — recording plumbing must never raise
        return None, None
    while ctx is not None:
        obj = getattr(ctx, "obj", None)
        if isinstance(obj, dict):
            record = obj.get("_operation_record")
            if isinstance(record, OperationRecord):
                if match and not any(m in record.command for m in match):
                    return None, None
                obj.pop("_operation_record", None)
                return record, obj.get("_operation_start")
        ctx = getattr(ctx, "parent", None)
    return None, None


# Singleton instance
_recorder: OperationRecorder | None = None
_recorder_lock = threading.Lock()


def get_operation_recorder() -> OperationRecorder:
    """Get the global operation recorder instance."""
    global _recorder
    if _recorder is None:
        with _recorder_lock:
            if _recorder is None:
                _recorder = OperationRecorder()
    return _recorder


# ============================================================================
# OPERATION RECORDING CONTEXT MANAGER
# ============================================================================


class RecordedOperation:
    """
    Context manager for recording CLI operations.

    Usage:
        with RecordedOperation("navig host list", op_type=OperationType.LOCAL_COMMAND) as rec:
            # do work
            rec.output = "success output"
            rec.success = True

        # Or with auto-detected success:
        with RecordedOperation("navig db query", host="myhost") as rec:
            result = run_query()
            rec.output = result
            # success is auto-detected from exceptions
    """

    def __init__(
        self,
        command: str,
        op_type: OperationType = OperationType.OTHER,
        host: str | None = None,
        app: str | None = None,
        args: dict[str, Any] | None = None,
        reversible: bool = False,
        tags: list[str] | None = None,
        filepath: str | None = None,
        session_id: str | None = None,
    ):
        self.command = command
        self.op_type = op_type
        self.host = host
        self.app = app
        self.args = args
        self.reversible = reversible
        self.tags = tags
        # Optional: path of the file being written/modified — triggers file history checkpoint
        self.filepath = filepath
        self.session_id = session_id

        # Results (can be set during execution)
        self.success: bool | None = None
        self.output: str = ""
        self.error: str = ""
        self.exit_code: int = 0
        self.undo_data: dict[str, Any] | None = None

        # Timing
        self._start_time: float | None = None
        self._record: OperationRecord | None = None
        self._recorder = get_operation_recorder()

    def __enter__(self) -> "RecordedOperation":
        import time

        self._start_time = time.time()
        self._record = self._recorder.start_operation(
            command=self.command,
            operation_type=self.op_type,
            host=self.host,
            app=self.app,
            args=self.args,
            reversible=self.reversible,
            tags=self.tags,
        )

        # File history checkpoint — snapshot the file before any write/modify
        if self.filepath and self.op_type in (
            OperationType.FILE_MODIFY,
            OperationType.FILE_CREATE,
        ):
            try:
                from navig.file_history import get_file_history_store
                session_id = self.session_id or self._record.id
                backup = get_file_history_store().checkpoint(
                    self.filepath, session_id, turn_id=self._record.id
                )
                if backup and self.undo_data is None:
                    self.undo_data = {"file_history_backup": str(backup)}
            except Exception as _fh_exc:  # noqa: BLE001
                logger.debug("file_history checkpoint skipped: %s", _fh_exc)

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        import time

        duration_ms = (time.time() - self._start_time) * 1000 if self._start_time else 0

        # Auto-detect success from exceptions if not explicitly set
        if self.success is None:
            self.success = exc_type is None

        # Capture exception info
        if exc_type is not None and not self.error:
            self.error = str(exc_val) if exc_val else exc_type.__name__
            self.exit_code = 1

        # Post-state fingerprint (T-068): with a file-history backup captured
        # at __enter__, the sha256 of the file AFTER the operation gives
        # `navig undo` a cheap drift check — it refuses to restore a file
        # that changed again since this operation.
        if (
            self.success
            and self.filepath
            and isinstance(self.undo_data, dict)
            and "file_history_backup" in self.undo_data
        ):
            try:
                _p = Path(self.filepath)
                if _p.is_file():
                    self.undo_data.setdefault("path", str(_p))
                    self.undo_data.setdefault(
                        "after_sha256", hashlib.sha256(_p.read_bytes()).hexdigest()
                    )
            except OSError as _exc:
                logger.debug("undo_data post-hash skipped: %s", _exc)

        # Record the operation
        self._recorder.complete_operation(
            record=self._record,
            success=self.success,
            output=self.output,
            error=self.error,
            exit_code=self.exit_code,
            duration_ms=duration_ms,
            undo_data=self.undo_data,
        )

        # Don't suppress exceptions
        return False


def record_operation(
    command: str,
    op_type: OperationType = OperationType.OTHER,
    host: str | None = None,
    app: str | None = None,
    tags: list[str] | None = None,
):
    """
    Decorator for recording CLI command execution.

    Usage:
        @record_operation("navig host list", op_type=OperationType.LOCAL_COMMAND)
        def host_list():
            ...
    """
    from functools import wraps

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Build command string from function name and args
            full_command = command
            if kwargs:
                full_command += " " + " ".join(
                    f"--{k}={v}" for k, v in kwargs.items() if v is not None
                )

            with RecordedOperation(
                command=full_command,
                op_type=op_type,
                host=host,
                app=app,
                tags=tags,
            ) as rec:
                try:
                    result = func(*args, **kwargs)
                    rec.success = True
                    if isinstance(result, str):
                        rec.output = result
                    return result
                except Exception as e:
                    rec.success = False
                    rec.error = str(e)
                    raise

        return wrapper

    return decorator


def quick_record(
    command: str,
    host: str | None = None,
    success: bool = True,
    output: str = "",
    error: str = "",
    duration_ms: float = 0,
    op_type: OperationType = OperationType.OTHER,
):
    """
    Quick one-liner to record an operation after the fact.

    Usage:
        quick_record("navig host list", success=True, output="Listed 5 hosts")
    """
    recorder = get_operation_recorder()
    record = recorder.start_operation(
        command=command,
        operation_type=op_type,
        host=host,
    )
    return recorder.complete_operation(
        record=record,
        success=success,
        output=output,
        error=error,
        duration_ms=duration_ms,
    )
