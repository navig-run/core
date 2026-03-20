"""
Operation Recorder for NAVIG - Command Replay & Time Travel

Records all CLI operations with full context for:
- Replaying successful commands
- Undoing reversible operations
- Auditing command history
- Exporting operation logs

Storage: ~/.navig/history/operations.jsonl (JSON Lines format)
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class OperationType(str, Enum):
    """Categories of operations for filtering and undo logic."""
    FILE_CREATE = "file_create"
    FILE_DELETE = "file_delete"
    FILE_MODIFY = "file_modify"
    FILE_UPLOAD = "file_upload"
    FILE_DOWNLOAD = "file_download"
    REMOTE_COMMAND = "remote_command"
    LOCAL_COMMAND = "local_command"
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
    host: Optional[str] = None
    app: Optional[str] = None
    working_dir: str = ""

    # Result
    status: OperationStatus = OperationStatus.PENDING
    output: str = ""
    error: str = ""
    exit_code: int = 0

    # For replay
    args: Dict[str, Any] = field(default_factory=dict)
    env_vars: Dict[str, str] = field(default_factory=dict)

    # For undo
    reversible: bool = False
    undo_data: Dict[str, Any] = field(default_factory=dict)

    # Metadata
    tags: List[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        d = asdict(self)
        d['operation_type'] = self.operation_type.value
        d['status'] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OperationRecord':
        """Create from dictionary."""
        data['operation_type'] = OperationType(data.get('operation_type', 'other'))
        data['status'] = OperationStatus(data.get('status', 'pending'))
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
        history_dir: Optional[Path] = None,
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

        # Ensure directory exists
        self.history_dir.mkdir(parents=True, exist_ok=True)

        # In-memory index for fast queries
        self._index_loaded = False
        self._operation_ids: List[str] = []
        self._operations_by_id: Dict[str, int] = {}  # id -> line number

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
            with open(self.history_file, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f):
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        op_id = data.get('id')
                        if op_id:
                            self._operation_ids.append(op_id)
                            self._operations_by_id[op_id] = line_num
                    except json.JSONDecodeError:
                        continue
        except (IOError, OSError):
            pass

        self._index_loaded = True

    def record(self, record: OperationRecord) -> str:
        """
        Record an operation.
        
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

        # Write to file
        try:
            with open(self.history_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record.to_dict()) + '\n')

            # Update index
            self._load_index()
            self._operation_ids.append(record.id)
            self._operations_by_id[record.id] = len(self._operation_ids) - 1

            # Check for rotation
            if len(self._operation_ids) > self.max_entries:
                self._rotate()

        except (IOError, OSError) as e:
            from navig import console_helper as ch
            ch.dim(f"Could not record operation: {e}")

        return record.id

    def start_operation(
        self,
        command: str,
        operation_type: OperationType = OperationType.OTHER,
        host: Optional[str] = None,
        app: Optional[str] = None,
        args: Optional[Dict[str, Any]] = None,
        reversible: bool = False,
        tags: Optional[List[str]] = None,
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
        undo_data: Optional[Dict[str, Any]] = None,
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
            
        Returns:
            The operation ID
        """
        record.status = OperationStatus.SUCCESS if success else OperationStatus.FAILED
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
                details={"command": record.command, "exit_code": exit_code, "error": error[:500] if error else ""},
                channel="cli",
                host=record.host,
                status="success" if success else "failed",
                duration_ms=int(duration_ms) if duration_ms else None,
            )
        except Exception:
            pass  # Never let audit failure block operation recording

        return self.record(record)

    def _truncate_output(self, output: str, max_bytes: int = 10240) -> str:
        """Truncate output to prevent huge history files."""
        if not output:
            return output

        output_bytes = output.encode('utf-8', errors='replace')
        if len(output_bytes) <= max_bytes:
            return output

        truncated = output_bytes[:max_bytes].decode('utf-8', errors='replace')
        return f"{truncated}\n... [TRUNCATED - {len(output_bytes)} bytes total]"

    def _rotate(self):
        """Rotate history file, keeping most recent entries."""
        try:
            # Read all entries
            entries = list(self.iter_operations())

            # Keep only recent entries
            keep_count = self.max_entries // 2  # Keep 50% on rotation
            recent_entries = entries[-keep_count:]

            # Write back
            backup_file = self.history_file.with_suffix('.jsonl.bak')
            self.history_file.rename(backup_file)

            with open(self.history_file, 'w', encoding='utf-8') as f:
                for entry in recent_entries:
                    f.write(json.dumps(entry.to_dict()) + '\n')

            # Update index
            self._index_loaded = False
            self._load_index()

            # Remove backup
            backup_file.unlink()

        except (IOError, OSError) as e:
            from navig import console_helper as ch
            ch.warning(f"Could not rotate history: {e}")

    def get_operation(self, op_id: str) -> Optional[OperationRecord]:
        """Get a specific operation by ID."""
        self._load_index()

        if op_id not in self._operations_by_id:
            return None

        line_num = self._operations_by_id[op_id]

        try:
            with open(self.history_file, 'r', encoding='utf-8') as f:
                for i, line in enumerate(f):
                    if i == line_num and line.strip():
                        return OperationRecord.from_dict(json.loads(line))
        except (IOError, OSError, json.JSONDecodeError):
            pass

        return None

    def iter_operations(
        self,
        limit: int = 100,
        offset: int = 0,
        operation_type: Optional[OperationType] = None,
        host: Optional[str] = None,
        status: Optional[OperationStatus] = None,
        since: Optional[str] = None,
        search: Optional[str] = None,
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
            with open(self.history_file, 'r', encoding='utf-8') as f:
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
                    continue

        except (IOError, OSError):
            pass

    def get_last_n(self, n: int = 10) -> List[OperationRecord]:
        """Get the last N operations."""
        return list(self.iter_operations(limit=n, reverse=True))

    def get_by_command(self, command_pattern: str, limit: int = 10) -> List[OperationRecord]:
        """Find operations matching a command pattern."""
        return list(self.iter_operations(limit=limit, search=command_pattern))

    def export_json(self, output_file: Path, **filters) -> int:
        """
        Export operations to JSON file.
        
        Returns:
            Number of operations exported
        """
        operations = list(self.iter_operations(limit=100000, **filters))

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump([op.to_dict() for op in operations], f, indent=2)

        return len(operations)

    def export_csv(self, output_file: Path, **filters) -> int:
        """
        Export operations to CSV file.
        
        Returns:
            Number of operations exported
        """
        import csv

        operations = list(self.iter_operations(limit=100000, **filters))

        if not operations:
            return 0

        fieldnames = ['timestamp', 'command', 'operation_type', 'host', 'status', 'duration_ms', 'exit_code']

        with open(output_file, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for op in operations:
                writer.writerow({
                    'timestamp': op.timestamp,
                    'command': op.command,
                    'operation_type': op.operation_type.value,
                    'host': op.host or '',
                    'status': op.status.value,
                    'duration_ms': op.duration_ms,
                    'exit_code': op.exit_code,
                })

        return len(operations)

    def clear_history(self) -> int:
        """
        Clear all history.
        
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
        return sum(1 for _ in self.iter_operations(limit=100000, **filters))


# Singleton instance
_recorder: Optional[OperationRecorder] = None


def get_operation_recorder() -> OperationRecorder:
    """Get the global operation recorder instance."""
    global _recorder
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
        host: Optional[str] = None,
        app: Optional[str] = None,
        args: Optional[Dict[str, Any]] = None,
        reversible: bool = False,
        tags: Optional[List[str]] = None,
    ):
        self.command = command
        self.op_type = op_type
        self.host = host
        self.app = app
        self.args = args
        self.reversible = reversible
        self.tags = tags

        # Results (can be set during execution)
        self.success: Optional[bool] = None
        self.output: str = ""
        self.error: str = ""
        self.exit_code: int = 0
        self.undo_data: Optional[Dict[str, Any]] = None

        # Timing
        self._start_time: Optional[float] = None
        self._record: Optional[OperationRecord] = None
        self._recorder = get_operation_recorder()

    def __enter__(self) -> 'RecordedOperation':
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
    host: Optional[str] = None,
    app: Optional[str] = None,
    tags: Optional[List[str]] = None,
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
                full_command += " " + " ".join(f"--{k}={v}" for k, v in kwargs.items() if v is not None)

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
    host: Optional[str] = None,
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
