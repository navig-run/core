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
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from navig.core.json_io import JsonReadError, atomic_write_json, load_json_for_update
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

    # Field bounds for a 5-field cron expression, in order. Day-of-week allows 7
    # (== Sunday == 0), so its max is 7, not 6.
    _CRON_FIELDS = (
        (0, 59, "minute"),
        (0, 23, "hour"),
        (1, 31, "day-of-month"),
        (1, 12, "month"),
        (0, 7, "day-of-week"),
    )

    @classmethod
    def is_valid(cls, schedule: str) -> bool:
        """True when *schedule* is something the scheduler will genuinely honour.

        Thin bool wrapper over :meth:`validate` — call ``validate`` directly when
        you want the human reason for a rejection.
        """
        return cls.validate(schedule)[0]

    @classmethod
    def validate(cls, schedule: str) -> tuple[bool, str | None]:
        """Whether *schedule* will actually fire when the user means — with a reason.

        Returns ``(ok, reason)``. A natural-language interval (``every 5 minutes``
        / ``daily``) or a 5-field cron with EVERY field in range → ``(True, None)``.
        Anything ``calculate_next`` would silently default to +1h — gibberish, too
        few fields, or a syntactically-plausible-but-dead field like ``61 * * * *``
        or ``*/0`` that parses yet never matches — → ``(False, <reason>)`` so the
        add/update surfaces can reject it instead of storing a job that silently
        never runs. ``is_valid`` only checked the character set, so out-of-range
        values slipped through and produced exactly such dead jobs.
        """
        if not schedule or not schedule.strip():
            return False, "schedule is empty"
        if cls.parse(schedule) is not None:
            return True, None  # natural-language interval
        parts = schedule.strip().split()
        if len(parts) < 5 or not all(cls._is_cron_field(p) for p in parts[:5]):
            return False, (
                f"unrecognized schedule {schedule!r} — use a 5-field cron expression "
                "(e.g. '0 9 * * 1-5') or an interval like 'every 30 minutes' / 'daily'"
            )
        for value, (lo, hi, label) in zip(parts[:5], cls._CRON_FIELDS):
            err = cls._field_error(value, lo, hi, label)
            if err:
                return False, err
        return True, None

    @classmethod
    def _field_error(cls, field: str, min_val: int, max_val: int, label: str) -> str | None:
        """A human reason *field* is invalid for ``[min_val, max_val]``, else None.

        Validates the same grammar :meth:`_matches_field` parses (``*`` · ``*/s`` ·
        ``a-b`` · ``a-b/s`` · ``n/s`` · ``n`` and comma-lists): numeric bounds plus
        ``step > 0``. Catches at add-time the fields that would otherwise parse but
        silently never match (``61``, ``*/0``, ``1-99``).
        """
        if field == "*":
            return None
        for part in field.split(","):
            part = part.strip()
            if not part:
                return f"{label} field {field!r} has an empty item"
            base, sep, step_s = part.partition("/")
            if sep == "/":
                try:
                    if int(step_s) <= 0:
                        return f"{label} field {field!r}: step must be a positive number"
                except ValueError:
                    return f"{label} field {field!r}: step {step_s!r} is not a number"
            if base == "*":
                continue
            endpoints = base.split("-")
            if len(endpoints) > 2:
                return f"{label} field {field!r} has a malformed range"
            for n in endpoints:
                try:
                    v = int(n.strip())
                except ValueError:
                    return f"{label} field {field!r}: {n.strip()!r} is not a number"
                if not (min_val <= v <= max_val):
                    return f"{label} value {v} is out of range {min_val}–{max_val}"
        return None

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
        logger.warning("Could not parse schedule: %s, defaulting to 1 hour", schedule)
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

        # Scan up to ~1 year of minutes so sparse schedules resolve (monthly-on-31st,
        # yearly). The loop exits on the FIRST match, so common schedules stay cheap;
        # only a genuinely unsatisfiable expression (e.g. Feb 30) pays the full scan.
        max_iterations = 60 * 24 * 366

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
        """Check if a datetime matches the five cron fields.

        Day-of-month and day-of-week follow the standard (Vixie) cron rule: when
        BOTH are restricted (neither is ``*``) the day matches if EITHER matches
        (an OR); otherwise it is the usual AND.
        """
        if not (
            cls._matches_field(dt.minute, minute, 0, 59)
            and cls._matches_field(dt.hour, hour, 0, 23)
            and cls._matches_field(dt.month, month, 1, 12)
        ):
            return False
        day_ok = cls._matches_field(dt.day, day, 1, 31)
        dow_ok = cls._matches_weekday(dt.weekday(), weekday)
        if day != "*" and weekday != "*":
            return day_ok or dow_ok
        return day_ok and dow_ok

    @classmethod
    def _matches_field(cls, value: int, field: str, min_val: int, max_val: int) -> bool:
        """Whether *value* satisfies a single cron field (minute/hour/day/month).

        Handles every composable form and any comma-separated list of them:
        ``*`` · ``*/step`` · ``a-b`` · ``a-b/step`` · ``n/step`` · ``n`` — e.g.
        ``9-17/2`` (9,11,13,15,17) or ``1,10-15`` (the 1st plus the 10th–15th).
        Steps count from the sub-range's start (``min_val`` for ``*``), per Vixie
        cron. A malformed sub-part is skipped, never raised: ``9-17/2`` used to
        reach ``int("17/2")`` and crash next-run calculation for a valid schedule.
        """
        if field == "*":
            return True
        for part in field.split(","):
            part = part.strip()
            if part and cls._matches_field_part(value, part, min_val, max_val):
                return True
        return False

    @classmethod
    def _matches_field_part(cls, value: int, part: str, min_val: int, max_val: int) -> bool:
        """Match one non-list cron sub-part: ``*`` · ``*/s`` · ``a-b`` · ``a-b/s`` · ``n/s`` · ``n``."""
        try:
            base, sep, step_s = part.partition("/")
            has_step = sep == "/"
            step = int(step_s) if has_step else 1
            if step <= 0:
                return False

            if base == "*":
                lo, hi = min_val, max_val
            elif "-" in base:
                lo_s, hi_s = base.split("-", 1)
                lo, hi = int(lo_s), int(hi_s)
            elif has_step:
                lo, hi = int(base), max_val  # "n/step": from n up to the field max
            else:
                return value == int(base)  # bare exact value, no step

            return lo <= value <= hi and (value - lo) % step == 0
        except ValueError:
            return False

    @classmethod
    def _matches_weekday(cls, py_weekday: int, field: str) -> bool:
        """Match a cron day-of-week FIELD against Python's ``datetime.weekday()``.

        Cron DOW is **Sun=0 … Sat=6** (and ``7`` also means Sunday); Python's
        ``weekday()`` is **Mon=0 … Sun=6**. Without converting, every day-of-week
        schedule fires a day late (``* * * * 1`` matched Tuesday, ``… 0`` matched
        Monday). This converts to cron numbering and honours ``7`` as Sunday.
        """
        if field == "*":
            return True
        cron_dow = (py_weekday + 1) % 7  # Mon0->1 … Sat5->6 … Sun6->0
        for part in field.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                if part.startswith("*/"):
                    step = int(part[2:])
                    if step > 0 and cron_dow % step == 0:
                        return True
                elif "-" in part:
                    range_s, sep, step_s = part.partition("/")
                    step = int(step_s) if sep == "/" else 1
                    if step <= 0:
                        continue
                    a_s, b_s = range_s.split("-", 1)
                    a, b = int(a_s), int(b_s)
                    a = 0 if a == 7 else a  # 7 == Sunday == 0
                    b = 0 if b == 7 else b
                    if a <= b:
                        if a <= cron_dow <= b and (cron_dow - a) % step == 0:
                            return True  # e.g. 1-5/2 = Mon,Wed,Fri
                    elif (cron_dow >= a or cron_dow <= b) and ((cron_dow - a) % 7) % step == 0:
                        return True  # wrap-around range (Fri-Mon); step counts across the wrap
                elif cron_dow == int(part) % 7:  # 7 -> 0 (Sunday)
                    return True
            except ValueError:
                continue
        return False


