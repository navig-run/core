"""Deck API — model fallback status + policy (Settings → AI & Models → Fallbacks).

``GET  /api/deck/llm/fallbacks`` → per mode: primary, health (liveness cache),
configured + effective fallback, degraded state; the policy; the last 20 switches;
and the candidate models (healthy ones the operator can pick).

``POST /api/deck/llm/fallbacks`` → any of::

    {"policy": {"auto": bool, "prefer": [spec], "never_from": [mode], "never_to": [p|spec]}}
    {"mode": "big_tasks", "fallback": "provider:model"}   # "" or null clears it
    {"mode": "coding", "never": true}                     # toggle never_from for one mode

Everything is validated BEFORE anything is written; the response is the GET payload
read back after the write, so the page renders what was actually stored. The data
lives in :mod:`navig.llm.model_fallback` — the CLI (``navig ai fallback``) shows the
same thing.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_PROVIDER_RE = re.compile(r"^[a-z0-9_.\-]+$")


def _payload() -> dict[str, Any]:
    from navig.llm import model_fallback

    return model_fallback.status(history_limit=20)


async def handle_deck_llm_fallbacks_get(request: "web.Request") -> "web.Response":
    try:
        data = await asyncio.to_thread(_payload)
    except Exception as exc:  # noqa: BLE001
        logger.error("fallback status failed: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)
    return web.json_response({"ok": True, **data})


def _bad(msg: str) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=400)


def _validate(body: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Turn the body into a write plan, or an error message. Writes nothing."""
    from navig.llm import model_fallback
    from navig.llm.router import CANONICAL_MODES

    plan: dict[str, Any] = {}
    pol = body.get("policy")
    if pol is not None:
        if not isinstance(pol, dict):
            return {}, "policy must be an object"
        unknown = set(pol) - {"auto", "prefer", "never_from", "never_to"}
        if unknown:
            return {}, f"unknown policy field(s): {', '.join(sorted(unknown))}"
        if "auto" in pol:
            if not isinstance(pol["auto"], bool):
                return {}, "policy.auto must be true or false"
            plan["auto"] = pol["auto"]
        for key in ("prefer", "never_from", "never_to"):
            if key not in pol:
                continue
            val = pol[key]
            if not isinstance(val, list) or not all(isinstance(v, str) for v in val):
                return {}, f"policy.{key} must be a list of strings"
            items = [v.strip() for v in val if v.strip()]
            if key == "prefer":
                bad = [v for v in items if not model_fallback.valid_spec(v)]
                if bad:
                    return {}, f"policy.prefer: expected provider:model, got {bad[0]!r}"
            elif key == "never_from":
                items = [v.lower() for v in items]
                bad = [v for v in items if v not in CANONICAL_MODES]
                if bad:
                    return {}, f"policy.never_from: unknown mode {bad[0]!r}"
            else:
                items = [v.lower() for v in items]
                bad = [v for v in items
                       if not (model_fallback.valid_spec(v) or _PROVIDER_RE.match(v))]
                if bad:
                    return {}, f"policy.never_to: expected a provider or provider:model, got {bad[0]!r}"
            plan[key] = items

    mode = body.get("mode")
    if mode is not None:
        if not isinstance(mode, str) or mode.strip().lower() not in CANONICAL_MODES:
            return {}, f"invalid mode: {mode!r} (modes: {', '.join(sorted(CANONICAL_MODES))})"
        mode = mode.strip().lower()
        if "fallback" in body:
            fb = body["fallback"]
            if fb is not None and not isinstance(fb, str):
                return {}, "fallback must be a provider:model string, or empty to clear"
            fb = (fb or "").strip()
            if fb and not model_fallback.valid_spec(fb):
                return {}, f"fallback: expected provider:model, got {fb!r}"
            plan["mode_fallback"] = (mode, fb or None)
        if "never" in body:
            if not isinstance(body["never"], bool):
                return {}, "never must be true or false"
            plan["mode_never"] = (mode, body["never"])
        if "fallback" not in body and "never" not in body:
            return {}, "mode given without fallback or never"
    elif "fallback" in body or "never" in body:
        return {}, "mode is required with fallback/never"

    if not plan:
        return {}, "nothing to change"
    if "never_from" in plan and "mode_never" in plan:
        return {}, "send policy.never_from OR mode+never, not both"
    return plan, None


def _apply(plan: dict[str, Any]) -> None:
    from navig.llm import model_fallback

    never_from = plan.get("never_from")
    if "mode_never" in plan:
        mode, never = plan["mode_never"]
        cur = list(model_fallback.load_policy().never_from)
        if never and mode not in cur:
            cur.append(mode)
        if not never:
            cur = [m for m in cur if m != mode]
        never_from = cur
    if any(k in plan for k in ("auto", "prefer", "never_to")) or never_from is not None:
        model_fallback.save_policy(
            auto=plan.get("auto"), prefer=plan.get("prefer"),
            never_from=never_from, never_to=plan.get("never_to"),
        )
    if "mode_fallback" in plan:
        mode, spec = plan["mode_fallback"]
        model_fallback.set_mode_fallback(mode, spec)


async def handle_deck_llm_fallbacks_post(request: "web.Request") -> "web.Response":
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _bad("invalid json")
    if not isinstance(body, dict):
        return _bad("body must be an object")
    plan, err = _validate(body)
    if err:
        return _bad(err)
    try:
        await asyncio.to_thread(_apply, plan)
    except ValueError as exc:
        return _bad(str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.error("fallback policy write failed: %s", exc)
        return web.json_response({"ok": False, "error": f"not saved: {exc}"}, status=500)
    try:
        data = await asyncio.to_thread(_payload)
    except Exception as exc:  # noqa: BLE001
        return web.json_response({"ok": True, "warning": f"saved, but read-back failed: {exc}"})
    return web.json_response({"ok": True, **data})
