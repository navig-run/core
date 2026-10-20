"""Deck API — Navig Recall: the knowledge bank (NAVIG OS → Knowledge → Recall) and the
session links (which Claude Code session touched, planned or committed what).

    GET  /api/deck/recall/status                  bank counts, enabled flag, folder, projects
    GET  /api/deck/recall/search?q=&project=&type=&status=&raw=1&limit=
                                                  records (with ``[``…``]`` snippet marks) and,
                                                  when nothing matched or raw=1, turns
    GET  /api/deck/recall/ask?q=&project=&limit=  earlier owner questions + the reply + decisions
    GET  /api/deck/recall/views/<name>?project=&status=&limit=
                                                  one named view (navig.recall.query.VIEWS)
    GET  /api/deck/recall/record?id=              record + cited evidence + timeline + related
    GET  /api/deck/recall/excerpt?source=&msg=&before=&after=
                                                  the turns around one cited message
    GET  /api/deck/recall/sessions?project=<hub id>|path=<abs path>|commit=<sha>[&limit=]
    POST /api/deck/recall/links    {session, target, action: "link"|"unlink"}

Everything here comes from the operator's own transcripts, so EVERY route is LOCAL ONLY —
never through the tunnel / Lighthouse / Mini App — and refuses browser-extension origins,
which the deck otherwise trusts on loopback (a Lens must never read Recall). Recall must be
enabled (``recall.read_local``); otherwise 409 ``{enabled: false}`` with the command that
enables it — except ``status``, which answers so a surface can show that Recall is off.

The bank routes are read-only (no route writes records). Shared chats below ``agent`` level
are shown here (they never leave the machine); the MCP bundle is the one that withholds them.
``project`` empty = every project. Responses use ``{"ok": true, "data": …}`` or
``{"ok": false, "error": …}``. Shared logic: :mod:`navig.recall.query` (bank) and
:mod:`navig.recall.session_links` (sessions).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

logger = logging.getLogger(__name__)

ENABLE_HINT = "navig recall enable"
_EXTENSION_ORIGINS = ("chrome-extension://", "moz-extension://", "safari-web-extension://")
_MAX_LIMIT = 200


def _ok(data: Any, status: int = 200) -> web.Response:
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 400, **extra: Any) -> web.Response:
    return web.json_response({"ok": False, "error": msg, **extra}, status=status)


def _guard(request: web.Request, *, need_enabled: bool = True) -> web.Response | None:
    from navig.gateway.deck.auth import is_local_request  # noqa: PLC0415

    origin = request.headers.get("Origin", "")
    if not is_local_request(request) or origin.startswith(_EXTENSION_ORIGINS):
        return _err("recall is available on this machine only", 403)
    if need_enabled and not _enabled():
        return _err("recall is not enabled", 409, enabled=False, hint=ENABLE_HINT)
    return None


def _enabled() -> bool:
    from navig.config import get_config_manager  # noqa: PLC0415
    from navig.core.coerce import coerce_bool  # noqa: PLC0415

    return coerce_bool(get_config_manager().get("recall.read_local"), default=False)


def _limit(raw: str | None) -> int:
    try:
        return max(1, min(500, int(raw or 100)))
    except ValueError:
        return 100


def _int(v: str | None, default: int, lo: int = 1, hi: int = _MAX_LIMIT) -> int:
    try:
        return max(lo, min(hi, int(v))) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _flag(v: str | None) -> bool:
    from navig.core.coerce import coerce_bool  # noqa: PLC0415

    return coerce_bool(v, default=False)


def _scope(request: web.Request):
    from navig.recall.query import Scope  # noqa: PLC0415

    return Scope.only(request.query.get("project", "").strip() or None)


async def _run(fn, *args: Any, **kwargs: Any) -> web.Response:
    """Run a sync query off the loop; a failure is a 500 with its type, never a traceback."""
    try:
        return _ok(await asyncio.to_thread(fn, *args, **kwargs))
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:  # noqa: BLE001
        logger.warning("recall deck route failed: %s", type(exc).__name__)
        return _err(f"recall failed: {type(exc).__name__}", 500)


# ── the bank (NAVIG OS → Knowledge → Recall) ──────────────────


async def handle_status(request: web.Request) -> web.Response:
    if (denied := _guard(request, need_enabled=False)) is not None:
        return denied
    from navig.recall import query  # noqa: PLC0415

    return await _run(query.status)


async def handle_search(request: web.Request) -> web.Response:
    if (denied := _guard(request)) is not None:
        return denied
    from navig.recall import query  # noqa: PLC0415

    q = request.query.get("q", "").strip()
    if not q:
        return _err("q is required")
    return await _run(query.search, q, scope=_scope(request), rtype=request.query.get("type") or None,
                      status=request.query.get("status") or None, raw=_flag(request.query.get("raw")),
                      limit=_int(request.query.get("limit"), 40))


async def handle_ask(request: web.Request) -> web.Response:
    if (denied := _guard(request)) is not None:
        return denied
    from navig.recall import query  # noqa: PLC0415

    q = request.query.get("q", "").strip()
    if not q:
        return _err("q is required")
    return await _run(query.ask, q, scope=_scope(request), limit=_int(request.query.get("limit"), 5, hi=50))


async def handle_view(request: web.Request) -> web.Response:
    if (denied := _guard(request)) is not None:
        return denied
    from navig.recall import query  # noqa: PLC0415

    name = request.match_info.get("name", "")
    if name not in query.VIEWS:
        return _err(f"unknown view '{name}' (use one of: {', '.join(query.VIEWS)})", 404)
    scope = _scope(request)
    status = request.query.get("status") or None
    limit = _int(request.query.get("limit"), 100)

    def _view() -> dict[str, Any]:
        recs = query.view(name, scope=scope, status=status, limit=limit)
        return {"view": name, "records": [query.record_row(r) for r in recs], "count": len(recs)}

    return await _run(_view)


async def handle_record(request: web.Request) -> web.Response:
    if (denied := _guard(request)) is not None:
        return denied
    from navig.recall import query  # noqa: PLC0415

    rid = request.query.get("id", "").strip()
    if not rid:
        return _err("id is required")
    detail = await asyncio.to_thread(query.record_detail, rid)
    if detail is None:
        return _err(f"no record {rid}", 404)
    return _ok(detail)


async def handle_excerpt(request: web.Request) -> web.Response:
    if (denied := _guard(request)) is not None:
        return denied
    from navig.recall import query  # noqa: PLC0415

    sid, mid = request.query.get("source", "").strip(), request.query.get("msg", "").strip()
    if not sid or not mid:
        return _err("source and msg are required")
    found = await asyncio.to_thread(query.excerpt, sid, mid, before=_int(request.query.get("before"), 3, 0, 20),
                                    after=_int(request.query.get("after"), 3, 0, 20))
    if found is None:
        return _err(f"no message {mid} in {sid}", 404)
    return _ok(found)


# ── session links ─────────────────────────────────────────────


def _sessions(project: str, path: str, commit: str, limit: int) -> dict[str, Any]:
    from navig.recall.projects import ProjectResolver  # noqa: PLC0415
    from navig.recall.session_links import find_sessions  # noqa: PLC0415
    from navig.recall.store import get_recall_store  # noqa: PLC0415

    resolver = ProjectResolver()
    recall_project = None
    if project:
        # A Projects-hub id is a space id; Recall files sessions by the project its folder resolves to.
        from navig.projects.model import resolve_project  # noqa: PLC0415

        _pid, root = resolve_project(project)
        recall_project = resolver.resolve(str(root)).id
    target = f"commit:{commit.strip().lower()[:12]}" if commit else (path or None)
    rows = find_sessions(get_recall_store(), target, project=recall_project, resolver=resolver, limit=limit)
    return {"project": recall_project, "target": target, "sessions": [r.to_dict() for r in rows],
            "count": len(rows)}


async def handle_recall_sessions(request: web.Request) -> web.Response:
    if (denied := _guard(request)) is not None:
        return denied
    q = request.query
    project, path, commit = q.get("project", ""), q.get("path", ""), q.get("commit", "")
    if not (project or path or commit):
        return _err("give one of: project, path, commit")
    from navig.projects.model import ProjectNotFound  # noqa: PLC0415
    from navig.recall.session_links import is_commit_id  # noqa: PLC0415

    if commit and not is_commit_id(commit):
        return _err("commit must be a 7–40 character hex id")

    try:
        return _ok(await asyncio.to_thread(_sessions, project, path, commit, _limit(q.get("limit"))))
    except ProjectNotFound as exc:
        return _err(str(exc), 404)
    except ValueError as exc:
        return _err(str(exc), 422)


def _change(session: str, target: str, action: str) -> dict[str, Any]:
    from navig.recall import session_links as sl  # noqa: PLC0415
    from navig.recall.store import get_recall_store  # noqa: PLC0415
    from navig.recall.vault import Vault  # noqa: PLC0415

    if action == "link":
        return {"changed": sl.link(get_recall_store(), Vault(), session, target)}
    return {"changed": sl.unlink(get_recall_store(), Vault(), session, target) > 0}


async def handle_recall_links(request: web.Request) -> web.Response:
    if (denied := _guard(request)) is not None:
        return denied
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — a malformed body is a client error, reported as one
        body = None
    if not isinstance(body, dict):
        return _err("JSON body required: {session, target, action}")
    session, target, action = str(body.get("session") or ""), str(body.get("target") or ""), body.get("action")
    if action not in ("link", "unlink") or not session or not target:
        return _err("session, target and action (link|unlink) are required")
    try:
        return _ok(await asyncio.to_thread(_change, session, target, action))
    except LookupError as exc:
        return _err(str(exc), 404)
    except ValueError as exc:
        return _err(str(exc), 422)


def register(app: web.Application) -> None:
    # Fixed paths; ids travel in the query string.
    app.router.add_get("/api/deck/recall/status", handle_status)
    app.router.add_get("/api/deck/recall/search", handle_search)
    app.router.add_get("/api/deck/recall/ask", handle_ask)
    app.router.add_get("/api/deck/recall/views/{name}", handle_view)
    app.router.add_get("/api/deck/recall/record", handle_record)
    app.router.add_get("/api/deck/recall/excerpt", handle_excerpt)
    app.router.add_get("/api/deck/recall/sessions", handle_recall_sessions)
    app.router.add_post("/api/deck/recall/links", handle_recall_links)
