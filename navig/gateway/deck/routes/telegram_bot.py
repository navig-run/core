"""
Telegram bot preferences — what the bot's own chat commands change, from the Mini App.

GET  /api/deck/telegram/bot/preferences    → language + focus mode + voice replies, in one read
POST /api/deck/telegram/bot/language       → {"language": "Russian" | "auto"} (same write as /lang)
POST /api/deck/telegram/bot/voice-replies  → {"enabled": bool} (same switch as /voiceon /voiceoff)
GET  /api/deck/telegram/bot/help           → the /help category tree, plus a "More" bucket

Every write here goes through the SAME store the chat command writes, never a parallel
copy, so the Mini App and the bot can never disagree:

* language → global ``user.language`` (``navig.core.language.CFG_LANGUAGE``), exactly as
  ``_handle_lang`` writes it, read back after the write so a write that silently failed
  never reports success;
* voice replies → per-user ``AudioConfig.voice_replies_enabled`` **and** the per-chat
  ``voice_replies_enabled`` override cleared. The send path lets a per-chat value win, so
  an old ``/voiceoff`` would otherwise silently beat the Mini App's switch forever.

Focus mode is not here: ``POST /api/deck/mode`` already owns it.

The /help tree serialises ``_HELP_CATEGORIES`` as the bot renders it, and appends every
visible registry command that no category lists. The hand-written tree has drifted
behind the registry (``/lang``, ``/extensions``, ``/todo`` … are in no category), and a
Mini App mirroring only the tree would hide those commands just as ``/help`` does.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

#: ``deck_user_id`` for a Bearer / local-desktop request: the auth middleware stores
#: ``abs(_DEV_BYPASS_SENTINEL)``, which identifies no Telegram user.
_NO_REAL_USER = {0, 1}

#: Offered in the picker. Values are what ``/lang`` stores (an English name — the
#: names ``language_code`` maps for speech models); labels are what a speaker reads.
LANGUAGE_OPTIONS: list[dict[str, str]] = [
    {"value": "auto", "label": "Auto"},
    {"value": "English", "label": "English"},
    {"value": "Russian", "label": "Русский"},
    {"value": "French", "label": "Français"},
    {"value": "Spanish", "label": "Español"},
    {"value": "German", "label": "Deutsch"},
    {"value": "Italian", "label": "Italiano"},
    {"value": "Portuguese", "label": "Português"},
    {"value": "Ukrainian", "label": "Українська"},
    {"value": "Polish", "label": "Polski"},
    {"value": "Turkish", "label": "Türkçe"},
    {"value": "Arabic", "label": "العربية"},
    {"value": "Hindi", "label": "हिन्दी"},
    {"value": "Chinese", "label": "中文"},
    {"value": "Japanese", "label": "日本語"},
    {"value": "Korean", "label": "한국어"},
]

#: Labels for the "More" bucket's subsections, keyed by registry ``category``.
_REGISTRY_CATEGORY_LABELS: dict[str, tuple[str, str]] = {
    "core": ("Core", "⭐"),
    "mesh": ("Mesh", "🕸️"),
    "monitoring": ("Monitoring", "📊"),
    "docker": ("Docker", "🐳"),
    "database": ("Database", "🗄️"),
    "tools": ("Tools", "🛠️"),
    "utilities": ("Utilities", "🧰"),
    "voice": ("Voice", "🎙️"),
    "diagnostics": ("Diagnostics", "🩺"),
    "habits": ("Habits", "📈"),
    "todo": ("To-do", "✅"),
    "paperwork": ("Paperwork", "📬"),
    "ai": ("AI", "🤖"),
    "media": ("Media", "🎵"),
    "social": ("Social", "👤"),
    "health": ("Health", "❤️"),
    "admin": ("Group admin", "🛡️"),
    "messaging": ("Messaging", "💬"),
}


def _err(msg: str, status: int = 500) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


async def _read_json(request: "web.Request") -> dict[str, Any] | None:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return None
    return body if isinstance(body, dict) else None


# ── who is "the owner" of this request ───────────────────────────────────────


def _allowed_telegram_users() -> list[int]:
    try:
        from navig.config import get_config_manager

        tg = (get_config_manager().global_config or {}).get("telegram") or {}
        raw = tg.get("allowed_users") or []
    except Exception:  # noqa: BLE001
        return []
    out: list[int] = []
    for item in raw if isinstance(raw, list) else []:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def resolve_owner_user_id(request: "web.Request") -> int | None:
    """The Telegram user a per-user preference write belongs to, or ``None``.

    Telegram initData carries the real id. A Bearer / local-desktop request does not
    (the middleware stamps a sentinel), and writing a per-user setting under that
    sentinel stores it for a user who does not exist — the bot never reads it. So
    fall back to the install's owner, but only when that is unambiguous: exactly one
    ``telegram.allowed_users`` entry. Several users → ``None``; the caller refuses
    rather than guessing whose voice setting to change.
    """
    uid = int(request.get("deck_user_id", 0) or 0)
    if uid not in _NO_REAL_USER:
        return uid
    users = _allowed_telegram_users()
    return users[0] if len(users) == 1 else None


_OWNER_UNKNOWN = (
    "can't tell which Telegram user this is — open the Mini App from Telegram, "
    "or set exactly one telegram.allowed_users entry"
)


# ── language ─────────────────────────────────────────────────────────────────


def _read_language() -> str | None:
    """``user.language`` normalised (``None`` = auto). Fresh read, never the singleton cache."""
    from navig.config import ConfigManager
    from navig.core.language import CFG_LANGUAGE, normalise_language

    return normalise_language(ConfigManager().get(CFG_LANGUAGE, ""))


def _canonical_language(value: object) -> str | None | bool:
    """Map a submitted value onto what ``/lang`` stores.

    Returns ``None`` for auto, the stored name for a known language, or ``False`` when
    the value is not a language this picker or the speech-model map recognises.
    """
    from navig.core.language import _NAME_TO_CODE, normalise_language

    text = normalise_language(value)
    if text is None:
        return None
    low = text.lower()
    for opt in LANGUAGE_OPTIONS:
        if opt["value"].lower() == low:
            return opt["value"]
    if low in _NAME_TO_CODE:
        return text.strip().title()
    # An ISO code ("ru") → its English name, so the stored value reads like /lang's.
    for name, code in _NAME_TO_CODE.items():
        if code == low:
            return name.title()
    return False


def _language_payload(current: str | None) -> dict[str, Any]:
    options = list(LANGUAGE_OPTIONS)
    if current and not any(o["value"].lower() == current.lower() for o in options):
        # Set via /lang to something outside the curated list — show it, don't hide it.
        options.append({"value": current, "label": current})
    return {"language": current or "auto", "options": options}


async def handle_bot_language_set(request: "web.Request") -> "web.Response":
    """Set ``user.language`` — the same write ``/lang`` performs, verified by read-back."""
    body = await _read_json(request)
    if body is None or "language" not in body:
        return _err("body must be JSON with a 'language' field", status=400)

    value = _canonical_language(body.get("language"))
    if value is False:
        return _err(f"unknown language '{body.get('language')}'", status=400)

    def _persist() -> str | None:
        from navig.config import ConfigManager
        from navig.core.language import CFG_LANGUAGE

        ConfigManager().set_global(CFG_LANGUAGE, value or "auto")
        return _read_language()

    try:
        saved = await asyncio.to_thread(_persist)
    except Exception as exc:  # noqa: BLE001
        logger.warning("bot language could not be saved: %s", exc)
        return _err("couldn't save the language — the config is not writable")

    if (saved or None) != (value or None):
        return _err(f"the language did not persist — config reads back as '{saved or 'auto'}'")

    await _emit(request, "telegram_bot_prefs_update", {"language": saved or "auto"})
    return web.json_response({"ok": True, **_language_payload(saved)})


# ── voice replies ────────────────────────────────────────────────────────────


def _dm_override(owner: int) -> bool | None:
    """The per-chat ``/voiceon`` / ``/voiceoff`` value in the owner's DM, if one exists."""
    try:
        from navig.gateway.channels.telegram_sessions import get_session_manager

        sm = get_session_manager()
        if sm._get_session_key(owner, owner, False) not in sm._sessions:
            return None  # never chatted — don't create a session just to read it
        val = sm.get_session_metadata(owner, owner, "voice_replies_enabled", None, is_group=False)
    except Exception:  # noqa: BLE001
        return None
    return val if isinstance(val, bool) else None


