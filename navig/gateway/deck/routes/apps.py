"""
LifeOps app routes for the Deck API.

Exposes life-data endpoints so the navig-deck LifeOps section can display
and interact with habits, reminders, tasks, plans/spaces, calendar events,
and a unified life-dashboard overview.

Routes registered in navig/gateway/deck/__init__.py:
    GET  /api/deck/apps/life                      → aggregated life dashboard
    GET  /api/deck/apps/health                    → habit summary / health overview
    GET  /api/deck/apps/tasks                     → tasks + habits list
    POST /api/deck/apps/tasks/add                 → add a task
    POST /api/deck/apps/tasks/toggle              → toggle task done
    POST /api/deck/apps/habits/toggle             → toggle habit active/paused
    GET  /api/deck/apps/reminders                 → active reminders
    POST /api/deck/apps/reminders/add             → create a reminder
    DELETE /api/deck/apps/reminders/{rid}         → cancel a reminder
    GET  /api/deck/apps/plans                     → spaces / plans summary
    GET  /api/deck/apps/finance                   → budget overview (stub)
    GET  /api/deck/apps/goals                     → goals overview
    POST /api/deck/apps/goals/update-milestone    → update milestone progress
    GET  /api/deck/apps/calendar                  → upcoming calendar events
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from navig.core.aio_subprocess import communicate_or_kill
from navig.core.json_io import atomic_write_json, load_json_for_update
from navig.scheduler.habit_store import HABIT_NAME_PREFIX

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_TASKS_FILE_NAME = "tasks.json"

# Per-file threading locks to prevent races between concurrent requests
_CRON_LOCK: threading.Lock = threading.Lock()
_TASKS_LOCK: threading.Lock = threading.Lock()


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _habit_run_stamp() -> datetime:
    """The clock a habit's ``last_run`` is stamped with — LOCAL, deliberately.

    Every reader asks "was this done today?" by string-prefixing last_run with a
    LOCAL date (``date.today()``, three call sites here), and the scheduler -- the
    other writer -- stamps a naive local ``datetime.now()``
    (cron_service._run_job_locked). A UTC stamp here disagreed with all of them:
    marking a habit done from the deck between local midnight and UTC midnight
    recorded yesterday, so the habit read back as NOT done immediately after the
    user ticked it. Do not "fix" this to UTC without moving the scheduler and all
    three readers with it -- last_run answers a calendar-day question, and the
    calendar the user lives in is the local one. ``created_at`` stays UTC via
    :func:`_utcnow`: that is an instant, not a day.
    """
    return datetime.now()


def _navig_dir() -> Path:
    """The NAVIG dir this install actually uses.

    config_dir(), not Path.home(): this feeds tasks.json and the spaces list, and spaces
    are WRITTEN to config_dir()/spaces (commands/install.py). Hardcoding the home meant the
    deck listed a different set of spaces than the CLI installed. Identical for a default
    install — config_dir() IS ~/.navig — so nothing moves.
    """
    from navig.platform.paths import config_dir

    return config_dir()


def _cron_jobs_path() -> Path:
    # The LIVE scheduler store (the legacy daemon/ store was never executed —
    # see navig.scheduler.habit_store).
    from navig.scheduler import habit_store  # noqa: PLC0415 — lazy import

    return habit_store.live_store_path()


def _tasks_path() -> Path:
    return _navig_dir() / _TASKS_FILE_NAME


def _load_cron_jobs() -> tuple[list[dict], int]:
    # Prefer the in-process live scheduler (race-free, fresh next_run values).
    from navig.scheduler.cron_service import get_live_service  # noqa: PLC0415

    svc = get_live_service()
    if svc is not None:
        return [j.to_dict() for j in svc.jobs.values()], svc._job_counter

    # File fallback (live scheduler down). load_json_for_update RAISES on a transient
    # read failure (a lock that survived retries) instead of returning empty — so a
    # subsequent save can't wipe the persisted reminders over a read blip.
    with _CRON_LOCK:
        data = load_json_for_update(_cron_jobs_path(), default={"jobs": [], "counter": 0})
    return data.get("jobs", []), data.get("counter", 0)


def _save_cron_jobs(jobs: list[dict], counter: int) -> None:
    p = _cron_jobs_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with _CRON_LOCK:
        atomic_write_json({"counter": counter, "jobs": jobs}, p)


async def _async_load_cron_jobs() -> tuple[list[dict], int]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _load_cron_jobs)


async def _async_save_cron_jobs(jobs: list[dict], counter: int) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _save_cron_jobs, jobs, counter)


def _load_tasks() -> list[dict]:
    # load_json_for_update: missing/empty file → []; corrupt → quarantined to *.corrupt
    # + []; but a file that EXISTS with content yet is transiently unreadable (a Windows
    # AV/backup lock) RAISES — so a read blip can't return [] and let the caller's save
    # wipe every task. (The old `except: return []` did exactly that = silent data loss.)
    with _TASKS_LOCK:
        return load_json_for_update(_tasks_path(), default=[])


def _save_tasks(tasks: list[dict]) -> None:
    p = _tasks_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with _TASKS_LOCK:
        atomic_write_json(tasks, p)


async def _async_load_tasks() -> list[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _load_tasks)


async def _async_save_tasks(tasks: list[dict]) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _save_tasks, tasks)


def _get_runtime_store():
    """Return RuntimeStore instance, or None if unavailable."""
    try:
        from navig.store.runtime import get_runtime_store  # type: ignore[import]
        return get_runtime_store()
    except Exception:
        return None


def _ok(data: Any) -> "web.Response":
    return web.json_response({"ok": True, "data": data})


def _err(msg: str, status: int = 400) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


# ── Health summary ─────────────────────────────────────────────────────────────

async def handle_deck_apps_health(request: "web.Request") -> "web.Response":
    """
    Return a HealthSummary from the space's metrics.csv, plus habit counts.

    Every body field was previously INVENTED: ``steps`` was the literal ``0``,
    ``active_minutes`` was ``habits_done_today * 20`` under a "rough proxy"
    comment, ``streak_days`` was the count of habits done today (not a streak,
    and not days), and the renderer manufactured its own sparkline from the step
    figure. The tab looked like measurement and contained none, which is worse
    than an empty state because it cannot be told apart from real data.

    Now the body numbers come from the file `navig body` and the Telegram
    check-in write, and anything not measured is ``None`` — never ``0``.
    "Not measured" and "you did zero" are different claims, and a green zero is
    the honesty failure this codebase keeps re-learning.

    Shape: { date, weight_kg, average_7d, trend_7d, points[], sleep_hours,
             mood_1_10, recorded_days, window_days, steps, active_minutes,
             streak_days, heart_rate_zone, habits_total, habits_done_today,
             unavailable }
    """
    today = date.today().isoformat()

    jobs, _ = await _async_load_cron_jobs()
    habit_jobs = [j for j in jobs if j.get("name", "").startswith(HABIT_NAME_PREFIX)]
    # "Done" is read from the RECORDS, never from the job's last_run: a job that
    # ran only means the bot ASKED. Counting that as done reported every habit
    # complete the moment its reminder fired, answered or not.
    completed_today = await asyncio.to_thread(_habits_done_today, habit_jobs, today)
    checkins = await asyncio.to_thread(_checkins_today)

    body: dict[str, Any] = {
        "weight_kg": None,
        "weight_date": None,
        "average_7d": None,
        "trend_7d": None,
        "points": [],
        "sleep_hours": None,
        "mood_1_10": None,
        "recorded_days": 0,
        "window_days": 30,
        "unavailable": None,
    }
    try:
        from navig.spaces import body_metrics as bm

        path = bm.default_path()
        summary = bm.summarize(path, days=30)
        _, rows = bm.read_metrics(path)
        row = next((r for r in rows if r.get("date") == today), {})

        def _num(value: str) -> float | None:
            try:
                return float((value or "").strip().replace(",", "."))
            except (TypeError, ValueError):
                return None

        body.update({
            "weight_kg": summary["latest"],
            "weight_date": summary["latest_date"],
            "average_7d": bm.moving_average(path, days=7),
            "trend_7d": bm.trend(path, days=7),
            "points": summary["points"],
            "sleep_hours": _num(row.get("sleep_hours", "")),
            "mood_1_10": _num(row.get("mood_1_10", "")),
            "recorded_days": summary["recorded"],
        })
    except Exception as exc:  # noqa: BLE001
        # Could-not-read is NOT no-data. Say so, so the UI can show a warning
        # instead of an empty state that reads as "you have logged nothing".
        logger.warning("health summary could not read the body record: %s", exc)
        body["unavailable"] = "Could not read the body record."

    return _ok({
        "date": today,
        **body,
        # Not measured anywhere yet — null, not 0. There is no wearable
        # integration, and pretending the answer is zero is a claim we cannot make.
        "steps": None,
        "active_minutes": None,
        "streak_days": None,
        "heart_rate_zone": None,
        "habits_total": len(habit_jobs),
        "habits_done_today": completed_today,
        "checkins": checkins,
    })


def _habits_path() -> Path:
    """The habits.csv to read with no chat to identify one (cf. body_metrics.default_path)."""
    from navig.spaces import habit_tracker as ht

    state = ht._read_state()
    paths = {ht._chat_state(state, int(k)).get("path") for k in state if str(k).lstrip("-").isdigit()}
    paths.discard(None)
    if len(paths) == 1:
        return Path(next(iter(paths)))
    return ht.tracker_path()


def _habits_done_today(habit_jobs: list[dict], today: str) -> int:
    """How many habit jobs have a DONE record today — habits.csv, metrics.csv.

    A habit is ``habit:<label>`` and is done when habits.csv holds a done row for
    ``(today, label)``. The two legacy body jobs that still wear the ``habit:``
    prefix are answered in metrics.csv instead: the weigh-in by today's weight,
    the weekly card by today's mood or sleep. An unreadable record counts as not
    done — never as done.
    """
    done = 0
    try:
        from navig.spaces import habit_tracker as ht

        day_map = ht.day_map(_habits_path(), today)
    except Exception as exc:  # noqa: BLE001
        logger.warning("habit record unreadable: %s", exc)
        day_map = {}
    body_row: dict[str, str] | None = None
    for job in habit_jobs:
        label = str(job.get("name", ""))[len(HABIT_NAME_PREFIX):]
        command = str(job.get("command") or "")
        if command.startswith("navig body checkin"):
            if body_row is None:
                try:
                    from navig.spaces import body_metrics as bm

                    _, rows = bm.read_metrics(bm.default_path())
                    body_row = next((r for r in rows if r.get("date") == today), {})
                except Exception:  # noqa: BLE001
                    body_row = {}
            if "--weekly" in command:
                done += int(bool((body_row.get("mood_1_10") or body_row.get("sleep_hours") or "").strip()))
            else:
                done += int(bool((body_row.get("weight_kg") or "").strip()))
            continue
        try:
            from navig.spaces import habit_tracker as ht

            done += int(label in day_map and ht.is_done(day_map[label]))
        except Exception:  # noqa: BLE001
            continue
    return done


def _checkins_today() -> list[dict]:
    """Every check-in's done-today, from its record (metrics.csv / checkins.jsonl)."""
    try:
        from navig.checkins import engine

        return [
            {
                "id": e["id"],
                "template": e["template"],
                "label": e["label"],
                "emoji": e["emoji"],
                "enabled": e.get("enabled", True),
                "done": bool(e.get("done")),
                "state": e.get("state"),
            }
            for e in engine.today_entries(None)
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("check-ins unreadable for the health summary: %s", exc)
        return []


# ── Tasks + Habits ─────────────────────────────────────────────────────────────

def _read_habit_tracker_rows() -> list[dict] | None:
    """The space's habits.csv history, or None when it cannot be read.

    Runs in a worker thread: the tracker file is disk I/O on a route the dashboard
    polls. ``None`` (unreadable) is kept distinct from ``[]`` (read, empty) so the
    caller can answer ``null`` instead of inventing a streak.
    """
    try:
        from navig.spaces import habit_tracker  # noqa: PLC0415

        return habit_tracker.read_tracker(habit_tracker.tracker_path())
    except Exception as exc:  # noqa: BLE001
        logger.debug("habit tracker unreadable; streaks omitted: %s", exc)
        return None


def _habit_streak(rows: list[dict] | None, key: str, completed_today: bool) -> int | None:
    """Consecutive done days for *key*; None when there is no history for it.

    A tick in the deck stamps the job's ``last_run`` but writes no tracker row, so
    today counts as done when either source says so. Never 0-by-default: the old
    literal ``0`` read as "you broke your streak" on every habit, every day.
    """
    if rows is None:
        return None
    from navig.spaces import habit_tracker  # noqa: PLC0415

    own = [r for r in rows if r.get("habit") == key]
    if completed_today:
        own.append({"date": date.today().isoformat(), "habit": key, "completed": "yes"})
    if not own:
        return None
    return habit_tracker.streak_for(own, key, date.today())


async def handle_deck_apps_tasks_get(request: "web.Request") -> "web.Response":
    """
    Return TaskHub: { tasks: TaskItem[], habits: HabitItem[] }
    """
    try:
        import base64

        tasks = await _async_load_tasks()
        jobs, _ = await _async_load_cron_jobs()
        habit_jobs = [j for j in jobs if j.get("name", "").startswith(HABIT_NAME_PREFIX)]

        today = date.today().isoformat()
        tracker_rows = await asyncio.to_thread(_read_habit_tracker_rows)

        habits = []
        for j in habit_jobs:
            name_raw = j.get("name", "")[len(HABIT_NAME_PREFIX):]
            # Decode the base64 message embedded in the command for a human label
            label = name_raw
            cmd = j.get("command", "")
            if ":" in cmd:
                parts = cmd.split(":", 2)
                if len(parts) == 3:
                    try:
                        label = base64.b64decode(parts[2]).decode("utf-8", errors="replace")
                    except Exception:
                        label = name_raw

            completed_today = (j.get("last_run") or "").startswith(today)
            habits.append({
                "id": j.get("id", name_raw),
                "title": label,
                # Counted from the space's habits.csv (the history `navig habit log`
                # and the Telegram check-in write). null = no history to count from;
                # the old literal 0 read as "you broke your streak" every day.
                "streak": _habit_streak(tracker_rows, name_raw, completed_today),
                "completed_today": completed_today,
                "active": j.get("status", "active") == "active",
                "cron": j.get("schedule", ""),
            })

        return _ok({"tasks": tasks, "habits": habits})
    except Exception as exc:
        # A server failure is a 500, not a 400: the request was fine.
        logger.exception("apps/tasks failed")
        return _err(str(exc), 500)


async def handle_deck_apps_tasks_add(request: "web.Request") -> "web.Response":
    """POST { title: str } → TaskItem"""
    try:
        body = await request.json()
    except Exception:
        return _err("Invalid JSON")

    title = (body.get("title") or "").strip()
    if not title:
        return _err("title is required")

    import uuid
    tasks = await _async_load_tasks()
    new_task = {
        "id": str(uuid.uuid4()),
        "title": title,
        "done": False,
        "due_at": body.get("due_at"),
        "created_at": _utcnow(),
    }
    tasks.append(new_task)
    await _async_save_tasks(tasks)
    return web.json_response({"ok": True, "data": new_task}, status=201)


async def handle_deck_apps_tasks_toggle(request: "web.Request") -> "web.Response":
    """POST { id: str, done: bool } → { ok }"""
    try:
        body = await request.json()
    except Exception:
        return _err("Invalid JSON")

    task_id = body.get("id") or ""
    done = bool(body.get("done", True))

    tasks = await _async_load_tasks()
    matched = False
    for task in tasks:
        if task.get("id") == task_id:
            task["done"] = done
            matched = True
            break

    if not matched:
        return _err("Task not found", 404)

    await _async_save_tasks(tasks)
    return _ok({"id": task_id, "done": done})


def _log_habit_in_tracker(key: str) -> bool | None:
    """Mirror a deck tick into the space's habits.csv — the history streaks count.

    The tick only stamped the job's ``last_run``, which holds ONE day: the deck
    showed a habit done while ``navig habit streak`` (and tomorrow's dashboard)
    never saw it, so a streak kept only by tapping the deck reset every day.
    Returns True when a row was written, False when today was already logged
    (a value logged from the CLI is kept as is), None when the log could not be
    written — the tick itself already succeeded, so that is reported, not raised.
    """
    try:
        from navig.spaces import habit_tracker  # noqa: PLC0415

        return habit_tracker.mark_done_today(key, note="deck")
    except Exception as exc:  # noqa: BLE001
        logger.warning("habit %s ticked but not written to habits.csv: %s", key, exc)
        return None


async def handle_deck_apps_habits_toggle(request: "web.Request") -> "web.Response":
    """POST { id: str } → marks habit as completed today by touching last_run."""
    try:
        body = await request.json()
    except Exception:
        return _err("Invalid JSON")

    habit_id = str(body.get("id") or "")

    # In-process live scheduler first — a raw file write would be clobbered by
    # the running service's next save.
    from navig.scheduler.cron_service import get_live_service  # noqa: PLC0415

    svc = get_live_service()
    if svc is not None:
        import functools  # noqa: PLC0415

        for job in svc.jobs.values():
            if not job.name.startswith(HABIT_NAME_PREFIX):
                continue  # only habit jobs are togglable here — never a plain cron job
            jname = job.name[len(HABIT_NAME_PREFIX):]
            if job.id == habit_id or jname == habit_id:
                # CronJob.last_run is a datetime (to_dict isoformats it) — pass
                # a real datetime, not the _utcnow() ISO string.
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    functools.partial(
                        svc.update_job, job.id, last_run=_habit_run_stamp()
                    ),
                )
                logged = await asyncio.to_thread(_log_habit_in_tracker, jname)
                return _ok({"ok": True, "id": habit_id, "logged": logged})
        return _err("Habit not found", 404)

    jobs, counter = await _async_load_cron_jobs()
    matched = False
    for j in jobs:
        name = j.get("name", "")
        if not name.startswith(HABIT_NAME_PREFIX):
            continue  # only habit jobs are togglable — never touch a plain cron job's last_run
        jid = str(j.get("id", ""))
        jname = name[len(HABIT_NAME_PREFIX):]
        if jid == habit_id or jname == habit_id:
            j["last_run"] = _habit_run_stamp().isoformat()
            matched = True
            break

    if not matched:
        return _err("Habit not found", 404)

    await _async_save_cron_jobs(jobs, counter)
    logged = await asyncio.to_thread(_log_habit_in_tracker, jname)
    return _ok({"ok": True, "id": habit_id, "logged": logged})


