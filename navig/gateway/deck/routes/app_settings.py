"""
Per-app settings — the one generic persistence endpoint for OS-app preferences.

GET  /api/deck/apps/{app_id}/settings   → {"ok": true, "id": ..., "settings": {...}}
POST /api/deck/apps/{app_id}/settings   → shallow-merge {"settings": {...}} into the
    subtree; a null value deletes the key. Returns the full merged subtree.

Storage: the ``apps.<id>.settings.*`` subtree of ~/.navig/config.yaml, written
exclusively through the Config() singleton (set + save — the inbox extract-mode
precedent). This subtree is owned by THIS endpoint; app-domain knobs that already
have a home (``inbox.extract.mode``, ``adapters.*``, the board store, the games
plugin store) stay where they are.

The server is schema-agnostic — field meaning lives in the OS descriptor registry
(apps/os .../pages/apps/settings/app-settings-registry.ts). Validation here is
generic: registry-resolved app id, safe key names, primitive values, size caps.
Enablement is deliberately rejected ("enabled" is reserved) so /modules/toggle
stays the single enable/disable path.

After every write an ``apps_settings_update`` event is broadcast on /api/events
(mirrors emit_modules_update) so other windows/surfaces sync live.
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

_KEY_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_RESERVED_KEYS = {"enabled"}  # enable/disable belongs to POST /modules/toggle
_MAX_KEYS = 32
_MAX_STR = 4096
_MAX_LIST = 64


def _err(msg: str, status: int = 500) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


def _known_app(app_id: str) -> bool:
    """App ids resolve against the module registry (plugin apps included)."""
    from navig.modules.registry import get_registry

    return get_registry().discover().get(app_id) is not None


def _valid_value(value: Any) -> bool:
    if isinstance(value, bool | int | float):
        return True
    if isinstance(value, str):
        return len(value) <= _MAX_STR
    if isinstance(value, list):
        return len(value) <= _MAX_LIST and all(
            isinstance(v, bool | int | float) or (isinstance(v, str) and len(v) <= _MAX_STR)
            for v in value
        )
    return False


def _read_settings(app_id: str) -> dict[str, Any]:
    from navig.core import Config

    val = Config().get(f"apps.{app_id}.settings", {}, scope="global")
    return dict(val) if isinstance(val, dict) else {}


async def emit_apps_settings_update(request: "web.Request", payload: dict[str, Any]) -> None:
    """Broadcast `apps_settings_update` on /api/events (best-effort, never raises)."""
    try:
        gw = request.app.get("gateway")
        queue = getattr(gw, "system_events", None) if gw else None
        if queue and hasattr(queue, "emit"):
            await queue.emit("apps_settings_update", payload)
    except Exception:  # noqa: BLE001
        logger.debug("apps_settings_update emit failed", exc_info=True)


async def handle_deck_app_settings_get(request: "web.Request") -> "web.Response":
    """Return the stored settings subtree for one app ({} when unset)."""
    app_id = request.match_info.get("app_id", "").strip()
    try:
        if not _known_app(app_id):
            return _err(f"unknown app '{app_id}'", status=404)
        settings = await asyncio.to_thread(_read_settings, app_id)
        return web.json_response({"ok": True, "id": app_id, "settings": settings})
    except Exception as exc:  # noqa: BLE001
        logger.exception("app settings get failed")
        return _err(str(exc))


async def handle_deck_app_settings_post(request: "web.Request") -> "web.Response":
    """Shallow-merge settings for one app; a null value deletes the key."""
    app_id = request.match_info.get("app_id", "").strip()
    try:
        body: dict[str, Any] = await request.json()
    except Exception:  # noqa: BLE001
        return _err("invalid JSON body", status=400)

    updates = body.get("settings")
    if not isinstance(updates, dict) or not updates:
        return _err("body must carry a non-empty 'settings' object", status=400)

    for key, value in updates.items():
        if not isinstance(key, str) or not _KEY_RE.match(key):
            return _err(f"invalid key '{key}' (want ^[a-z0-9_]{{1,64}}$)", status=400)
        if key in _RESERVED_KEYS:
            return _err("'enabled' is reserved — use POST /api/deck/modules/toggle", status=400)
        if value is not None and not _valid_value(value):
            return _err(f"unsupported value for '{key}' (primitives or a short list of them)", status=400)

    try:
        if not _known_app(app_id):
            return _err(f"unknown app '{app_id}'", status=404)

        def _persist() -> dict[str, Any]:
            from navig.core import Config

            merged = _read_settings(app_id)
            for key, value in updates.items():
                if value is None:
                    merged.pop(key, None)
                else:
                    merged[key] = value
            if len(merged) > _MAX_KEYS:
                raise ValueError(f"too many settings keys for '{app_id}' (max {_MAX_KEYS})")
            cfg = Config()
            cfg.set(f"apps.{app_id}.settings", merged, scope="global")
            cfg.save(scope="global")
            return merged

        merged = await asyncio.to_thread(_persist)
    except ValueError as exc:
        return _err(str(exc), status=400)
    except Exception as exc:  # noqa: BLE001
        logger.exception("app settings post failed")
        return _err(str(exc))

    await emit_apps_settings_update(request, {"id": app_id, "settings": merged})
    return web.json_response({"ok": True, "id": app_id, "settings": merged})
