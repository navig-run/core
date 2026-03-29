"""
Telegram Bot plugin for NAVIG — reference handler implementation.

Lifecycle contract
------------------
on_load(ctx)        Called once when the plugin is activated.
                    ctx keys: plugin_id, plugin_dir, store_dir, config, logger
                    Return value is ignored; raise to abort loading.

on_unload(ctx)      Called once when the plugin is deactivated (or navig exits).
                    Best-effort — must not raise; swallow all exceptions internally.

on_<event>(ctx)     Called for every matching hook in plugin.json's "hooks" list.
                    ctx also includes: event_data (dict)
                    Return a dict to contribute data back to the dispatcher, or None.

This file is executed in the standard Python import system inside an isolated
module namespace. Do NOT import navig internals at module level — import inside
the lifecycle functions to avoid circular dependencies and keep startup fast.
"""

from __future__ import annotations

import logging
from typing import Any

_BOT_TOKEN: str | None = None
_CHAT_ID: str | None = None
_logger: logging.Logger | None = None


# ---------------------------------------------------------------------------
# Lifecycle: on_load
# ---------------------------------------------------------------------------


def on_load(ctx: dict[str, Any]) -> None:
    """
    Initialise the Telegram bot.

    Expected config keys (read from navig config / env):
        TELEGRAM_BOT_TOKEN   — BotFather token
        TELEGRAM_CHAT_ID     — target chat / channel id
    """
    global _BOT_TOKEN, _CHAT_ID, _logger

    _logger = ctx.get("logger") or logging.getLogger(__name__)
    cfg = ctx.get("config", {})

    _BOT_TOKEN = cfg.get("telegram_bot_token") or _environ("TELEGRAM_BOT_TOKEN")
    _CHAT_ID = cfg.get("telegram_chat_id") or _environ("TELEGRAM_CHAT_ID")

    if not _BOT_TOKEN:
        _logger.warning(
            "telegram-bot-navig: TELEGRAM_BOT_TOKEN not set — pack loaded but inactive. "
            "Add it to ~/.navig/config.yaml or export as TELEGRAM_BOT_TOKEN env var."
        )
        return  # degrade gracefully; on_message will no-op via _send guard

    _logger.info("telegram-bot-navig loaded (chat_id=%s)", _CHAT_ID or "(broadcast)")


# ---------------------------------------------------------------------------
# Lifecycle: on_unload
# ---------------------------------------------------------------------------


def on_unload(ctx: dict[str, Any]) -> None:  # noqa: ARG001
    """Teardown — swallow all exceptions per contract."""
    global _BOT_TOKEN, _CHAT_ID, _logger
    try:
        _logger and _logger.info("telegram-bot-navig unloaded")
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical
    _BOT_TOKEN = _CHAT_ID = _logger = None


# ---------------------------------------------------------------------------
# Event hooks
# ---------------------------------------------------------------------------


def on_message(ctx: dict[str, Any]) -> dict[str, Any] | None:
    """
    Forward a NAVIG message event to Telegram.

    event_data keys expected:
        text   — human-readable message content
        source — origin subsystem (optional)
    """
    data = ctx.get("event_data", {})
    text = data.get("text", "")
    if not text:
        return None

    source = data.get("source", "navig")
    payload = f"[{source}] {text}"

    _send(payload)
    return {"telegram_sent": True}


def on_heartbeat(ctx: dict[str, Any]) -> None:  # noqa: ARG001
    """Periodic heartbeat — can be used to push status summaries."""
    # Intentionally a no-op in the reference implementation.
    pass


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _send(text: str) -> None:
    """Fire-and-forget Telegram message. Swallows network errors."""
    if not _BOT_TOKEN or not _CHAT_ID:
        return
    try:
        import json as _json  # noqa: E401
        import urllib.request

        payload = _json.dumps({"chat_id": _CHAT_ID, "text": text}).encode()
        url = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage"
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=5)  # noqa: S310
    except Exception as exc:  # noqa: BLE001
        _logger and _logger.warning("telegram-bot-navig: send failed — %s", exc)


def _environ(key: str) -> str | None:
    import os

    return os.environ.get(key)
