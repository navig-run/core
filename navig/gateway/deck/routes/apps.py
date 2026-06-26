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
import os
import re
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_HABIT_NAME_PREFIX = "habit:"
_TASKS_FILE_NAME = "tasks.json"

# Per-file threading locks to prevent races between concurrent requests
_CRON_LOCK: threading.Lock = threading.Lock()
_TASKS_LOCK: threading.Lock = threading.Lock()


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _navig_dir() -> Path:
    return Path.home() / ".navig"


def _cron_jobs_path() -> Path:
    return _navig_dir() / "daemon" / "cron_jobs.json"


def _tasks_path() -> Path:
    return _navig_dir() / _TASKS_FILE_NAME


def _load_cron_jobs() -> tuple[list[dict], int]:
    p = _cron_jobs_path()
    if not p.exists():
        return [], 0
    try:
        with _CRON_LOCK:
            data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("jobs", []), data.get("counter", 0)
    except Exception:
        return [], 0


def _save_cron_jobs(jobs: list[dict], counter: int) -> None:
    p = _cron_jobs_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"counter": counter, "jobs": jobs}
    with _CRON_LOCK:
        tmp_fd, tmp_name = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(data, indent=2))
            os.replace(tmp_name, p)
        except Exception:
            Path(tmp_name).unlink(missing_ok=True)
            raise


async def _async_load_cron_jobs() -> tuple[list[dict], int]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _load_cron_jobs)


async def _async_save_cron_jobs(jobs: list[dict], counter: int) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _save_cron_jobs, jobs, counter)


