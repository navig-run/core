"""Inbox document routing for the Deck API.

Surfaces inbox documents (un-routed files + routed history) with full controls,
backed by the existing ``navig.inbox`` engine (classifier + router + store).

    GET  /api/deck/inbox[?status=&classify=]   → events (pending scan + history)
    POST /api/deck/inbox/{id}/route            → route one into a space/dest
    POST /api/deck/inbox/{id}/skip             → keep in inbox (ignore)
    POST /api/deck/inbox/{id}/reroute          → route to a different space/dest
    POST /api/deck/inbox/process-all           → batch-route; low-confidence → asks

Document routing is FREE (core_ops). The optional ``?classify=llm`` branch is
the only AI-gated bit: without the ``ai_operator`` capability it falls back to
the heuristic BM25 classifier and flags ``needs_ai_operator`` rather than 402.

Registered in ``navig/gateway/deck/__init__.py``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

from navig.inbox.extract_hook import content_for_classify

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)

_MIN_CONFIDENCE = 0.30  # below this, ask the user instead of auto-routing


def _ok(data: object, status: int = 200) -> "web.Response":
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 500) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


def _gateway(request: "web.Request"):
    return request.app.get("gateway") if hasattr(request, "app") else None


def _has_capability(module: str) -> bool:
    try:
        from navig.license import current_status

        return module in list(current_status().capabilities or [])
    except Exception:
        return False


def _find_project_root() -> Path:
    try:
        from navig.commands.plans import _find_project_root as _fpr

        return _fpr()
    except Exception:
        return Path.cwd()


def _space_inbox_dirs() -> list[tuple[Path, str]]:
    """``(.space/inbox dir, space_name)`` for every discovered space.

    Ensures the ``.space/inbox`` folder exists for each real space so the user
    can drop files straight into a space's inbox — "adding a space inbox" is just
    having a space. (Mirrors how ``InboxWatcher`` auto-creates its watched dirs.)
    """
    out: list[tuple[Path, str]] = []
    try:
        from navig.spaces.resolver import discover_space_paths

        for name, cfg in (discover_space_paths() or {}).items():
            base = str(getattr(cfg, "path", "") or "")
            if not base:
                continue
            inbox = Path(base) / ".space" / "inbox"
            try:
                if inbox.parent.exists():  # a real space has a .space/ marker
                    inbox.mkdir(parents=True, exist_ok=True)
            except Exception:
                logger.debug("could not ensure space inbox %s", inbox, exc_info=True)
            out.append((inbox, name))
    except Exception:
        logger.debug("space inbox discovery failed", exc_info=True)
    return out


def _scan_inbox_entries(project_root: Path) -> list[tuple[Path, str | None]]:
    """Un-routed files from global + project + per-space inboxes (deduped).

    Each entry is ``(file, space_name | None)`` so callers can show which space a
    document came from and route it back into that space. ``None`` = the global /
    project inbox (no owning space).
    """
    sources: list[tuple[Path, str | None]] = [
        (project_root / ".navig" / "wiki" / "inbox", None),
        (project_root / ".navig" / "plans" / "inbox", None),
    ]
    try:
        from navig.platform.paths import navig_data_dir

        sources.append((navig_data_dir() / "inbox", None))
    except Exception:
        pass
    sources.extend(_space_inbox_dirs())

    entries: list[tuple[Path, str | None]] = []
    seen: set[str] = set()
    for d, space in sources:
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if not f.is_file() or f.name.startswith("."):
                continue
            rp = str(f.resolve())
            if rp not in seen:
                seen.add(rp)
                entries.append((f, space))
    return entries


def _scan_inbox_dirs(project_root: Path) -> list[Path]:
    """Back-compat: just the un-routed files, without space attribution."""
    return [f for f, _ in _scan_inbox_entries(project_root)]


def _space_root(space_name: str | None) -> Path | None:
    if not space_name:
        return None
    try:
        from navig.spaces.resolver import discover_space_paths

        cfg = (discover_space_paths() or {}).get(space_name)
        if cfg is not None:
            p = Path(str(getattr(cfg, "path", "")))
            return p if str(p) else None
    except Exception:
        logger.debug("space resolve failed for %s", space_name, exc_info=True)
    return None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _resolve_source(event_id: str) -> tuple[Path | None, object | None]:
    """Resolve an event id to a source Path. Pending files use ``path:<abs>``."""
    if event_id.startswith("path:"):
        return Path(event_id[5:]), None
    try:
        eid = int(event_id)
    except ValueError:
        return None, None
    from navig.inbox.store import InboxStore

    ev = InboxStore().get_event(eid)
    return (Path(ev.source_path), ev) if ev else (None, None)


def _route_file(
    source: Path,
    project_root: Path,
    *,
    space: str | None = None,
    destination: str | None = None,
    category: str | None = None,
    mode: str = "copy",
    conflict: str = "rename",
) -> dict:
    """Classify (unless category given), route, and persist. Returns a summary."""
    from navig.inbox.classifier import Classifier, ClassifyResult
    from navig.inbox.router import ConflictStrategy, InboxRouter, RouteMode
    from navig.inbox.store import InboxEvent, InboxStore, RoutingDecision

    content = content_for_classify(source, full=True)
    if category:
        cr = ClassifyResult(category=category, confidence=1.0, method="manual")
    else:
        cr = Classifier(use_llm=False).classify(content, filename=source.name)

    root = _space_root(space) or project_root
    dest_override = {cr.category: destination} if destination else None
    router = InboxRouter(
        project_root=root,
        mode=RouteMode(mode) if mode in ("copy", "move", "link") else RouteMode.COPY,
        conflict=(
            ConflictStrategy(conflict)
            if conflict in ("rename", "skip", "overwrite")
            else ConflictStrategy.RENAME
        ),
        dest_override=dest_override,
    )

    from navig.inbox.extract import _kind_for

    if _kind_for(source) == "text":
        result = router.route(source, cr, dry_run=False)
    else:
        # Binary: preserve the original bytes under _originals/ (never lost) and
        # route the EXTRACTED markdown so the wiki holds searchable text.
        from navig.inbox import retention
        from navig.inbox.extract import extract, to_markdown
        from navig.inbox.extract_hook import _shared_cache

        preserved = retention.preserve_original(source, root)
        res = extract(source, cache=_shared_cache())
        md = to_markdown(res, source_label=source.name, original_preserved=preserved)
        result = router.route_url(str(source), md, f"{source.stem}.md", cr)
    executed = result.status in ("routed", "redirected")

    store = InboxStore()
    ev = InboxEvent(
        source_path=str(source),
        source_type="file",
        filename=source.name,
        size_bytes=source.stat().st_size if source.exists() else 0,
        content_hash=hashlib.sha256(content.encode()).hexdigest()[:16],
        status="routed" if executed else result.status,
    )
    eid = store.insert_event(ev)
    store.insert_decision(
        RoutingDecision(
            event_id=eid,
            category=cr.category,
            confidence=cr.confidence,
            mode=router.mode.value,
            destination=result.destination or "",
            result_path=result.result_path,
            executed=executed,
            classifier=cr.method,
        )
    )
    return {
        "status": result.status,
        "destination": result.destination,
        "result_path": result.result_path,
        "category": cr.category,
        "confidence": cr.confidence,
        "error": result.error,
    }


def _decision_dict(d) -> dict:
    return {
        "category": d.category,
        "confidence": d.confidence,
        "mode": d.mode,
        "destination": d.destination,
        "executed": bool(d.executed),
        "classifier": d.classifier,
    }


async def handle_deck_inbox_list(request: "web.Request") -> "web.Response":
    """List inbox documents: live-scanned pending files + routed history."""
    status = request.rel_url.query.get("status")
    want_llm = request.rel_url.query.get("classify") == "llm"
    project_root = _find_project_root()

    rows: list[dict] = []
    needs_ai = False

    # ── Pending: live-scan the inbox folders and classify on the fly ──────────
    if status in (None, "pending"):
        from navig.inbox.classifier import Classifier
        from navig.inbox.router import InboxRouter

        use_llm = want_llm and _has_capability("ai_operator")
        if want_llm and not use_llm:
            needs_ai = True
        classifier = Classifier(use_llm=use_llm)
        routers: dict[str, InboxRouter] = {}

        def _router_for(space: str | None) -> InboxRouter:
            root = _space_root(space) or project_root
            key = str(root)
            if key not in routers:
                routers[key] = InboxRouter(project_root=root)
            return routers[key]

        def _scan() -> list[dict]:
            out: list[dict] = []
            for f, space in _scan_inbox_entries(project_root):
                # Cheap pass for the live list — filename/kind only, no OCR/STT.
                content = content_for_classify(f, full=False)
                cr = classifier.classify(content, filename=f.name)
                preview = _router_for(space).route(f, cr, dry_run=True)
                from navig.inbox.extract import _kind_for

                out.append({
                    "id": "path:" + str(f.resolve()),
                    "created_at": f.stat().st_mtime,
                    "filename": f.name,
                    "source_path": str(f),
                    "source_type": "file",
                    "status": "pending",
                    "space": space,
                    "kind": _kind_for(f),
                    "decision": {
                        "category": cr.category,
                        "confidence": cr.confidence,
                        "mode": "copy",
                        "destination": preview.destination or "",
                        "executed": False,
                        "classifier": cr.method,
                    },
                })
            return out

        try:
            rows.extend(await asyncio.to_thread(_scan))
        except Exception:
            logger.exception("inbox scan failed")

    # ── History: routed / ignored / error rows from the store ─────────────────
    if status != "pending":
        try:
            from navig.inbox.store import InboxStore

            store = InboxStore()
            for ev in store.list_events(status=status, limit=200):
                decisions = store.decisions_for_event(ev.id) if ev.id else []
                latest = decisions[-1] if decisions else None
                rows.append({
                    "id": str(ev.id),
                    "created_at": ev.created_at,
                    "filename": ev.filename,
                    "source_path": ev.source_path,
                    "source_type": ev.source_type,
                    "status": ev.status,
                    "decision": _decision_dict(latest) if latest else None,
                })
        except Exception:
            logger.exception("inbox history read failed")

    rows.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
    return _ok({"events": rows, "needs_ai_operator": needs_ai})


async def handle_deck_inbox_route(request: "web.Request") -> "web.Response":
    """Route one document into a chosen space / destination."""
    event_id = request.match_info.get("event_id", "")
    try:
        body = await request.json()
    except Exception:
        body = {}

    source, _ev = _resolve_source(event_id)
    if source is None or not source.exists():
        return _err("source not found", 404)

    project_root = _find_project_root()
    try:
        result = await asyncio.to_thread(
            _route_file,
            source,
            project_root,
            space=body.get("space"),
            destination=body.get("destination"),
            category=body.get("category"),
            mode=body.get("mode", "copy"),
            conflict=body.get("conflict_strategy", "rename"),
        )
    except Exception as exc:
        logger.exception("inbox route failed")
        return _err(str(exc))

    if result.get("status") in ("routed", "redirected"):
        await _emit_inbox_event(request, "inbox_routed", {
            "filename": source.name,
            "category": result.get("category"),
            "destination": result.get("destination"),
        })
        await _reindex(project_root)
    return _ok(result)


async def handle_deck_inbox_skip(request: "web.Request") -> "web.Response":
    """Keep a document in the inbox (mark ignored)."""
    event_id = request.match_info.get("event_id", "")
    from navig.inbox.store import InboxEvent, InboxStore

    store = InboxStore()
    if event_id.startswith("path:"):
        p = Path(event_id[5:])
        store.insert_event(
            InboxEvent(source_path=str(p), source_type="file", filename=p.name, status="ignored")
        )
        return _ok({"status": "ignored"})
    try:
        eid = int(event_id)
    except ValueError:
        return _err("bad id", 400)
    store.update_event_status(eid, "ignored")
    return _ok({"status": "ignored"})


async def handle_deck_inbox_reroute(request: "web.Request") -> "web.Response":
    """Route a document to a different space / destination (same as route)."""
    return await handle_deck_inbox_route(request)


async def _emit_inbox_event(request: "web.Request", kind: str, payload: dict) -> None:
    """Best-effort SSE broadcast so every surface refreshes live."""
    try:
        gateway = request.app.get("gateway") if hasattr(request, "app") else None
        queue = getattr(gateway, "system_events", None) if gateway else None
        if queue and hasattr(queue, "emit"):
            await queue.emit(kind, payload)
    except Exception as exc:  # never fail a mutation because of telemetry
        logger.debug("inbox SSE emit failed: %s", exc)


async def handle_deck_inbox_promote(request: "web.Request") -> "web.Response":
    """Promote an inbox item up the plan tiers (roadmap / deferred / after-mvp).

    Never deletes: appends a summarized bullet to the active space's plan file and
    drops a wiki record. Body: ``{to_tier, space?, summary?}``.
    """
    event_id = request.match_info.get("event_id", "")
    try:
        body = await request.json()
    except Exception:
        body = {}

    to_tier = body.get("to_tier") or body.get("to") or "roadmap"
    space = body.get("space")
    summary = body.get("summary")
    project_root = _find_project_root()

    from navig.inbox.promotion import promote as _promote
    from navig.inbox.store import InboxStore

    try:
        result = await asyncio.to_thread(
            _promote,
            event_id,
            to_tier=to_tier,
            space=space,
            summary=summary,
            project_root=project_root,
            store=InboxStore(),
        )
    except Exception as exc:
        logger.exception("inbox promote failed")
        return _err(str(exc))

    if not result.get("ok"):
        return _err(result.get("error") or "promotion failed", 400)

    await _emit_inbox_event(request, "inbox_promoted", {
        "to_tier": result["to_tier"],
        "plan_file": result["plan_file"],
        "summary": result["summary"],
    })
    # Refresh searchable index so the promoted bullet/record reaches the LLM context.
    await _reindex(project_root)
    return _ok(result)


async def _reindex(project_root: Path) -> None:
    """Incrementally re-index .navig/wiki + plans so new content is searchable."""
    try:
        from navig.memory.project_indexer import ProjectIndexer

        await asyncio.to_thread(lambda: ProjectIndexer(project_root).update_incremental())
    except Exception as exc:  # noqa: BLE001
        logger.debug("reindex skipped: %s", exc)


def _unique_path(dest_dir: Path, filename: str) -> Path:
    """A non-colliding path in *dest_dir* for *filename* (basename only)."""
    safe = Path(filename).name or "upload.bin"
    target = dest_dir / safe
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    i = 1
    while True:
        cand = dest_dir / f"{stem}_{i}{suffix}"
        if not cand.exists():
            return cand
        i += 1


def _upload_dest(space: str | None) -> Path:
    """Resolve where dropped files land: a space's ``.space/inbox`` or global."""
    if space:
        root = _space_root(space)
        if root is not None:
            return root / ".space" / "inbox"
    try:
        from navig.platform.paths import navig_data_dir

        return navig_data_dir() / "inbox"
    except Exception:
        return _find_project_root() / ".navig" / "wiki" / "inbox"


