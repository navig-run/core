"""Distill a slice of the operations ledger into a draft SKILL.md — over the Deck API.

Surfaces the CLI's ``navig skill distill`` (T-069, ``navig/skill_distill.py``) as
two ``/api/deck/*`` endpoints so a History/Skills UI can offer a one-click
"Distill last session → skill":

    POST /api/deck/skills/distill   → preview (dry-run, default) OR write the draft
    GET  /api/deck/ledger/recent    → the recent operations slice the UI distills from

Both call the SAME engine functions the CLI uses (``slice_ledger`` / ``distill`` /
``render_skill_md``) — no logic is duplicated here. The engine sanitizes secrets
and instance-specific values BEFORE anything leaves it (plan §3), so the preview
markdown and the placeholder review are safe to render.

Contract (mirrors the CLI, minus ``--out`` — the Deck never writes to a
client-supplied path; drafts always land in the user skill store,
``store_dir()/skills/<slug>/SKILL.md``):

    body = {
      "last":    "2h",         # slice window; ignored when "ops" is given
      "ops":     ["op-…", …],  # explicit ids — overrides "last"
      "name":    "deploy-flow",# optional skill id (kebab-cased)
      "dry_run": true,         # DEFAULT: preview only, write nothing
      "force":   false         # only relevant when dry_run=false (overwrite)
    }

Preview (dry_run) returns the drafted markdown + the full ``DistillResult``
summary (steps / pitfalls / placeholders / safety / counts) and the path it
*would* write, without touching disk. A write (dry_run=false) returns the same
plus the real ``path`` and ``overwritten`` flag; it refuses an existing draft
with 409 unless ``force`` is set.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

try:
    from aiohttp import web
except ImportError:  # pragma: no cover - aiohttp always present at runtime
    web = None

logger = logging.getLogger(__name__)

#: Hard ceiling on a single ledger read so a UI cannot ask for the whole history.
_MAX_RECENT = 500


def _ok(data: object, status: int = 200) -> "web.Response":
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 500, *, hint: str = "") -> "web.Response":
    payload: dict[str, Any] = {"ok": False, "error": msg}
    if hint:
        payload["hint"] = hint
    return web.json_response(payload, status=status)


def _op_to_dict(op: Any) -> dict[str, Any]:
    """The fields a ledger/history view needs — the operator's own local data.

    ``command`` is re-redacted at display time. The recorder already redacts
    known secret patterns at record time (T-068), but this route is reachable
    over a Lighthouse-fronted Deck, so an old ledger line written before that
    redaction (or a token the record-time sweep missed) must not surface raw —
    defense-in-depth, the same ``redact_sensitive_text`` the distill engine uses.
    """
    from navig.core.security import redact_sensitive_text

    return {
        "id": op.id,
        "timestamp": op.timestamp,
        "command": redact_sensitive_text(op.command or ""),
        "operation_type": op.operation_type.value,
        "status": op.status.value,
        "exit_code": op.exit_code,
        "host": op.host,
        "reversibility": op.reversibility or "",
    }


async def handle_deck_ledger_recent(request: "web.Request") -> "web.Response":
    """Recent operations (newest first) — the slice a distill would draw from.

    Query: ``?last=2h`` (a slice window) and/or ``?limit=100`` (cap; ≤ 500).
    Without ``last`` it returns the last ``limit`` operations.
    """
    q = request.rel_url.query
    last = (q.get("last") or "").strip()

    try:
        limit = int(q.get("limit", "100"))
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, _MAX_RECENT))

    window = None
    if last:
        from navig.skill_distill import parse_duration

        try:
            window = parse_duration(last)
        except ValueError as exc:
            return _err(str(exc), 400)

    def _read() -> list[dict[str, Any]]:
        from navig.operation_recorder import get_operation_recorder

        recorder = get_operation_recorder()
        since = None
        if window is not None:
            since = (datetime.now(timezone.utc) - window).isoformat()
        ops = recorder.iter_operations(limit=limit, since=since, reverse=True)
        return [_op_to_dict(o) for o in ops]

    try:
        operations = await asyncio.to_thread(_read)
    except Exception as exc:  # noqa: BLE001 — a read must never 500 the panel
        logger.debug("ledger recent read failed: %s", exc)
        operations = []

    return _ok(
        {
            "count": len(operations),
            "window": f"last {last}" if last else "recent",
            "operations": operations,
        }
    )


async def handle_deck_skill_distill(request: "web.Request") -> "web.Response":
    """Preview (default) or write a SKILL.md distilled from the ledger."""
    try:
        body = await request.json()
    except Exception:
        return _err("invalid JSON body", 400)
    if not isinstance(body, dict):
        return _err("invalid JSON body — expected an object", 400)

    last = str(body.get("last") or "2h")
    name = body.get("name")
    name = str(name).strip() if name else None
    dry_run = bool(body.get("dry_run", True))
    force = bool(body.get("force", False))

    raw_ops = body.get("ops")
    op_ids: list[str] | None = None
    if isinstance(raw_ops, list) and raw_ops:
        op_ids = [str(t).strip() for t in raw_ops if str(t).strip()] or None

    from navig.skill_distill import (
        DistillError,
        distill,
        parse_duration,
        render_skill_md,
        slice_ledger,
    )

    window = None
    if not op_ids:
        try:
            window = parse_duration(last)
        except ValueError as exc:
            return _err(str(exc), 400)

    def _compute():
        from navig.operation_recorder import get_operation_recorder

        recorder = get_operation_recorder()
        records = slice_ledger(recorder, last=window, op_ids=op_ids)
        if not records:
            raise DistillError(
                "no operations matched — nothing to distill"
                if op_ids
                else f"no operations recorded in the last {last} — nothing to distill"
            )
        result = distill(records, name=name, window_label="" if op_ids else f"last {last}")
        return result, render_skill_md(result)

    try:
        result, markdown = await asyncio.to_thread(_compute)
    except DistillError as exc:
        return _err(str(exc), 422, hint="inspect the slice with: navig ledger show")
    except Exception as exc:  # noqa: BLE001 — surface, never leak a 500 stack
        logger.exception("skill distill failed")
        return _err(f"distillation failed: {exc}", 500)

    from navig.platform.paths import store_dir

    slug = result.slug
    target_dir = store_dir() / "skills" / slug
    skill_file = target_dir / "SKILL.md"
    exists = skill_file.exists()

    data: dict[str, Any] = {
        **result.to_dict(),
        "markdown": markdown,
        "slug": slug,
        "path": str(skill_file),
        "exists": exists,
        "dry_run": dry_run,
        "lint_hint": f"navig skill lint {target_dir}",
    }

    if dry_run:
        data["would_overwrite"] = exists
        return _ok(data)

    if exists and not force:
        return _err(
            f"'{slug}' already exists: {skill_file}",
            409,
            hint="send force:true to overwrite, or a different name",
        )

    def _write() -> None:
        target_dir.mkdir(parents=True, exist_ok=True)
        skill_file.write_text(markdown, encoding="utf-8")

    try:
        await asyncio.to_thread(_write)
    except OSError as exc:
        return _err(f"could not write {skill_file}: {exc}", 500)

    data["overwritten"] = exists
    return _ok(data, status=201 if not exists else 200)
