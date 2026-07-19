"""Schedule — Reminders + Crons + Triggers + Briefing (slice B5: the automations editor).

Reminders are SQLite-backed via `BotStatsStore` (the same store the Telegram
reminder poller uses).

Cron jobs operate on the REAL scheduler engine (`navig.scheduler.CronService`):
the live in-process service when the gateway is running (`request.app["gateway"]`
— the normal case, since these routes are served BY the gateway), else a
detached file-backed instance over the same store (`<config_dir>/scheduler/
cron_jobs.json`). NOTE: the legacy `~/.navig/daemon/cron_jobs.json` file the
habit CLI writes is a DIFFERENT store the scheduler never executes — the old
read-only crons list pointed there and therefore showed nothing while jobs ran.

Triggers ride `navig.commands.triggers.TriggerManager` (`<config_dir>/triggers/
triggers.yaml` + `history.jsonl`) — previously CLI-only, now editable from the
deck/OS automations editor.

Routes:
  GET  /api/deck/schedule/reminders                 list active reminders for user
  POST /api/deck/schedule/reminders                 create reminder
  DEL  /api/deck/schedule/reminders/{id}            cancel reminder
  GET  /api/deck/schedule/crons                     list cron jobs (live engine)
  POST /api/deck/schedule/crons                     create cron job
  GET  /api/deck/schedule/crons/runs                recent runs (?job_id=&limit=)
  POST /api/deck/schedule/crons/{job_id}            update (name/schedule/command/timeout/enabled)
  DEL  /api/deck/schedule/crons/{job_id}            delete
  POST /api/deck/schedule/crons/{job_id}/enable     enable (recalculates next run)
  POST /api/deck/schedule/crons/{job_id}/disable    disable (clears next run)
  POST /api/deck/schedule/crons/{job_id}/run        run now (waits for the result)
  GET  /api/deck/schedule/triggers                  list event triggers
  POST /api/deck/schedule/triggers                  create trigger (single action)
  GET  /api/deck/schedule/triggers/history          fire history (?trigger_id=&limit=)
  POST /api/deck/schedule/triggers/{trigger_id}     update
  DEL  /api/deck/schedule/triggers/{trigger_id}     delete
  POST /api/deck/schedule/triggers/{trigger_id}/enable
  POST /api/deck/schedule/triggers/{trigger_id}/disable
  POST /api/deck/schedule/triggers/{trigger_id}/fire   body {dry_run?: bool}
  GET  /api/deck/schedule/briefing                  text briefing (best-effort)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)


def _ok(data: object, status: int = 200) -> "web.Response":
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 500) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


async def _read_body(request: "web.Request") -> dict[str, Any]:
    try:
        return await request.json()
    except Exception:
        return {}


def _resolve_default_user_chat() -> tuple[int, int]:
    """Best-effort lookup of (user_id, chat_id) from the first allowed Telegram user."""
    try:
        from navig.config import get_config_manager  # type: ignore[import]
        cm = get_config_manager()
        tg_cfg = (cm.global_config or {}).get("telegram", {}) or {}
        users = tg_cfg.get("allowed_users", []) or []
        if users:
            uid = int(users[0])
            return uid, uid
    except Exception:
        pass
    return 0, 0


# ─── Reminders ───────────────────────────────────────────────────────────────


async def handle_deck_reminders_list(request: "web.Request") -> "web.Response":
    user_id_raw = request.query.get("user_id", "")
    user_id, _ = _resolve_default_user_chat()
    if user_id_raw:
        try:
            user_id = int(user_id_raw)
        except ValueError:
            return _err("invalid user_id", status=400)
    try:
        from navig.bot.stats_store import get_bot_store  # type: ignore[import]
        store = get_bot_store()
        rows = store.get_user_reminders(user_id) if user_id else []
        return _ok({
            "user_id": user_id,
            "count": len(rows),
            "reminders": [r.to_dict() for r in rows],
        })
    except Exception as exc:
        logger.exception("reminders list failed")
        return _err(str(exc))


async def handle_deck_reminders_create(request: "web.Request") -> "web.Response":
    body = await _read_body(request)
    message = (body.get("message") or "").strip()
    when_iso = (body.get("remind_at") or "").strip()
    minutes_from_now = body.get("in_minutes")
    if not message:
        return _err("'message' is required", status=400)

    try:
        if when_iso:
            remind_at = datetime.fromisoformat(when_iso.replace("Z", "+00:00"))
            if remind_at.tzinfo is None:
                remind_at = remind_at.replace(tzinfo=timezone.utc)
        elif minutes_from_now:
            remind_at = datetime.now(timezone.utc) + timedelta(minutes=int(minutes_from_now))
        else:
            return _err("either 'remind_at' (ISO) or 'in_minutes' is required", status=400)
    except Exception as exc:
        return _err(f"invalid time: {exc}", status=400)

    user_id_raw = body.get("user_id")
    chat_id_raw = body.get("chat_id")
    user_id, chat_id = _resolve_default_user_chat()
    if user_id_raw is not None:
        user_id = int(user_id_raw)
    if chat_id_raw is not None:
        chat_id = int(chat_id_raw)
    if not user_id or not chat_id:
        return _err("no Telegram allowed_users configured — provide user_id+chat_id", status=400)

    try:
        from navig.bot.stats_store import get_bot_store  # type: ignore[import]
        store = get_bot_store()
        rem = store.create_reminder(user_id, chat_id, message, remind_at)
        return _ok(rem.to_dict(), status=201)
    except Exception as exc:
        logger.exception("reminder create failed")
        return _err(str(exc))


async def handle_deck_reminder_cancel(request: "web.Request") -> "web.Response":
    rid_raw = request.match_info.get("reminder_id", "")
    try:
        rid = int(rid_raw)
    except ValueError:
        return _err("invalid reminder id", status=400)
    user_id, _ = _resolve_default_user_chat()
    user_id_raw = request.query.get("user_id", "")
    if user_id_raw:
        try:
            user_id = int(user_id_raw)
        except ValueError:
            return _err("invalid user_id", status=400)
    try:
        from navig.bot.stats_store import get_bot_store  # type: ignore[import]
        store = get_bot_store()
        ok = store.cancel_reminder(rid, user_id)
        if not ok:
            return _err("reminder not found or not owned by user", status=404)
        return _ok({"cancelled": rid})
    except Exception as exc:
        logger.exception("reminder cancel failed")
        return _err(str(exc))


# ─── Cron jobs (the real scheduler engine — slice B5) ────────────────────────


def _cron_service(request: "web.Request"):
    """The live gateway CronService when available, else a detached instance.

    The live path is the production path (these routes are served by the
    gateway process, and file-level writes would be clobbered by the running
    scheduler's next save). The detached instance operates on the same store
    (`<config_dir>/scheduler/`) for tests and gateway-less contexts.
    """
    gateway = request.app.get("gateway") if hasattr(request, "app") else None
    svc = getattr(gateway, "cron_service", None)
    if svc is not None:
        return svc
    from navig.platform import paths  # noqa: PLC0415 — lazy: not on the help path
    from navig.scheduler.cron_service import CronService  # noqa: PLC0415

    return CronService(gateway, paths.config_dir() / "scheduler")


def _schedule_error(schedule: str) -> str | None:
    """None when *schedule* is honourable, else a human reason to show the user."""
    from navig.scheduler.cron_service import CronParser  # noqa: PLC0415

    ok, reason = CronParser.validate(schedule)
    return None if ok else (reason or f"unparseable schedule: {schedule!r}")


async def handle_deck_crons_list(request: "web.Request") -> "web.Response":
    try:
        cs = _cron_service(request)
        jobs = [j.to_dict() for j in cs.list_jobs()]
        return _ok({"count": len(jobs), "jobs": jobs})
    except Exception as exc:
        logger.exception("crons list failed")
        return _err(str(exc))


async def handle_deck_crons_create(request: "web.Request") -> "web.Response":
    body = await _read_body(request)
    name = str(body.get("name") or "").strip()
    schedule = str(body.get("schedule") or "").strip()
    command = str(body.get("command") or "").strip()
    if not name or not schedule or not command:
        return _err("'name', 'schedule' and 'command' are required", status=400)
    sched_err = _schedule_error(schedule)
    if sched_err:
        return _err(sched_err, status=400)
    timeout_raw = body.get("timeout_seconds")
    try:
        timeout = int(timeout_raw) if timeout_raw is not None else None
        if timeout is not None and timeout <= 0:
            return _err("'timeout_seconds' must be positive", status=400)
    except (TypeError, ValueError):
        return _err("invalid 'timeout_seconds'", status=400)
    try:
        cs = _cron_service(request)
        job = cs.add_job(
            name=name,
            schedule=schedule,
            command=command,
            enabled=bool(body.get("enabled", True)),
            timeout_seconds=timeout,
        )
        return _ok(job.to_dict(), status=201)
    except Exception as exc:
        logger.exception("cron create failed")
        return _err(str(exc))


async def handle_deck_cron_update(request: "web.Request") -> "web.Response":
    job_id = request.match_info.get("job_id", "")
    body = await _read_body(request)

    updates: dict[str, Any] = {}
    if "name" in body:
        name = str(body.get("name") or "").strip()
        if not name:
            return _err("'name' cannot be empty", status=400)
        updates["name"] = name
    if "schedule" in body:
        schedule = str(body.get("schedule") or "").strip()
        sched_err = _schedule_error(schedule)
        if sched_err:
            return _err(sched_err, status=400)
        updates["schedule"] = schedule
    if "command" in body:
        command = str(body.get("command") or "").strip()
        if not command:
            return _err("'command' cannot be empty", status=400)
        updates["command"] = command
    if "timeout_seconds" in body:
        try:
            timeout = int(body["timeout_seconds"])
        except (TypeError, ValueError):
            return _err("invalid 'timeout_seconds'", status=400)
        if timeout <= 0:
            return _err("'timeout_seconds' must be positive", status=400)
        updates["timeout_seconds"] = timeout

    enabled = body.get("enabled")
    if not updates and enabled is None:
        return _err("nothing to update", status=400)

    try:
        cs = _cron_service(request)
        if updates:
            job = cs.update_job(job_id, **updates)
            if job is None:
                return _err("job not found", status=404)
        # enable/disable via the dedicated verbs so next_run stays consistent
        # (enable recalculates it, disable clears it).
        if enabled is True and not cs.enable_job(job_id):
            return _err("job not found", status=404)
        if enabled is False and not cs.disable_job(job_id):
            return _err("job not found", status=404)
        job = cs.get_job(job_id)
        if job is None:
            return _err("job not found", status=404)
        return _ok(job.to_dict())
    except Exception as exc:
        logger.exception("cron update failed")
        return _err(str(exc))


async def handle_deck_cron_delete(request: "web.Request") -> "web.Response":
    job_id = request.match_info.get("job_id", "")
    try:
        cs = _cron_service(request)
        if not cs.remove_job(job_id):
            return _err("job not found", status=404)
        return _ok({"deleted": job_id})
    except Exception as exc:
        logger.exception("cron delete failed")
        return _err(str(exc))


def _cron_toggle_handler(enable: bool):
    async def handler(request: "web.Request") -> "web.Response":
        job_id = request.match_info.get("job_id", "")
        try:
            cs = _cron_service(request)
            ok = cs.enable_job(job_id) if enable else cs.disable_job(job_id)
            if not ok:
                return _err("job not found", status=404)
            job = cs.get_job(job_id)
            return _ok(job.to_dict() if job else {"enabled": enable})
        except Exception as exc:
            logger.exception("cron toggle failed")
            return _err(str(exc))

    return handler


handle_deck_cron_enable = _cron_toggle_handler(enable=True)
handle_deck_cron_disable = _cron_toggle_handler(enable=False)


async def handle_deck_cron_run(request: "web.Request") -> "web.Response":
    job_id = request.match_info.get("job_id", "")
    try:
        cs = _cron_service(request)
        result = await cs.run_job_now(job_id)
        if result is None:
            return _err("job not found", status=404)
        return _ok(result)
    except Exception as exc:
        logger.exception("cron run-now failed")
        return _err(str(exc))


async def handle_deck_cron_runs(request: "web.Request") -> "web.Response":
    job_id = request.query.get("job_id") or None
    try:
        limit = max(1, min(int(request.query.get("limit", "50")), 500))
    except ValueError:
        return _err("invalid limit", status=400)
    try:
        cs = _cron_service(request)
        runs = cs.get_runs(job_id=job_id, limit=limit)
        return _ok({"count": len(runs), "runs": runs})
    except Exception as exc:
        logger.exception("cron runs failed")
        return _err(str(exc))


# ─── Event triggers (TriggerManager — slice B5) ──────────────────────────────

_TRIGGER_TYPES = frozenset(
    {"health", "schedule", "threshold", "webhook", "file", "command", "manual", "calendar", "email"}
)
_TRIGGER_ACTION_TYPES = frozenset({"command", "workflow", "notify", "webhook", "script"})


def _trigger_manager():
    """A TriggerManager over `<config_dir>/triggers/` (env-respecting).

    Passes a minimal config shim instead of the global ConfigManager singleton:
    `global_config_dir` is the only attribute TriggerManager reads, and the shim
    keeps `NAVIG_CONFIG_DIR` overrides (tests) authoritative even when the
    singleton was created earlier with a different environment.
    """
    from types import SimpleNamespace  # noqa: PLC0415

    from navig.commands.triggers import TriggerManager  # noqa: PLC0415
    from navig.platform import paths  # noqa: PLC0415

    return TriggerManager(config_manager=SimpleNamespace(global_config_dir=paths.config_dir()))


def _build_trigger_action(body: dict[str, Any]):
    from navig.commands.triggers import ActionType, TriggerAction  # noqa: PLC0415

    action_type = str(body.get("action_type") or "command").strip().lower()
    if action_type not in _TRIGGER_ACTION_TYPES:
        raise ValueError(
            f"invalid action_type: {action_type!r} (one of {', '.join(sorted(_TRIGGER_ACTION_TYPES))})"
        )
    target = str(body.get("action_target") or "").strip()
    if not target:
        raise ValueError("'action_target' is required")
    return TriggerAction(type=ActionType(action_type), target=target)


async def handle_deck_triggers_list(request: "web.Request") -> "web.Response":
    try:
        mgr = _trigger_manager()
        triggers = [t.to_dict() for t in mgr.list_triggers()]
        return _ok({"count": len(triggers), "triggers": triggers})
    except Exception as exc:
        logger.exception("triggers list failed")
        return _err(str(exc))


async def handle_deck_triggers_create(request: "web.Request") -> "web.Response":
    body = await _read_body(request)
    name = str(body.get("name") or "").strip()
    if not name:
        return _err("'name' is required", status=400)
    trigger_type = str(body.get("type") or "manual").strip().lower()
    if trigger_type not in _TRIGGER_TYPES:
        return _err(
            f"invalid type: {trigger_type!r} (one of {', '.join(sorted(_TRIGGER_TYPES))})",
            status=400,
        )
    schedule = str(body.get("schedule") or "").strip()
    if trigger_type == "schedule" and schedule:
        sched_err = _schedule_error(schedule)
        if sched_err:
            return _err(sched_err, status=400)
    try:
        action = _build_trigger_action(body)
    except ValueError as exc:
        return _err(str(exc), status=400)

    try:
        from navig.commands.triggers import Trigger, TriggerType  # noqa: PLC0415

        trigger = Trigger(
            id="",
            name=name,
            type=TriggerType(trigger_type),
            description=str(body.get("description") or ""),
            actions=[action],
            schedule=schedule,
            cooldown_seconds=int(body.get("cooldown_seconds", 60)),
            max_fires_per_hour=int(body.get("max_fires_per_hour", 10)),
        )
        mgr = _trigger_manager()
        if not mgr.add_trigger(trigger):
            return _err(f"trigger '{trigger.id}' already exists", status=409)
        return _ok(trigger.to_dict(), status=201)
    except (TypeError, ValueError) as exc:
        return _err(str(exc), status=400)
    except Exception as exc:
        logger.exception("trigger create failed")
        return _err(str(exc))


async def handle_deck_trigger_update(request: "web.Request") -> "web.Response":
    """Update a trigger. When action fields are present, the (single) action is
    replaced — the editor operates on single-action triggers; multi-action
    triggers keep their extra actions only when no action field is sent."""
    trigger_id = request.match_info.get("trigger_id", "")
    body = await _read_body(request)
    try:
        mgr = _trigger_manager()
        trigger = mgr.get_trigger(trigger_id)
        if trigger is None:
            return _err("trigger not found", status=404)

        if "name" in body:
            name = str(body.get("name") or "").strip()
            if not name:
                return _err("'name' cannot be empty", status=400)
            trigger.name = name
        if "description" in body:
            trigger.description = str(body.get("description") or "")
        if "type" in body:
            from navig.commands.triggers import TriggerType  # noqa: PLC0415

            trigger_type = str(body.get("type") or "").strip().lower()
            if trigger_type not in _TRIGGER_TYPES:
                return _err(f"invalid type: {trigger_type!r}", status=400)
            trigger.type = TriggerType(trigger_type)
        if "schedule" in body:
            schedule = str(body.get("schedule") or "").strip()
            if schedule:
                sched_err = _schedule_error(schedule)
                if sched_err:
                    return _err(sched_err, status=400)
            trigger.schedule = schedule
        if "cooldown_seconds" in body:
            trigger.cooldown_seconds = int(body["cooldown_seconds"])
        if "max_fires_per_hour" in body:
            trigger.max_fires_per_hour = int(body["max_fires_per_hour"])
        if "action_target" in body or "action_type" in body:
            merged = {
                "action_type": body.get("action_type")
                or (trigger.actions[0].type.value if trigger.actions else "command"),
                "action_target": body.get("action_target")
                or (trigger.actions[0].target if trigger.actions else ""),
            }
            trigger.actions = [_build_trigger_action(merged)]

        if not mgr.update_trigger(trigger):
            return _err("trigger not found", status=404)
        return _ok(trigger.to_dict())
    except (TypeError, ValueError) as exc:
        return _err(str(exc), status=400)
    except Exception as exc:
        logger.exception("trigger update failed")
        return _err(str(exc))


async def handle_deck_trigger_delete(request: "web.Request") -> "web.Response":
    trigger_id = request.match_info.get("trigger_id", "")
    try:
        mgr = _trigger_manager()
        if not mgr.remove_trigger(trigger_id):
            return _err("trigger not found", status=404)
        return _ok({"deleted": trigger_id})
    except Exception as exc:
        logger.exception("trigger delete failed")
        return _err(str(exc))


def _trigger_toggle_handler(enable: bool):
    async def handler(request: "web.Request") -> "web.Response":
        trigger_id = request.match_info.get("trigger_id", "")
        try:
            mgr = _trigger_manager()
            ok = mgr.enable_trigger(trigger_id) if enable else mgr.disable_trigger(trigger_id)
            if not ok:
                return _err("trigger not found", status=404)
            trigger = mgr.get_trigger(trigger_id)
            return _ok(trigger.to_dict() if trigger else {"enabled": enable})
        except Exception as exc:
            logger.exception("trigger toggle failed")
            return _err(str(exc))

    return handler


handle_deck_trigger_enable = _trigger_toggle_handler(enable=True)
handle_deck_trigger_disable = _trigger_toggle_handler(enable=False)


async def handle_deck_trigger_fire(request: "web.Request") -> "web.Response":
    """Fire (or dry-run) a trigger. Actions may spawn subprocesses, so the
    synchronous TriggerManager work runs in the default executor."""
    import asyncio  # noqa: PLC0415

    trigger_id = request.match_info.get("trigger_id", "")
    body = await _read_body(request)
    dry_run = bool(body.get("dry_run", False))
    try:
        mgr = _trigger_manager()
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: mgr.fire_trigger(trigger_id, dry_run=dry_run)
        )
        if result is None:
            return _err("trigger not found", status=404)
        return _ok({
            "trigger_id": result.trigger_id,
            "success": result.success,
            "dry_run": dry_run,
            "actions_run": result.actions_run,
            "actions_succeeded": result.actions_succeeded,
            "actions_failed": result.actions_failed,
            "message": result.message,
            "duration_ms": result.duration_ms,
            "timestamp": result.timestamp,
        })
    except Exception as exc:
        logger.exception("trigger fire failed")
        return _err(str(exc))


async def handle_deck_triggers_history(request: "web.Request") -> "web.Response":
    import asyncio  # noqa: PLC0415

    trigger_id = request.query.get("trigger_id") or None
    try:
        limit = max(1, min(int(request.query.get("limit", "50")), 500))
    except ValueError:
        return _err("invalid limit", status=400)
    try:
        mgr = _trigger_manager()
        loop = asyncio.get_event_loop()
        history = await loop.run_in_executor(
            None, lambda: mgr.get_history(trigger_id=trigger_id, limit=limit)
        )
        return _ok({"count": len(history), "history": history})
    except Exception as exc:
        logger.exception("trigger history failed")
        return _err(str(exc))


# ─── Briefing (best-effort) ─────────────────────────────────────────────────


async def handle_deck_briefing(request: "web.Request") -> "web.Response":
    """Try several places that already compose a daily briefing — return first hit."""
    # 1. compose_briefing in bizops (if it exists)
    try:
        from navig.bizops.briefing import compose_briefing  # type: ignore[import]
        text = compose_briefing()
        if text:
            return _ok({"source": "bizops", "text": str(text),
                         "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    except Exception:
        pass
    # 2. life dashboard briefing
    try:
        from navig.commands.life_dashboard import build_dashboard  # type: ignore[import]
        d = build_dashboard()
        if isinstance(d, str) and d:
            return _ok({"source": "life_dashboard", "text": d,
                         "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    except Exception:
        pass
    return _ok({"source": "none", "text": "", "note": "No briefing source available yet.",
                 "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")})