async def handle_deck_inbox_upload(request: "web.Request") -> "web.Response":
    """Accept dropped files (multipart) into an inbox folder for processing.

    Body (``multipart/form-data``):
      ``files`` — one or more file parts (repeatable)
      ``space`` — optional space name → writes into that space's ``.space/inbox/``

    Files land in the target inbox dir, where the normal scan/classify/route
    pipeline picks them up — they appear as ``pending`` in ``GET /api/deck/inbox``.
    """
    ctype = request.content_type or ""
    if "multipart/form-data" not in ctype:
        return _err("expected multipart/form-data", 400)

    try:
        reader = await request.multipart()
    except Exception:
        return _err("could not read upload", 400)

    space: str | None = None
    files: list[tuple[str, bytes]] = []
    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name == "space":
            val = await part.read()
            space = val.decode("utf-8", "replace").strip() or None
        elif part.name in ("files", "file") and part.filename:
            files.append((part.filename, await part.read()))

    if not files:
        return _err("no files in upload", 400)

    dest = _upload_dest(space)
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return _err(f"could not open inbox folder: {exc}", 500)

    saved = 0
    errors = 0
    for filename, data in files:
        try:
            _unique_path(dest, filename).write_bytes(data)
            saved += 1
        except Exception:
            logger.exception("inbox upload write failed for %s", filename)
            errors += 1

    return _ok({"saved": saved, "errors": errors, "space": space, "dest": str(dest)})