# The gateway-attached ("live") scheduler instance for this process, if any.
# In-process consumers must mutate THIS instance — direct file writes would be
# clobbered by its next save. Registered by CronService.__init__.
_LIVE_SERVICE: "CronService | None" = None


def _register_live_service(svc: "CronService") -> None:
    global _LIVE_SERVICE
    _LIVE_SERVICE = svc


def get_live_service() -> "CronService | None":
    """The running gateway's CronService in this process, or None."""
    return _LIVE_SERVICE


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

        # mtime of cron_jobs.json as of our last read/write. Lets the running
        # daemon notice EXTERNAL edits — the `navig … schedule` CLI writes the file
        # directly (separate process, can't reach this in-memory service) — and
        # resync instead of ignoring them until restart or clobbering them on the
        # next save.
        self._jobs_file_mtime: float | None = None

        # Running state
        self._running = False
        self._task: asyncio.Task | None = None

        # Semaphore for concurrent job limit
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent_jobs)

        # Job counter for ID generation
        self._job_counter = 0

        if gateway is not None:
            # The gateway-attached instance is THE live scheduler: adopt any jobs
            # stranded in the legacy daemon/ store (see habit_store) before
            # loading, and register so in-process consumers (Telegram commands,
            # deck routes) mutate this instance instead of racing the file.
            try:
                from navig.scheduler import habit_store  # noqa: PLC0415 — lazy: avoids import cycle

                habit_store.migrate_legacy_store()
            except Exception as exc:  # pragma: no cover — migration is best-effort
                logger.error("Legacy cron store migration failed at startup: %s", exc)
            _register_live_service(self)

        # In-memory job set + read-state. `_jobs_readable` is False after a load that
        # couldn't READ the store (a transient lock); while False, `_save_jobs` refuses to
        # overwrite the file — so a momentary lock never wipes every schedule. Initialised
        # before the first load so a failing load can keep (an empty) `self.jobs`.
        self.jobs: dict[str, CronJob] = {}
        self._jobs_file_mtime: float | None = None
        self._jobs_readable = True

        # Load jobs
        self._load_jobs()

    # Run-history bounds: append-only JSONL, compacted back to _RUNS_KEEP
    # entries whenever it grows past _RUNS_CAP (so the file stays small).
    _RUNS_KEEP = 1000
    _RUNS_CAP = 2000

    def _get_jobs_path(self) -> Path:
        return self.storage_path / "cron_jobs.json"

    def _get_runs_path(self) -> Path:
        return self.storage_path / "cron_runs.jsonl"

    def _load_jobs(self) -> None:
        """(Re)load jobs from disk, replacing the in-memory set so external removals
        are honoured. Records the file mtime we just read.

        A transient read failure (an OS lock / half-written file that survives the
        retries) leaves the current in-memory jobs UNTOUCHED and marks the store
        unreadable, so the next `_save_jobs` refuses to overwrite it. This store holds
        every system-wide schedule, and daemon boot (`start()`) saves right after load —
        so collapsing an unreadable read into "zero jobs" here used to persist an empty
        set over every schedule permanently. A genuinely corrupt file is quarantined as
        `*.corrupt` and treated as empty (bytes already lost); one malformed job entry is
        skipped, not fatal to the rest.
        """
        jobs_path = self._get_jobs_path()
        self._jobs_readable = True
        try:
            data = load_json_for_update(jobs_path, default={})
        except JsonReadError as exc:
            logger.error(
                "cron: schedule store unreadable (%s); keeping %s in-memory job(s) and "
                "refusing to overwrite it",
                exc,
                len(self.jobs),
            )
            self._jobs_readable = False
            return

        new_jobs: dict[str, CronJob] = {}
        for job_data in data.get("jobs", []):
            try:
                job = CronJob.from_dict(job_data)
                new_jobs[job.id] = job
            except Exception as e:  # one malformed entry must not drop every job
                logger.error("cron: skipping a malformed job entry: %s", e)
        self.jobs = new_jobs
        self._job_counter = data.get("counter", 0)
        try:
            self._jobs_file_mtime = jobs_path.stat().st_mtime
        except OSError:
            pass
        logger.info("Loaded %s cron jobs", len(self.jobs))

    def _reload_if_changed(self) -> None:
        """Resync from disk when cron_jobs.json was edited by another writer (the
        `navig … schedule` CLI). No-op for our own saves — we compare against the
        mtime we last wrote. Called at the top of each scheduler tick, when no job
        is mid-run (the loop awaits its jobs before sleeping), so replacing the job
        set is safe."""
        try:
            mtime = self._get_jobs_path().stat().st_mtime
        except OSError:
            return  # no file yet
        if self._jobs_file_mtime is not None and mtime <= self._jobs_file_mtime:
            return  # our own write, or unchanged
        self._load_jobs()  # replaces self.jobs + records the new mtime
        for job in self.jobs.values():
            if job.enabled and not job.next_run:
                job.next_run = CronParser.calculate_next(job.schedule)
        logger.info("cron: reloaded %s jobs after an external edit to cron_jobs.json", len(self.jobs))

    def _save_jobs(self) -> None:
        """Save jobs to disk atomically.

        Refuses when the last load could not READ the store (a transient lock): the
        in-memory view may be incomplete, and writing it would overwrite schedules we
        never managed to read — the exact wipe this guards against. The refusal is
        temporary and self-healing: the next clean load clears the flag.
        """
        if not self._jobs_readable:
            logger.warning(
                "cron: not saving — the schedule store was unreadable at the last load; "
                "refusing to overwrite it (will retry once it reads cleanly)"
            )
            return

        data = {
            "counter": self._job_counter,
            "jobs": [j.to_dict() for j in self.jobs.values()],
        }
        jobs_path = self._get_jobs_path()
        try:
            atomic_write_json(data, jobs_path)  # temp-file + fsync + atomic replace, with retry
        except OSError as exc:
            logger.error("cron: failed to save schedule store: %s", exc)
            return
        try:  # remember our own write so _reload_if_changed skips it
            self._jobs_file_mtime = jobs_path.stat().st_mtime
        except OSError:
            pass

    def _log_run(self, job: CronJob, trigger: str, started_at: datetime, duration_s: float) -> None:
        """Append one execution record to the run-history JSONL (best-effort)."""
        entry = {
            "job_id": job.id,
            "name": job.name,
            "trigger": trigger,  # "schedule" | "manual"
            "started_at": started_at.isoformat(),
            "duration_s": round(duration_s, 3),
            "status": job.last_status.value if job.last_status else None,
            "output": (job.last_output or "")[:500],
        }
        try:
            runs_path = self._get_runs_path()
            self.storage_path.mkdir(parents=True, exist_ok=True)
            with open(runs_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._compact_runs(runs_path)
        except Exception as e:
            logger.debug("Cron run-history append failed: %s", e)

    def _compact_runs(self, runs_path: Path) -> None:
        """Keep the history file bounded: past _RUNS_CAP lines, keep the last _RUNS_KEEP."""
        try:
            with open(runs_path, encoding="utf-8") as fh:
                lines = fh.readlines()
            if len(lines) <= self._RUNS_CAP:
                return
            _tmp_path: Path | None = None
            try:
                _fd, _tmp = tempfile.mkstemp(dir=runs_path.parent, suffix=".tmp")
                _tmp_path = Path(_tmp)
                with os.fdopen(_fd, "w", encoding="utf-8") as fh:
                    fh.writelines(lines[-self._RUNS_KEEP:])
                os.replace(_tmp_path, runs_path)
                _tmp_path = None
            finally:
                if _tmp_path is not None:
                    _tmp_path.unlink(missing_ok=True)
        except Exception as e:
            logger.debug("Cron run-history compaction failed: %s", e)

    def get_runs(self, job_id: str | None = None, limit: int = 50) -> list[dict]:
        """Recent execution records, newest first (optionally for one job)."""
        runs_path = self._get_runs_path()
        if not runs_path.exists():
            return []
        entries: list[dict] = []
        try:
            with open(runs_path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if job_id and entry.get("job_id") != job_id:
                        continue
                    entries.append(entry)
        except Exception as e:
            logger.debug("Cron run-history read failed: %s", e)
            return []
        return list(reversed(entries[-max(1, limit):]))

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

        logger.info("Cron service started with %s jobs", len(self.jobs))

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
        timeout_seconds: int | None = None,
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

        logger.info("Added cron job: %s (%s)", name, schedule)

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

        logger.info("Removed cron job: %s", job_id)
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
                # Pick up external edits (the schedule CLI) before evaluating jobs.
                self._reload_if_changed()

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
                logger.error("Scheduler loop error: %s", e)
                await asyncio.sleep(30)

    async def _run_job(self, job: CronJob, trigger: str = "schedule") -> None:
        """Execute a cron job."""
        async with self._semaphore:
            logger.info("Running cron job: %s", job.name)

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

                logger.info("Cron job completed: %s", job.name)

                # Emit success event (detached instances have no gateway)
                if self.gateway is not None and self.gateway.event_queue:
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

                logger.error("Cron job timed out: %s", job.name)

            except Exception as e:
                job.last_status = JobStatus.FAILED
                job.last_output = str(e)
                job.retry_count += 1

                logger.error("Cron job failed: %s - %s", job.name, e)

                # Emit failure event (detached instances have no gateway)
                if self.gateway is not None and self.gateway.event_queue:
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
                logger.info("Job %s will retry in 5 minutes", job.name)

            self._log_run(job, trigger, start_time, (datetime.now() - start_time).total_seconds())
            self._save_jobs()

    async def _execute_job_command(self, job: CronJob) -> str:
        """Execute the job's command."""
        command = job.command.strip()

        # Habit reminder delivery: write a due-now entry to RuntimeStore so that
        # _poll_due_reminders() (telegram.py:615) picks it up within 15 seconds.
        # Format: NAVIG_HABIT_REMINDER:<chat_id>:<base64_message>
        if command.startswith("NAVIG_HABIT_REMINDER:"):
            try:
                import base64 as _b64

                _prefix, chat_id_str, b64_msg = command.split(":", 2)
                chat_id = int(chat_id_str)
                message = _b64.b64decode(b64_msg).decode("utf-8")

                from navig.store.runtime import get_runtime_store

                store = get_runtime_store()
                store.create_reminder(
                    user_id=0,
                    chat_id=chat_id,
                    message=message,
                    remind_at=datetime.now(),
                )
                return f"Habit reminder queued for chat {chat_id}: {message[:60]}"
            except Exception as exc:
                raise RuntimeError(f"Failed to queue habit reminder: {exc}") from exc

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

    async def run_job_now(self, job_id: str) -> dict[str, Any] | None:
        """Manually trigger a job.

        Returns ``{"success", "output", "error", "job"}`` — the contract the
        gateway ``/cron/jobs/{id}/run`` route (and the deck run-now route)
        consume — or ``None`` when the job does not exist. (Previously returned
        the bare ``last_output`` string, which broke both HTTP consumers.)
        """
        job = self.jobs.get(job_id)
        if not job:
            return None

        logger.info("Manual trigger: %s", job.name)
        await self._run_job(job, trigger="manual")

        success = job.last_status == JobStatus.SUCCESS
        return {
            "success": success,
            "output": job.last_output or "",
            "error": None if success else (job.last_output or "job failed"),
            "job": job.to_dict(),
        }

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