# ── Reminders ─────────────────────────────────────────────────────────────────

async def handle_deck_apps_reminders_get(request: "web.Request") -> "web.Response":
    """
    GET active (upcoming) reminders.
    Uses user_id 0 (system/global) since the Deck is a personal dashboard.
    Returns all non-completed future reminders.
    """
    store = _get_runtime_store()
    if store is None:
        # A missing store is NOT an empty list: saying "no reminders" here hid a
        # broken install behind the same words a genuinely empty schedule shows.
        return _err("the reminder store is not available on this Brain", 200)

    try:
        # We return all non-completed upcoming reminders across all users for the Deck.
        # Compare against the canonical UTC '…Z' shape remind_at is STORED in (RuntimeStore
        # normalizes via _to_utc_iso, #635) — a bare `.isoformat()` here is '+00:00', which
        # string-sorts differently from '…Z' at the boundary (same cross-format class as #639).
        from navig.store.base import _utcnow as _store_utcnow

        rows = store._read_all(  # type: ignore[attr-defined]
            "SELECT id, user_id, chat_id, message, remind_at, created_at "
            "FROM reminders WHERE completed = 0 AND remind_at > ? ORDER BY remind_at LIMIT 50",
            (_store_utcnow(),),
        )
        items = [
            {
                "id": r["id"],
                "message": r["message"],
                "fire_at": r["remind_at"],
                "chat_id": r["chat_id"],
                "user_id": r["user_id"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
        return _ok(items)
    except Exception as exc:
        logger.warning("reminders_get error: %s", exc)
        return _err(f"could not read reminders: {exc}", 200)


async def handle_deck_apps_reminders_add(request: "web.Request") -> "web.Response":
    """
    POST { message: str, fire_at: ISO8601 | null, in_minutes: int | null }
    Creates a reminder via RuntimeStore.
    """
    try:
        body = await request.json()
    except Exception:
        return _err("Invalid JSON")

    message = (body.get("message") or "").strip()
    if not message:
        return _err("message is required")

    fire_at_str = body.get("fire_at")
    in_minutes = body.get("in_minutes")

    if fire_at_str:
        try:
            # Preserve any real UTC offset (e.g. -04:00) instead of discarding it, accept a
            # trailing 'Z', default a naive value to UTC, then normalize to UTC so the stored
            # isoformat is a canonical UTC string — RuntimeStore compares remind_at
            # lexicographically against _utcnow(). The old `rstrip("Z").replace(tzinfo=utc)`
            # kept the wall-clock and forced UTC, so an offset-bearing time fired at the wrong
            # instant (18:00-04:00 was stored as 18:00Z instead of 22:00Z).
            fire_at = datetime.fromisoformat(str(fire_at_str).replace("Z", "+00:00"))
            if fire_at.tzinfo is None:
                fire_at = fire_at.replace(tzinfo=timezone.utc)
            fire_at = fire_at.astimezone(timezone.utc)
        except Exception:
            return _err("Invalid fire_at ISO8601 value")
    elif in_minutes is not None:
        try:
            fire_at = datetime.now(timezone.utc) + timedelta(minutes=int(in_minutes))
        except Exception:
            return _err("Invalid in_minutes value")
    else:
        return _err("Either fire_at or in_minutes is required")

    store = _get_runtime_store()
    if store is None:
        return _err("Runtime store unavailable", 503)

    # Resolve the operator's Telegram chat so the reminder PUSHES to Telegram, not just the
    # deck feed. Falls back to 0 when no Telegram user is configured — a chat-less reminder
    # then delivers via the notify channels (deck feed) instead of being dropped (#623). Keep
    # user_id=0: the deck's get returns all reminders and delete has a no-user fallback, so the
    # deck's own list/cancel stay consistent regardless of chat_id.
    from navig.gateway.deck.routes.schedule import _resolve_default_user_chat

    _uid, chat_id = _resolve_default_user_chat()
    try:
        rid = store.create_reminder(
            user_id=0,
            chat_id=chat_id,
            message=message,
            remind_at=fire_at,
        )
        return web.json_response({
            "ok": True,
            "data": {
                "id": rid,
                "message": message,
                "fire_at": fire_at.isoformat(),
            },
        }, status=201)
    except Exception as exc:
        logger.warning("reminders_add error: %s", exc)
        return _err(str(exc), 500)


async def handle_deck_apps_reminders_delete(request: "web.Request") -> "web.Response":
    """DELETE /api/deck/apps/reminders/{rid}"""
    try:
        rid = int(request.match_info["rid"])
    except (KeyError, ValueError):
        return _err("Invalid reminder id")

    store = _get_runtime_store()
    if store is None:
        return _err("Runtime store unavailable", 503)

    try:
        # cancel_reminder requires user_id — use 0 (deck context)
        deleted = store.cancel_reminder(rid, user_id=0)
        if not deleted:
            # Try without user_id filter (deck is trusted)
            store._write("DELETE FROM reminders WHERE id = ?", (rid,))  # type: ignore[attr-defined]
            deleted = True
        return _ok({"deleted": deleted, "id": rid})
    except Exception as exc:
        logger.warning("reminders_delete error: %s", exc)
        return _err(str(exc), 500)


# ── Plans / Spaces ─────────────────────────────────────────────────────────────

def _get_active_space_name() -> str | None:
    """Read the active space NAME (a string), or None when unset. Silent-fail.

    Delegates to the canonical resolver (navig space's ``resolve_active_space``) so the deck
    honours the SAME source of truth as the CLI — the ``NAVIG_SPACE`` env, then the cache file
    (``~/.navig/cache/active_space.txt``), then the config-key mirror — and can never drift.
    A config-only read (the old form) missed the cache file that ``_set_active_space`` writes
    first; delegating closes that gap. Returns None when no space is set (callers supply the
    fallback: the plans list marks nothing active; the space summary returns its empty shape).
    """
    try:
        from navig.commands.space import resolve_active_space  # type: ignore[import]
        return resolve_active_space()
    except Exception:
        return None


def _plan_path(space_dir: Path, name: str) -> Path:
    """Where *space_dir* keeps plan *name* (``.navig/plans``, ``plans/`` or the root).

    These readers used to look at the root only, so a space that keeps its plans in
    ``.navig/plans/`` — most of them — showed no vision, phase or milestones. The
    root path is still returned when no copy exists, so callers' "missing" handling
    is unchanged.
    """
    from navig.spaces.plan_files import plan_file  # noqa: PLC0415

    return plan_file(space_dir, name) or (space_dir / name)


def _get_spaces_dir() -> Path:
    return _navig_dir() / "spaces"


def _confined_space_dir(name: str | None) -> Path | None:
    """A single space directory under the spaces dir, or None if *name* escapes it.

    *name* is a CLIENT-SUPPLIED space / goal id (deck API, remotely reachable via Lighthouse),
    so a value like ``../../etc`` must never resolve outside the spaces tree — goals_milestone
    reads AND writes ``<dir>/ROADMAP.md``. Thin wrapper over the shared ``confine_under`` guard.
    """
    from navig.gateway.deck.routes._utils import confine_under  # noqa: PLC0415

    return confine_under(_get_spaces_dir(), name)


def _parse_roadmap_milestones(roadmap_path: Path) -> list[dict]:
    """Parse ROADMAP.md for milestone lines: `- [ ]` or `- [x]`."""
    if not roadmap_path.exists():
        return []
    try:
        text = roadmap_path.read_text(encoding="utf-8")
    except Exception:
        return []

    milestones = []
    import uuid as _uuid
    for i, line in enumerate(text.splitlines()):
        m = re.match(r"\s*-\s*\[([ xX])\]\s*(.+)", line)
        if m:
            done = m.group(1).lower() == "x"
            title = m.group(2).strip()
            milestones.append({
                "id": str(_uuid.uuid5(_uuid.NAMESPACE_URL, f"{roadmap_path}:{i}")),
                "title": title,
                "progress": 100 if done else 0,
                "target": 100,
                "done": done,
            })
    return milestones


def _read_file_content(p: Path, max_bytes: int = 4096) -> str:
    if not p.exists():
        return ""
    try:
        return p.read_bytes()[:max_bytes].decode("utf-8", errors="replace")
    except Exception:
        return ""


async def handle_deck_apps_plans(request: "web.Request") -> "web.Response":
    """
    GET plans/spaces summary.
    Shape: PlansSummary { space, phase, completion_pct, next_action, vision_headline }
    """
    try:
        spaces_dir = _get_spaces_dir()
        active = _get_active_space_name()

        plans = []
        if spaces_dir.is_dir():
            for space_dir in sorted(spaces_dir.iterdir()):
                if not space_dir.is_dir():
                    continue

                name = space_dir.name
                is_active = name == active

                vision_text = _read_file_content(_plan_path(space_dir, "VISION.md"), 512)
                vision_headline = ""
                for line in vision_text.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        vision_headline = line[:120]
                        break
                    if line.startswith("# "):
                        vision_headline = line[2:].strip()[:120]
                        break

                phase_text = _read_file_content(_plan_path(space_dir, "CURRENT_PHASE.md"), 512)
                current_phase = ""
                for line in phase_text.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        current_phase = line[:120]
                        break
                    if line.startswith("# "):
                        current_phase = line[2:].strip()[:120]
                        break

                milestones = _parse_roadmap_milestones(_plan_path(space_dir, "ROADMAP.md"))
                total = len(milestones)
                done = sum(1 for m in milestones if m.get("done"))
                completion_pct = int(done / total * 100) if total else 0

                # Next action: first undone milestone
                next_action = next(
                    (m["title"] for m in milestones if not m.get("done")),
                    None,
                )

                plans.append({
                    "space": name,
                    "active": is_active,
                    "phase": current_phase,
                    "completion_pct": completion_pct,
                    "next_action": next_action,
                    "vision_headline": vision_headline,
                    "milestones_total": total,
                    "milestones_done": done,
                })

        # Sort active space first
        plans.sort(key=lambda p: (0 if p["active"] else 1, p["space"]))

        return _ok(plans)
    except Exception as exc:
        logger.exception("apps/plans failed")
        return _err(str(exc))


# ── Finance (stub) ─────────────────────────────────────────────────────────────

async def handle_deck_apps_finance(request: "web.Request") -> "web.Response":
    """
    Finance overview — finance.py is stubbed (beancount not yet implemented).
    Returns empty overview so the UI renders without error.
    """
    from datetime import date
    return _ok({
        "month": date.today().strftime("%Y-%m"),
        "budget_total": 0,
        "spent_total": 0,
        "categories": [],
        "stub": True,
        "note": "Finance integration coming soon — connect via navig vault add finance",
    })


# ── Goals ─────────────────────────────────────────────────────────────────────

#: Parsed ROADMAP milestones, keyed by path, stamped with (mtime_ns, size). The goals
#: card polls; a space whose roadmap has not changed must not be re-parsed. The stamp
#: is a pair, not mtime alone: a rewrite inside the same nanosecond still changes size.
_ROADMAP_CACHE: dict[str, tuple[tuple[int, int], list[dict]]] = {}


def _cached_roadmap_milestones(roadmap_path: Path) -> list[dict]:
    """Milestones for *roadmap_path*, re-parsed only when the file changed."""
    try:
        st = roadmap_path.stat()
    except OSError:
        _ROADMAP_CACHE.pop(str(roadmap_path), None)
        return []
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _ROADMAP_CACHE.get(str(roadmap_path))
    if hit is not None and hit[0] == stamp:
        return hit[1]
    milestones = _parse_roadmap_milestones(roadmap_path)
    _ROADMAP_CACHE[str(roadmap_path)] = (stamp, milestones)
    return milestones


def _collect_goals(spaces_dir: Path) -> list[dict]:
    """Every space's roadmap as a goal. Directory walk + file reads: worker thread."""
    if not spaces_dir.is_dir():
        return []
    goals = []
    for space_dir in sorted(spaces_dir.iterdir()):
        if not space_dir.is_dir():
            continue
        milestones = _cached_roadmap_milestones(_plan_path(space_dir, "ROADMAP.md"))
        if not milestones:
            continue
        total = len(milestones)
        done = sum(1 for m in milestones if m.get("done"))
        goals.append({
            "id": space_dir.name,
            "title": space_dir.name.replace("-", " ").replace("_", " ").title(),
            "progress_pct": int(done / total * 100) if total else 0,
            "milestones": milestones,
        })
    return goals


async def handle_deck_apps_goals(request: "web.Request") -> "web.Response":
    """
    Goals overview derived from spaces ROADMAP.md milestone lists.
    Shape: GoalsOverview { goals: [{id, title, progress_pct, milestones}] }

    The walk + parse runs in a worker thread: it reads every space's ROADMAP.md off
    disk, and the goals card polls. A failed scan answers a JSON 500 the UI can show,
    not aiohttp's bare error page.
    """
    try:
        goals = await asyncio.to_thread(_collect_goals, _get_spaces_dir())
    except Exception as exc:  # noqa: BLE001
        logger.exception("apps/goals failed")
        return _err(f"Could not read goals: {exc}", 500)
    return _ok({"goals": goals})


async def handle_deck_apps_goals_milestone(request: "web.Request") -> "web.Response":
    """
    POST { goal_id: str, milestone_id: str, done: bool }
    Updates the checkbox state of a milestone in the space's ROADMAP.md.
    """
    try:
        body = await request.json()
    except Exception:
        return _err("Invalid JSON")

    goal_id = (body.get("goal_id") or "").strip()
    milestone_id = (body.get("milestone_id") or "").strip()
    done = bool(body.get("done", True))

    if not goal_id or not milestone_id:
        return _err("goal_id and milestone_id are required")

    space_dir = _confined_space_dir(goal_id)  # goal_id is client-supplied — never let it traverse
    if space_dir is None:
        return _err("invalid goal id", 400)
    roadmap_path = _plan_path(space_dir, "ROADMAP.md")
    if not roadmap_path.exists():
        return _err("ROADMAP.md not found for this space", 404)

    # Regenerate milestones to find the index
    milestones = _parse_roadmap_milestones(roadmap_path)
    target = next((m for m in milestones if m["id"] == milestone_id), None)
    if target is None:
        return _err("Milestone not found", 404)

    # Rewrite only the specific line identified by the parsed line index.
    # milestone IDs are uuid5 of f"{roadmap_path}:{line_index}", so we
    # find the exact line by re-iterating — no title-based regex needed.
    try:
        import uuid as _uuid
        lines = roadmap_path.read_text(encoding="utf-8").splitlines(keepends=True)
        new_mark = "x" if done else " "
        replaced = False
        for idx, line in enumerate(lines):
            if re.match(r"\s*-\s*\[[ xX]\]", line):
                expected_id = str(_uuid.uuid5(_uuid.NAMESPACE_URL, f"{roadmap_path}:{idx}"))
                if expected_id == milestone_id:
                    lines[idx] = re.sub(r"(\s*-\s*\[)[ xX](\])", r"\g<1>" + new_mark + r"\g<2>", line, count=1)
                    replaced = True
                    break
        if not replaced:
            return _err("Milestone line not found in file", 404)
        roadmap_path.write_text("".join(lines), encoding="utf-8")
        # A rewrite inside the same mtime tick would otherwise serve the pre-tap list.
        _ROADMAP_CACHE.pop(str(roadmap_path), None)
    except Exception as exc:
        logger.warning("goals_milestone update error: %s", exc)
        return _err(str(exc), 500)

    return _ok({"goal_id": goal_id, "milestone_id": milestone_id, "done": done})


# ── Calendar ──────────────────────────────────────────────────────────────────

async def handle_deck_apps_calendar(request: "web.Request") -> "web.Response":
    """
    GET upcoming calendar events (next 7 days).
    Calls `navig calendar list --json` as a subprocess if available.
    Falls back to empty list if calendar is not configured.
    Shape: CalendarEvent[]  { id, title, start_at, end_at, all_day, source }
    """
    import asyncio

    from navig.platform.navig_cli import navig_launcher

    try:
        proc = await asyncio.create_subprocess_exec(
            *navig_launcher(), "calendar", "list", "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await communicate_or_kill(proc, 8.0)
        if proc.returncode == 0 and stdout:
            try:
                events = json.loads(stdout.decode("utf-8"))
                return _ok(events)
            except Exception:
                pass
    except Exception as exc:
        logger.debug("calendar list subprocess error: %s", exc)

    # Reaching here means the calendar command failed or printed nothing usable.
    # An empty list would claim "nothing scheduled today"; the honest answer is
    # that this Brain could not read the calendar at all.
    return _err("the calendar is not available on this Brain", 200)


# ── Life dashboard domain providers ────────────────────────────────────────────

async def _life_habits_today(today: str) -> dict:
    """Return habit summary for *today*."""
    jobs, _ = await _async_load_cron_jobs()
    habit_jobs = [j for j in jobs if j.get("name", "").startswith(HABIT_NAME_PREFIX)]
    habits_done = sum(1 for j in habit_jobs if (j.get("last_run") or "").startswith(today))
    return {"habits_total": len(habit_jobs), "habits_done_today": habits_done}


async def _life_reminders_summary() -> dict:
    """Return active reminder count and soonest upcoming reminder."""
    store = _get_runtime_store()
    active_reminders = 0
    next_reminder = None
    if store is not None:
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            rows = store._read_all(  # type: ignore[attr-defined]
                "SELECT id, message, remind_at FROM reminders "
                "WHERE completed = 0 AND remind_at > ? ORDER BY remind_at LIMIT 1",
                (now_iso,),
            )
            count_rows = store._read_all(  # type: ignore[attr-defined]
                "SELECT COUNT(*) as cnt FROM reminders WHERE completed = 0 AND remind_at > ?",
                (now_iso,),
            )
            active_reminders = count_rows[0]["cnt"] if count_rows else 0
            if rows:
                next_reminder = {
                    "id": rows[0]["id"],
                    "message": rows[0]["message"],
                    "fire_at": rows[0]["remind_at"],
                }
        except Exception:
            pass
    return {"active_reminders": active_reminders, "next_reminder": next_reminder}


def _roadmap_summary(name: str, root: Path) -> dict:
    """Build {active_space, completion_pct, next_action} from a space dir's ROADMAP.
    Reads ROADMAP.md, else .navig/plans/ROADMAP.md (federated workshops)."""
    milestones = _parse_roadmap_milestones(root / "ROADMAP.md")
    if not milestones:
        milestones = _parse_roadmap_milestones(root / ".navig" / "plans" / "ROADMAP.md")
    total = len(milestones)
    done = sum(1 for m in milestones if m.get("done"))
    return {
        "active_space": name,
        "space_completion_pct": int(done / total * 100) if total else 0,
        "space_next_action": next((m["title"] for m in milestones if not m.get("done")), None),
    }


async def _life_space_summary() -> dict:
    """Return active space name, completion %, and next action.

    Workshop-aware: resolves the active folder-space from the registry
    (~/.navig/spaces.json `active` pointer) first — reading milestones from
    ROADMAP.md or .navig/plans/ROADMAP.md — and falls back to the legacy
    name-based space under ~/.navig/spaces/<name>/ROADMAP.md.
    """
    empty = {"active_space": None, "space_completion_pct": 0, "space_next_action": None}

    # 1. New folder-space (explicit registry `active` pointer).
    try:
        from navig.spaces import registry as _registry  # noqa: PLC0415

        active_path = _registry.load_registry().get("active")
        if active_path:
            root = Path(active_path).expanduser()
            if root.is_dir():
                name = root.name
                try:
                    from navig.spaces.space_manifest import load_space_manifest  # noqa: PLC0415

                    name = load_space_manifest(root).resolved_name or name
                except Exception:  # noqa: BLE001
                    pass
                return _roadmap_summary(name, root)
    except Exception:  # noqa: BLE001
        pass

    # 2. Legacy name-based space.
    active_space = _get_active_space_name()
    if not active_space:
        return empty
    return _roadmap_summary(active_space, _get_spaces_dir() / active_space)


async def _life_fleet_count() -> dict:
    """Return number of configured remote hosts."""
    count = 0
    try:
        from navig.config import get_config_manager  # type: ignore[import]
        count = len(get_config_manager().list_hosts())
    except Exception:
        pass
    return {"fleet_count": count}


async def _life_pending_tasks() -> dict:
    """Return count of pending (undone) tasks."""
    tasks = await _async_load_tasks()
    return {"tasks_pending": sum(1 for t in tasks if not t.get("done"))}


# ── Life dashboard (aggregated) ────────────────────────────────────────────────

async def handle_deck_apps_life(request: "web.Request") -> "web.Response":
    """
    Aggregated LifeOps dashboard — mirrors `navig life show` output as JSON.
    Each domain is fetched via an independent async provider and merged.
    """
    try:
        today = date.today().isoformat()
        results = await asyncio.gather(
            _life_habits_today(today),
            _life_reminders_summary(),
            _life_space_summary(),
            _life_fleet_count(),
            _life_pending_tasks(),
            return_exceptions=True,
        )

        _defaults = [
            {"habits_total": 0, "habits_done_today": 0},
            {"active_reminders": 0, "next_reminder": None},
            {"active_space": None, "space_completion_pct": 0, "space_next_action": None},
            {"fleet_count": 0},
            {"tasks_pending": 0},
        ]

        merged: dict = {"date": today}
        for result, default in zip(results, _defaults):
            if isinstance(result, Exception):
                logger.warning("life dashboard provider failed: %s", result)
                merged.update(default)
            else:
                merged.update(result)

        return _ok(merged)
    except Exception as exc:
        logger.exception("apps/life failed")
        return _err(str(exc))


# ---------------------------------------------------------------------------
# Apps Hub routes
# GET  /api/deck/apps/passport
# GET  /api/deck/apps/wallet
# POST /api/deck/apps/wallet/send
# GET  /api/deck/apps/knowledge
# POST /api/deck/apps/knowledge/add
# GET  /api/deck/apps/devops
# ---------------------------------------------------------------------------

_KNOWLEDGE_LOCK = threading.Lock()


def _wiki_root() -> Path:
    """Resolve the wiki root at CALL time — never import time.

    Uses ``config_dir()`` (honours ``NAVIG_CONFIG_DIR``) instead of a
    hardcoded ``Path.home() / ".navig"``, matching ``wiki_rag``; a frozen
    module constant would read/write the operator's real wiki from
    isolated tests/daemons (see ``navig/vault/migrate.py:_legacy_db_path``).
    """
    from navig.platform.paths import config_dir

    return config_dir() / "wiki"


def _build_knowledge_dir() -> Path:
    d = _wiki_root() / "knowledge"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sync_list_knowledge() -> list[dict]:
    root = _build_knowledge_dir()
    items: list[dict] = []
    for md_file in sorted(root.rglob("*.md")):
        try:
            text = md_file.read_text(encoding="utf-8", errors="replace")
            title = md_file.stem.replace("-", " ").replace("_", " ").title()
            # First heading overrides stem title
            for line in text.splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
            items.append({
                "id": md_file.stem,
                "title": title,
                "type": "note",
                "content": text[:300],
                "tags": [],
                "created_at": datetime.fromtimestamp(
                    md_file.stat().st_ctime, tz=timezone.utc
                ).isoformat(),
            })
        except Exception:
            pass
    return items


async def _async_list_knowledge() -> list[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_list_knowledge)


def _sync_add_knowledge(title: str, content: str) -> dict:
    root = _build_knowledge_dir()
    slug = re.sub(r"[^\w]+", "-", title.lower()).strip("-") or "note"
    path = root / f"{slug}.md"
    counter = 1
    with _KNOWLEDGE_LOCK:
        while path.exists():
            path = root / f"{slug}-{counter}.md"
            counter += 1
        path.write_text(f"# {title}\n\n{content}\n", encoding="utf-8")
    return {
        "id": path.stem,
        "title": title,
        "type": "note",
        "content": content[:300],
        "tags": [],
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
    }


async def _async_add_knowledge(title: str, content: str) -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_add_knowledge, title, content)


async def handle_deck_apps_passport(request: "web.Request") -> "web.Response":
    """GET /api/deck/apps/passport — operator identity card."""
    display_name = "Operator"
    fleet_count = 0
    vault_count = 0
    try:
        from navig.config import get_config_manager  # type: ignore[import]
        cm = get_config_manager()
        display_name = cm.get("ui.display_name") or cm.get("operator.name") or "Operator"
        fleet_count = len(cm.list_hosts())
    except Exception:
        pass
    try:
        # `VaultProvider` does not exist in navig.vault; the credential store is `Vault`
        # (via get_vault()), and its item count is `count()`.
        from navig.vault import get_vault
        vault_count = get_vault().count()
    except Exception:
        pass

    membership: str
    if fleet_count >= 10 or vault_count >= 20:
        membership = "elite"
    elif fleet_count >= 3 or vault_count >= 5:
        membership = "pro"
    else:
        membership = "free"

    return _ok({
        "display_name": display_name,
        "membership": membership,
        "achievements": [f"{fleet_count} hosts connected"] if fleet_count else [],
        "credentials": [],
        "share_token": "",
    })


async def handle_deck_apps_wallet(request: "web.Request") -> "web.Response":
    """GET /api/deck/apps/wallet — TON wallet overview (stub / vault-backed)."""
    address = ""
    try:
        # `VaultProvider().get(...)` never existed. reveal_secret returns a plaintext str
        # ("" if absent) — the old code assigned the get() result straight into a string
        # field, so a real Vault.get() (which returns a Credential object) would have put
        # an object where an address string belongs.
        from navig.vault import get_vault, reveal_secret
        address = reveal_secret(get_vault(), "ton_wallet_address")
    except Exception:
        pass

    return _ok({
        "address": address,
        "network": "TON",
        "balances": [{"symbol": "TON", "amount": 0, "fiat_usd": None}] if address else [],
        "transactions": [],
    })


async def handle_deck_apps_wallet_send(request: "web.Request") -> "web.Response":
    """POST /api/deck/apps/wallet/send — initiate transfer (stub)."""
    try:
        body: dict = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)

    to = body.get("to", "")
    amount = body.get("amount", 0)
    if not to or not amount:
        return web.json_response({"ok": False, "error": "to and amount required"}, status=400)

    return web.json_response(
        {"ok": False, "error": "Wallet transactions are not yet configured. Add a TON wallet to vault to enable."},
        status=503,
    )


async def handle_deck_apps_knowledge_get(request: "web.Request") -> "web.Response":
    """GET /api/deck/apps/knowledge — list wiki knowledge items."""
    items = await _async_list_knowledge()
    return _ok({"items": items, "total": len(items)})


async def handle_deck_apps_knowledge_add(request: "web.Request") -> "web.Response":
    """POST /api/deck/apps/knowledge/add — create a new knowledge note."""
    try:
        body: dict = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)

    title = str(body.get("title", "")).strip()
    content = str(body.get("content", "")).strip()
    if not title:
        return web.json_response({"ok": False, "error": "title is required"}, status=400)

    item = await _async_add_knowledge(title, content)
    return _ok({"item": item})


async def handle_deck_apps_devops(request: "web.Request") -> "web.Response":
    """GET /api/deck/apps/devops — DevOps summary."""
    host_status = "offline"
    deploy_health = "warning"
    recent_activity: list[dict] = []
    fleet_count = 0

    try:
        from navig.config import get_config_manager  # type: ignore[import]
        cm = get_config_manager()
        fleet_count = len(cm.list_hosts())
        if fleet_count > 0:
            host_status = "online"
    except Exception:
        pass

    # `navig.daemon_client.get_daemon_status` has never existed — neither the module nor the
    # function — so this whole block always raised and the except branch below was the only
    # code that ever ran: deploy_health came from fleet_count and recent_activity stayed [].
    # That fallback is kept verbatim (it is the panel's only ever-observed behaviour); the
    # activity feed is now filled from the real source sibling deck routes already use.
    deploy_health = "healthy" if fleet_count > 0 else "warning"
    try:
        from navig.gateway.system_events import get_system_events

        queue = get_system_events()
        if queue is not None:
            recent_activity = [
                {"id": ev.id, "label": ev.event_type, "ts": ev.timestamp.isoformat()}
                for ev in reversed(queue.get_history(limit=5))  # newest first
            ]
    except Exception:  # noqa: BLE001 - an activity feed must never fail the summary
        logger.debug("devops summary: recent activity unavailable", exc_info=True)

    return _ok({
        "host_status": host_status,
        "deploy_health": deploy_health,
        "recent_activity": recent_activity,
        "fleet_count": fleet_count,
    })