async def handle_deck_inbox_capture(request: "web.Request") -> "web.Response":
    """Capture a page / selection / link / signal into the inbox (PRODUCER path).

    Body (JSON): ``{type?, url?, title?, content?, selection?, space?}``. Writes a
    markdown note into the inbox folder where the normal scan/route pipeline picks
    it up. Used by the browser extension's "Send to NAVIG inbox" and by signals.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    kind = (body.get("type") or "page").strip()
    url = (body.get("url") or "").strip()
    title = (body.get("title") or url or "Captured item").strip()
    content = (body.get("content") or body.get("selection") or "").strip()
    space = body.get("space")

    lines = [f"# {title}", ""]
    if url:
        lines += [f"Source: {url}", ""]
    if content:
        lines.append(content)
    note = "\n".join(lines) + "\n"

    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in title.lower())[:48].strip("-") or "capture"
    dest = _upload_dest(space)
    try:
        dest.mkdir(parents=True, exist_ok=True)
        target = _unique_path(dest, f"{slug}.md")
        from navig.core.yaml_io import atomic_write_text

        atomic_write_text(target, note)
    except Exception as exc:
        return _err(f"capture write failed: {exc}", 500)

    from navig.inbox.store import InboxEvent, InboxStore

    try:
        InboxStore().insert_event(
            InboxEvent(
                source_path=str(target),
                source_type="url" if url else "capture",
                filename=target.name,
                size_bytes=len(note.encode()),
                status="pending",
            )
        )
    except Exception:  # noqa: BLE001
        logger.debug("capture event insert failed", exc_info=True)

    await _emit_inbox_event(request, "inbox_captured", {"title": title, "type": kind, "dest": str(target)})
    return _ok({"captured": True, "path": str(target), "type": kind, "space": space})


async def handle_deck_extract_mode_get(request: "web.Request") -> "web.Response":
    """Current extraction posture (local | auto | cloud) for the inbox."""
    from navig.inbox.extract import ExtractPolicy

    return _ok({"mode": ExtractPolicy.from_config().mode})


async def handle_deck_extract_mode_set(request: "web.Request") -> "web.Response":
    """Set the extraction posture. ``local`` = offline only; ``auto`` = local-first,
    cloud when keys+budget allow; ``cloud`` = prefer cloud providers."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    mode = (body.get("mode") or "").strip().lower()
    if mode not in ("local", "auto", "cloud"):
        return _err("mode must be one of: local | auto | cloud", 400)

    def _persist() -> None:
        from navig.core import Config

        cfg = Config()
        cfg.set("inbox.extract.mode", mode, scope="global")
        cfg.save(scope="global")

    try:
        await asyncio.to_thread(_persist)
    except Exception as exc:
        logger.exception("set extract mode failed")
        return _err(str(exc))
    return _ok({"mode": mode})


