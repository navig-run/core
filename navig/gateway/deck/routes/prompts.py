"""Deck routes for the prompt library, used by NAVIG OS → Knowledge → Prompts.

    GET    /api/deck/prompts                 list. Query params: q, domain, category,
                                             collection, tag, project, fav=1, archived=1,
                                             sort, limit. Rows carry no body.
    GET    /api/deck/prompts/facets          counts for the filter rail
    GET    /api/deck/prompts/item?id=…       one prompt, including its body
    POST   /api/deck/prompts                 create {title, body, domain?, category?,
                                             use_when?, tags?, collection?, dedupe?}.
                                             dedupe=true returns the live prompt with the
                                             same body (``existing: true``) instead of a copy.
    POST   /api/deck/prompts/batch           {prompts: [{title, body, tags?, project?,
                                             source_url?, use_when?}]} (≤ 200). Dedupes by
                                             content hash (whitespace-normalised body), also
                                             within the batch; answers one result per item,
                                             in order: ``created`` | ``duplicate`` | ``error``.
    PATCH  /api/deck/prompts/item?id=…       update any editable field, plus
                                             favorite and archived
    DELETE /api/deck/prompts/item?id=…       archive the prompt (&purge=1 deletes it for good)
    POST   /api/deck/prompts/use             {id, values?}. Returns the filled text
                                             and counts it as a use.
    POST   /api/deck/prompts/import          {path, dry_run?}. Re-imports a folder.
                                             Prompts you edited are kept.
    POST   /api/deck/prompts/send            {id, values?, as_file?}. Sends the prompt
                                             to the owner's Telegram chat.

Ids are slug paths (``dev/coding/debug-root-cause``), so they travel in the query
string. A ``{id}`` path segment cannot hold ``/``. Every write is read back, and
the response is what was stored. Responses use ``{"ok": true, "data": …}`` or
``{"ok": false, "error": …}``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aiohttp import web

logger = logging.getLogger(__name__)


def _ok(data: Any, status: int = 200) -> "web.Response":
    from aiohttp import web

    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 400) -> "web.Response":
    from aiohttp import web

    return web.json_response({"ok": False, "error": msg}, status=status)


def _store():
    from navig.store.prompt_library import get_prompt_store

    return get_prompt_store()


def _flag(v: str | None) -> bool:
    from navig.core.coerce import coerce_bool

    return coerce_bool(v, default=False)


async def _json(request: "web.Request") -> dict[str, Any] | None:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — malformed JSON is a 400, not a 500
        return None
    return body if isinstance(body, dict) else None


async def handle_list(request: "web.Request") -> "web.Response":
    q = request.query
    try:
        limit = int(q.get("limit", "1000"))
    except ValueError:
        return _err("limit must be a number")

    def _run():
        rows = _store().list(
            q=q.get("q", ""), domain=q.get("domain", ""), category=q.get("category", ""),
            collection=q.get("collection", ""), tag=q.get("tag", ""), favorite=_flag(q.get("fav")),
            include_archived=_flag(q.get("archived")), project=q.get("project", ""),
            sort=q.get("sort", "title"), limit=limit,
        )
        return [r.to_dict(with_body=False) for r in rows]

    return _ok(await asyncio.to_thread(_run))


async def handle_facets(request: "web.Request") -> "web.Response":
    return _ok(await asyncio.to_thread(_store().facets))


async def handle_get(request: "web.Request") -> "web.Response":
    pid = request.query.get("id", "")
    rec = await asyncio.to_thread(_store().get, pid)
    if rec is None:
        return _err(f"no prompt {pid!r}", 404)
    data = rec.to_dict()
    from navig.prompts.delivery import next_step

    nxt = await asyncio.to_thread(next_step, rec)
    data["next_step"] = {"id": nxt.id, "title": nxt.title} if nxt else None
    return _ok(data)


async def handle_create(request: "web.Request") -> "web.Response":
    body = await _json(request)
    if body is None:
        return _err("expected a JSON object")
    if not str(body.get("body") or "").strip():
        return _err("body is required")
    if _flag(str(body.get("dedupe", ""))):
        # Capture surfaces (the browser lens) save what they see; saving the same
        # prompt twice returns the one already in the library instead of a copy.
        existing = await asyncio.to_thread(_store().find_by_body, str(body.get("body") or ""))
        if existing is not None:
            return _ok({**existing.to_dict(), "existing": True})
    try:
        rec = await asyncio.to_thread(
            _store().create,
            title=str(body.get("title") or ""), body=str(body.get("body") or ""),
            domain=str(body.get("domain") or ""), category=str(body.get("category") or ""),
            use_when=str(body.get("use_when") or ""), tags=body.get("tags") or [],
            collection=str(body.get("collection") or ""),
        )
    except ValueError as exc:
        return _err(str(exc))
    stored = await asyncio.to_thread(_store().get, rec.id)
    return _ok(stored.to_dict() if stored else None, 201)


BATCH_MAX = 200
_MAX_BODY = 200_000


def _batch_item(store: Any, item: Any, seen: dict[str, str]) -> dict[str, Any]:
    from navig.store.prompt_library import content_hash

    if not isinstance(item, dict):
        return {"status": "error", "error": "expected an object"}
    body = str(item.get("body") or "").strip()
    if not body:
        return {"status": "error", "error": "body is required"}
    if len(body) > _MAX_BODY:
        return {"status": "error", "error": f"body is longer than {_MAX_BODY} characters"}
    digest = content_hash(body)
    if digest in seen:
        return {"status": "duplicate", "id": seen[digest], "hash": digest}
    existing = store.find_by_hash(digest)
    if existing is not None:
        seen[digest] = existing.id
        return {"status": "duplicate", "id": existing.id, "title": existing.title, "hash": digest}
    title = str(item.get("title") or "").strip()[:120] or " ".join(body.split())[:60]
    source_url = str(item.get("source_url") or "").strip()[:2048]
    if source_url and not source_url.startswith(("http://", "https://")):
        return {"status": "error", "error": "source_url must be an http(s) address"}
    tags = item.get("tags") or []
    if not isinstance(tags, (list, str)):
        return {"status": "error", "error": "tags must be a list"}
    try:
        rec = store.create(
            title=title, body=body, use_when=str(item.get("use_when") or "")[:1000], tags=tags,
            project=str(item.get("project") or "")[:120], source_url=source_url, source="capture",
        )
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}
    seen[digest] = rec.id
    return {"status": "created", "id": rec.id, "title": rec.title, "hash": digest}


async def handle_batch(request: "web.Request") -> "web.Response":
    body = await _json(request)
    if body is None:
        return _err("expected a JSON object")
    items = body.get("prompts")
    if not isinstance(items, list) or not items:
        return _err("prompts must be a non-empty list")
    if len(items) > BATCH_MAX:
        return _err(f"at most {BATCH_MAX} prompts per batch", 413)

    def _run() -> list[dict[str, Any]]:
        store = _store()
        seen: dict[str, str] = {}
        return [_batch_item(store, it, seen) for it in items]

    results = await asyncio.to_thread(_run)
    counts = {k: sum(1 for r in results if r["status"] == k) for k in ("created", "duplicate", "error")}
    return _ok({"results": results, **counts})


async def handle_update(request: "web.Request") -> "web.Response":
    pid = request.query.get("id", "")
    body = await _json(request)
    if body is None:
        return _err("expected a JSON object")
    try:
        await asyncio.to_thread(_store().update, pid, body)
    except KeyError:
        return _err(f"no prompt {pid!r}", 404)
    except ValueError as exc:
        return _err(str(exc))
    stored = await asyncio.to_thread(_store().get, pid)
    return _ok(stored.to_dict() if stored else None)


async def handle_delete(request: "web.Request") -> "web.Response":
    pid = request.query.get("id", "")
    purge = _flag(request.query.get("purge"))
    done = await asyncio.to_thread(_store().delete, pid, purge=purge)
    if not done:
        return _err(f"no prompt {pid!r}", 404)
    return _ok({"id": pid, "purged": purge, "archived": not purge})


def _fill(rec: Any, values: Any) -> str:
    from navig.prompts.importer import fill_variables

    if not isinstance(values, dict):
        return rec.body
    return fill_variables(rec.body, {str(k): str(v) for k, v in values.items() if str(v).strip()})


async def handle_use(request: "web.Request") -> "web.Response":
    body = await _json(request)
    if body is None:
        return _err("expected a JSON object")
    pid = str(body.get("id") or "")
    rec = await asyncio.to_thread(_store().get, pid)
    if rec is None:
        return _err(f"no prompt {pid!r}", 404)
    text = _fill(rec, body.get("values"))
    await asyncio.to_thread(_store().record_use, pid)
    return _ok({"id": pid, "text": text, "unfilled": [v for v in rec.variables if v in text]})


async def handle_import(request: "web.Request") -> "web.Response":
    from pathlib import Path

    from navig.prompts.importer import import_library

    body = await _json(request)
    if body is None:
        return _err("expected a JSON object")
    raw = str(body.get("path") or "").strip()
    if not raw:
        return _err("path is required")
    root = Path(raw).expanduser()
    if not root.is_absolute():
        return _err("path must be absolute")
    try:
        report = await asyncio.to_thread(import_library, root, dry_run=_flag(str(body.get("dry_run", ""))))
    except FileNotFoundError as exc:
        return _err(str(exc), 404)
    return _ok(report.to_dict())


async def handle_send(request: "web.Request") -> "web.Response":
    from navig.gateway.deck.routes.telegram_manager import _channel
    from navig.prompts.delivery import send_prompt
    from navig.telegram.os_access_actions import owner_chat_id

    body = await _json(request)
    if body is None:
        return _err("expected a JSON object")
    pid = str(body.get("id") or "")
    rec = await asyncio.to_thread(_store().get, pid)
    if rec is None:
        return _err(f"no prompt {pid!r}", 404)
    channel = _channel(request)
    if channel is None or not hasattr(channel, "send_rich_message"):
        return _err("Telegram is not connected on this NAVIG — set it up in Settings → Telegram.", 503)
    chat_id = owner_chat_id()
    if chat_id is None:
        return _err("No owner chat — add your Telegram id to telegram.allowed_users.", 409)
    text = _fill(rec, body.get("values"))
    ok = await send_prompt(channel, chat_id, rec, body=text, as_file=bool(body.get("as_file")))
    if not ok:
        return _err("Telegram did not accept the message.", 502)
    return _ok({"id": pid, "sent": True})


def register(app: "web.Application") -> None:
    # Fixed paths only — ids travel in the query string (they contain "/").
    app.router.add_get("/api/deck/prompts", handle_list)
    app.router.add_post("/api/deck/prompts", handle_create)
    app.router.add_get("/api/deck/prompts/facets", handle_facets)
    app.router.add_get("/api/deck/prompts/item", handle_get)
    app.router.add_patch("/api/deck/prompts/item", handle_update)
    app.router.add_delete("/api/deck/prompts/item", handle_delete)
    app.router.add_post("/api/deck/prompts/use", handle_use)
    app.router.add_post("/api/deck/prompts/import", handle_import)
    app.router.add_post("/api/deck/prompts/batch", handle_batch)
    app.router.add_post("/api/deck/prompts/send", handle_send)