def _read_voice(owner: int) -> dict[str, Any]:
    from navig.gateway.channels.audio_menu.state import load_config

    per_user = bool(load_config(owner).voice_replies_enabled)
    override = _dm_override(owner)
    return {
        # What the bot will actually do in the owner's DM — the send path's own rule.
        "enabled": per_user if override is None else override,
        "chat_override": override,
    }


def _write_voice(owner: int, enabled: bool) -> dict[str, Any]:
    from navig.gateway.channels.audio_menu.state import load_config, save_config

    cfg = load_config(owner)
    cfg.voice_replies_enabled = enabled
    save_config(owner, cfg)
    if _dm_override(owner) is not None:
        from navig.gateway.channels.telegram_sessions import get_session_manager

        get_session_manager().set_session_metadata(
            owner, owner, "voice_replies_enabled", None, is_group=False
        )
    return _read_voice(owner)


async def handle_bot_voice_replies_set(request: "web.Request") -> "web.Response":
    body = await _read_json(request)
    enabled = (body or {}).get("enabled")
    if not isinstance(enabled, bool):
        return _err("body must be JSON with a boolean 'enabled'", status=400)
    owner = resolve_owner_user_id(request)
    if owner is None:
        return _err(_OWNER_UNKNOWN, status=409)
    try:
        state = await asyncio.to_thread(_write_voice, owner, enabled)
    except Exception as exc:  # noqa: BLE001
        logger.warning("voice replies could not be saved: %s", exc)
        return _err("couldn't save the voice-reply setting")
    if state["enabled"] != enabled:
        return _err("the voice-reply setting did not take effect")
    await _emit(request, "telegram_bot_prefs_update", {"voice_replies": enabled})
    return web.json_response({"ok": True, **state})


