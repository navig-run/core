"""Formations + Council routes for the Deck API (slice B6).

The first HTTP surface for the ``navig/formations`` engine — the multi-agent
teams + Council deliberation that used to live only in the archived
navig-formations VS Code extension (ledger: docs/forge-consolidation.md),
now rendered by the OS Mesh app's Formations/Council tabs.

The *engine* stays where it lives: listing reuses
``navig.formations.loader.list_available_formations`` (the same function
behind ``navig formation list``), the active formation prefers the gateway's
boot-time ``FormationRegistry`` cache, and Council runs go through the
pre-existing ``navig.formations.council.run_council`` — the exact entry point
``navig council run`` already uses (its LLM calls ride the existing
``navig.ai`` seam; nothing new is orchestrated here).

Routes (registered in navig/gateway/deck/__init__.py):
    GET  /api/deck/formations                 → discovered formations (+ active id)
    GET  /api/deck/formations/active          → active formation with its agents
    GET  /api/deck/formations/detail?id=      → any formation with its agents
    POST /api/deck/formations/council/run     → run a deliberation (engine-side)
    GET  /api/deck/formations/council/history → past deliberations (?id= for one)

Council results are persisted as JSON under ``data_dir()/council/`` so the OS
can show deliberation history; ``NAVIG_DATA_DIR`` isolates them in tests.
Agent ``system_prompt`` bodies are deliberately NEVER serialized — they are
composed multi-file prompts (SOUL/PERSONALITY/PLAYBOOK) that would bloat every
payload for nothing the UI renders.

Streamed deliberations: ``POST …/council/run`` with ``{"stream": true}``
returns ``202 {council_id, started}`` immediately and broadcasts per-round /
per-agent progress on the gateway event stream (``GET /api/events``) as
``council_update`` events — the SAME broadcast path the board's
``board_update`` rides, so the deck and the OS consume it with their existing
SSE subscriptions. Every event carries the ``council_id`` (== the persisted
history id) for correlation; the terminal ``done`` event carries
``history_id`` so the UI fetches the full record from history (plus
``synthesis_error: true`` when the final synthesis failed — the record's
``final_decision`` is then an operator-facing failure sentence, and the
error message rides the record's ``synthesis_error`` field). Without the
flag the route blocks and returns the full result exactly as before (older
clients unchanged). Only ONE council may run at a time per gateway — a second
run 409s — so event streams never interleave.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from navig.formations.types import Formation

logger = logging.getLogger(__name__)

_MAX_QUESTION_CHARS = 2_000
_MAX_ROUNDS = 5
_MIN_TIMEOUT_S = 5.0
_MAX_TIMEOUT_S = 300.0
_HISTORY_LIMIT = 50
_DECISION_PREVIEW_CHARS = 240
# History ids are our own minted filenames — anything else is a traversal probe.
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


# ── Helpers (house pattern — see routes/plans.py / routes/wiki.py) ────────────

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


def _council_dir() -> Path:
    from navig.platform.paths import data_dir

    return data_dir() / "council"


def _agent_payload(formation: "Formation") -> list[dict[str, Any]]:
    """Agents in declared order; missing/unloadable ones flagged, never dropped."""
    agents: list[dict[str, Any]] = []
    for agent_id in formation.agents:
        spec = formation.loaded_agents.get(agent_id)
        if spec is None:
            agents.append({"id": agent_id, "loaded": False})
            continue
        agents.append({
            "id": spec.id,
            "name": spec.name,
            "role": spec.role,
            "traits": list(spec.traits),
            "personality": spec.personality,
            "scope": list(spec.scope),
            "council_weight": spec.council_weight,
            "tools": list(spec.tools),
            "loaded": True,
            # system_prompt intentionally omitted (see module docstring).
        })
    return agents


def _formation_payload(formation: "Formation") -> dict[str, Any]:
    return {
        "id": formation.id,
        "name": formation.name,
        "version": formation.version,
        "description": formation.description,
        "default_agent": formation.default_agent,
        "aliases": list(formation.aliases),
        "agents": _agent_payload(formation),
        "loaded_count": len(formation.loaded_agents),
        "agents_count": len(formation.agents),
    }


def _active_formation() -> "Formation | None":
    """The gateway's boot-time registry cache first; loader fallback outside it."""
    try:
        from navig.formations.registry import FormationRegistry

        registry = FormationRegistry.get_instance()
        if registry._initialized:  # noqa: SLF001 — read-only initialization probe
            return registry.get_active()
    except Exception:  # noqa: BLE001 — cache miss never blocks the loader path
        logger.debug("formation registry cache unavailable", exc_info=True)
    from navig.formations.loader import get_active_formation

    return get_active_formation()


# ── Formations routes ─────────────────────────────────────────────────────────

