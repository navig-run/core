"""
navig.agent.background_task — Background task manager for the agent.

Allows the agent to spawn long-running processes (test suites, build watchers,
servers) in the background and continue the conversation.  Output is captured to
disk files under ``~/.navig/bg_tasks/``.

FB-04 implementation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

MAX_CONCURRENT = 10
OUTPUT_DIR = Path.home() / ".navig" / "bg_tasks"


# ─────────────────────────────────────────────────────────────
# BackgroundTask dataclass
# ─────────────────────────────────────────────────────────────


@dataclass
class BackgroundTask:
    """Represents a single background task."""

    task_id: int
    label: str
    command: str
    pid: int | None = None
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    exit_code: int | None = None
    output_file: str = ""

    @property
    def is_running(self) -> bool:
        """True if the task has not yet completed."""
        return self.exit_code is None

    @property
    def duration(self) -> float:
        """Elapsed wall-clock seconds since start."""
        end = self.completed_at or time.time()
        return end - self.started_at


# ─────────────────────────────────────────────────────────────
# BackgroundTaskManager
# ─────────────────────────────────────────────────────────────


class BackgroundTaskManager:
    """Manages background tasks for the agent.

    Tasks are subprocess shells whose stdout/stderr are captured to disk.
    The manager tracks up to ``MAX_CONCURRENT`` running tasks and exposes
    start / status / output / kill / list / cleanup operations.
    """

    def __init__(self, output_dir: Path | None = None) -> None:
        self._tasks: dict[int, BackgroundTask] = {}
        self._processes: dict[int, asyncio.subprocess.Process] = {}
        self._output_handles: dict[int, object] = {}  # open file handles
        self._monitor_futures: dict[int, asyncio.Task] = {}  # monitor coroutines
        self._next_id: int = 1
        self._output_dir: Path = output_dir or OUTPUT_DIR

    # ── public API ──────────────────────────────────────────

    async def start(
        self,
        command: str,
        label: str = "",
        cwd: str | None = None,
    ) -> BackgroundTask:
        """Start a background task.

        Returns immediately after spawning the subprocess.

        Raises
        ------
        RuntimeError
            If ``MAX_CONCURRENT`` running tasks would be exceeded.
        ValueError
            If *command* is empty.
        """
        if not command or not command.strip():
            raise ValueError("command must not be empty")

        running = self._running_tasks
        if len(running) >= MAX_CONCURRENT:
            raise RuntimeError(
                f"Max {MAX_CONCURRENT} concurrent background tasks reached "
                f"({len(running)} running)"
            )

        task = BackgroundTask(
            task_id=self._next_id,
            label=label or command[:40],
            command=command,
        )
        self._next_id += 1

        # Prepare output directory and file
        self._output_dir.mkdir(parents=True, exist_ok=True)
        task.output_file = str(self._output_dir / f"task_{task.task_id}.log")

        # Open output file handle
        output_fh = open(  # noqa: SIM115
            task.output_file,
            "w",
            encoding="utf-8",
            errors="replace",
        )

        # Determine shell
        shell_cmd: str | list[str] = command
        if os.name == "nt":
            # On Windows use cmd.exe (asyncio.create_subprocess_shell default)
            pass

        # Start subprocess
        work_dir = cwd or "."
        proc = await asyncio.create_subprocess_shell(
            shell_cmd,
            stdout=output_fh,
            stderr=asyncio.subprocess.STDOUT,
            cwd=work_dir,
        )
        task.pid = proc.pid

        # Store references
        self._tasks[task.task_id] = task
        self._processes[task.task_id] = proc
        self._output_handles[task.task_id] = output_fh

        # Monitor completion in background
        fut = asyncio.ensure_future(self._monitor(task, proc, output_fh))
        self._monitor_futures[task.task_id] = fut

        logger.debug(
            "Background task #%d started: %s (pid %s)",
            task.task_id,
            task.label,
            task.pid,
        )
        return task

    def status(self, task_id: int) -> dict:
        """Get status dict for a single task.

        Returns a dict with ``error`` key if the task is not found.
        """
        task = self._tasks.get(task_id)
        if not task:
            return {"error": f"No task with id {task_id}"}

        output_lines = self._count_output_lines(task)

        return {
            "task_id": task.task_id,
            "label": task.label,
            "command": task.command,
            "status": "running" if task.is_running else "completed",
            "duration": f"{task.duration:.1f}s",
            "exit_code": task.exit_code,
            "output_lines": output_lines,
            "pid": task.pid,
        }

    def get_output(self, task_id: int, tail: int = 50) -> str:
        """Return the last *tail* lines of output for a task.

        Returns a descriptive string if the task is not found or has no output.
        """
        task = self._tasks.get(task_id)
        if not task:
            return f"No task with id {task_id}"

        output_path = Path(task.output_file)
        if not output_path.exists():
            return "(no output yet)"

        try:
            text = output_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"(error reading output: {exc})"

        lines = text.splitlines()
        if not lines:
            return "(empty output)"

        tail = max(1, tail)
        selected = lines[-tail:]
        return "\n".join(selected)

    async def kill(self, task_id: int) -> bool:
        """Kill a running background task.

        Returns True if the process was successfully terminated,
        False if the task was not found or already completed.
        """
        task = self._tasks.get(task_id)
        if not task:
            return False

        if not task.is_running:
            return False

        proc = self._processes.get(task_id)
        if not proc:
            return False

        try:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
                await asyncio.wait_for(proc.wait(), timeout=5.0)
        except (ProcessLookupError, OSError) as exc:
            logger.debug("Error killing task #%d: %s", task_id, exc)
            # Mark completed anyway
            task.exit_code = -1
            task.completed_at = time.time()
            return True

        return True

    def list_tasks(self) -> list[dict]:
        """Return status dicts for all tracked tasks (running + completed)."""
        return [self.status(tid) for tid in sorted(self._tasks)]

    def cleanup(self, max_age: float = 3600.0) -> int:
        """Remove completed task records older than *max_age* seconds.

        Also deletes the corresponding output log files.
        Returns the number of tasks removed.
        """
        now = time.time()
        to_remove: list[int] = []

        for tid, task in self._tasks.items():
            if (
                not task.is_running
                and task.completed_at is not None
                and (now - task.completed_at) > max_age
            ):
                to_remove.append(tid)

        for tid in to_remove:
            task = self._tasks.pop(tid)
            self._processes.pop(tid, None)
            self._output_handles.pop(tid, None)
            try:
                Path(task.output_file).unlink(missing_ok=True)
            except OSError as exc:
                logger.debug("cleanup: failed to remove %s: %s", task.output_file, exc)

        if to_remove:
            logger.debug("Cleaned up %d background task(s)", len(to_remove))

        return len(to_remove)

    async def shutdown(self) -> None:
        """Terminate all running tasks and wait for monitors to finish.

        This ensures all file handles are closed and asyncio tasks are
        collected — important for clean teardown on Windows.
        """
        # Kill all running processes
        for tid, task in list(self._tasks.items()):
            if task.is_running:
                proc = self._processes.get(tid)
                if proc:
                    try:
                        proc.terminate()
                    except (ProcessLookupError, OSError):
                        pass

        # Wait for all monitors to finish (they close file handles)
        pending = [
            fut for fut in self._monitor_futures.values()
            if not fut.done()
        ]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._monitor_futures.clear()

    # ── internal ────────────────────────────────────────────

    async def _monitor(
        self,
        task: BackgroundTask,
        proc: asyncio.subprocess.Process,
        output_fh: object,
    ) -> None:
        """Wait for a subprocess to finish and record its exit status."""
        try:
            exit_code = await proc.wait()
            task.exit_code = exit_code
            task.completed_at = time.time()
            logger.debug(
                "Background task #%d completed: exit_code=%s duration=%.1fs",
                task.task_id,
                exit_code,
                task.duration,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Monitor error for task #%d: %s", task.task_id, exc)
            task.exit_code = -1
            task.completed_at = time.time()
        finally:
            try:
                output_fh.close()  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                pass
            self._output_handles.pop(task.task_id, None)

    @property
    def _running_tasks(self) -> list[BackgroundTask]:
        """Return list of currently running tasks."""
        return [t for t in self._tasks.values() if t.is_running]

    def _count_output_lines(self, task: BackgroundTask) -> int:
        """Count lines in the task's output file."""
        output_path = Path(task.output_file)
        if not output_path.exists():
            return 0
        try:
            text = output_path.read_text(encoding="utf-8", errors="replace")
            return len(text.splitlines())
        except OSError:
            return 0


# ─────────────────────────────────────────────────────────────
# Module-level singleton (lazy)
# ─────────────────────────────────────────────────────────────

_manager: BackgroundTaskManager | None = None


def get_manager(output_dir: Path | None = None) -> BackgroundTaskManager:
    """Return the module-level BackgroundTaskManager singleton.

    Creates the instance on first call. If *output_dir* is supplied on the
    first call it overrides the default ``OUTPUT_DIR``.
    """
    global _manager
    if _manager is None:
        _manager = BackgroundTaskManager(output_dir=output_dir)
    return _manager


def reset_manager() -> None:
    """Reset the singleton — used by tests."""
    global _manager
    _manager = None
