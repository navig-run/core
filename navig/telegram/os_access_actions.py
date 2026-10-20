"""“Allow Safari on iPad to use NAVIG OS?” — the owner's Telegram prompt for a new device.

Someone signed in to the navig.run account this computer is linked to, from a device that
has never been paired. navig.run vouched for the sign-in (a signed ticket); this computer
still decides. The prompt goes to the OWNER — the first entry of ``telegram.allowed_users``,
the same convention the gateway uses for every other approval — and only the owner's tap
counts: other allowed users can chat with the bot, but letting a device drive this computer
is the owner's call alone.

The same decision can be made in NAVIG OS (Settings → Remote access). Whichever lands first
wins; the other surface is updated so it never offers a button that no longer does anything.
"""

from __future__ import annotations

import html
import logging
from typing import Any

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "osr:"

# request id -> (chat_id, message_id) of the prompt we sent, so a decision made on the
# desktop can retire the Telegram buttons too. In memory on purpose: after a restart the
# request has expired or been decided, and a stale button answers "no longer waiting".
_prompts: dict[str, tuple[int, int]] = {}
# The gateway that sent the prompts. The Telegram channel holds no reference to its
# gateway, and a tap must still reach the desktop (system event) and the shared store.
_gateway: Any = None


def _store() -> Any:
    from navig.store.os_devices import get_os_device_store

    return get_os_device_store()


def owner_chat_id() -> int | None:
    """The owner's Telegram id: the first ``telegram.allowed_users`` entry, or None."""
    try:
        from navig.config import get_config_manager

        users = get_config_manager().get("telegram.allowed_users", []) or []
        if isinstance(users, (str, int)):
            users = [users]
        return int(list(users)[0]) if users else None
    except (TypeError, ValueError, IndexError):
        return None
    except Exception:  # noqa: BLE001 — no config means no prompt, never a crash
        logger.debug("owner chat id unavailable", exc_info=True)
        return None


def _t(key: str, **fields: Any) -> str:
    """One localized string (``osaccess.*`` in navig/locales), or "" so a caller can fall
    back. The owner reads this prompt in THEIR language — the operator's install runs in
    Russian, and an English "Allow" is what test_no_english_button_reaches_the_operator
    exists to catch."""
    try:
        from navig.core import i18n

        text = i18n.t(key, **fields)
    except Exception:  # noqa: BLE001 — a prompt must never fail on a locale read
        return ""
    return "" if text == key else text


def _prompt_text(req: dict[str, Any]) -> str:
    name = html.escape(req.get("name") or "A device")
    email = html.escape(req.get("email") or "your account")
    title = _t("osaccess.prompt.title") or "🔐 <b>New device wants to use NAVIG OS</b>"
    body = _t("osaccess.prompt.body", name=name, email=email) or (
        f"<b>{name}</b> signed in as {email}.\n\n"
        "Allow it to drive this computer? It will be able to do anything you can do in "
        "NAVIG OS. You can remove it later in Settings → Remote access.\n\n"
        "<i>Didn't sign in just now? Tap Deny.</i>"
    )
    return f"{title}\n\n{body}"


def _decided_text(req: dict[str, Any], approved: bool, where: str) -> str:
    name = html.escape(req.get("name") or "The device")
    place = html.escape(where)
    if approved:
        return _t("osaccess.decided.allowed", name=name, where=place) or (
            f"✅ <b>{name}</b> can now use NAVIG OS. <i>({place})</i>"
        )
    return _t("osaccess.decided.denied", name=name, where=place) or (
        f"🚫 <b>{name}</b> was denied. <i>({place})</i>"
    )


async def send_prompt(gateway: Any, req: dict[str, Any]) -> bool:
    """Ask the owner in Telegram. False when there is no bot or no owner to ask."""
    channels = getattr(gateway, "channels", None) or {}
    channel = channels.get("telegram") if hasattr(channels, "get") else None
    owner = owner_chat_id()
    if channel is None or owner is None:
        return False
    global _gateway
    _gateway = gateway
    rid = req["id"]
    keyboard = [[
        {"text": _t("osaccess.button.allow") or "✅ Allow", "callback_data": f"{CALLBACK_PREFIX}ok:{rid}"},
        {"text": _t("osaccess.button.deny") or "🚫 Deny", "callback_data": f"{CALLBACK_PREFIX}no:{rid}"},
    ]]
    try:
        sent = await channel.send_message(owner, _prompt_text(req), parse_mode="HTML", keyboard=keyboard)
    except Exception:  # noqa: BLE001 — the desktop prompt still works
        logger.warning("could not send the new-device prompt to Telegram", exc_info=True)
        return False
    message_id = (sent or {}).get("message_id") if isinstance(sent, dict) else None
    if message_id:
        _prompts[rid] = (owner, int(message_id))
    return bool(sent)