# ── preferences (one read for the page) ──────────────────────────────────────


def _focus_payload() -> dict[str, Any]:
    from navig.gateway.channels.focus_modes import DEFAULT_MODE, FOCUS_MODES

    try:
        from navig.agent.proactive.user_state import get_user_state_tracker

        current = get_user_state_tracker().get_preference("chat_mode", DEFAULT_MODE)
    except Exception:  # noqa: BLE001
        current = DEFAULT_MODE
    return {
        "current": current if current in FOCUS_MODES else DEFAULT_MODE,
        "modes": [
            {"id": fm.id, "emoji": fm.emoji, "label": fm.label, "description": fm.description}
            for fm in FOCUS_MODES.values()
        ],
    }


async def handle_bot_preferences(request: "web.Request") -> "web.Response":
    owner = resolve_owner_user_id(request)

    def _collect() -> dict[str, Any]:
        out: dict[str, Any] = {"ok": True}
        try:
            out.update(_language_payload(_read_language()))
        except Exception as exc:  # noqa: BLE001
            logger.debug("bot prefs: language unreadable: %s", exc)
            out["language_error"] = "language setting unreadable"
        out["focus"] = _focus_payload()
        if owner is None:
            out["voice_replies"] = None
            out["voice_replies_error"] = _OWNER_UNKNOWN
        else:
            try:
                out["voice_replies"] = _read_voice(owner)
            except Exception as exc:  # noqa: BLE001
                logger.debug("bot prefs: voice unreadable: %s", exc)
                out["voice_replies"] = None
                out["voice_replies_error"] = "voice-reply setting unreadable"
        return out

    return web.json_response(await asyncio.to_thread(_collect))


# ── /help tree ───────────────────────────────────────────────────────────────


def build_help_tree() -> dict[str, Any]:
    """The /help encyclopedia as data: categories → (subcategories →) command names.

    Each command is listed once, with ``live`` from ``command_is_live`` — the predicate
    the bot itself uses to decide what to offer. Commands the hand-written tree omits
    land in a trailing "More" category, grouped by their registry category.
    """
    from navig.gateway.channels.telegram_commands import (
        _HELP_CATEGORIES,
        _iter_unique_registry,
        command_is_live,
    )

    registry = {e.command.lower(): e for e in _iter_unique_registry(visible_only=False)}
    placed: set[str] = set()

    def _cmds(names: list[str]) -> list[dict[str, Any]]:
        out = []
        for raw in names:
            name = raw.lower()
            entry = registry.get(name)
            if entry is None or name in placed:
                continue
            placed.add(name)
            out.append({"name": name, "live": command_is_live(name, entry.category)})
        return out

    categories: list[dict[str, Any]] = []
    for cat in _HELP_CATEGORIES:
        node: dict[str, Any] = {"key": cat.key, "label": cat.label, "emoji": cat.emoji}
        if cat.subcategories:
            node["subcategories"] = [
                {"key": s.key, "label": s.label, "emoji": s.emoji, "commands": _cmds(s.commands)}
                for s in cat.subcategories
            ]
        else:
            node["commands"] = _cmds(cat.commands or [])
        categories.append(node)

    groups: dict[str, list[str]] = {}
    for name, entry in registry.items():
        if entry.visible and name not in placed:
            groups.setdefault(entry.category or "other", []).append(name)
    if groups:
        subs = []
        for reg_cat, names in groups.items():
            label, emoji = _REGISTRY_CATEGORY_LABELS.get(reg_cat, (reg_cat.title(), "•"))
            subs.append({"key": reg_cat, "label": label, "emoji": emoji, "commands": _cmds(names)})
        categories.append(
            {"key": "more", "label": "More commands", "emoji": "➕", "subcategories": subs}
        )

    return {"categories": categories}


