"""The Telegram transport — the one place core reaches the Bot API.

Why this module exists: until now these helpers lived in ``navig.commands.telegram``, so
core code (`checkins`, `habit`, `body`, `gateway`, `messaging.notify_operator`) imported a
**command module** to send a message. That made the CLI un-movable — a plugin cannot be a
core dependency — and it put the transport one refactor away from a silent break.

The command layer now imports *from here* and re-exports the names, so:

  * core sends through a module that belongs to core;
  * the CLI (`navig telegram`) can move to a plugin without core following it;
  * dotted-path patches should target **this** module (``navig.telegram.api.telegram_send``),
    which is where every caller looks the function up.
"""

from __future__ import annotations

from typing import Any

from navig import console_helper as ch


def load_telegram_token() -> str:
    """The bot token, raising when it is not configured.

    Public: `navig-email` needs it to send through Telegram, and it previously had to
    import `_load_telegram_token` — a private name, so any core refactor would break
    an outside plugin at call time rather than at review.
    """
    from navig.messaging.secrets import resolve_telegram_bot_token

    token = resolve_telegram_bot_token()
    if not token:
        raise RuntimeError("Telegram bot token missing. Configure with: navig init")
    return token


#: Old private name, kept so in-tree callers keep working.
_load_telegram_token = load_telegram_token


def api_call(token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST one Bot API method; raise on ``ok: false``. Notifies the tidy ledger on sends."""
    import requests

    url = f"https://api.telegram.org/bot{token}/{method}"
    response = requests.post(url, json=payload, timeout=15)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(str(data.get("description") or "Telegram API call failed"))
    if method.startswith("send"):
        from navig.telegram import chat_tidy

        chat_tidy.note(data.get("result"))
    return data


#: Old private name — the whole in-tree surface used this spelling.
_api_call = api_call


def resolve_chat_id(token: str, target: str) -> int:
    raw = target.strip()
    if not raw:
        raise RuntimeError("Empty target")

    # Direct numeric chat id
    if raw.lstrip("-").isdigit():
        return int(raw)

    # Best-effort @username lookup from recent updates
    if raw.startswith("@"):
        import requests

        wanted = raw.lstrip("@").lower()
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        response = requests.get(url, params={"limit": 100}, timeout=15)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError("Failed to fetch updates for username resolution")

        for item in data.get("result", []):
            message = item.get("message") or item.get("edited_message") or {}
            chat = message.get("chat") or {}
            if str(chat.get("username") or "").lower() == wanted:
                chat_id = chat.get("id")
                if isinstance(chat_id, int):
                    return chat_id

            sender = message.get("from") or {}
            if str(sender.get("username") or "").lower() == wanted:
                chat_id = chat.get("id")
                if isinstance(chat_id, int):
                    return chat_id

        raise RuntimeError(
            "Could not resolve @username from recent updates. Ask the user to message the bot "
            "first or use numeric chat_id."
        )

    raise RuntimeError("Target must be numeric chat_id or @username")


def telegram_send(
    *,
    target: str,
    message: str,
    parse_mode: str = "Markdown",
    resolve_only: bool = False,
    host: str = "",
    reply_markup: dict[str, Any] | None = None,
    disable_notification: bool = False,
) -> int:
    """Send one message; returns the resolved chat_id.

    `host` is a legacy compatibility argument and is ignored.
    """
    del host
    token = _load_telegram_token()
    chat_id = resolve_chat_id(token, target)

    if resolve_only:
        ch.info(f"Resolved target {target} -> {chat_id}")
        return chat_id

    payload: dict[str, Any] = {"chat_id": chat_id, "text": message}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if disable_notification:
        payload["disable_notification"] = True

    _api_call(token, "sendMessage", payload)
    return chat_id


#: Old private name, kept for in-tree callers and dotted-path patches.
_resolve_chat_id = resolve_chat_id

__all__ = [
    "api_call",
    "resolve_chat_id",
    "load_telegram_token",
    "telegram_send",
]

