"""Telegram Manager (MTProto user-client) — deck API.

This is the deck/HTTP face of the *owner's own* Telegram account engine in
``navig.telegram`` (Telethon / MTProto). It wraps the already-built async engine
so the deck "✈️ Telegram" file-manager UI can:

  • log the owner's account in (phone → code → optional 2FA)
  • list/filter every dialog (channels / groups / DMs / forums) + forum topics
  • backfill history into the catalog (one chat or everything) with live progress
  • search across everything backfilled (catalog FTS)
  • organize: forward / move (copy+delete) / rename / delete / links / dedupe
  • drive the Business rights matrix (per-tool owner|both|off + master + alerts)

SECURITY: every route here lives under ``/api/deck/telegram/*`` which is already
behind the deck auth middleware (owner / loopback only) — that IS the owner gate.
We do NOT add a second gate. Destructive ops stay behind an explicit ``confirm``
body flag (passed straight through to the engine; default ``False`` = dry-run, so
the owner always previews before anything mutates).

The MTProto engine needs the optional ``telethon`` dependency. Every handler
checks :func:`navig.telegram.telethon_available` first and degrades to a clean
503 with the exact CLI hint when it is absent. Engine calls are awaited directly
(the engine is async) and wrapped in try/except → ``_err``.

Routes (all under /api/deck/telegram):
  GET    /user/status                  {telethon, api_creds, logged_in, me}
  POST   /user/login                   {phone} → request_code
  POST   /user/confirm                 {code, password?} → confirm_code
  POST   /user/logout
  GET    /user/dialogs                 ?kind= → list_dialogs
  GET    /user/topics/{chat}           list_topics
  POST   /user/history/sync            {chat?|all, limit?} (+ SSE progress)
  GET    /user/search                  ?q= ?chat= ?limit=
  POST   /user/forward                 {from, ids[], to, copy?}
  POST   /user/move                    {from, ids[], to, confirm?}
  POST   /user/rename                  {chat, title, confirm?}
  POST   /user/delete                  {chat, ids[], confirm?}
  GET    /user/links/{chat}            link index
  POST   /user/dedupe                  {chat, confirm?}
  GET    /business                     rights matrix + state + emoji legend
  POST   /business/enable             {enabled}
  POST   /business/rights             {tool, who}
  POST   /business/alerts             {on}
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from aiohttp import web
except ImportError:  # pragma: no cover - aiohttp always present at runtime
    web = None

logger = logging.getLogger(__name__)


# ─── Envelope helpers ─────────────────────────────────────────────────────────


def _ok(data: object, status: int = 200) -> "web.Response":
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 500) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


async def _body(request: "web.Request") -> dict[str, Any]:
    try:
        return await request.json()
    except Exception:  # noqa: BLE001 — empty / malformed body → {}
        return {}


def _truthy(v: Any) -> bool:
    return str(v).lower() in ("1", "true", "yes", "on")


def _require_telethon() -> "web.Response | None":
    """Return a 503 response when telethon is missing, else None."""
    import navig.telegram as tg

    if not tg.telethon_available():
        return _err(
            "telethon not installed — run: pip install 'navig-core[telegram]'",
            status=503,
        )
    return None


def _ids(raw: Any) -> list[int]:
    """Coerce a body field (list / csv-string / scalar) into ``[int, …]``."""
    if raw is None:
        return []
    if isinstance(raw, (int, float)):
        return [int(raw)]
    if isinstance(raw, str):
        return [int(x) for x in raw.replace(",", " ").split() if x.strip()]
    if isinstance(raw, (list, tuple)):
        out: list[int] = []
        for x in raw:
            try:
                out.append(int(x))
            except (TypeError, ValueError):
                continue
        return out
    return []


def _emit(request: "web.Request", kind: str, payload: dict) -> None:
    """Schedule a best-effort SSE broadcast (fire-and-forget).

    Never raises — telemetry must not fail a live operation. Uses the gateway's
    :class:`SystemEventQueue` (``gateway.system_events``); falls back to the
    process-global queue when the gateway ref is unavailable.
    """
    import asyncio

    async def _send() -> None:
        try:
            gw = request.app.get("gateway") if hasattr(request, "app") else None
            queue = getattr(gw, "system_events", None) if gw is not None else None
            if queue is None:
                from navig.gateway.system_events import get_system_events

                queue = get_system_events()
            if queue is not None and hasattr(queue, "emit"):
                await queue.emit(kind, payload)
        except Exception as exc:  # noqa: BLE001
            logger.debug("telegram mtproto SSE emit failed: %s", exc)

    try:
        asyncio.get_running_loop().create_task(_send())
    except RuntimeError:  # no running loop — drop silently
        pass


# ─── User account: status / login / logout ────────────────────────────────────


async def handle_user_status(request: "web.Request") -> "web.Response":
    """Account snapshot: telethon present, creds set, logged in, and who."""
    try:
        import navig.telegram as tg
        from navig.telegram import config, user_client

        have_telethon = tg.telethon_available()
        creds = bool(config.have_api_credentials())
        logged_in = bool(config.is_logged_in())
        me = None
        if have_telethon and logged_in:
            try:
                me = await user_client.whoami()
            except Exception as exc:  # noqa: BLE001 — session may be invalid
                logger.debug("telegram whoami failed: %s", exc)
                me = None
        return _ok({
            "telethon": have_telethon,
            "api_creds": creds,
            "logged_in": logged_in,
            "me": me,
        })
    except Exception as exc:  # noqa: BLE001
        logger.exception("telegram user status failed")
        return _err(str(exc))


async def handle_user_login(request: "web.Request") -> "web.Response":
    """Step 1 of login: send a login code to the owner's Telegram app."""
    guard = _require_telethon()
    if guard is not None:
        return guard
    body = await _body(request)
    phone = (body.get("phone") or "").strip()
    if not phone:
        return _err("'phone' is required", status=400)
    try:
        from navig.telegram import auth

        status = await auth.request_code(phone)
        return _ok({"status": status})
    except Exception as exc:  # noqa: BLE001
        logger.exception("telegram login (request_code) failed")
        return _err(str(exc), status=503)