async def retire_prompt(gateway: Any, req: dict[str, Any], approved: bool, where: str) -> None:
    """The request was decided (anywhere): replace the buttons with the outcome."""
    target = _prompts.pop(req.get("id", ""), None)
    if target is None:
        return
    channels = getattr(gateway, "channels", None) or {}
    channel = channels.get("telegram") if hasattr(channels, "get") else None
    if channel is None:
        return
    try:
        await channel.edit_message(target[0], target[1], _decided_text(req, approved, where), parse_mode="HTML")
        await channel.edit_message_reply_markup(target[0], target[1], None)
    except Exception:  # noqa: BLE001 — cosmetic; the decision already stands
        logger.debug("could not retire the Telegram device prompt", exc_info=True)


async def decide(gateway: Any, request_id: str, *, approve: bool, where: str) -> tuple[bool, dict[str, Any] | None]:
    """Apply the owner's decision from any surface. Returns ``(took, request)``.

    ``took`` is False when the request was already decided, expired or unknown — the
    caller says so instead of pretending the tap did something.
    """
    import asyncio

    store = _store()
    took = await asyncio.to_thread(store.decide_access_request, request_id, approve=approve, by=where)
    req = await asyncio.to_thread(store.get_access_request, request_id)
    if took and req is not None:
        await retire_prompt(gateway, req, approve, where)
        events = getattr(gateway, "system_events", None)
        if events is not None:
            try:
                await events.emit(
                    "os.device.request.decided",
                    {"id": request_id, "name": req.get("name"), "approved": approve},
                )
            except Exception:  # noqa: BLE001
                logger.debug("os.device.request.decided not emitted", exc_info=True)
    return took, req


async def handle_callback(
    channel: Any,
    cb_data: str,
    chat_id: int,
    message_id: int,
    user_id: int | None = None,
) -> str:
    """The owner tapped Allow or Deny. Returns the toast. Never raises."""
    try:
        body = cb_data[len(CALLBACK_PREFIX):]
        action, _, request_id = body.partition(":")
        if action not in ("ok", "no") or not request_id:
            return _t("osaccess.toast.invalid") or "That button is no longer valid."
        owner = owner_chat_id()
        if owner is None or user_id is None or int(user_id) != owner:
            return _t("osaccess.toast.owner_only") or "Only the owner of this computer can allow new devices."
        gateway = _gateway if _gateway is not None else _Shim(channel)
        # The prompt we are answering is this very message, even after a restart.
        _prompts.setdefault(request_id, (int(chat_id), int(message_id)))
        took, req = await decide(gateway, request_id, approve=action == "ok", where="Telegram")
        if not took:
            _prompts.pop(request_id, None)
            state = (req or {}).get("status", "")
            try:
                await channel.edit_message_reply_markup(chat_id, message_id, None)
            except Exception:  # noqa: BLE001
                pass
            if state in ("approved", "done"):
                return _t("osaccess.toast.already_allowed") or "Already allowed."
            if state == "denied":
                return _t("osaccess.toast.already_denied") or "Already denied."
            return _t("osaccess.toast.gone") or "That request is no longer waiting."
        if action == "ok":
            return _t("osaccess.toast.allowed") or "Allowed — the device is connecting."
        return _t("osaccess.toast.denied") or "Denied."
    except Exception:  # noqa: BLE001
        logger.warning("new-device callback failed", exc_info=True)
        return _t("osaccess.toast.error") or "⚠️ Could not apply that. Use NAVIG OS → Settings → Remote access."


class _Shim:
    """A gateway stand-in when the channel does not know its gateway: enough for
    :func:`retire_prompt` to reach the channel. No system event is emitted then."""

    def __init__(self, channel: Any):
        self.channels = {"telegram": channel}
        self.system_events = None
