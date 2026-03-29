"""
Cron Service - Persistent job scheduling

Features:
- Cron expression support
- Persistent job storage
- Natural language scheduling
- Job history and retry
"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from navig.debug_logger import get_debug_logger

if TYPE_CHECKING:
    from navig.gateway.server import NavigGateway

logger = get_debug_logger()


class JobStatus(Enum):
    """Job execution status."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    DISABLED = "disabled"


@dataclass
class CronConfig:
    """Cron service configuration."""

    enabled: bool = True
    max_concurrent_jobs: int = 5
    default_timeout_seconds: int = 300
    retry_failed: bool = True
    max_retries: int = 3

    @classmethod
    def from_dict(cls, data: dict) -> "CronConfig":
        return cls(
            enabled=data.get("enabled", True),
            max_concurrent_jobs=data.get("max_concurrent", 5),
            default_timeout_seconds=data.get("timeout", 300),
            retry_failed=data.get("retry_failed", True),
            max_retries=data.get("max_retries", 3),
        )


@dataclass
class CronJob:
    """A scheduled cron job."""

    id: str
    name: str
    schedule: str  # Cron expression or natural language
    command: str  # NAVIG command or AI prompt
    enabled: bool = True
    timeout_seconds: int = 300
    retry_count: int = 0
    max_retries: int = 3
    last_run: datetime | None = None
    next_run: datetime | None = None
    last_status: JobStatus | None = None
    last_output: str | None = None
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "schedule": self.schedule,
            "command": self.command,
            "enabled": self.enabled,
            "timeout_seconds": self.timeout_seconds,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "next_run": self.next_run.isoformat() if self.next_run else None,
            "last_status": self.last_status.value if self.last_status else None,
            "last_output": self.last_output,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CronJob":
        return cls(
            id=data["id"],
            name=data["name"],
            schedule=data["schedule"],
            command=data["command"],
            enabled=data.get("enabled", True),
            timeout_seconds=data.get("timeout_seconds", 300),
            retry_count=data.get("retry_count", 0),
            max_retries=data.get("max_retries", 3),
            last_run=(datetime.fromisoformat(data["last_run"]) if data.get("last_run") else None),
            next_run=(datetime.fromisoformat(data["next_run"]) if data.get("next_run") else None),
            last_status=(JobStatus(data["last_status"]) if data.get("last_status") else None),
            last_output=data.get("last_output"),
            created_at=(
                datetime.fromisoformat(data["created_at"])
                if data.get("created_at")
                else datetime.now()
            ),
        )