async def handle_user_confirm(request: "web.Request") -> "web.Response":
    """Step 2 of login: complete sign-in with the code (+ 2FA password if needed)."""
    guard = _require_telethon()
    if guard is not None:
        return guard
    body = await _body(request)
    code = str(body.get("code") or "").strip()
    password = body.get("password") or None
    if not code:
        return _err("'code' is required", status=400)
    try:
        from navig.telegram import auth

        result = await auth.confirm_code(code, password=password)
        return _ok(result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("telegram confirm failed")
        return _err(str(exc), status=503)


async def handle_user_logout(request: "web.Request") -> "web.Response":
    """Forget the stored session (+ any in-progress login)."""
    try:
        from navig.telegram import auth

        ret = auth.logout()
        if hasattr(ret, "__await__"):
            await ret  # type: ignore[func-returns-value]
        return _ok({"logged_out": True})
    except Exception as exc:  # noqa: BLE001
        logger.exception("telegram logout failed")
        return _err(str(exc))


# ─── Inventory: dialogs + topics ──────────────────────────────────────────────


async def handle_user_dialogs(request: "web.Request") -> "web.Response":
    """List every dialog with metadata. ``?kind=`` filters (csv allowed)."""
    guard = _require_telethon()
    if guard is not None:
        return guard
    raw_kind = request.query.get("kind") or ""
    kinds = [k.strip() for k in raw_kind.replace(",", " ").split() if k.strip()] or None
    try:
        from navig.telegram import dialogs

        rows = await dialogs.list_dialogs(kinds=kinds)
        return _ok({"dialogs": rows, "count": len(rows)})
    except Exception as exc:  # noqa: BLE001
        logger.exception("telegram dialogs failed")
        return _err(str(exc), status=503)


async def handle_user_topics(request: "web.Request") -> "web.Response":
    """List forum topics for a forum supergroup."""
    guard = _require_telethon()
    if guard is not None:
        return guard
    chat = request.match_info.get("chat")
    if not chat:
        return _err("chat is required", status=400)
    try:
        from navig.telegram import dialogs

        rows = await dialogs.list_topics(_chat_ref(chat))
        return _ok({"topics": rows, "count": len(rows)})
    except Exception as exc:  # noqa: BLE001
        logger.exception("telegram topics failed")
        return _err(str(exc), status=503)


def _chat_ref(value: Any) -> Any:
    """Pass numeric chat ids through as ints; leave @usernames / links as strings."""
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    if s.lstrip("-").isdigit():
        try:
            return int(s)
        except ValueError:
            return s
    return s


# ─── History backfill (+ live SSE progress) ───────────────────────────────────


async def handle_user_history_sync(request: "web.Request") -> "web.Response":
    """Backfill history into the catalog. ``{all: true}`` syncs every dialog;
    otherwise ``{chat}`` syncs one. Emits ``telegram_history_progress`` SSE events
    as it runs (throttled to ~every 200 messages)."""
    guard = _require_telethon()
    if guard is not None:
        return guard
    body = await _body(request)
    do_all = _truthy(body.get("all", ""))
    chat = body.get("chat")
    limit = body.get("limit")
    try:
        limit = int(limit) if limit not in (None, "") else None
    except (TypeError, ValueError):
        limit = None
    if not do_all and not chat:
        return _err("provide 'chat' or set 'all': true", status=400)

    # Throttle SSE emits: at most one per ~200 messages, keyed by current chat.
    state: dict[str, Any] = {"last_emit": 0, "chat": str(chat) if chat else "all"}

    def _progress(n: int, kind: str) -> None:
        if n - state["last_emit"] >= 200:
            state["last_emit"] = n
            _emit(request, "telegram_history_progress", {
                "chat": state["chat"], "messages": n, "kind": kind,
            })

    try:
        from navig.telegram import history

        if do_all:
            result = await history.sync_all(limit_per_chat=limit, progress=_progress)
        else:
            result = await history.sync_chat(_chat_ref(chat), limit=limit, progress=_progress)
        # Final completion frame so the UI snaps the bar to 100%.
        _emit(request, "telegram_history_progress", {
            "chat": state["chat"],
            "messages": result.get("messages", state["last_emit"]),
            "media": result.get("media"),
            "done": True,
        })
        _emit(request, "telegram_catalog_update", {"source": "history_sync"})
        return _ok(result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("telegram history sync failed")
        return _err(str(exc), status=503)


# ─── Search ───────────────────────────────────────────────────────────────────


async def handle_user_search(request: "web.Request") -> "web.Response":
    """Full-text search the catalog (everything backfilled). Synchronous engine."""
    q = (request.query.get("q") or "").strip()
    if not q:
        return _ok({"results": [], "count": 0})
    chat = request.query.get("chat")
    try:
        limit = int(request.query.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    chat_id = None
    if chat:
        try:
            chat_id = int(chat)
        except (TypeError, ValueError):
            chat_id = None
    try:
        from navig.telegram import search as tg_search

        rows = tg_search.search(q, chat_id=chat_id, limit=limit)
        return _ok({"results": rows, "count": len(rows)})
    except Exception as exc:  # noqa: BLE001
        logger.exception("telegram search failed")
        return _err(str(exc))


# ─── Organize: forward / move / rename / delete / links / dedupe ──────────────


async def handle_user_forward(request: "web.Request") -> "web.Response":
    """Forward (or copy, with ``copy: true``) messages to another chat."""
    guard = _require_telethon()
    if guard is not None:
        return guard
    body = await _body(request)
    frm = body.get("from")
    to = body.get("to")
    ids = _ids(body.get("ids"))
    if frm is None or to is None or not ids:
        return _err("'from', 'to' and non-empty 'ids' are required", status=400)
    copy = _truthy(body.get("copy", ""))
    try:
        from navig.telegram import organize

        result = await organize.forward(
            _chat_ref(frm), ids, _chat_ref(to), drop_author=copy
        )
        _emit(request, "telegram_catalog_update", {"source": "forward"})
        return _ok(result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("telegram forward failed")
        return _err(str(exc), status=503)


async def handle_user_move(request: "web.Request") -> "web.Response":
    """Move = copy then delete originals. Destructive — confirm-gated (dry-run default)."""
    guard = _require_telethon()
    if guard is not None:
        return guard
    body = await _body(request)
    frm = body.get("from")
    to = body.get("to")
    ids = _ids(body.get("ids"))
    if frm is None or to is None or not ids:
        return _err("'from', 'to' and non-empty 'ids' are required", status=400)
    confirm = _truthy(body.get("confirm", ""))
    try:
        from navig.telegram import organize

        result = await organize.move(
            _chat_ref(frm), ids, _chat_ref(to), confirm=confirm
        )
        if confirm:
            _emit(request, "telegram_catalog_update", {"source": "move"})
        return _ok(result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("telegram move failed")
        return _err(str(exc), status=503)


async def handle_user_rename(request: "web.Request") -> "web.Response":
    """Rename a chat/channel title (admin rights). Confirm-gated (dry-run default)."""
    guard = _require_telethon()
    if guard is not None:
        return guard
    body = await _body(request)
    chat = body.get("chat")
    title = (body.get("title") or "").strip()
    if chat is None or not title:
        return _err("'chat' and 'title' are required", status=400)
    confirm = _truthy(body.get("confirm", ""))
    try:
        from navig.telegram import organize

        result = await organize.rename(_chat_ref(chat), title, confirm=confirm)
        if confirm:
            _emit(request, "telegram_catalog_update", {"source": "rename"})
        return _ok(result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("telegram rename failed")
        return _err(str(exc), status=503)


async def handle_user_delete(request: "web.Request") -> "web.Response":
    """Delete messages (revoke for all). Confirm-gated (dry-run default)."""
    guard = _require_telethon()
    if guard is not None:
        return guard
    body = await _body(request)
    chat = body.get("chat")
    ids = _ids(body.get("ids"))
    if chat is None or not ids:
        return _err("'chat' and non-empty 'ids' are required", status=400)
    confirm = _truthy(body.get("confirm", ""))
    try:
        from navig.telegram import organize

        result = await organize.delete_messages(_chat_ref(chat), ids, confirm=confirm)
        if confirm:
            _emit(request, "telegram_catalog_update", {"source": "delete"})
        return _ok(result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("telegram delete failed")
        return _err(str(exc), status=503)


async def handle_user_links(request: "web.Request") -> "web.Response":
    """Scan a chat's recent messages → a deduped link index (tiktok/youtube/url)."""
    guard = _require_telethon()
    if guard is not None:
        return guard
    chat = request.match_info.get("chat")
    if not chat:
        return _err("chat is required", status=400)
    try:
        limit = int(request.query.get("limit", 500))
    except (TypeError, ValueError):
        limit = 500
    try:
        from navig.telegram import organize

        result = await organize.links(_chat_ref(chat), limit=limit)
        return _ok(result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("telegram links failed")
        return _err(str(exc), status=503)


async def handle_user_dedupe(request: "web.Request") -> "web.Response":
    """Live-scan a chat for duplicate media → classify. With ``confirm: true`` also
    deletes the SAFE set (EXACT + INCOMING extras only). Destructive — dry-run default."""
    guard = _require_telethon()
    if guard is not None:
        return guard
    body = await _body(request)
    chat = body.get("chat")
    if chat is None:
        return _err("'chat' is required", status=400)
    confirm = _truthy(body.get("confirm", ""))
    limit = body.get("limit")
    try:
        limit = int(limit) if limit not in (None, "") else None
    except (TypeError, ValueError):
        limit = None
    try:
        from navig.telegram import dedupe, history, organize

        records = await history.collect_dedupe_records(_chat_ref(chat), limit=limit)
        report = dedupe.find_duplicates(records)
        deleted = 0
        if confirm and report.get("safe_delete"):
            # safe_delete is [{chat_id, message_id}] — group by chat then delete.
            by_chat: dict[int, list[int]] = {}
            for row in report["safe_delete"]:
                cid = row.get("chat_id")
                mid = row.get("message_id")
                if cid is None or mid is None:
                    continue
                by_chat.setdefault(int(cid), []).append(int(mid))
            for cid, mids in by_chat.items():
                await organize.delete_messages(cid, mids, confirm=True)
                deleted += len(mids)
            _emit(request, "telegram_catalog_update", {"source": "dedupe"})
        return _ok({**report, "deleted": deleted, "confirmed": confirm})
    except Exception as exc:  # noqa: BLE001
        logger.exception("telegram dedupe failed")
        return _err(str(exc), status=503)


# ─── Business rights matrix ───────────────────────────────────────────────────


async def handle_business_status(request: "web.Request") -> "web.Response":
    """Business catcher state: master on/off, deletion alert, arming block reason,
    per-tool policies, and the emoji→tool legend. No telethon required (config-only)."""
    try:
        from navig.telegram import ai_actions, business, permissions

        return _ok({
            "enabled": permissions.business_enabled(),
            "deletion_alert": business.deletion_alert_enabled(),
            "blocked": permissions.arming_blocked_reason(),
            "policies": permissions.all_policies(),
            "tools": list(permissions.BUSINESS_TOOLS),
            "emoji": ai_actions.EMOJI_TOOLS,
        })
    except Exception as exc:  # noqa: BLE001
        logger.exception("telegram business status failed")
        return _err(str(exc))


async def handle_business_enable(request: "web.Request") -> "web.Response":
    """Master enable/disable for the Business catcher (respects arming guard)."""
    body = await _body(request)
    if "enabled" not in body:
        return _err("'enabled' is required", status=400)
    enabled = _truthy(body.get("enabled"))
    try:
        from navig.telegram import permissions

        if enabled:
            reason = permissions.arming_blocked_reason()
            if reason:
                return _err(reason, status=412)
        permissions.set_business_enabled(enabled)
        return _ok({"enabled": permissions.business_enabled()})
    except Exception as exc:  # noqa: BLE001
        logger.exception("telegram business enable failed")
        return _err(str(exc))


async def handle_business_rights(request: "web.Request") -> "web.Response":
    """Set a per-tool policy: ``{tool, who}`` where who ∈ owner|both|off."""
    body = await _body(request)
    tool = (body.get("tool") or "").strip()
    who = (body.get("who") or "").strip()
    if not tool or not who:
        return _err("'tool' and 'who' are required", status=400)
    try:
        from navig.telegram import permissions

        permissions.set_tool_policy(tool, who)
        return _ok({"tool": tool, "who": permissions.tool_policy(tool),
                    "policies": permissions.all_policies()})
    except ValueError as exc:
        return _err(str(exc), status=400)
    except Exception as exc:  # noqa: BLE001
        logger.exception("telegram business rights failed")
        return _err(str(exc))


async def handle_business_alerts(request: "web.Request") -> "web.Response":
    """Toggle the deleted-message → DM-you alert. ``{on: bool}``."""
    body = await _body(request)
    if "on" not in body:
        return _err("'on' is required", status=400)
    on = _truthy(body.get("on"))
    try:
        from navig.telegram import business

        business.set_deletion_alert(on)
        return _ok({"deletion_alert": business.deletion_alert_enabled()})
    except Exception as exc:  # noqa: BLE001
        logger.exception("telegram business alerts failed")
        return _err(str(exc))


# ─── Registration ─────────────────────────────────────────────────────────────


def register(app: "web.Application") -> None:
    """Attach the Telegram-Manager (MTProto) routes to the deck app."""
    # User account
    app.router.add_get("/api/deck/telegram/user/status", handle_user_status)
    app.router.add_post("/api/deck/telegram/user/login", handle_user_login)
    app.router.add_post("/api/deck/telegram/user/confirm", handle_user_confirm)
    app.router.add_post("/api/deck/telegram/user/logout", handle_user_logout)
    # Inventory
    app.router.add_get("/api/deck/telegram/user/dialogs", handle_user_dialogs)
    app.router.add_get("/api/deck/telegram/user/topics/{chat}", handle_user_topics)
    # History + search
    app.router.add_post("/api/deck/telegram/user/history/sync", handle_user_history_sync)
    app.router.add_get("/api/deck/telegram/user/search", handle_user_search)
    # Organize
    app.router.add_post("/api/deck/telegram/user/forward", handle_user_forward)
    app.router.add_post("/api/deck/telegram/user/move", handle_user_move)
    app.router.add_post("/api/deck/telegram/user/rename", handle_user_rename)
    app.router.add_post("/api/deck/telegram/user/delete", handle_user_delete)
    app.router.add_get("/api/deck/telegram/user/links/{chat}", handle_user_links)
    app.router.add_post("/api/deck/telegram/user/dedupe", handle_user_dedupe)
    # Business rights matrix
    app.router.add_get("/api/deck/telegram/business", handle_business_status)
    app.router.add_post("/api/deck/telegram/business/enable", handle_business_enable)
    app.router.add_post("/api/deck/telegram/business/rights", handle_business_rights)
    app.router.add_post("/api/deck/telegram/business/alerts", handle_business_alerts)