def _load_tasks() -> list[dict]:
    p = _tasks_path()
    if not p.exists():
        return []
    try:
        with _TASKS_LOCK:
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_tasks(tasks: list[dict]) -> None:
    p = _tasks_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with _TASKS_LOCK:
        tmp_fd, tmp_name = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(tasks, indent=2))
            os.replace(tmp_name, p)
        except Exception:
            Path(tmp_name).unlink(missing_ok=True)
            raise


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
    Return a HealthSummary derived from active habit cron jobs.

    Shape: { date, steps, active_minutes, streak_days, heart_rate_zone }
    """
    jobs, _ = await _async_load_cron_jobs()
    habit_jobs = [j for j in jobs if j.get("name", "").startswith(_HABIT_NAME_PREFIX)]

    # Count habits completed today (last_run is today)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    completed_today = sum(
        1
        for j in habit_jobs
        if (j.get("last_run") or "").startswith(today)
    )
    total_habits = len(habit_jobs)
    streak = completed_today  # simple proxy for streak_days

    return _ok({
        "date": today,
        "steps": 0,                   # wearable integration not yet available
        "active_minutes": completed_today * 20,  # rough proxy
        "streak_days": streak,
        "heart_rate_zone": None,
        "habits_total": total_habits,
        "habits_done_today": completed_today,
    })


# ── Tasks + Habits ─────────────────────────────────────────────────────────────

async def handle_deck_apps_tasks_get(request: "web.Request") -> "web.Response":
    """
    Return TaskHub: { tasks: TaskItem[], habits: HabitItem[] }
    """
    try:
        import base64

        tasks = await _async_load_tasks()
        jobs, _ = await _async_load_cron_jobs()
        habit_jobs = [j for j in jobs if j.get("name", "").startswith(_HABIT_NAME_PREFIX)]

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        habits = []
        for j in habit_jobs:
            name_raw = j.get("name", "")[len(_HABIT_NAME_PREFIX):]
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
            # Streak: count consecutive days in last_runs history (not available in schema;
            # use active status as a proxy)
            habits.append({
                "id": j.get("id", name_raw),
                "title": label,
                "streak": 0,
                "completed_today": completed_today,
                "active": j.get("status", "active") == "active",
                "cron": j.get("schedule", ""),
            })

        return _ok({"tasks": tasks, "habits": habits})
    except Exception as exc:
        logger.exception("apps/tasks failed")
        return _err(str(exc))


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


async def handle_deck_apps_habits_toggle(request: "web.Request") -> "web.Response":
    """POST { id: str } → marks habit as completed today by touching last_run."""
    try:
        body = await request.json()
    except Exception:
        return _err("Invalid JSON")

    habit_id = str(body.get("id") or "")

    jobs, counter = await _async_load_cron_jobs()
    matched = False
    for j in jobs:
        jid = str(j.get("id", ""))
        jname = j.get("name", "")[len(_HABIT_NAME_PREFIX):]
        if jid == habit_id or jname == habit_id:
            j["last_run"] = _utcnow()
            matched = True
            break

    if not matched:
        return _err("Habit not found", 404)

    await _async_save_cron_jobs(jobs, counter)
    return _ok({"ok": True, "id": habit_id})


# ── Reminders ─────────────────────────────────────────────────────────────────

async def handle_deck_apps_reminders_get(request: "web.Request") -> "web.Response":
    """
    GET active (upcoming) reminders.
    Uses user_id 0 (system/global) since the Deck is a personal dashboard.
    Returns all non-completed future reminders.
    """
    store = _get_runtime_store()
    if store is None:
        return _ok([])

    try:
        # user_id=0 is the fallback for reminders created without a Telegram user context
        # We return all non-completed upcoming reminders across all users for the Deck
        rows = store._read_all(  # type: ignore[attr-defined]
            "SELECT id, user_id, chat_id, message, remind_at, created_at "
            "FROM reminders WHERE completed = 0 AND remind_at > ? ORDER BY remind_at LIMIT 50",
            (datetime.now(timezone.utc).isoformat(),),
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
        return _ok([])


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
            fire_at = datetime.fromisoformat(fire_at_str.rstrip("Z")).replace(tzinfo=timezone.utc)
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

    try:
        rid = store.create_reminder(
            user_id=0,
            chat_id=0,
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
    """Read the active space name from config. Silent-fail."""
    try:
        from navig.config import get_config_manager  # type: ignore[import]
        cfg = get_config_manager()
        return cfg.get("spaces.active") or cfg.get("space") or None
    except Exception:
        return None


def _get_spaces_dir() -> Path:
    return _navig_dir() / "spaces"


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

                vision_text = _read_file_content(space_dir / "VISION.md", 512)
                vision_headline = ""
                for line in vision_text.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        vision_headline = line[:120]
                        break
                    if line.startswith("# "):
                        vision_headline = line[2:].strip()[:120]
                        break

                phase_text = _read_file_content(space_dir / "CURRENT_PHASE.md", 512)
                current_phase = ""
                for line in phase_text.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        current_phase = line[:120]
                        break
                    if line.startswith("# "):
                        current_phase = line[2:].strip()[:120]
                        break

                milestones = _parse_roadmap_milestones(space_dir / "ROADMAP.md")
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

async def handle_deck_apps_goals(request: "web.Request") -> "web.Response":
    """
    Goals overview derived from spaces ROADMAP.md milestone lists.
    Shape: GoalsOverview { goals: [{id, title, progress_pct, milestones}] }
    """
    spaces_dir = _get_spaces_dir()
    if not spaces_dir.is_dir():
        return _ok({"goals": []})

    goals = []
    for space_dir in sorted(spaces_dir.iterdir()):
        if not space_dir.is_dir():
            continue
        milestones = _parse_roadmap_milestones(space_dir / "ROADMAP.md")
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

    roadmap_path = _get_spaces_dir() / goal_id / "ROADMAP.md"
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
    import shutil

    navig_bin = shutil.which("navig") or "navig"

    try:
        proc = await asyncio.create_subprocess_exec(
            navig_bin, "calendar", "list", "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8.0)
        if proc.returncode == 0 and stdout:
            try:
                events = json.loads(stdout.decode("utf-8"))
                return _ok(events)
            except Exception:
                pass
    except Exception as exc:
        logger.debug("calendar list subprocess error: %s", exc)

    return _ok([])


# ── Life dashboard domain providers ────────────────────────────────────────────

async def _life_habits_today(today: str) -> dict:
    """Return habit summary for *today*."""
    jobs, _ = await _async_load_cron_jobs()
    habit_jobs = [j for j in jobs if j.get("name", "").startswith(_HABIT_NAME_PREFIX)]
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
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
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

_WIKI_ROOT = Path.home() / ".navig" / "wiki"
_KNOWLEDGE_LOCK = threading.Lock()


def _build_knowledge_dir() -> Path:
    d = _WIKI_ROOT / "knowledge"
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
        from navig.vault import VaultProvider  # type: ignore[import]
        vault_count = len(VaultProvider().list_keys())
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
        from navig.vault import VaultProvider  # type: ignore[import]
        address = VaultProvider().get("ton_wallet_address") or ""
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

    try:
        from navig.daemon_client import get_daemon_status  # type: ignore[import]
        status = get_daemon_status()
        if status and status.get("running"):
            deploy_health = "healthy"
        recent_activity = [
            {"id": str(i), "label": ev.get("label", "event"), "ts": ev.get("ts", "")}
            for i, ev in enumerate(status.get("recent_events", [])[:5])
        ] if status else []
    except Exception:
        deploy_health = "healthy" if fleet_count > 0 else "warning"

    return _ok({
        "host_status": host_status,
        "deploy_health": deploy_health,
        "recent_activity": recent_activity,
        "fleet_count": fleet_count,
    })