async def handle_formations_list(request: "web.Request") -> "web.Response":
    """Discovered formations (lightweight — no agents loaded) + the active id."""

    def _read() -> dict[str, Any]:
        from navig.formations.loader import list_available_formations

        active = _active_formation()
        active_id = active.id if active else None
        formations = [
            {
                "id": f.id,
                "name": f.name,
                "version": f.version,
                "description": f.description,
                "agents_count": len(f.agents),
                "default_agent": f.default_agent,
                "aliases": list(f.aliases),
                "active": f.id == active_id,
            }
            for f in list_available_formations()
        ]
        return {"formations": formations, "active_id": active_id}

    try:
        return _ok(await asyncio.to_thread(_read))
    except Exception as exc:
        logger.exception("formations listing failed")
        return _err(str(exc), 500)


async def handle_formation_active(request: "web.Request") -> "web.Response":
    """The active formation with its agents/roles — ``formation: null`` when none."""

    def _read() -> dict[str, Any]:
        formation = _active_formation()
        return {"formation": _formation_payload(formation) if formation else None}

    try:
        return _ok(await asyncio.to_thread(_read))
    except Exception as exc:
        logger.exception("active formation read failed")
        return _err(str(exc), 500)


async def handle_formation_detail(request: "web.Request") -> "web.Response":
    """Any discovered formation, fully loaded (?id= a formation id or alias)."""
    formation_id = (request.rel_url.query.get("id") or "").strip()
    if not formation_id:
        return _err("id is required")

    def _read() -> dict[str, Any] | None:
        from navig.formations.loader import discover_formations, load_formation

        formation_dir = discover_formations().get(formation_id)
        if formation_dir is None:
            return None
        formation = load_formation(formation_dir)
        return {"formation": _formation_payload(formation)} if formation else None

    try:
        payload = await asyncio.to_thread(_read)
    except Exception as exc:
        logger.exception("formation detail read failed")
        return _err(str(exc), 500)
    if payload is None:
        return _err(f"unknown formation: {formation_id}", 404)
    return _ok(payload)


# ── Council routes ────────────────────────────────────────────────────────────

# One deliberation at a time per gateway (events would interleave otherwise).
# Check-and-set happens synchronously on the event loop (no await between), so
# concurrent requests can never both claim it.
_active_council_id: str | None = None
# Strong ref to the background deliberation (asyncio tasks are weakly held);
# tests await it to drain a streamed run deterministically.
_council_task: "asyncio.Task | None" = None


def _mint_council_id() -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}"


def _persist_council_result(result: dict[str, Any], record_id: str) -> str | None:
    """Write one deliberation record; returns its id (None on write failure)."""
    record = {"id": record_id, "created_at": time.time(), **result}
    try:
        council_dir = _council_dir()
        council_dir.mkdir(parents=True, exist_ok=True)
        path = council_dir / f"{record_id}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return record_id
    except OSError:
        logger.warning("could not persist council record %s", record_id, exc_info=True)
        return None