async def handle_deck_plan_context(request: "web.Request") -> "web.Response":
    """Canonical context snapshot — the one engine every surface should consume
    instead of re-reading ``.navig/`` (replaces forge/spaces TS reimplementations).

    Query: ``?space=<name>`` (default: active space). Returns the PlanContext
    snapshot + the formatted system-prompt block.
    """
    space = request.rel_url.query.get("space")
    project_root = _find_project_root()

    def _gather() -> dict:
        from navig.plans.context import PlanContext

        pc = PlanContext(cwd=project_root)
        snapshot = pc.gather(space)
        return {"snapshot": snapshot, "prompt": pc.format_for_prompt(snapshot)}

    try:
        result = await asyncio.to_thread(_gather)
    except Exception as exc:
        logger.exception("plan context gather failed")
        return _err(str(exc))
    return _ok(result)


async def handle_deck_inbox_process_all(request: "web.Request") -> "web.Response":
    """Batch-route all pending docs. Low-confidence items become route-asks."""
    project_root = _find_project_root()
    gw = _gateway(request)
    reg = getattr(gw, "request_registry", None) if gw else None

    from navig.inbox.classifier import Classifier

    classifier = Classifier(use_llm=False)
    entries = await asyncio.to_thread(_scan_inbox_entries, project_root)

    routed = 0
    asked = 0
    errors = 0
    for f, space in entries:
        content = content_for_classify(f, full=True)
        cr = classifier.classify(content, filename=f.name)

        if cr.confidence < _MIN_CONFIDENCE and reg is not None:
            # Ambiguous — ask the user where it should go.
            src = f  # bind for the closure

            async def _on_answer(answer: dict, _src: Path = src, _space: str | None = space) -> None:
                choice = answer.get("choice")
                if choice in (None, "skip"):
                    from navig.inbox.store import InboxEvent, InboxStore

                    InboxStore().insert_event(
                        InboxEvent(
                            source_path=str(_src), source_type="file",
                            filename=_src.name, status="ignored",
                        )
                    )
                    return
                await asyncio.to_thread(
                    _route_file, _src, project_root,
                    category=choice if isinstance(choice, str) else None,
                    destination=answer.get("custom"),
                    space=_space,
                )

            await reg.create(
                kind="route",
                title=f"Where should '{f.name}' go?",
                body=f"navig isn't sure ({cr.confidence:.0%} confidence). Pick a destination.",
                options=[
                    {"id": cr.category, "label": f"Route to {cr.category}"},
                    *[{"id": alt, "label": f"Route to {alt}"} for alt, _ in cr.alternatives[:2]],
                    {"id": "skip", "label": "Keep in inbox"},
                ],
                allow_custom=True,
                source="inbox",
                priority="normal",
                on_answer=_on_answer,
            )
            asked += 1
            continue

        try:
            res = await asyncio.to_thread(
                _route_file, f, project_root, category=cr.category, space=space
            )
            if res.get("status") in ("routed", "redirected"):
                routed += 1
            else:
                errors += 1
        except Exception:
            logger.exception("process-all route failed for %s", f)
            errors += 1

    return _ok({"routed": routed, "asks": asked, "errors": errors, "total": len(entries)})
