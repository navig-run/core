"""Deck routes for universal presets (NAVIG OS → Studio preset picker / editor).

    GET    /api/deck/presets                 list. Query: tool, layer, q
    GET    /api/deck/presets/facets          per-tool counts, defaults, malformed files
    GET    /api/deck/presets/item?id=…       one preset
    POST   /api/deck/presets                 create {name, tool, layer?, instructions?, params?, …}
    PATCH  /api/deck/presets/item?id=…       update (a builtin becomes your override)
    DELETE /api/deck/presets/item?id=…       delete one you created
    POST   /api/deck/presets/duplicate       {id, name?}
    POST   /api/deck/presets/reset           {id}  — drop your edits to a builtin
    POST   /api/deck/presets/favorite        {id, value?}
    POST   /api/deck/presets/default         {tool, id|null}
    POST   /api/deck/presets/trust           {id}  — mark an imported preset reviewed
    GET    /api/deck/presets/export?id=…     a scrubbed, shareable document (+ warnings)
    POST   /api/deck/presets/import          {document, overwrite?}
    POST   /api/deck/presets/resolve         {tool, ids?, task?, values?, overrides?, provider?}
                                             → exactly what the tool would receive. Nothing runs.

Ids are slug paths, so they travel in the query string or the body. Every write is
read back. Responses use ``{"ok": true, "data": …}`` or ``{"ok": false, "error": …}``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aiohttp import web

logger = logging.getLogger(__name__)

_MAX_IMPORT_BYTES = 256_000


def _ok(data: Any, status: int = 200) -> "web.Response":
    from aiohttp import web

    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 400) -> "web.Response":
    from aiohttp import web

    return web.json_response({"ok": False, "error": msg}, status=status)


def _cat():
    from navig.presets import catalog

    return catalog


async def _json(request: "web.Request") -> dict[str, Any] | None:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — malformed JSON is a 400, not a 500
        return None
    return body if isinstance(body, dict) else None


async def _call(fn, *args, **kwargs) -> "web.Response":
    """Run a catalog call off the loop; map its errors onto HTTP statuses."""
    from navig.presets import PresetError

    try:
        result = await asyncio.to_thread(fn, *args, **kwargs)
    except KeyError as exc:
        return _err(f"no preset {exc.args[0]!r}", 404)
    except PresetError as exc:
        return _err(str(exc))
    return _ok(result)


async def handle_list(request: "web.Request") -> "web.Response":
    q = request.query

    def _run():
        return [
            p.to_dict()
            for p in _cat().list_presets(tool=q.get("tool", ""), layer=q.get("layer", ""), q=q.get("q", ""))
        ]

    return await _call(_run)


async def handle_facets(request: "web.Request") -> "web.Response":
    return await _call(_cat().facets)


async def handle_get(request: "web.Request") -> "web.Response":
    pid = request.query.get("id", "")

    def _run():
        p = _cat().get_preset(pid)
        if p is None:
            raise KeyError(pid)
        return p.to_dict()

    return await _call(_run)


async def handle_create(request: "web.Request") -> "web.Response":
    body = await _json(request)
    if body is None:
        return _err("expected a JSON object")
    body = {**body, "id": ""}  # the server owns ids for new presets

    def _run():
        return _cat().save_preset(body).to_dict()

    resp = await _call(_run)
    if resp.status == 200:
        resp.set_status(201)
    return resp


async def handle_update(request: "web.Request") -> "web.Response":
    pid = request.query.get("id", "")
    body = await _json(request)
    if body is None:
        return _err("expected a JSON object")

    def _run():
        cat = _cat()
        cur = cat.get_preset(pid)
        if cur is None:
            raise KeyError(pid)
        fields = set(body) - {"id", "favorite"}
        if fields:
            data = {**cur.to_portable(), **{k: v for k, v in body.items() if k not in ("id", "favorite")}, "id": cur.id}
            cur = cat.save_preset(data, source=cur.source if cur.editable else "user")
        if "favorite" in body:
            cur = cat.set_favorite(pid, bool(body["favorite"]))
        return cur.to_dict()

    return await _call(_run)


async def handle_delete(request: "web.Request") -> "web.Response":
    pid = request.query.get("id", "")

    def _run():
        if not _cat().delete_preset(pid):
            raise KeyError(pid)
        return {"id": pid, "deleted": True}

    return await _call(_run)


async def handle_duplicate(request: "web.Request") -> "web.Response":
    body = await _json(request) or {}
    return await _call(lambda: _cat().duplicate_preset(str(body.get("id") or ""), str(body.get("name") or "")).to_dict())


async def handle_reset(request: "web.Request") -> "web.Response":
    body = await _json(request) or {}
    pid = str(body.get("id") or "")

    def _run():
        removed = _cat().reset_preset(pid)
        p = _cat().get_preset(pid)
        return {"reset": removed, "preset": p.to_dict() if p else None}

    return await _call(_run)


async def handle_favorite(request: "web.Request") -> "web.Response":
    body = await _json(request) or {}
    value = body.get("value")
    return await _call(
        lambda: _cat().set_favorite(str(body.get("id") or ""), None if value is None else bool(value)).to_dict()
    )


async def handle_default(request: "web.Request") -> "web.Response":
    body = await _json(request) or {}
    tool = str(body.get("tool") or "")
    pid = body.get("id")

    def _run():
        _cat().set_default(tool, str(pid) if pid else None)
        return {"tool": tool, "id": _cat().get_default(tool)}

    return await _call(_run)


async def handle_trust(request: "web.Request") -> "web.Response":
    body = await _json(request) or {}
    return await _call(lambda: _cat().trust_preset(str(body.get("id") or "")).to_dict())


async def handle_export(request: "web.Request") -> "web.Response":
    pid = request.query.get("id", "")

    def _run():
        doc, warnings = _cat().export_preset(pid)
        return {"document": doc, "warnings": warnings}

    return await _call(_run)


async def handle_import(request: "web.Request") -> "web.Response":
    if (request.content_length or 0) > _MAX_IMPORT_BYTES:
        return _err("preset document is too large (limit 256 KB)", 413)
    body = await _json(request)
    if body is None:
        return _err("expected a JSON object")
    doc = body.get("document")
    overwrite = bool(body.get("overwrite"))
    return await _call(lambda: _cat().import_preset(doc, overwrite=overwrite).to_dict())


async def handle_resolve(request: "web.Request") -> "web.Response":
    body = await _json(request)
    if body is None:
        return _err("expected a JSON object")
    from navig.presets.resolve import resolve_ids

    tool = str(body.get("tool") or "")
    ids = [str(i) for i in body.get("ids") or [] if i]
    values = body.get("values") if isinstance(body.get("values"), dict) else {}
    overrides = body.get("overrides") if isinstance(body.get("overrides"), dict) else {}
    provider = str(body.get("provider") or "")

    def _run():
        res = resolve_ids(ids, tool=tool, task=str(body.get("task") or ""), values=values, overrides=overrides)
        data = res.to_dict()
        if tool == "image" and provider:
            from navig.presets.adapters import translate_image

            data["provider_request"] = translate_image(provider, res.prompt, res.params, res.negative).to_dict()
        return data

    return await _call(_run)


def register(app: "web.Application") -> None:
    # Fixed paths only — ids travel in the query string or body (they contain "/").
    app.router.add_get("/api/deck/presets", handle_list)
    app.router.add_post("/api/deck/presets", handle_create)
    app.router.add_get("/api/deck/presets/facets", handle_facets)
    app.router.add_get("/api/deck/presets/item", handle_get)
    app.router.add_patch("/api/deck/presets/item", handle_update)
    app.router.add_delete("/api/deck/presets/item", handle_delete)
    app.router.add_post("/api/deck/presets/duplicate", handle_duplicate)
    app.router.add_post("/api/deck/presets/reset", handle_reset)
    app.router.add_post("/api/deck/presets/favorite", handle_favorite)
    app.router.add_post("/api/deck/presets/default", handle_default)
    app.router.add_post("/api/deck/presets/trust", handle_trust)
    app.router.add_get("/api/deck/presets/export", handle_export)
    app.router.add_post("/api/deck/presets/import", handle_import)
    app.router.add_post("/api/deck/presets/resolve", handle_resolve)