async def handle_bot_help(request: "web.Request") -> "web.Response":
    try:
        tree = await asyncio.to_thread(build_help_tree)
    except Exception as exc:  # noqa: BLE001
        logger.warning("help tree unavailable: %s", exc)
        return _err("command registry unavailable")
    return web.json_response({"ok": True, **tree})


# ── weigh-in cadence ─────────────────────────────────────────────────────────


async def handle_bot_weigh_in_get(request: "web.Request") -> "web.Response":
    """The scheduled weigh-in's cadence — same keys ``navig body cadence`` shows."""
    from navig.spaces import weigh_cadence as wc

    settings = await asyncio.to_thread(wc.load_settings)
    return web.json_response({"ok": True, **settings.as_dict()})


def _validated_weigh_in(body: dict[str, Any]) -> dict[str, Any]:
    """The subset of *body* to write, validated like the CLI. Raises ``ValueError``."""
    from navig.spaces import weigh_cadence as wc

    updates: dict[str, Any] = {}
    if "enabled" in body:
        if not isinstance(body["enabled"], bool):
            raise ValueError("'enabled' must be true or false")
        updates["enabled"] = body["enabled"]
    if "every_days" in body:
        every = body["every_days"]
        if isinstance(every, bool) or not isinstance(every, int):
            raise ValueError("'every_days' must be a whole number")
        if not wc.MIN_EVERY_DAYS <= every <= wc.MAX_EVERY_DAYS:
            raise ValueError(f"'every_days' must be {wc.MIN_EVERY_DAYS}–{wc.MAX_EVERY_DAYS}")
        updates["every_days"] = every
    if "weekdays" in body:
        days = wc.parse_weekdays(body["weekdays"])  # raises ValueError on a bad name
        updates["weekdays"] = [wc.WEEKDAY_NAMES[i] for i in days]
    if "latest_hour" in body:
        hour = body["latest_hour"]
        if isinstance(hour, bool) or not isinstance(hour, int):
            raise ValueError("'latest_hour' must be a whole number")
        if not wc.MIN_LATEST_HOUR <= hour <= wc.MAX_LATEST_HOUR:
            raise ValueError(f"'latest_hour' must be {wc.MIN_LATEST_HOUR}–{wc.MAX_LATEST_HOUR}")
        updates["latest_hour"] = hour
    if not updates:
        raise ValueError("nothing to change — send enabled, every_days, weekdays or latest_hour")
    return updates


async def handle_bot_weigh_in_set(request: "web.Request") -> "web.Response":
    """Change the weigh-in cadence; writes ``health.weigh_in.*`` like the CLI does."""
    from navig.spaces import weigh_cadence as wc

    body = await _read_json(request)
    if body is None:
        return _err("body must be a JSON object", status=400)
    try:
        updates = _validated_weigh_in(body)
    except ValueError as exc:
        return _err(str(exc), status=400)

    def _persist() -> dict[str, Any]:
        return wc.save_settings(updates).as_dict()

    try:
        saved = await asyncio.to_thread(_persist)
    except Exception as exc:  # noqa: BLE001
        logger.warning("weigh-in cadence could not be saved: %s", exc)
        return _err("couldn't save the weigh-in settings")
    # Read back: a value the loader rejected must not be reported as saved.
    mismatched = [k for k, v in updates.items() if saved.get(k) != v]
    if mismatched:
        return _err(f"did not persist: {', '.join(mismatched)}")
    await _emit(request, "telegram_bot_prefs_update", {"weigh_in": saved})
    return web.json_response({"ok": True, **saved})


async def _emit(request: "web.Request", event: str, payload: dict[str, Any]) -> None:
    """Broadcast on /api/events so other open surfaces refresh (best-effort)."""
    try:
        gw = request.app.get("gateway")
        queue = getattr(gw, "system_events", None) if gw else None
        if queue and hasattr(queue, "emit"):
            await queue.emit(event, payload)
    except Exception:  # noqa: BLE001
        logger.debug("%s emit failed", event, exc_info=True)


def register(app: "web.Application") -> None:
    app.router.add_get("/api/deck/telegram/bot/preferences", handle_bot_preferences)
    app.router.add_post("/api/deck/telegram/bot/language", handle_bot_language_set)
    app.router.add_post("/api/deck/telegram/bot/voice-replies", handle_bot_voice_replies_set)
    app.router.add_get("/api/deck/telegram/bot/help", handle_bot_help)
    app.router.add_get("/api/deck/telegram/bot/weigh-in", handle_bot_weigh_in_get)
    app.router.add_post("/api/deck/telegram/bot/weigh-in", handle_bot_weigh_in_set)
