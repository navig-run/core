"""Schedule — Reminders + Crons + (placeholders for) Plans / Intake / Briefing.

Reminders are SQLite-backed via `BotStatsStore` (the same store the Telegram
reminder poller uses). Cron jobs read `~/.navig/daemon/cron_jobs.json` — the
single source of truth shared with the running CronService and the habit CLI.

Routes:
  GET  /api/deck/schedule/reminders        list active reminders for user
  POST /api/deck/schedule/reminders        create reminder
  DEL  /api/deck/schedule/reminders/{id}   cancel reminder
  GET  /api/deck/schedule/crons            list cron jobs
  GET  /api/deck/schedule/briefing         text briefing (best-effort)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
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


# ─── Cron jobs ───────────────────────────────────────────────────────────────


def _cron_jobs_path() -> Path:
    return Path.home() / ".navig" / "daemon" / "cron_jobs.json"


async def handle_deck_crons_list(request: "web.Request") -> "web.Response":
    p = _cron_jobs_path()
    if not p.exists():
        return _ok({"count": 0, "jobs": []})
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        jobs = data.get("jobs") or []
        return _ok({"count": len(jobs), "jobs": jobs})
    except Exception as exc:
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
