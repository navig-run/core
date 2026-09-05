"""Operations-ledger integrity + confirm-gated Undo — over the Deck API.

Surfaces the shipped ledger engines (T-067/T-068) as two ``/api/deck/*``
endpoints so a History/Activity UI can show chain integrity and offer a
confirm-gated Undo of the last green operation:

    GET  /api/deck/ledger/verify   → re-walk the hash chain, report intact/broken
    POST /api/deck/ledger/undo     → preview (default) OR perform an undo

Both call the SAME engine functions the CLI uses — ``verify_ledger``
(navig.ledger_chain, behind `navig ledger verify`) and the undo engine
(navig.undo, behind `navig undo`). No safety logic is duplicated or relaxed
here: the undo route runs ``ensure_undoable`` + ``check_drift`` before it will
touch anything, and performs through ``execute_undo`` — the ONE write path
that records the undo on the chain (tagged ``undo``). Secrets never surface:
a secret-bearing / sensitive op is refused (409) before any preview is built,
and ``describe_undo`` never renders plaintext.

The recent-operations slice (``GET /api/deck/ledger/recent``) lives in the
sibling ``skill_distill`` module — it is imported by name in
``gateway/deck/__init__.py`` and left there deliberately.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

try:
    from aiohttp import web
except ImportError:  # pragma: no cover - aiohttp always present at runtime
    web = None

logger = logging.getLogger(__name__)


def _ok(data: object, status: int = 200) -> "web.Response":
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 500, *, hint: str = "", code: str = "") -> "web.Response":
    payload: dict[str, Any] = {"ok": False, "error": msg}
    if hint:
        payload["hint"] = hint
    if code:
        # The coarse UndoRefused.code — the same slug /ledger/recent puts in
        # `undo_blocked`, so a client can branch on one vocabulary either way.
        payload["code"] = code
    return web.json_response(payload, status=status)


async def handle_deck_ledger_verify(request: "web.Request") -> "web.Response":
    """Re-walk the operations hash chain and report integrity.

    Returns the clean ``LedgerVerification.to_dict()`` — ``status``
    (intact/broken/legacy/empty/missing), counts (total/chained/verified),
    ``breaks`` (``[{line, reason}]``), the rotation ``anchor``, restarts, and
    the honesty fields (``algorithm``, ``guarantee: "tamper-evident"``). It
    resolves the ledger the same way `navig ledger verify` does —
    ``get_operation_recorder().history_file``.
    """

    def _verify() -> dict[str, Any]:
        from navig.ledger_chain import verify_ledger
        from navig.operation_recorder import get_operation_recorder

        path = get_operation_recorder().history_file
        return verify_ledger(path).to_dict()

    try:
        data = await asyncio.to_thread(_verify)
    except Exception as exc:  # noqa: BLE001 — verify_ledger never raises, but be safe
        logger.exception("ledger verify failed")
        return _err(f"ledger verify failed: {exc}", 500)

    return _ok(data)


async def handle_deck_ledger_undo(request: "web.Request") -> "web.Response":
    """Preview (default) or perform an undo of a specific operation.

    Body: ``{"id": "op-…", "confirm": bool}``.

    - unknown id                         → 404
    - not green / drifted / already-undone / secret-bearing (``UndoRefused``)
                                         → 409 (honest refusal — never bypassed)
    - ``confirm`` false/absent           → dry preview, NO side effects:
      ``{"id", "would", "requires_confirm": true, "label"}``
    - ``confirm`` true                   → perform via ``execute_undo`` (chain-
      recorded, tagged ``undo``):
      ``{"undo_id", "id", "undone"}``

    Mirrors the `navig undo <id>` CLI path exactly (same engine, same guards).
    """
    try:
        body = await request.json()
    except Exception:
        return _err("invalid JSON body", 400)
    if not isinstance(body, dict):
        return _err("invalid JSON body — expected an object", 400)

    raw_id = body.get("id")
    op_id = str(raw_id).strip() if raw_id else ""
    if not op_id:
        return _err("missing 'id' — the operation to undo", 422)
    confirm = bool(body.get("confirm", False))

    def _resolve():
        """Load the target + the undo-marker map off the loop (file reads)."""
        from navig.operation_recorder import get_operation_recorder
        from navig.undo import collect_undone, recent_records

        recorder = get_operation_recorder()
        target = recorder.get_operation(op_id)
        if target is None:
            return recorder, None, {}
        records = recent_records(recorder)
        return recorder, target, collect_undone(records)

    try:
        recorder, target, undone = await asyncio.to_thread(_resolve)
    except Exception as exc:  # noqa: BLE001 — a read must never leak a 500 stack
        logger.exception("ledger undo resolve failed")
        return _err(f"undo failed: {exc}", 500)

    if target is None:
        return _err(f"operation not found: {op_id}", 404)

    from navig.undo import (
        UndoRefused,
        check_drift,
        describe_undo,
        effective_label,
        ensure_undoable,
    )

    # Engine-enforced refusal checks (green-only · drift · double-undo · secrets).
    # check_drift reads current config/files, so run it off the loop too.
    def _guard() -> None:
        ensure_undoable(target, undone)
        check_drift(target)

    try:
        await asyncio.to_thread(_guard)
    except UndoRefused as exc:
        return _err(str(exc), 409, code=getattr(exc, "code", "refused"))
    except Exception as exc:  # noqa: BLE001 — unexpected guard failure, don't leak a stack
        logger.exception("ledger undo guard failed")
        return _err(f"undo failed: {exc}", 500)

    # describe_undo / effective_label are pure reads of the record — safe on the
    # loop, and describe_undo never renders plaintext (secrets already refused).
    if not confirm:
        return _ok(
            {
                "id": target.id,
                "would": describe_undo(target),
                "requires_confirm": True,
                "label": effective_label(target),
            }
        )

    def _perform() -> str:
        from navig.undo import execute_undo

        return execute_undo(recorder, target)

    try:
        undo_id = await asyncio.to_thread(_perform)
    except Exception as exc:  # noqa: BLE001 — failure already recorded by execute_undo
        logger.exception("ledger undo perform failed")
        return _err(f"undo failed: {exc}", 500)

    return _ok(
        {
            "undo_id": undo_id,
            "id": target.id,
            "undone": describe_undo(target),
        },
        status=200,
    )
