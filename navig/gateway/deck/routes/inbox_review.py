"""Inbox review queue + sandbox routes for the Deck API (slice B3).

Surfaces the ``navig.plans`` inbox lifecycle (``.navig/inbox`` markdown items
with suffix states ``.md`` / ``.md.done`` / ``.md.archive`` / ``.md.review``)
over HTTP — the Review Queue and Sandbox that lived in the archived
navig-inbox VS Code extension (ledger: docs/forge-consolidation.md).

This is a DIFFERENT engine from ``routes/inbox.py`` (the ``navig.inbox``
document router over wiki categories): this one is the plans-scoped
approve/reject decision loop with an analyse-without-routing sandbox.

Routes (registered in navig/gateway/deck/__init__.py, prefix /api/deck/inbox/review):
    GET  /                 → list items with review state (+ per-state counts)
    GET  /item?name=<file> → one item's full detail (content, frontmatter, reason)
    POST /analyse          → sandbox: classify + propose a routing target (NO side effects)
    POST /approve          → route content to its target (wiki/docs/plans) + mark .md.done
    POST /reject           → park the item as .md.archive
    POST /requeue          → undo: return any state to the active .md queue

Every route takes an optional ``space`` param (query for GET, body for POST):
a space id from the spaces registry resolved to its root, same as the plans
routes. Omitted → the active project root.

File ops are confined to the space's ``.navig/inbox`` (bare filenames only)
and routing targets confined to ``.navig/`` (traversal → 400). Analysis is
heuristic-only here (``lm_client=None``): the optional LM spot-checks stay
behind the InboxProcessor's LMClient seam — no new LLM call sites.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None  # type: ignore[assignment]

if TYPE_CHECKING:  # heavy engine types only for annotations — runtime imports stay lazy
    from navig.plans.inbox_reader import InboxItem

logger = logging.getLogger(__name__)

_PREVIEW_CHARS = 200

# suffix_state (inbox_reader) → the API's review state vocabulary.
_API_STATE = {"active": "pending", "review": "review", "done": "approved", "archive": "rejected"}


# ── Helpers (house pattern — see routes/plans.py) ─────────────────────────────

def _ok(data: Any, status: int = 200) -> "web.Response":
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 400) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


async def _json(request: "web.Request") -> dict | None:
    try:
        data = await request.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


class _ReviewOpError(Exception):
    """A user-reportable review-operation failure carrying an HTTP status."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _find_project_root() -> Path:
    try:
        from navig.commands.plans import _find_project_root as _fpr

        return _fpr()
    except Exception:
        return Path.cwd()


def _space_root(space_id: str) -> Path | None:
    """Resolve a spaces-registry id to its root path (same seam as plans routes)."""
    try:
        from navig.spaces.resolver import discover_space_paths

        cfg = (discover_space_paths() or {}).get(space_id)
        if cfg is not None:
            p = Path(str(getattr(cfg, "path", "")))
            return p if str(p) else None
    except Exception:
        logger.debug("space resolve failed for %s", space_id, exc_info=True)
    return None


def _resolve_root(space: str | None) -> Path:
    """Space id → space root; no space → the active project root."""
    if space:
        root = _space_root(space)
        if root is None:
            raise _ReviewOpError(f"unknown space: {space}", 404)
        return root
    return _find_project_root()


def _space_param(request: "web.Request", body: dict | None = None) -> str | None:
    space = (body or {}).get("space") or request.rel_url.query.get("space")
    space = str(space).strip() if space else ""
    return space or None


def _item_name(body: dict) -> str:
    """The bare inbox filename from a POST body; bad shapes → 400."""
    name = str(body.get("name") or "").strip()
    if not name:
        raise _ReviewOpError("name is required (a bare .navig/inbox filename)")
    if name.startswith(".") or "/" in name or "\\" in name or Path(name).name != name:
        raise _ReviewOpError("name must be a bare filename inside .navig/inbox")
    return name


# ── Payload serializers (sync — called from thread workers) ───────────────────

def _preview(body: str) -> str:
    text = " ".join(body.split())
    return text[:_PREVIEW_CHARS] + ("…" if len(text) > _PREVIEW_CHARS else "")


