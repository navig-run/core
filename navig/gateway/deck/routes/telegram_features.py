"""Telegram feature switches the deck could not reach — one allowlisted catalog.

The 18 Telegram **extensions** already have their own route
(``/api/deck/social/telegram/extensions``) and behaviour toggles live on
``/api/deck/social/telegram``. What was left over is a handful of plain config
switches — the room catalog, link cards, the boot greeting, the daily tidy … —
that the bot reads and nothing in the Mini App could flip. This module is the
one place that names them.

Routes (registered in ``navig/gateway/deck/__init__.py``)::

    GET  /api/deck/telegram/features   -> {ok, features: [...], groups: [...]}
    POST /api/deck/telegram/features   -> {"id": "<feature id>", "enabled": bool}

⚠ **The catalog IS the security boundary.** This is not a generic config-write
route: an id that is not in ``FEATURES`` is refused, and the value written is
always a real bool at a key the catalog fixed. Adding a switch here is a
deliberate act — never accept a key from the request.

Every GET value comes from the SAME resolver the bot reads (``read``), so the
Mini App can never show "on" for a feature the bot treats as off.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

try:
    from aiohttp import web
except ImportError:  # pragma: no cover — aiohttp is a gateway dependency
    web = None

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeatureSwitch:
    id: str
    label: str
    description: str
    group: str
    key: str
    default: bool
    read: Callable[[], bool]
    about: tuple[str, ...] = ()
    # "live" = the next message sees it; "minute" = a cached read refreshes within
    # 60 s; "restart" = only read at gateway start.
    applies: str = "live"
    # The Telegram extension that must ALSO be on (the gates compose as AND).
    extension: str | None = None
    # Another feature in this catalog that must be on for this one to matter.
    depends_on: str | None = None


def _cfg_bool(key: str, default: bool) -> Callable[[], bool]:
    def _read() -> bool:
        from navig.config import get_config_manager  # noqa: PLC0415
        from navig.core.coerce import coerce_bool  # noqa: PLC0415

        return coerce_bool(get_config_manager().get(key, default), default=default)

    return _read


def _read_catalog() -> bool:
    from navig.gateway.channels.telegram_catalog_ingest import catalog_enabled  # noqa: PLC0415

    return catalog_enabled()


def _read_tiktok() -> bool:
    from navig.telegram.tiktok_actions import enabled  # noqa: PLC0415

    return enabled()


def _read_tiktok_cover() -> bool:
    from navig.telegram.tiktok_actions import _cover_enabled  # noqa: PLC0415

    return _cover_enabled()


def _read_music() -> bool:
    from navig.telegram.music_actions import enabled  # noqa: PLC0415

    return enabled()


def _read_boot_greeting() -> bool:
    from navig.boot_messages import boot_greeting_enabled  # noqa: PLC0415

    return boot_greeting_enabled()


GROUP_ORDER: tuple[str, ...] = ("chat", "media", "automation")
GROUP_LABELS: dict[str, str] = {
    "chat": "Chat",
    "media": "Links & media",
    "automation": "Automation",
}

FEATURES: tuple[FeatureSwitch, ...] = (
    # -- Chat -------------------------------------------------------------------
    FeatureSwitch(
        id="rich_messages",
        label="Rich messages",
        description="Formatted cards, tables and blocks in the bot's replies.",
        group="chat",
        key="telegram.rich_messages",
        default=True,
        read=_cfg_bool("telegram.rich_messages", True),
        about=("replies arrive as plain formatted text instead of rich blocks",),
    ),
    FeatureSwitch(
        id="boot_greeting",
        label="Hello on restart",
        description="A short greeting when the brain comes back online.",
        group="chat",
        key="telegram.boot_greeting.enabled",
        default=True,
        read=_read_boot_greeting,
        about=("restarts happen silently",),
        applies="restart",
    ),
    # -- Links & media ------------------------------------------------------------
    FeatureSwitch(
        id="catalog",
        label="Room catalog",
        description="Indexes the rooms, messages and media the bot can see, so they are searchable.",
        group="media",
        key="telegram.catalog.enabled",
        default=True,
        read=_read_catalog,
        about=(
            "new messages and media stop being indexed",
            "everything already catalogued stays searchable",
        ),
        extension="media",
    ),
    FeatureSwitch(
        id="catalog_auto_analyze",
        label="Analyse new media",
        description="Reads text in photos and transcribes voice and video as they arrive in the catalog.",
        group="media",
        key="telegram.catalog.auto_analyze",
        default=True,
        read=_cfg_bool("telegram.catalog.auto_analyze", True),
        about=("new media is catalogued but not analysed",),
        extension="media",
        depends_on="catalog",
    ),
    FeatureSwitch(
        id="tiktok_cards",
        label="TikTok cards",
        description="Send a TikTok link in a private chat and get a summary card back.",
        group="media",
        key="telegram.tiktok_cards.enabled",
        default=True,
        read=_read_tiktok,
        about=("TikTok links are treated like any other message",),
        extension="media",
    ),
    FeatureSwitch(
        id="tiktok_cover",
        label="TikTok cover image",
        description="Show the post's cover picture above the TikTok card.",
        group="media",
        key="telegram.tiktok_cards.photo",
        default=True,
        read=_read_tiktok_cover,
        about=("cards arrive without the picture",),
        extension="media",
        depends_on="tiktok_cards",
    ),
    FeatureSwitch(
        id="music_links",
        label="Music links",
        description="Send a song link and get the same track on your other music services.",
        group="media",
        key="telegram.music_links.enabled",
        default=True,
        read=_read_music,
        about=("music links are treated like any other message",),
        extension="media",
    ),
    # -- Automation ---------------------------------------------------------------
    FeatureSwitch(
        id="proactive",
        label="Proactive watcher",
        description="Watches your calendar and mailbox and messages you first about what matters.",
        group="automation",
        key="proactive.enabled",
        default=True,
        read=_cfg_bool("proactive.enabled", True),
        about=("no calendar or email alerts reach the chat",),
        applies="restart",
    ),
)

_BY_ID: dict[str, FeatureSwitch] = {f.id: f for f in FEATURES}


def _resolve(feature: FeatureSwitch) -> bool:
    """The bot's own answer for this feature. Fails to the catalog default."""
    try:
        return bool(feature.read())
    except Exception:  # noqa: BLE001 — one unreadable switch must not break the list
        logger.debug("feature %s: resolver raised", feature.id, exc_info=True)
        return feature.default