class CronParser:
    """
    Parses cron expressions and natural language schedules.

    Supports:
    - Standard cron: "*/5 * * * *" (every 5 minutes)
    - Natural language: "every 30 minutes", "daily at 9am"
    """

    # Natural language patterns
    PATTERNS = [
        (r"every (\d+) ?min(ute)?s?", lambda m: timedelta(minutes=int(m.group(1)))),
        (r"every (\d+) ?hours?", lambda m: timedelta(hours=int(m.group(1)))),
        (r"every (\d+) ?days?", lambda m: timedelta(days=int(m.group(1)))),
        (r"hourly", lambda m: timedelta(hours=1)),
        (r"daily", lambda m: timedelta(days=1)),
        (r"weekly", lambda m: timedelta(weeks=1)),
    ]

    @classmethod
    def parse(cls, schedule: str) -> timedelta | None:
        """
        Parse schedule string to interval.

        For simple interval-based scheduling, returns timedelta.
        For complex cron expressions, returns None (use calculate_next instead).
        """
        schedule_lower = schedule.lower().strip()

        # Try natural language patterns
        for pattern, handler in cls.PATTERNS:
            match = re.match(pattern, schedule_lower)
            if match:
                return handler(match)

        # Check for standard cron expression
        if cls._is_cron_expression(schedule):
            return None  # Use calculate_next for cron

        return None

    @classmethod
    def _is_cron_expression(cls, schedule: str) -> bool:
        """Check if schedule is a cron expression."""
        parts = schedule.strip().split()
        return len(parts) >= 5 and all(cls._is_cron_field(p) for p in parts[:5])

    @classmethod
    def _is_cron_field(cls, field: str) -> bool:
        """Check if string is a valid cron field."""
        # Allow *, numbers, ranges, lists, steps
        return bool(re.match(r"^[\d\*\-,/]+$", field))

    @classmethod
    def calculate_next(cls, schedule: str, from_time: datetime = None) -> datetime:
        """
        Calculate the next run time for a schedule.

        For interval-based schedules, adds interval to from_time.
        For cron expressions, calculates next matching time.
        """
        from_time = from_time or datetime.now()

        # Try simple interval
        interval = cls.parse(schedule)
        if interval:
            return from_time + interval

        # Try cron expression
        if cls._is_cron_expression(schedule):
            return cls._next_cron_time(schedule, from_time)

        # Default to 1 hour
        logger.warning(f"Could not parse schedule: {schedule}, defaulting to 1 hour")
        return from_time + timedelta(hours=1)

    @classmethod
    def _next_cron_time(cls, cron_expr: str, from_time: datetime) -> datetime:
        """
        Calculate next run time for cron expression.

        Simple implementation - for complex cron expressions,
        consider using croniter library.
        """
        parts = cron_expr.strip().split()
        if len(parts) < 5:
            return from_time + timedelta(hours=1)

        minute, hour, day, month, weekday = parts[:5]

        # Start from next minute
        candidate = from_time.replace(second=0, microsecond=0) + timedelta(minutes=1)

        # Simple implementation for common patterns
        max_iterations = 60 * 24 * 31  # Max ~1 month of minutes

        for _ in range(max_iterations):
            if cls._matches_cron(candidate, minute, hour, day, month, weekday):
                return candidate
            candidate += timedelta(minutes=1)

        # Fallback
        return from_time + timedelta(hours=1)

    @classmethod
    def _matches_cron(
        cls, dt: datetime, minute: str, hour: str, day: str, month: str, weekday: str
    ) -> bool:
        """Check if datetime matches cron fields."""
        return (
            cls._matches_field(dt.minute, minute, 0, 59)
            and cls._matches_field(dt.hour, hour, 0, 23)
            and cls._matches_field(dt.day, day, 1, 31)
            and cls._matches_field(dt.month, month, 1, 12)
            and cls._matches_field(dt.weekday(), weekday, 0, 6)
        )

    @classmethod
    def _matches_field(cls, value: int, field: str, min_val: int, max_val: int) -> bool:
        """Check if value matches cron field."""
        if field == "*":
            return True

        # Handle step (*/5)
        if field.startswith("*/"):
            step = int(field[2:])
            return value % step == 0

        # Handle range (1-5)
        if "-" in field:
            start, end = field.split("-")
            return int(start) <= value <= int(end)

        # Handle list (1,3,5)
        if "," in field:
            values = [int(v) for v in field.split(",")]
            return value in values

        # Exact match
        return value == int(field)