def _item_row(item: "InboxItem", decisions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    state = _API_STATE.get(item.suffix_state, item.suffix_state)
    row: dict[str, Any] = {
        "name": item.name,
        "filename": item.path.name,
        "state": state,
        "title": _title_of(item),
        "reason": item.frontmatter.get("review_reason", ""),
        "preview": _preview(item.body),
        "size": item.path.stat().st_size if item.path.exists() else 0,
        "mtime": item.path.stat().st_mtime if item.path.exists() else 0,
    }
    decision = decisions.get(item.name)
    if state == "approved" and decision and decision.get("decision") == "approved":
        row["routed_to"] = decision.get("target")
    return row


def _title_of(item: "InboxItem") -> str:
    title = item.frontmatter.get("title", "")
    if title:
        return title
    return item.name.removesuffix(".md").replace("_", " ").replace("-", " ")


def _analysis_payload(item: "InboxItem", result: Any) -> dict[str, Any]:
    from navig.plans.review_queue import NAMED_TARGETS

    return {
        "name": item.name,
        "filename": item.path.name,
        "state": _API_STATE.get(item.suffix_state, item.suffix_state),
        "title": _title_of(item),
        "decision": result.decision,
        "target_dir": result.target_dir,
        "reason": result.reason,
        "duplicate_of": result.duplicate_of,
        "conflict_with": result.conflict_with,
        "stale_days": result.stale_days,
        "named_targets": dict(NAMED_TARGETS),
        "preview": _preview(item.body),
        "frontmatter": dict(item.frontmatter),
    }


# ── List + detail ─────────────────────────────────────────────────────────────

async def handle_inbox_review_list(request: "web.Request") -> "web.Response":
    """All ``.navig/inbox`` items with review state; optional ``?state=`` filter."""
    state_filter = request.rel_url.query.get("state", "").strip().lower() or None
    if state_filter and state_filter not in _API_STATE.values():
        return _err("state must be one of: pending | review | approved | rejected")
    try:
        root = _resolve_root(_space_param(request))
    except _ReviewOpError as exc:
        return _err(str(exc), exc.status)

    def _read() -> dict[str, Any]:
        from navig.plans.inbox_reader import InboxReader
        from navig.plans.review_queue import ReviewQueue

        decisions = ReviewQueue(root).latest_decisions()
        items = InboxReader(root).scan(include_done=True)
        rows = [_item_row(i, decisions) for i in items]
        counts = dict.fromkeys(("pending", "review", "approved", "rejected"), 0)
        for r in rows:
            if r["state"] in counts:
                counts[r["state"]] += 1
        if state_filter:
            rows = [r for r in rows if r["state"] == state_filter]
        rows.sort(key=lambda r: r.get("mtime") or 0, reverse=True)
        return {"items": rows, "counts": counts}

    try:
        return _ok(await asyncio.to_thread(_read))
    except Exception as exc:
        logger.exception("inbox review list failed")
        return _err(str(exc), 500)


async def handle_inbox_review_item(request: "web.Request") -> "web.Response":
    """One inbox item's full detail (content + frontmatter + review reason)."""
    name = request.rel_url.query.get("name", "").strip()
    try:
        _item_name({"name": name})  # validate the shape (bare filename only)
        root = _resolve_root(_space_param(request))
    except _ReviewOpError as exc:
        return _err(str(exc), exc.status)

    def _read() -> dict[str, Any] | None:
        from navig.plans.inbox_reader import InboxReader
        from navig.plans.review_queue import ReviewQueue

        item = InboxReader(root).read_item(name)
        if item is None:
            return None
        row = _item_row(item, ReviewQueue(root).latest_decisions())
        row["content"] = item.content
        row["frontmatter"] = dict(item.frontmatter)
        row["body"] = item.body
        return row

    try:
        detail = await asyncio.to_thread(_read)
    except Exception as exc:
        logger.exception("inbox review item read failed")
        return _err(str(exc), 500)
    if detail is None:
        return _err("inbox item not found", 404)
    return _ok(detail)


# ── Sandbox (analyse — no side effects) ───────────────────────────────────────

async def handle_inbox_review_analyse(request: "web.Request") -> "web.Response":
    """Sandbox: classify one item + propose a routing target. NO side effects."""
    body = await _json(request) or {}
    try:
        name = _item_name(body)
        root = _resolve_root(_space_param(request, body))
    except _ReviewOpError as exc:
        return _err(str(exc), exc.status)

    def _run() -> dict[str, Any] | None:
        from navig.plans.inbox_processor import InboxProcessor
        from navig.plans.inbox_reader import InboxReader

        reader = InboxReader(root)
        item = reader.read_item(name)
        if item is None:
            return None
        # Heuristic-only sandbox: the LM spot-check seam (LMClient) stays unset —
        # LLM classification is the agent layer's job, never a new call site here.
        corpus = reader.scan(include_done=True)
        result = InboxProcessor(root, lm_client=None).analyse(item, corpus=corpus)
        return _analysis_payload(item, result)

    try:
        payload = await asyncio.to_thread(_run)
    except Exception as exc:
        logger.exception("inbox review analyse failed")
        return _err(str(exc), 500)
    if payload is None:
        return _err("inbox item not found", 404)
    return _ok(payload)


# ── Decisions (approve / reject / requeue) ────────────────────────────────────

async def handle_inbox_review_approve(request: "web.Request") -> "web.Response":
    """Approve: route the item to its target (wiki/docs/plans) + mark ``.md.done``."""
    body = await _json(request) or {}
    try:
        name = _item_name(body)
        root = _resolve_root(_space_param(request, body))
    except _ReviewOpError as exc:
        return _err(str(exc), exc.status)
    target = body.get("target")
    if target is not None and not isinstance(target, str):
        return _err("target must be a string (wiki/docs/plans or a .navig-relative dir)")

    def _run() -> dict[str, Any] | None:
        from navig.plans.review_queue import ReviewQueue

        return ReviewQueue(root).approve_item(name, target=target)

    try:
        result = await asyncio.to_thread(_run)
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        logger.exception("inbox review approve failed")
        return _err(str(exc), 500)
    if result is None:
        return _err("inbox item not found", 404)
    return _ok(result)


async def _decision_mutation(request: "web.Request", fn_name: str) -> "web.Response":
    """Shared thin wrapper over a :class:`ReviewQueue` state mutation."""
    body = await _json(request) or {}
    try:
        name = _item_name(body)
        root = _resolve_root(_space_param(request, body))
    except _ReviewOpError as exc:
        return _err(str(exc), exc.status)

    def _run() -> dict[str, Any] | None:
        from navig.plans.review_queue import ReviewQueue

        return getattr(ReviewQueue(root), fn_name)(name)

    try:
        result = await asyncio.to_thread(_run)
    except Exception as exc:
        logger.exception("inbox review %s failed", fn_name)
        return _err(str(exc), 500)
    if result is None:
        return _err("inbox item not found", 404)
    return _ok(result)


async def handle_inbox_review_reject(request: "web.Request") -> "web.Response":
    """Reject: park the item as ``.md.archive`` (never deleted)."""
    return await _decision_mutation(request, "reject_item")


async def handle_inbox_review_requeue(request: "web.Request") -> "web.Response":
    """Undo: return an item (any state) to the active ``.md`` queue."""
    return await _decision_mutation(request, "requeue_item")