def _extension_on(ext_id: str | None) -> bool:
    if not ext_id:
        return True
    try:
        from navig.gateway.channels.telegram_extensions import is_enabled  # noqa: PLC0415

        return bool(is_enabled(ext_id))
    except Exception:  # noqa: BLE001
        return True


def feature_row(feature: FeatureSwitch, state: dict[str, bool] | None = None) -> dict:
    """One feature as the deck renders it."""
    enabled = state[feature.id] if state and feature.id in state else _resolve(feature)
    blocked_by: str | None = None
    if not _extension_on(feature.extension):
        blocked_by = f"extension:{feature.extension}"
    elif feature.depends_on:
        parent = _BY_ID[feature.depends_on]
        parent_on = state[parent.id] if state and parent.id in state else _resolve(parent)
        if not parent_on:
            blocked_by = f"feature:{parent.id}"
    return {
        "id": feature.id,
        "label": feature.label,
        "description": feature.description,
        "group": feature.group,
        "enabled": enabled,
        "default": feature.default,
        "about": list(feature.about),
        "applies": feature.applies,
        "extension": feature.extension,
        "depends_on": feature.depends_on,
        # Set when the switch is on but something above it is off, so the
        # feature still does nothing — the Mini App says so instead of lying.
        "blocked_by": blocked_by,
    }


def list_features() -> dict:
    state = {f.id: _resolve(f) for f in FEATURES}
    return {
        "features": [feature_row(f, state) for f in FEATURES],
        "groups": [{"id": g, "label": GROUP_LABELS[g]} for g in GROUP_ORDER],
    }


def set_feature(feature_id: str, enabled: bool) -> FeatureSwitch | None:
    """Persist one switch. Returns None for an id outside the catalog."""
    feature = _BY_ID.get(feature_id)
    if feature is None:
        return None
    from navig.config import get_config_manager  # noqa: PLC0415

    # set_global = refresh → deep-set the leaf → save: never clobbers a sibling key.
    get_config_manager().set_global(feature.key, bool(enabled))
    return feature


async def handle_deck_telegram_features(request: web.Request) -> web.Response:
    """GET /api/deck/telegram/features."""
    try:
        payload = list_features()
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram features unavailable: %s", exc)
        return web.json_response({"ok": False, "error": "features unavailable"}, status=500)
    return web.json_response({"ok": True, **payload})


async def handle_deck_telegram_feature_set(request: web.Request) -> web.Response:
    """POST /api/deck/telegram/features — {"id": "catalog", "enabled": false}."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "expected an object"}, status=400)
    feature_id = body.get("id")
    enabled = body.get("enabled")
    if not isinstance(feature_id, str) or not isinstance(enabled, bool):
        return web.json_response(
            {"ok": False, "error": "id (string) and enabled (bool) are required"}, status=400
        )
    if feature_id not in _BY_ID:
        return web.json_response(
            {"ok": False, "error": "unknown feature", "id": feature_id}, status=404
        )
    try:
        feature = set_feature(feature_id, enabled)
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram feature %s could not be saved: %s", feature_id, exc)
        return web.json_response({"ok": False, "error": "could not save"}, status=500)
    if feature is None:  # unreachable: the id was checked above
        return web.json_response({"ok": False, "error": "unknown feature"}, status=404)
    row = feature_row(feature)
    if row["enabled"] != enabled:
        # Written, but the bot's own resolver still disagrees — say so rather than
        # report a switch the bot will not honour.
        return web.json_response(
            {"ok": False, "error": "saved but not applied", "feature": row}, status=409
        )
    return web.json_response({"ok": True, "feature": row})


def register(app: web.Application) -> None:
    app.router.add_get("/api/deck/telegram/features", handle_deck_telegram_features)
    app.router.add_post("/api/deck/telegram/features", handle_deck_telegram_feature_set)
