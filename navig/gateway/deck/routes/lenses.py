"""
Deck Lens endpoints — the one store every Lens host reads.

GET    /api/deck/lenses                     → {"ok", "lenses": [record…]}
GET    /api/deck/lenses/compiled?host=os    → {"etag", "lenses": [{id, matches, runAt, code…}], "skipped"}
                                               (ETag / If-None-Match → 304, so hosts can poll cheaply)
POST   /api/deck/lenses/preview             {"source"} | {"url"} → {"ok", "manifest", "source"}  (no write)
POST   /api/deck/lenses                     {"source"} | {"url"}, "origin"?, "enabled"? → {"ok", "lens"}
GET    /api/deck/lenses/{id}                → {"ok", "lens"} (with source)
PATCH  /api/deck/lenses/{id}                {"enabled": bool} → {"ok", "lens"}
DELETE /api/deck/lenses/{id}                → {"ok"}
GET    /api/deck/lenses/reviews             → {"ok", "lenses": [record…]}  (waiting on a person)
POST   /api/deck/lenses/check-updates       {"id"?} → {"ok", "results": [{id, status, …}]}  (stages, never applies)
GET    /api/deck/lenses/{id}/review         → {"ok", "kind", "diff", "changes", "current", "proposed", "lens"}
POST   /api/deck/lenses/{id}/review         {"action": "approve"|"reject"} → {"ok", "lens"|null}

Hosts: NAVIG OS (pane + agent worker), the Chrome extension's web bar, `navig cdp`
browsers (those read the store in-process). Every store call runs in a worker thread —
it touches disk, may fetch an https URL, and may run `node --check`.

An agent's new Lens is stored off and ``proposed``; its edit of an approved Lens is staged
beside it (see navig.lenses.store). Either one pushes a ``lens_review`` notification.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _err(msg: str, status: int = 500) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


def _store():
    from navig.lenses import LensStore

    return LensStore()


async def _body(request: "web.Request") -> dict[str, Any] | None:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return None
    return body if isinstance(body, dict) else None


async def handle_deck_lenses(request: "web.Request") -> "web.Response":
    try:
        records = await asyncio.to_thread(lambda: _store().records())
        return web.json_response({"ok": True, "lenses": [r.to_dict() for r in records]})
    except Exception as exc:  # noqa: BLE001
        logger.exception("deck lenses list failed")
        return _err(str(exc))


async def handle_deck_lenses_compiled(request: "web.Request") -> "web.Response":
    from navig.lenses import HOSTS

    host = request.query.get("host", "os")
    if host not in HOSTS:
        return _err(f"unknown host {host!r} (expected one of {', '.join(HOSTS)})", status=400)
    try:
        compiled = await asyncio.to_thread(lambda: _store().compiled(host))
    except Exception as exc:  # noqa: BLE001
        logger.exception("deck lenses compile failed")
        return _err(str(exc))
    etag = f'"{compiled.etag}"'
    if request.headers.get("If-None-Match") == etag:
        return web.Response(status=304, headers={"ETag": etag})
    return web.json_response(compiled.to_dict(), headers={"ETag": etag})


async def _source_from(body: dict[str, Any]) -> tuple[str, str | None]:
    """The Lens source from ``{"source"}`` or ``{"url"}`` (fetched with the SSRF guard)."""
    from navig.lenses import fetch_text

    source = body.get("source")
    url = str(body.get("url") or "").strip() or None
    if isinstance(source, str) and source.strip():
        return source, url
    if url:
        return await asyncio.to_thread(fetch_text, url), url
    raise ValueError("send 'source' (the .user.js text) or 'url' (an https link to it)")


async def handle_deck_lenses_preview(request: "web.Request") -> "web.Response":
    from navig.lenses import LensFetchError, LensFormatError, parse_manifest

    body = await _body(request)
    if body is None:
        return _err("invalid JSON body", status=400)
    try:
        source, url = await _source_from(body)
        manifest = parse_manifest(source)
    except (ValueError, LensFetchError, LensFormatError) as exc:
        return _err(str(exc), status=400)
    return web.json_response(
        {"ok": True, "manifest": manifest.to_dict(), "source": source, "url": url}
    )


async def handle_deck_lens_save(request: "web.Request") -> "web.Response":
    from navig.lenses import LensFetchError, LensFormatError
    from navig.lenses.store import ORIGINS

    body = await _body(request)
    if body is None:
        return _err("invalid JSON body", status=400)
    origin = str(body.get("origin") or ("url" if body.get("url") else "user"))
    if origin not in ORIGINS:
        return _err(f"unknown origin {origin!r}", status=400)
    enabled = body.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        return _err("'enabled' must be true or false", status=400)
    try:
        source, url = await _source_from(body)
        rec = await asyncio.to_thread(
            lambda: _store().save(source, origin=origin, install_url=url, enabled=enabled)
        )
    except (ValueError, LensFetchError, LensFormatError) as exc:
        return _err(str(exc), status=400)
    except Exception as exc:  # noqa: BLE001 — includes JsonReadError: refuse, never wipe
        logger.exception("deck lens save failed")
        return _err(str(exc))
    out = rec.to_dict(with_source=True)
    if origin == "agent" and rec.needs_review:
        from navig.core.background import spawn
        from navig.lenses.notify import notify_review, proposal_message

        spawn(notify_review(*proposal_message(out), lens_ids=[rec.id]))
    return web.json_response({"ok": True, "lens": out})


async def handle_deck_lens_get(request: "web.Request") -> "web.Response":
    lens_id = request.match_info.get("lens_id", "")
    try:
        rec = await asyncio.to_thread(lambda: _store().get(lens_id))
    except ValueError as exc:
        return _err(str(exc), status=400)
    if rec is None:
        return _err(f"no lens {lens_id!r}", status=404)
    return web.json_response({"ok": True, "lens": rec.to_dict(with_source=True)})


async def handle_deck_lens_patch(request: "web.Request") -> "web.Response":
    lens_id = request.match_info.get("lens_id", "")
    body = await _body(request)
    if body is None or not isinstance(body.get("enabled"), bool):
        return _err("send {\"enabled\": true|false}", status=400)
    try:
        rec = await asyncio.to_thread(lambda: _store().set_enabled(lens_id, body["enabled"]))
    except KeyError:
        return _err(f"no lens {lens_id!r}", status=404)
    except ValueError as exc:
        return _err(str(exc), status=400)
    except Exception as exc:  # noqa: BLE001
        logger.exception("deck lens toggle failed")
        return _err(str(exc))
    return web.json_response({"ok": True, "lens": rec.to_dict()})


async def handle_deck_lens_delete(request: "web.Request") -> "web.Response":
    lens_id = request.match_info.get("lens_id", "")
    try:
        removed = await asyncio.to_thread(lambda: _store().remove(lens_id))
    except ValueError as exc:
        return _err(str(exc), status=400)
    except Exception as exc:  # noqa: BLE001
        logger.exception("deck lens delete failed")
        return _err(str(exc))
    if not removed:
        return _err(f"no lens {lens_id!r}", status=404)
    return web.json_response({"ok": True})


async def handle_deck_lens_reviews(request: "web.Request") -> "web.Response":
    try:
        records = await asyncio.to_thread(lambda: _store().reviews())
    except Exception as exc:  # noqa: BLE001
        logger.exception("deck lens reviews failed")
        return _err(str(exc))
    return web.json_response({"ok": True, "lenses": [r.to_dict() for r in records]})


async def handle_deck_lens_review_get(request: "web.Request") -> "web.Response":
    lens_id = request.match_info.get("lens_id", "")
    try:
        details = await asyncio.to_thread(lambda: _store().review_details(lens_id))
    except KeyError:
        return _err(f"{lens_id!r} has nothing waiting for review", status=404)
    except ValueError as exc:
        return _err(str(exc), status=400)
    except Exception as exc:  # noqa: BLE001
        logger.exception("deck lens review read failed")
        return _err(str(exc))
    return web.json_response({"ok": True, **details})


async def handle_deck_lens_review_post(request: "web.Request") -> "web.Response":
    from navig.lenses import LensFetchError, LensFormatError

    lens_id = request.match_info.get("lens_id", "")
    body = await _body(request)
    action = (body or {}).get("action")
    if action not in ("approve", "reject"):
        return _err('send {"action": "approve"|"reject"}', status=400)
    try:
        if action == "approve":
            rec = await asyncio.to_thread(lambda: _store().approve(lens_id))
            return web.json_response({"ok": True, "lens": rec.to_dict()})
        kept = await asyncio.to_thread(lambda: _store().reject(lens_id))
        rec = await asyncio.to_thread(lambda: _store().get(lens_id)) if kept else None
        return web.json_response({"ok": True, "lens": rec.to_dict() if rec else None})
    except KeyError:
        return _err(f"{lens_id!r} has nothing waiting for review", status=404)
    except (ValueError, LensFetchError, LensFormatError) as exc:
        return _err(str(exc), status=400)
    except Exception as exc:  # noqa: BLE001
        logger.exception("deck lens review %s failed", action)
        return _err(str(exc))


async def handle_deck_lens_check_updates(request: "web.Request") -> "web.Response":
    from navig.lenses.notify import notify_review, updates_message

    body = await _body(request) or {}
    only = body.get("id") if isinstance(body.get("id"), str) else None
    try:
        results = await asyncio.to_thread(lambda: _store().check_updates(only=only))
    except Exception as exc:  # noqa: BLE001
        logger.exception("deck lens update check failed")
        return _err(str(exc))
    message = updates_message(results)
    if message:  # the person asked, but a phone ping still helps if they walked away
        from navig.core.background import spawn

        staged = [r["id"] for r in results if r.get("status") == "staged"]
        spawn(notify_review(*message, lens_ids=staged))
    return web.json_response({"ok": True, "results": results})
