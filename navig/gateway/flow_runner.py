"""
navig/gateway/flow_runner.py
─────────────────────────────
Isolated flow execution with failure-destination routing (Item 8).

Each flow run executes as an isolated async coroutine.  Completion output is
delivered to a configurable *success* destination; failures are routed to a
separate *failure_destination* so alerts never loop back into the triggering
session.

Run logs are appended to a per-flow file and pruned beyond
``flow.run_log_max_entries`` (config key, default 100).

Usage::

    runner = FlowRunner(
        name="daily-backup",
        run_fn=do_backup,
        success_destination=Destination(kind="session", id="chat_42"),
        failure_destination=Destination(kind="channel", id="alerts"),
        log_path=Path("~/.navig/flows/daily-backup.jsonl"),
        max_log_entries=100,
    )
    result = await runner.run()

Delivery to destinations is intentionally abstracted: a
``DeliveryBackend`` protocol defines one async method
``deliver(destination, message)``.  The caller supplies the backend; no
dependency on Telegram or any transport is taken at this layer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

_log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULT_MAX_LOG_ENTRIES: int = 100

# ──────────────────────────────────────────────────────────────────────────────
# Public types
# ──────────────────────────────────────────────────────────────────────────────


class DestinationKind(str, Enum):
    """Where to route a flow result message."""

    SESSION = "session"    # Telegram chat session
    WEBHOOK = "webhook"    # HTTP webhook URL
    CHANNEL = "channel"    # Named gateway channel


@dataclass(frozen=True)
class Destination:
    """A typed delivery target.

    Parameters
    ----------
    kind:
        One of ``session``, ``webhook``, or ``channel``.
    id:
        Identifier for the target (chat id, URL, or channel name).
    """

    kind: DestinationKind
    id: str

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Destination.id must not be empty")


class FlowRunStatus(str, Enum):
    """Terminal status of a single flow run."""

    SUCCESS = "success"
    FAILURE = "failure"


@dataclass
class FlowRunResult:
    """Result produced by :class:`FlowRunner` after a run completes.

    Attributes
    ----------
    flow_name:
        Logical name of the flow.
    run_id:
        Unique run identifier (epoch-based, string).
    status:
        ``SUCCESS`` or ``FAILURE``.
    output:
        The value returned by the run function (on success).
    error:
        Exception message (on failure).
    started_at:
        Unix timestamp at run start.
    finished_at:
        Unix timestamp at run finish.
    """

    flow_name: str
    run_id: str
    status: FlowRunStatus
    output: Any = None
    error: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0

    def as_dict(self) -> dict:
        return {
            "flow_name": self.flow_name,
            "run_id": self.run_id,
            "status": self.status.value,
            "output": str(self.output) if self.output is not None else None,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


# ──────────────────────────────────────────────────────────────────────────────
# Delivery backend protocol
# ──────────────────────────────────────────────────────────────────────────────


class DeliveryBackend(Protocol):
    """Protocol for routing flow result messages to a destination.

    Implement this interface to wire the runner into Telegram, a webhook,
    or any other transport.
    """

    async def deliver(self, destination: Destination, message: str) -> None:
        """Send *message* to *destination*."""
        ...  # pragma: no cover


# ──────────────────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────────────────


class FlowRunner:
    """Execute a single named flow in an isolated coroutine.

    Parameters
    ----------
    name:
        Human-readable flow identifier used in log entries.
    run_fn:
        Async callable that performs the actual work.  Must return a
        JSON-serialisable value (or ``None``).
    success_destination:
        Where to route the completion message on success.
    failure_destination:
        Where to route error alerts.  **Must differ from
        ``success_destination``** to prevent alert loops.
    backend:
        Optional :class:`DeliveryBackend`.  When ``None``, delivery is
        silently skipped (useful in tests or dry-run mode).
    log_path:
        Path to the per-flow JSONL run log.  Created on first write.
    max_log_entries:
        Entries beyond this limit are pruned from the log on each write.
    """

    def __init__(
        self,
        name: str,
        run_fn: Callable[[], Awaitable[Any]],
        success_destination: Destination,
        failure_destination: Destination,
        backend: DeliveryBackend | None = None,
        log_path: Path | None = None,
        max_log_entries: int = _DEFAULT_MAX_LOG_ENTRIES,
    ) -> None:
        if not name:
            raise ValueError("FlowRunner: name must not be empty")
        if max_log_entries < 1:
            raise ValueError(
                f"FlowRunner: max_log_entries must be ≥ 1, got {max_log_entries}"
            )
        self.name = name
        self._fn = run_fn
        self._success_dest = success_destination
        self._failure_dest = failure_destination
        self._backend = backend
        self._log_path = log_path
        self._max_log_entries = max_log_entries

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> FlowRunResult:
        """Execute the flow and route the result.

        Returns
        -------
        FlowRunResult
            Always returns (never raises).  Check ``result.status``.
        """
        run_id = _make_run_id(self.name)
        result = FlowRunResult(
            flow_name=self.name,
            run_id=run_id,
            status=FlowRunStatus.SUCCESS,
            started_at=time.time(),
        )
        try:
            result.output = await self._fn()
            result.status = FlowRunStatus.SUCCESS
        except Exception as exc:  # noqa: BLE001
            _log.exception("FlowRunner[%s] run %s failed", self.name, run_id)
            result.status = FlowRunStatus.FAILURE
            result.error = f"{type(exc).__name__}: {exc}"
        finally:
            result.finished_at = time.time()

        self._append_log(result)
        await self._deliver(result)
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _deliver(self, result: FlowRunResult) -> None:
        if self._backend is None:
            return
        dest = (
            self._success_dest
            if result.status == FlowRunStatus.SUCCESS
            else self._failure_dest
        )
        message = _format_message(result)
        try:
            await self._backend.deliver(dest, message)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "FlowRunner[%s]: delivery to %s/%s failed: %s",
                self.name,
                dest.kind,
                dest.id,
                exc,
            )

    def _append_log(self, result: FlowRunResult) -> None:
        if self._log_path is None:
            return
        try:
            log_path = Path(self._log_path).expanduser()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            # Read existing entries
            existing: list[dict] = []
            if log_path.exists():
                with log_path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            try:
                                existing.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass  # corrupt entry — skip
            # Append new; prune
            existing.append(result.as_dict())
            if len(existing) > self._max_log_entries:
                existing = existing[-self._max_log_entries:]
            # Atomic write (temp + rename)
            tmp = log_path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                for entry in existing:
                    fh.write(json.dumps(entry) + "\n")
            tmp.replace(log_path)
        except OSError as exc:
            _log.warning("FlowRunner[%s]: could not write log: %s", self.name, exc)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers (module-private)
# ──────────────────────────────────────────────────────────────────────────────


def _make_run_id(name: str) -> str:
    ts = int(time.time() * 1000)
    # Sanitise name for use in the ID
    safe = name.replace(" ", "-").lower()[:32]
    return f"{safe}-{ts}"


def _format_message(result: FlowRunResult) -> str:
    duration = result.finished_at - result.started_at
    if result.status == FlowRunStatus.SUCCESS:
        return (
            f"Flow '{result.flow_name}' completed successfully "
            f"in {duration:.1f}s (run {result.run_id})."
        )
    return (
        f"Flow '{result.flow_name}' FAILED after {duration:.1f}s "
        f"(run {result.run_id}): {result.error}"
    )
