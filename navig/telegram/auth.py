"""Two-step interactive login for the Telegram user account (Telethon).

  request_code(phone)            → Telegram sends a login code to the user's app
  confirm_code(code[, password]) → completes login; persists the StringSession to vault
  submit_password(password)      → finishes a pending 2FA (when confirm returned need_2fa)
  logout()                       → forgets the session

The partial (pre-auth) session + phone_code_hash are held transiently in the
vault between request and confirm so the flow survives across separate CLI/HTTP
calls. Secrets are never printed.
"""

from __future__ import annotations

import json
import logging

from . import config
from .user_client import TelegramNotConfigured, build_client, require_telethon

logger = logging.getLogger(__name__)

_LOGIN_STATE = "telegram_user_login_state"  # vault label for the transient partial session


async def request_code(phone: str) -> str:
    """Step 1 — send a login code. Returns 'code_sent' or 'already_authorized'."""
    require_telethon()
    client = build_client(session_str="")  # fresh, empty session
    await client.connect()
    try:
        if await client.is_user_authorized():
            return "already_authorized"
        sent = await client.send_code_request(phone)
        state = {"phone": phone, "hash": sent.phone_code_hash, "session": client.session.save()}
        config._vault_put(_LOGIN_STATE, json.dumps(state))
        return "code_sent"
    finally:
        await client.disconnect()


async def confirm_code(code: str, password: str | None = None) -> dict:
    """Step 2 — complete login with the code (+ 2FA password if required).

    Returns ``{"status": "logged_in", "username", "id"}`` or ``{"status": "need_2fa"}``.
    """
    require_telethon()
    from telethon.errors import SessionPasswordNeededError

    raw = config._vault_get(_LOGIN_STATE)
    if not raw:
        raise TelegramNotConfigured("No login in progress — run `navig telegram login <phone>` first.")
    state = json.loads(raw)
    client = build_client(session_str=state["session"])
    await client.connect()
    try:
        try:
            await client.sign_in(state["phone"], code, phone_code_hash=state["hash"])
        except SessionPasswordNeededError:
            # Use the explicit password, else fall back to the owner's stored 2FA.
            pw = password or config.get_2fa_password()
            if not pw:
                state["session"] = client.session.save()  # carry the awaiting-2fa state forward
                config._vault_put(_LOGIN_STATE, json.dumps(state))
                return {"status": "need_2fa"}
            await client.sign_in(password=pw)
        me = await client.get_me()
        config.set_session_string(client.session.save())  # the authorized session → vault
        config._vault_del(_LOGIN_STATE)
        return {"status": "logged_in", "username": me.username or me.first_name, "id": me.id}
    finally:
        await client.disconnect()


async def submit_password(password: str) -> dict:
    """Finish a pending 2FA (after confirm_code returned need_2fa)."""
    require_telethon()
    raw = config._vault_get(_LOGIN_STATE)
    if not raw:
        raise TelegramNotConfigured("No login in progress.")
    state = json.loads(raw)
    client = build_client(session_str=state["session"])
    await client.connect()
    try:
        if not await client.is_user_authorized():
            await client.sign_in(password=password)
        me = await client.get_me()
        config.set_session_string(client.session.save())
        config._vault_del(_LOGIN_STATE)
        return {"status": "logged_in", "username": me.username or me.first_name, "id": me.id}
    finally:
        await client.disconnect()


def logout() -> None:
    """Forget the stored session + any in-progress login."""
    config.clear_session()
    config._vault_del(_LOGIN_STATE)
