"""
Persona settings handlers for the Deck API.

Exposes the persona switcher that used to be invoked via /persona and /personas
in chat. The slash commands are deleted; users now switch via the Deck (or via
natural language phrases like "switch persona to tyler", handled by the NL
settings-intent module).

Routes:
    GET  /api/deck/persona    → active persona, installed personas, and the community
                                personas available to install (``available``)
    POST /api/deck/persona    → switch to a named persona

Persona discovery walks the filesystem (and, cold, the plugin package scan), so
every blocking call runs in a worker thread — never on the event loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

try:
    from aiohttp import web
except ImportError:
    web = None

from navig.gateway.deck.routes.telegram_bot import _OWNER_UNKNOWN, resolve_owner_user_id

logger = logging.getLogger(__name__)

# The built-in NAVIG voice. Always listed first and labelled "Default" in the deck.
DEFAULT_PERSONA = "default"


def _resolve_chat_id(request: "web.Request", owner: int) -> int:
    """Best-effort chat_id resolution. Falls back to the owner (their DM) when unknown."""
    val = request.get("deck_chat_id")
    if val:
        try:
            return int(val)
        except Exception:
            pass
    return owner


#: Where "Install" downloads from (``github:navig-run/registry/personas/<id>``).
_UPSTREAM_PERSONAS_URL = "https://api.github.com/repos/navig-run/registry/contents/personas"
_UPSTREAM_TTL_S = 3600.0
_upstream_cache: tuple[float, frozenset[str]] | None = None


def _upstream_persona_ids() -> frozenset[str]:
    """Persona ids that actually exist where Install downloads from.

    The local registry index can run ahead of the published community repo — it
    listed ``the-navigator`` while upstream did not have it, so the picker offered
    an Install that could only fail. Cached for an hour; raises when GitHub cannot
    be reached so the caller can say so instead of offering a doomed button.
    """
    global _upstream_cache
    import json
    import time
    import urllib.request

    now = time.monotonic()
    if _upstream_cache is not None and now - _upstream_cache[0] < _UPSTREAM_TTL_S:
        return _upstream_cache[1]
    req = urllib.request.Request(
        _UPSTREAM_PERSONAS_URL,
        headers={"User-Agent": "navig-deck", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 — fixed https URL
        listing = json.loads(resp.read().decode("utf-8"))
    ids = frozenset(
        str(item.get("name"))
        for item in listing
        if isinstance(item, dict) and item.get("type") == "dir" and item.get("name")
    )
    _upstream_cache = (now, ids)
    return ids


def _available_personas(installed: set[str]) -> list[dict[str, Any]]:
    """Community personas (the catalog's source) that are not installed yet.

    Reads the same registry the catalog route does (``_community_root()``):
    ``personas/registry.json`` for the index, each persona's ``soul.yaml`` for its
    display name and tone. Raises on an unreadable source so the caller can report
    ``available_error`` rather than claim there is nothing to install.
    """
    from navig.gateway.deck.routes.catalog import _community_root, _read_json
    from navig.personas.loader import read_soul_yaml

    root = _community_root()
    if root is None:
        raise FileNotFoundError("community persona registry not found")
    personas_dir = root / "personas"
    reg = _read_json(personas_dir / "registry.json")
    entries = reg.get("personas") if isinstance(reg, dict) else None
    if not isinstance(entries, list):
        # No index — fall back to the directory listing.
        entries = (
            [{"id": d.name} for d in sorted(personas_dir.iterdir()) if d.is_dir()]
            if personas_dir.is_dir()
            else []
        )

    # Only offer what Install can actually fetch. Unreachable → raise, and the
    # picker shows "can't reach the community library" rather than dead buttons.
    try:
        upstream = _upstream_persona_ids()
    except Exception as exc:  # noqa: BLE001
        raise ConnectionError("can't reach the community persona library") from exc

    out: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        pid = str(entry.get("id") or "").strip()
        if not pid or pid in installed or pid not in upstream:
            continue
        display_name = str(entry.get("name") or pid)
        tone = ""
        description = str(entry.get("description") or "")
        try:
            adapted = read_soul_yaml(personas_dir / pid)
        except Exception as exc:  # noqa: BLE001 — one bad soul.yaml never hides the rest
            logger.debug("available persona %s: unreadable soul.yaml: %s", pid, exc)
            adapted = None
        if adapted is not None:
            data, _soul = adapted
            display_name = str(data.get("display_name") or display_name)
            tone = str(data.get("tone") or "")
        out.append(
            {
                "name": pid,
                "display_name": display_name,
                "tone": tone,
                "description": description,
            }
        )
    return out


def _persona_snapshot(owner: int | None) -> dict[str, Any]:
    """Everything GET returns — blocking (store read + filesystem walk)."""
    from navig.personas.manager import list_personas
    from navig.personas.store import get_active_persona

    active = (get_active_persona(owner) if owner is not None else DEFAULT_PERSONA) or DEFAULT_PERSONA
    configs = list_personas()
    personas: list[dict[str, Any]] = [
        {
            "name": p.name,
            "display_name": p.display_name,
            "tone": p.tone,
            "model_hint": p.model_hint,
            "is_default": p.name == DEFAULT_PERSONA,
        }
        for p in configs
    ]
    # NAVIG (the default) first; the rest keep discovery order.
    personas.sort(key=lambda p: 0 if p["is_default"] else 1)

    result: dict[str, Any] = {"active": active, "personas": personas}
    try:
        result["available"] = _available_personas({p["name"] for p in personas})
    except Exception as exc:  # noqa: BLE001 — offline / missing registry never fails the picker
        logger.info("available personas unavailable: %s", exc)
        result["available"] = []
        result["available_error"] = str(exc) or type(exc).__name__
    return result


async def handle_deck_persona_get(request: "web.Request") -> "web.Response":
    """GET /api/deck/persona — active persona + installed + installable personas."""
    owner = resolve_owner_user_id(request)
    try:
        return web.json_response(await asyncio.to_thread(_persona_snapshot, owner))
    except Exception as exc:
        logger.warning("handle_deck_persona_get failed: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def handle_deck_persona_post(request: "web.Request") -> "web.Response":
    """POST /api/deck/persona — switch persona for the calling user.

    Body: { "name": "<persona-name>" }. Returns the new active persona or an
    error if the named persona doesn't exist / failed to load. 409 when the
    caller's Telegram user cannot be determined (a persona is per user).
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "expected object"}, status=400)

    name = str(body.get("name", "")).strip()
    if not name:
        return web.json_response({"ok": False, "error": "name required"}, status=400)

    owner = resolve_owner_user_id(request)
    if owner is None:
        return web.json_response({"ok": False, "error": _OWNER_UNKNOWN}, status=409)
    chat_id = _resolve_chat_id(request, owner)

    try:
        from navig.personas.manager import switch_persona

        # switch_persona's store reads/writes and persona resolution are blocking;
        # run the whole switch on its own loop in a worker thread.
        cfg = await asyncio.to_thread(
            lambda: asyncio.run(
                switch_persona(
                    name,
                    user_id=owner,
                    chat_id=chat_id,
                    deliver_assets=False,  # Deck doesn't deliver wallpapers/sounds
                    bot_client=None,
                )
            )
        )
        return web.json_response(
            {
                "ok": True,
                "active": cfg.name,
                "display_name": cfg.display_name,
                "tone": cfg.tone,
            }
        )
    except Exception as exc:
        logger.warning("handle_deck_persona_post failed: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
