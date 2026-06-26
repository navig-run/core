"""Studio — social composer & scheduler deck API.

Routes (all under /api/deck):
  GET    /studio/networks                  connected + available publish targets
  POST   /studio/media                     multipart upload → {media_id, path, url, kind}
  GET    /studio/media/{id}/raw            serve an uploaded media file (preview)
  GET    /studio/posts                     list posts (?status=)
  POST   /studio/posts                     create a draft / scheduled post
  GET    /studio/posts/{id}                get one post
  PATCH  /studio/posts/{id}                update a post
  DELETE /studio/posts/{id}                delete a post
  POST   /studio/posts/{id}/publish        publish now
  POST   /studio/ai                        AI assist (draft/rewrite/shorten/hashtags/variants)
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)

# Messaging networks (1:1 adapters that double as publish targets).
_MESSAGING = [
    {"network": "telegram", "label": "Telegram", "char_limit": 4096, "media": True},
    {"network": "discord", "label": "Discord", "char_limit": 2000, "media": True},
    {"network": "whatsapp", "label": "WhatsApp", "char_limit": 4096, "media": True},
    {"network": "sms", "label": "SMS", "char_limit": 1600, "media": False},
]


def _ok(data: object, status: int = 200) -> "web.Response":
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 500) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


async def _body(request: "web.Request") -> dict[str, Any]:
    try:
        return await request.json()
    except Exception:
        return {}


def _store():
    from navig.store.scheduled_posts import get_scheduled_posts

    return get_scheduled_posts()


def _media_dir() -> Path:
    from navig.platform import paths

    d = paths.data_dir() / "studio" / "media"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _kind_from_mime(mime: str, filename: str) -> str:
    mime = (mime or "").lower()
    if mime.startswith("image/"):
        return "photo"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "voice" if filename.lower().endswith(".ogg") else "audio"
    return "document"


# ─── Networks ────────────────────────────────────────────────────────────────


async def handle_studio_networks(request: "web.Request") -> "web.Response":
    """List publish targets: messaging adapters + social publishers + TG channels."""
    try:
        from navig.messaging.adapter_registry import get_adapter_registry
        from navig.social.registry import get_publisher_registry

        out: list[dict[str, Any]] = []

        areg = get_adapter_registry()
        for m in _MESSAGING:
            connected = False
            try:
                connected = areg.is_available(m["network"])
            except Exception:  # noqa: BLE001
                connected = False
            targets: list[dict[str, str]] = []
            if m["network"] == "telegram":
                targets = _telegram_targets()
            out.append({
                "network": m["network"], "label": m["label"], "group": "messaging",
                "connected": connected, "capabilities": {"char_limit": m["char_limit"], "media": m["media"]},
                "targets": targets,
            })

        preg = get_publisher_registry()
        for pub in preg.all():
            connected = False
            try:
                connected = pub.is_configured()
            except Exception:  # noqa: BLE001
                connected = False
            out.append({
                "network": pub.name, "label": pub.name.title(), "group": "publishing",
                "connected": connected, "capabilities": pub.capabilities, "targets": [],
            })

        return _ok({"networks": out})
    except Exception as exc:
        logger.exception("studio networks failed")
        return _err(str(exc))


def _telegram_targets() -> list[dict[str, str]]:
    """Catalogued Telegram channels/groups the bot can post to."""
    try:
        from navig.store.telegram_catalog import get_telegram_catalog

        rooms = get_telegram_catalog().list_rooms()
        return [
            {"target": str(r["chat_id"]), "display": r.get("title") or str(r["chat_id"])}
            for r in rooms
            if r.get("type") in ("channel", "supergroup", "group")
        ]
    except Exception:  # noqa: BLE001
        return []


# ─── Media upload ────────────────────────────────────────────────────────────


async def handle_studio_media_upload(request: "web.Request") -> "web.Response":
    try:
        post = await request.post()
        field = post.get("file")
        if field is None or not hasattr(field, "file"):
            return _err("no file field", status=400)
        filename = getattr(field, "filename", None) or "upload"
        mime = getattr(field, "content_type", None) or "application/octet-stream"
        data = field.file.read()
        if not data:
            return _err("empty file", status=400)

        media_id = uuid.uuid4().hex
        ext = Path(filename).suffix
        path = _media_dir() / f"{media_id}{ext}"
        path.write_bytes(data)
        kind = _kind_from_mime(mime, filename)
        meta = {"media_id": media_id, "filename": filename, "mime": mime, "kind": kind,
                "size": len(data), "path": str(path)}
        (_media_dir() / f"{media_id}.json").write_text(json.dumps(meta), encoding="utf-8")
        meta["url"] = f"/api/deck/studio/media/{media_id}/raw"
        return _ok(meta)
    except Exception as exc:
        logger.exception("studio media upload failed")
        return _err(str(exc))


async def handle_studio_media_raw(request: "web.Request") -> "web.Response":
    media_id = request.match_info.get("id", "")
    sidecar = _media_dir() / f"{media_id}.json"
    if not sidecar.exists():
        return _err("media not found", status=404)
    try:
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
        path = Path(meta["path"])
        if not path.exists():
            return _err("media file missing", status=404)
        return web.FileResponse(path, headers={"Content-Type": meta.get("mime", "application/octet-stream")})
    except Exception as exc:
        logger.exception("studio media raw failed")
        return _err(str(exc))


# ─── Posts CRUD ──────────────────────────────────────────────────────────────


async def handle_studio_posts_list(request: "web.Request") -> "web.Response":
    try:
        status = request.query.get("status") or None
        return _ok({"posts": _store().list(status=status)})
    except Exception as exc:
        logger.exception("studio posts list failed")
        return _err(str(exc))


async def handle_studio_post_create(request: "web.Request") -> "web.Response":
    body = await _body(request)
    try:
        content = body.get("content") or {"body": body.get("body", "")}
        pid = _store().create(
            body=body.get("body", "") or content.get("body", ""),
            content=content,
            targets=body.get("targets") or [],
            status=body.get("status") or "draft",
            schedule_kind=body.get("schedule_kind") or "now",
            run_at=body.get("run_at"),
            cron_expr=body.get("cron_expr"),
        )
        return _ok({"post": _store().get(pid)}, status=201)
    except Exception as exc:
        logger.exception("studio post create failed")
        return _err(str(exc))


async def handle_studio_post_get(request: "web.Request") -> "web.Response":
    try:
        pid = int(request.match_info["id"])
    except (KeyError, ValueError):
        return _err("invalid id", status=400)
    post = _store().get(pid)
    return _ok({"post": post}) if post else _err("post not found", status=404)


async def handle_studio_post_update(request: "web.Request") -> "web.Response":
    try:
        pid = int(request.match_info["id"])
    except (KeyError, ValueError):
        return _err("invalid id", status=400)
    body = await _body(request)
    fields: dict[str, Any] = {}
    for k in ("body", "status", "schedule_kind", "run_at", "cron_expr"):
        if k in body:
            fields[k] = body[k]
    if "content" in body:
        fields["content"] = body["content"]
    if "targets" in body:
        fields["targets"] = body["targets"]
    if not _store().update(pid, **fields):
        return _err("nothing updated", status=400)
    return _ok({"post": _store().get(pid)})


async def handle_studio_post_delete(request: "web.Request") -> "web.Response":
    try:
        pid = int(request.match_info["id"])
    except (KeyError, ValueError):
        return _err("invalid id", status=400)
    return _ok({"deleted": _store().delete(pid)})


async def handle_studio_post_publish(request: "web.Request") -> "web.Response":
    try:
        pid = int(request.match_info["id"])
    except (KeyError, ValueError):
        return _err("invalid id", status=400)
    post = _store().get(pid)
    if not post:
        return _err("post not found", status=404)
    try:
        from navig.social.scheduler_service import ScheduledPostService

        gateway = request.app.get("gateway") if hasattr(request, "app") else None
        svc = ScheduledPostService(gateway)
        result = await svc.run_post(_store(), post)
        return _ok(result)
    except Exception as exc:
        logger.exception("studio publish failed")
        return _err(str(exc), status=502)


# ─── AI assist ───────────────────────────────────────────────────────────────

_AI_PROMPTS = {
    "draft": "Write a concise, engaging social post about the following. Return only the post text.",
    "rewrite": "Rewrite this social post to be clearer and more engaging. Return only the rewritten text.",
    "shorten": "Shorten this social post while keeping its meaning. Return only the shortened text.",
    "hashtags": "Suggest 5-8 relevant hashtags for this post. Return them space-separated, each starting with #.",
    "variants": "Write 3 distinct variants of this social post, one per line. Return only the variants.",
}


async def handle_studio_ai(request: "web.Request") -> "web.Response":
    body = await _body(request)
    action = (body.get("action") or "rewrite").lower()
    text = (body.get("text") or "").strip()
    if not text:
        return _err("'text' is required", status=400)
    system = _AI_PROMPTS.get(action, _AI_PROMPTS["rewrite"])
    try:
        import asyncio

        from navig.llm_generate import llm_generate

        out = await asyncio.to_thread(
            llm_generate,
            [{"role": "system", "content": system}, {"role": "user", "content": text}],
            "summarize" if action in ("shorten", "hashtags") else "big_tasks",
            None, None, None, None, 120,
        )
        return _ok({"action": action, "result": (out or "").strip()})
    except Exception as exc:
        logger.exception("studio ai failed")
        return _err(str(exc), status=502)


# ─── Registration ────────────────────────────────────────────────────────────


def register(app: "web.Application") -> None:
    app.router.add_get("/api/deck/studio/networks", handle_studio_networks)
    app.router.add_post("/api/deck/studio/media", handle_studio_media_upload)
    app.router.add_get("/api/deck/studio/media/{id}/raw", handle_studio_media_raw)
    app.router.add_get("/api/deck/studio/posts", handle_studio_posts_list)
    app.router.add_post("/api/deck/studio/posts", handle_studio_post_create)
    app.router.add_get("/api/deck/studio/posts/{id}", handle_studio_post_get)
    app.router.add_patch("/api/deck/studio/posts/{id}", handle_studio_post_update)
    app.router.add_delete("/api/deck/studio/posts/{id}", handle_studio_post_delete)
    app.router.add_post("/api/deck/studio/posts/{id}/publish", handle_studio_post_publish)
    app.router.add_post("/api/deck/studio/ai", handle_studio_ai)