async def handle_council_run(request: "web.Request") -> "web.Response":
    """Run a Council deliberation over the ACTIVE formation (engine-side).

    Body: ``{question, rounds?, timeout_s?, stream?}``.

    Default (no ``stream``): synchronous — the response carries the full
    engine result (rounds of agent responses + the synthesized final
    decision) and the persisted history id, exactly as before.

    ``stream: true``: returns ``202 {council_id, started: true}`` right away
    and runs the deliberation in the background; progress broadcasts as
    ``council_update`` events on ``GET /api/events`` (see module docstring),
    ending with ``done`` (carrying ``history_id``) or ``error``.

    409 when no formation (or no agents) is active, or when another
    deliberation is already running (single-council guard).
    """
    global _active_council_id, _council_task
    body = await _json(request) or {}
    question = str(body.get("question") or "").strip()
    if not question:
        return _err("question is required")
    if len(question) > _MAX_QUESTION_CHARS:
        return _err(f"question too long (max {_MAX_QUESTION_CHARS} chars)")
    try:
        rounds = int(body.get("rounds", 1))
    except (TypeError, ValueError):
        return _err("rounds must be an integer")
    rounds = max(1, min(rounds, _MAX_ROUNDS))
    timeout_s: float | None = None
    if body.get("timeout_s") is not None:
        try:
            timeout_s = float(body["timeout_s"])
        except (TypeError, ValueError):
            return _err("timeout_s must be a number (seconds)")
        timeout_s = max(_MIN_TIMEOUT_S, min(timeout_s, _MAX_TIMEOUT_S))
    stream = bool(body.get("stream"))

    formation = await asyncio.to_thread(_active_formation)
    if formation is None or not formation.loaded_agents:
        return _err(
            "no active formation with loaded agents — initialize one with "
            "`navig formation init <formation-id>`",
            409,
        )

    # ── Single-council guard (atomic: no await between check and set) ────────
    if _active_council_id is not None:
        return _err("a council deliberation is already running — wait for it to finish", 409)
    council_id = _mint_council_id()
    _active_council_id = council_id

    gateway = request.app.get("gateway") if hasattr(request, "app") else None
    queue = getattr(gateway, "system_events", None) if gateway else None
    loop = asyncio.get_running_loop()

    def _broadcast(event: dict[str, Any]) -> None:
        """Emit one council_update event (thread-safe; telemetry never raises)."""
        if queue is None or not hasattr(queue, "emit"):
            return
        payload = {"council_id": council_id, **event}
        try:
            asyncio.run_coroutine_threadsafe(queue.emit("council_update", payload), loop)
        except Exception as exc:  # noqa: BLE001 — never fail a run on telemetry
            logger.debug("council_update emit failed: %s", exc)

    def _on_engine_event(event: dict[str, Any]) -> None:
        # The engine's terminal "done" is withheld — the route emits the final
        # enriched done (history_id, persisted) AFTER the record is written.
        if event.get("type") == "done":
            return
        _broadcast(event)

    def _run() -> dict[str, Any]:
        from navig.formations.council import run_council

        return run_council(
            formation=formation,
            question=question,
            rounds=rounds,
            timeout_per_agent=timeout_s,
            on_event=_on_engine_event,
        )

    async def _deliberate() -> dict[str, Any]:
        """Run + persist + terminal event; ALWAYS releases the run guard."""
        global _active_council_id
        try:
            try:
                result = await asyncio.to_thread(_run)
            except Exception as exc:
                logger.exception("council run failed")
                _broadcast({"type": "error", "message": str(exc)})
                return {"error_status": 500, "error": str(exc)}
            if result.get("error"):
                _broadcast({"type": "error", "message": str(result["error"])})
                return {"error_status": 500, "error": str(result["error"])}
            record_id = await asyncio.to_thread(_persist_council_result, result, council_id)
            done_event: dict[str, Any] = {
                "type": "done",
                "history_id": record_id,
                "persisted": record_id is not None,
                "overall_confidence": result.get("overall_confidence"),
                "total_duration_ms": result.get("total_duration_ms"),
            }
            if result.get("synthesis_error"):
                # Badge only: synthesis failed — final_decision is an
                # operator-facing sentence, not the council's real answer.
                done_event["synthesis_error"] = True
            _broadcast(done_event)
            return {"record_id": record_id, "result": result}
        finally:
            _active_council_id = None

    if stream:
        _council_task = asyncio.get_running_loop().create_task(_deliberate())
        return _ok({"council_id": council_id, "started": True}, 202)

    outcome = await _deliberate()
    if outcome.get("error") is not None:
        return _err(str(outcome["error"]), int(outcome.get("error_status") or 500))
    record_id = outcome.get("record_id")
    return _ok({
        "id": record_id,
        "council_id": council_id,
        "persisted": record_id is not None,
        "result": outcome["result"],
    })


def _history_summary(record: dict[str, Any]) -> dict[str, Any]:
    decision = str(record.get("final_decision") or "")
    return {
        "id": record.get("id"),
        "created_at": record.get("created_at"),
        "question": record.get("question"),
        "formation": record.get("formation"),
        "pack": record.get("pack"),
        "overall_confidence": record.get("overall_confidence"),
        "agents_count": record.get("agents_count"),
        "rounds": len(record.get("rounds") or []),
        "total_duration_ms": record.get("total_duration_ms"),
        "decision_preview": decision[:_DECISION_PREVIEW_CHARS],
        # Present (truthy string) only on runs whose synthesis failed — lets
        # list UIs badge errored runs without fetching every full record.
        "synthesis_error": record.get("synthesis_error"),
    }


async def handle_council_history(request: "web.Request") -> "web.Response":
    """Past deliberations, newest first; ``?id=`` returns one full record."""
    record_id = (request.rel_url.query.get("id") or "").strip()
    if record_id and not _ID_RE.fullmatch(record_id):
        return _err("invalid history id")

    def _read() -> dict[str, Any] | None:
        council_dir = _council_dir()
        if record_id:
            path = council_dir / f"{record_id}.json"
            if not path.is_file():
                return None
            return {"record": json.loads(path.read_text(encoding="utf-8"))}
        entries: list[dict[str, Any]] = []
        if council_dir.is_dir():
            for path in council_dir.glob("*.json"):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue  # a corrupt record never breaks the feed
                if isinstance(record, dict):
                    entries.append(_history_summary(record))
        entries.sort(key=lambda e: e.get("created_at") or 0, reverse=True)
        return {"entries": entries[:_HISTORY_LIMIT], "count": len(entries)}

    try:
        payload = await asyncio.to_thread(_read)
    except Exception as exc:
        logger.exception("council history read failed")
        return _err(str(exc), 500)
    if payload is None:
        return _err("council record not found", 404)
    return _ok(payload)