class CronService:
    """
    Persistent cron-like job scheduler.

    Features:
    - Add/remove/update jobs
    - Persistent storage
    - Automatic next-run calculation
    - Job execution via AI agent
    """

    def __init__(
        self,
        gateway: "NavigGateway",
        storage_path: Path,
        config: CronConfig | None = None,
    ):
        self.gateway = gateway
        self.storage_path = storage_path
        self.config = config or CronConfig()

        # Jobs indexed by ID
        self.jobs: dict[str, CronJob] = {}

        # Running state
        self._running = False
        self._task: asyncio.Task | None = None

        # Semaphore for concurrent job limit
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent_jobs)

        # Job counter for ID generation
        self._job_counter = 0

        # Load jobs
        self._load_jobs()

    def _get_jobs_path(self) -> Path:
        return self.storage_path / "cron_jobs.json"

    def _load_jobs(self) -> None:
        """Load jobs from disk."""
        jobs_path = self._get_jobs_path()

        if jobs_path.exists():
            try:
                data = json.loads(jobs_path.read_text())

                for job_data in data.get("jobs", []):
                    job = CronJob.from_dict(job_data)
                    self.jobs[job.id] = job

                self._job_counter = data.get("counter", 0)

                logger.info(f"Loaded {len(self.jobs)} cron jobs")

            except Exception as e:
                logger.error(f"Failed to load cron jobs: {e}")

    def _save_jobs(self) -> None:
        """Save jobs to disk."""
        self.storage_path.mkdir(parents=True, exist_ok=True)

        data = {
            "counter": self._job_counter,
            "jobs": [j.to_dict() for j in self.jobs.values()],
        }

        self._get_jobs_path().write_text(json.dumps(data, indent=2))

    def _generate_id(self) -> str:
        """Generate unique job ID."""
        self._job_counter += 1
        return f"job_{self._job_counter}"

    async def start(self) -> None:
        """Start the cron service."""
        if self._running:
            return

        if not self.config.enabled:
            logger.info("Cron service disabled in config")
            return

        self._running = True

        # Calculate next run times for all jobs
        for job in self.jobs.values():
            if job.enabled and not job.next_run:
                job.next_run = CronParser.calculate_next(job.schedule)

        self._save_jobs()

        # Start scheduler loop
        self._task = asyncio.create_task(self._scheduler_loop())

        logger.info(f"Cron service started with {len(self.jobs)} jobs")

    async def stop(self) -> None:
        """Stop the cron service."""
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass  # task cancelled; expected during shutdown

        self._save_jobs()
        logger.info("Cron service stopped")

    def add_job(
        self,
        name: str,
        schedule: str,
        command: str,
        enabled: bool = True,
        timeout_seconds: int = None,
    ) -> CronJob:
        """
        Add a new cron job.

        Args:
            name: Human-readable job name
            schedule: Cron expression or natural language
            command: NAVIG command or AI prompt
            enabled: Whether job is active
            timeout_seconds: Max execution time

        Returns:
            Created job
        """
        job = CronJob(
            id=self._generate_id(),
            name=name,
            schedule=schedule,
            command=command,
            enabled=enabled,
            timeout_seconds=timeout_seconds or self.config.default_timeout_seconds,
            next_run=CronParser.calculate_next(schedule) if enabled else None,
        )

        self.jobs[job.id] = job
        self._save_jobs()

        logger.info(f"Added cron job: {name} ({schedule})")

        return job

    def update_job(self, job_id: str, **kwargs) -> CronJob | None:
        """Update a job's properties."""
        if job_id not in self.jobs:
            return None

        job = self.jobs[job_id]

        for key, value in kwargs.items():
            if hasattr(job, key):
                setattr(job, key, value)

        # Recalculate next run if schedule changed
        if "schedule" in kwargs:
            job.next_run = CronParser.calculate_next(job.schedule)

        self._save_jobs()
        return job

    def remove_job(self, job_id: str) -> bool:
        """Remove a job."""
        if job_id not in self.jobs:
            return False

        del self.jobs[job_id]
        self._save_jobs()

        logger.info(f"Removed cron job: {job_id}")
        return True

    def enable_job(self, job_id: str) -> bool:
        """Enable a job."""
        if job_id not in self.jobs:
            return False

        job = self.jobs[job_id]
        job.enabled = True
        job.next_run = CronParser.calculate_next(job.schedule)

        self._save_jobs()
        return True

    def disable_job(self, job_id: str) -> bool:
        """Disable a job."""
        if job_id not in self.jobs:
            return False

        job = self.jobs[job_id]
        job.enabled = False
        job.next_run = None

        self._save_jobs()
        return True

    def list_jobs(self) -> list[CronJob]:
        """List all jobs."""
        return list(self.jobs.values())

    def get_job(self, job_id: str) -> CronJob | None:
        """Get a specific job."""
        return self.jobs.get(job_id)

    async def _scheduler_loop(self) -> None:
        """Main scheduler loop."""
        while self._running:
            try:
                now = datetime.now()

                # Find jobs due to run
                due_jobs = [
                    job
                    for job in self.jobs.values()
                    if job.enabled and job.next_run and job.next_run <= now
                ]

                # Run due jobs (with concurrency limit)
                tasks = []
                for job in due_jobs:
                    task = asyncio.create_task(self._run_job(job))
                    tasks.append(task)

                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

                # Sleep until next check
                await asyncio.sleep(10)  # Check every 10 seconds

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scheduler loop error: {e}")
                await asyncio.sleep(30)

    async def _run_job(self, job: CronJob) -> None:
        """Execute a cron job."""
        async with self._semaphore:
            logger.info(f"Running cron job: {job.name}")

            start_time = datetime.now()
            job.last_run = start_time
            job.last_status = JobStatus.RUNNING

            try:
                # Run the command
                output = await asyncio.wait_for(
                    self._execute_job_command(job), timeout=job.timeout_seconds
                )

                job.last_status = JobStatus.SUCCESS
                job.last_output = output[:5000]  # Limit output size
                job.retry_count = 0

                logger.info(f"Cron job completed: {job.name}")

                # Emit success event
                if self.gateway.event_queue:
                    from navig.gateway.system_events import EventTypes

                    await self.gateway.event_queue.emit(
                        EventTypes.CRON_JOB_COMPLETE,
                        {
                            "job_id": job.id,
                            "job_name": job.name,
                            "duration": (datetime.now() - start_time).total_seconds(),
                        },
                    )

            except asyncio.TimeoutError:
                job.last_status = JobStatus.FAILED
                job.last_output = "Job timed out"
                job.retry_count += 1

                logger.error(f"Cron job timed out: {job.name}")

            except Exception as e:
                job.last_status = JobStatus.FAILED
                job.last_output = str(e)
                job.retry_count += 1

                logger.error(f"Cron job failed: {job.name} - {e}")

                # Emit failure event
                if self.gateway.event_queue:
                    from navig.gateway.system_events import EventTypes

                    await self.gateway.event_queue.emit(
                        EventTypes.CRON_JOB_FAILED,
                        {
                            "job_id": job.id,
                            "job_name": job.name,
                            "error": str(e),
                        },
                    )

            # Calculate next run
            job.next_run = CronParser.calculate_next(job.schedule)

            # Handle retries
            if (
                job.last_status == JobStatus.FAILED
                and self.config.retry_failed
                and job.retry_count < job.max_retries
            ):
                # Schedule retry sooner
                job.next_run = datetime.now() + timedelta(minutes=5)
                logger.info(f"Job {job.name} will retry in 5 minutes")

            self._save_jobs()

    async def _execute_job_command(self, job: CronJob) -> str:
        """Execute the job's command."""
        command = job.command.strip()

        # Check if it's a direct NAVIG command
        if command.startswith("navig "):
            import shlex

            process = await asyncio.create_subprocess_exec(
                *shlex.split(command),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            
            stdout_bytes, stderr_bytes = await process.communicate()
            
            output = stdout_bytes.decode()
            if stderr_bytes:
                output += f"\n{stderr_bytes.decode()}"

            if process.returncode != 0:
                raise RuntimeError(f"Command failed with exit code {process.returncode}")

            return output

        # Otherwise, treat as AI prompt
        response = await self.gateway.run_agent_turn(
            agent_id="cron",
            session_key=f"cron:{job.id}",
            message=command,
        )

        return response

    async def run_job_now(self, job_id: str) -> str | None:
        """Manually trigger a job."""
        job = self.jobs.get(job_id)
        if not job:
            return None

        logger.info(f"Manual trigger: {job.name}")
        await self._run_job(job)

        return job.last_output

    def get_status(self) -> dict[str, Any]:
        """Get cron service status."""
        now = datetime.now()

        next_job = None
        next_run_in = None

        for job in self.jobs.values():
            if job.enabled and job.next_run:
                if next_job is None or job.next_run < next_job.next_run:
                    next_job = job

        if next_job:
            delta = next_job.next_run - now
            minutes = int(delta.total_seconds() / 60)
            next_run_in = f"{minutes}m" if minutes > 0 else "now"

        return {
            "running": self._running,
            "enabled": self.config.enabled,
            "total_jobs": len(self.jobs),
            "enabled_jobs": sum(1 for j in self.jobs.values() if j.enabled),
            "next_job": next_job.name if next_job else None,
            "next_run_in": next_run_in,
        }
