"""Telethon user-client lifecycle.

Builds a Telethon client from the vault-stored ``StringSession`` + api_id/api_hash,
so nothing sensitive ever touches disk in plaintext. Telethon is imported lazily
(optional dependency). The client is the *owner's* account — only the owner drives
it from the CLI/deck; it is never reachable from an inbound bot message.
"""

from __future__ import annotations

import logging

from . import config

logger = logging.getLogger(__name__)


class TelethonMissing(RuntimeError):
    """telethon is not installed."""


class TelegramNotConfigured(RuntimeError):
    """api creds missing, or the user account is not logged in."""


def require_telethon() -> None:
    try:
        import telethon  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        raise TelethonMissing(
            "telethon is not installed. Install it under the NAVIG runtime "
            "(the installer adds it) or `pip install telethon`."
        ) from exc


def build_client(session_str: str | None = None):
    """Construct (but do not connect) a Telethon client from vault creds.

    ``session_str`` overrides the stored session (used by the login flow); when
    None the persisted session is used.
    """
    require_telethon()
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    api_id = config.get_api_id()
    api_hash = config.get_api_hash()
    if not api_id or not api_hash:
        raise TelegramNotConfigured(
            "Telegram api_id/api_hash are not set. Run `navig telegram setup`."
        )
    sess = session_str if session_str is not None else (config.get_session_string() or "")
    return TelegramClient(StringSession(sess), api_id, api_hash)


class UserClient:
    """Async context manager → a connected, authorized Telethon client."""

    def __init__(self) -> None:
        self._client = None

    async def __aenter__(self):
        self._client = build_client()
        await self._client.connect()
        if not await self._client.is_user_authorized():
            await self._client.disconnect()
            raise TelegramNotConfigured(
                "Telegram user account is not logged in. Run `navig telegram login <phone>`."
            )
        return self._client

    async def __aexit__(self, *exc) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self._client = None


async def whoami() -> dict | None:
    """Return ``{id, username, name, phone}`` for the logged-in account, or None."""
    if not config.is_logged_in():
        return None
    try:
        async with UserClient() as c:
            me = await c.get_me()
            return {
                "id": me.id,
                "username": getattr(me, "username", None),
                "name": getattr(me, "first_name", None),
                "phone": getattr(me, "phone", None),
            }
    except Exception:  # noqa: BLE001
        logger.debug("telegram whoami failed", exc_info=True)
        return None
