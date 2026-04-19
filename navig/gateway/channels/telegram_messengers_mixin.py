"""Telegram Messengers Hub — /messengers command and msg:* callback handlers.

``TelegramMessengersMixin`` provides an inline keyboard hub for viewing and
configuring outbound messaging adapters: SMS (Twilio/Vonage), WhatsApp Cloud,
Discord, and the built-in Telegram adapter.

Injected lazily by :class:`~navig.gateway.channels.telegram.TelegramChannel`
dispatch, following the same pattern as ``TelegramMessagingMixin``.
Callback routing is wired in
:class:`~navig.gateway.channels.telegram_keyboards.CallbackHandler` via the
``msg:`` prefix.
"""

from __future__ import annotations

import html
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Per-adapter display metadata ─────────────────────────────────────────────

_ADAPTER_DISPLAY: dict[str, dict[str, str]] = {
    "telegram": {
        "label": "Telegram",
        "icon": "💬",
        "desc": "Built-in — active whenever the bot is running",
        "docs": "",
    },
    "sms": {
        "label": "SMS  (Twilio / Vonage)",
        "icon": "📱",
        "desc": "Send SMS to any phone number worldwide",
        "docs": "https://console.twilio.com",
    },
    "whatsapp": {
        "label": "WhatsApp",
        "icon": "💚",
        "desc": "WhatsApp Cloud API — Meta Business account required",
        "docs": "https://developers.facebook.com/docs/whatsapp/cloud-api",
    },
    "discord": {
        "label": "Discord",
        "icon": "🎮",
        "desc": "Discord bot adapter",
        "docs": "https://discord.com/developers/applications",
    },
}

# Vault keys that must all be present for an adapter to be considered configured
_ADAPTER_VAULT_GROUPS: dict[str, list[list[str]]] = {
    # SMS: Twilio OR Vonage credential set must be complete
    "sms": [
        ["twilio_account_sid", "twilio/account_sid"],
        ["twilio_auth_token", "twilio/auth_token"],
        ["twilio_phone_number", "twilio/phone_number"],
    ],
    "whatsapp": [
        ["whatsapp_cloud_token", "whatsapp/access_token"],
        ["whatsapp_phone_number_id", "whatsapp/phone_number_id"],
    ],
    "discord": [
        ["discord_bot_token", "discord/bot_token"],
    ],
}

# Setup commands shown in the detail view
_ADAPTER_VAULT_SETUP: dict[str, list[tuple[str, str]]] = {
    "sms": [
        ("Account SID", "navig vault set twilio_account_sid ACxxxxxxx"),
        ("Auth Token", "navig vault set twilio_auth_token xxxxxxxx"),
        ("From Number", "navig vault set twilio_phone_number +1234567890"),
    ],
    "whatsapp": [
        ("Access Token", "navig vault set whatsapp_cloud_token EAAxxxxxxx"),
        ("Phone Number ID", "navig vault set whatsapp_phone_number_id 123456"),
    ],
    "discord": [
        ("Bot Token", "navig vault set discord_bot_token MTxxxxxxx"),
    ],
}

_ADAPTER_ORDER = ["telegram", "sms", "whatsapp", "discord"]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _vault_get(key: str) -> str:
    try:
        from navig.vault.core import get_vault

        v = get_vault()
        if v is None:
            return ""
        return (v.get_secret(key) or "").strip()
    except Exception:
        return ""


def _check_vault_group(keys: list[str]) -> bool:
    """Return True if at least one key in the list has a non-empty vault value."""
    return any(_vault_get(k) for k in keys)


def _adapter_vault_status(name: str) -> tuple[bool, list[str]]:
    """Return ``(all_present, list_of_missing_group_labels)`` for an adapter."""
    groups = _ADAPTER_VAULT_GROUPS.get(name, [])
    if not groups:
        return True, []
    missing: list[str] = []
    for group in groups:
        if not _check_vault_group(group):
            # Use the primary key name as the missing label
            missing.append(group[0])
    return (len(missing) == 0), missing


def _get_adapter_enabled(name: str) -> bool:
    try:
        from navig.config import get_config_manager

        cfg = get_config_manager()
        return bool(cfg.global_config.get("adapters", {}).get(name, {}).get("enabled", False))
    except Exception:
        return False


def _registry_available(name: str) -> bool:
    try:
        from navig.messaging.adapter_registry import get_adapter_registry

        return get_adapter_registry().is_available(name)
    except Exception:
        return False


# ── Mixin ────────────────────────────────────────────────────────────────────

class TelegramMessengersMixin:
    """Mixin exposing the ``/messengers`` hub and ``msg:*`` callback handlers.

    Methods are called via :func:`functools.partial` injection in
    :class:`~navig.gateway.channels.telegram.TelegramChannel`.
    """

    # ── Main hub ─────────────────────────────────────────────────────────────

    async def _handle_messengers(
        self,
        chat_id: int,
        user_id: int = 0,
        message_id: int | None = None,
        text: str = "",
    ) -> None:
        """Render the Messaging Adapters Hub inline keyboard."""
        lines: list[str] = ["<b>📡 Messaging Adapters</b>", ""]
        keyboard_rows: list[list[dict[str, Any]]] = []

        any_active = False

        for name in _ADAPTER_ORDER:
            info = _ADAPTER_DISPLAY.get(name, {})
            label = info.get("label", name.title())
            icon = info.get("icon", "•")

            if name == "telegram":
                is_active = True
                vault_ok = True
                missing: list[str] = []
                enabled = True
            else:
                vault_ok, missing = _adapter_vault_status(name)
                enabled = _get_adapter_enabled(name)
                is_active = _registry_available(name)

            if is_active:
                any_active = True
                status_icon = "✅"
                status_text = "active"
                btn_text = f"✅ {icon} {label}"
            elif vault_ok and not enabled:
                status_icon = "⚡"
                status_text = "credentials ready — disabled"
                btn_text = f"⚡ {icon} {label}"
            elif missing:
                status_icon = "🔒"
                n = len(missing)
                status_text = f"not configured — {n} credential{'s' if n != 1 else ''} missing"
                btn_text = f"🔒 {icon} {label}"
            else:
                status_icon = "⏸"
                status_text = "disabled"
                btn_text = f"⏸ {icon} {label}"

            status_line = (
                f"{status_icon} <b>{html.escape(label)}</b>"
                f"  <i>{html.escape(status_text)}</i>"
            )
            lines.append(status_line)
            keyboard_rows.append([{"text": btn_text, "callback_data": f"msg:detail:{name}"}])

        lines.append("")

        # ── Action row ────────────────────────────────────────────────────
        action_row: list[dict[str, Any]] = [
            {"text": "📋 Contacts", "callback_data": "msg:contacts"},
            {"text": "🔁 Threads", "callback_data": "msg:threads"},
        ]
        if any_active:
            action_row.append({"text": "🔄 Refresh", "callback_data": "msg:refresh"})
        keyboard_rows.append(action_row)

        if message_id:
            keyboard_rows.append([
                {"text": "🔙 Back", "callback_data": "nav:back"},
                {"text": "🏠 Home", "callback_data": "nav:home"},
            ])
        keyboard_rows.append([{"text": "✖ Close", "callback_data": "msg:close"}])

        payload = "\n".join(lines)
        if message_id:
            try:
                await self.edit_message(  # type: ignore[attr-defined]
                    chat_id, message_id, payload,
                    parse_mode="HTML", keyboard=keyboard_rows,
                )
                return
            except Exception:
                pass
        await self.send_message(  # type: ignore[attr-defined]
            chat_id, payload, parse_mode="HTML", keyboard=keyboard_rows,
        )

    # ── Callback dispatcher (invoked from CallbackHandler via msg: prefix) ───

    async def _handle_messengers_callback(
        self,
        cb_id: str,
        cb_data: str,
        chat_id: int,
        message_id: int | None,
        user_id: int,
    ) -> None:
        """Route ``msg:*`` callback data from inline button presses."""
        try:
            await self._api_call(  # type: ignore[attr-defined]
                "answerCallbackQuery", {"callback_query_id": cb_id}
            )
        except Exception:
            pass

        if cb_data == "msg:close":
            try:
                await self.delete_message(chat_id, message_id)  # type: ignore[attr-defined]
            except Exception:
                pass
            return

        if cb_data in ("msg:back", "msg:refresh"):
            await TelegramMessengersMixin._handle_messengers(self, chat_id, user_id, message_id)
            return

        if cb_data == "msg:contacts":
            try:
                from navig.gateway.channels.telegram_messaging_mixin import TelegramMessagingMixin
                import functools
                handler = functools.partial(TelegramMessagingMixin._handle_messaging_contacts, self)
                await handler(chat_id=chat_id, user_id=user_id, text="")
            except Exception as exc:
                logger.debug("msg:contacts delegation failed: %s", exc)
                await self.send_message(  # type: ignore[attr-defined]
                    chat_id, "Use <code>/contacts</code> to list your contacts.", parse_mode="HTML",
                )
            return

        if cb_data == "msg:threads":
            try:
                from navig.gateway.channels.telegram_messaging_mixin import TelegramMessagingMixin
                import functools
                handler = functools.partial(TelegramMessagingMixin._handle_messaging_threads, self)
                await handler(chat_id=chat_id, user_id=user_id, text="")
            except Exception as exc:
                logger.debug("msg:threads delegation failed: %s", exc)
                await self.send_message(  # type: ignore[attr-defined]
                    chat_id,
                    "Use <code>/threads</code> to list active conversations.",
                    parse_mode="HTML",
                )
            return

        if cb_data.startswith("msg:detail:"):
            name = cb_data[len("msg:detail:"):]
            await TelegramMessengersMixin._show_messenger_detail(self, chat_id, message_id, user_id, name)
            return

        if cb_data.startswith("msg:enable:"):
            name = cb_data[len("msg:enable:"):]
            await TelegramMessengersMixin._enable_messenger_adapter(self, chat_id, message_id, user_id, name)
            return

        # Unknown msg: callback — silently ignore
        logger.debug("Unknown msg: callback: %s", cb_data)

    # ── Detail view ──────────────────────────────────────────────────────────

    async def _show_messenger_detail(
        self,
        chat_id: int,
        message_id: int | None,
        user_id: int,
        name: str,
    ) -> None:
        """Show configuration status and setup instructions for one adapter."""
        info = _ADAPTER_DISPLAY.get(name, {})
        label = info.get("label", name.title())
        icon = info.get("icon", "•")
        desc = info.get("desc", "")
        docs = info.get("docs", "")

        lines: list[str] = [
            f"<b>{icon} {html.escape(label)}</b>",
            f"<i>{html.escape(desc)}</i>",
        ]
        if docs:
            lines.append(f"<a href=\"{html.escape(docs)}\">📖 Documentation</a>")
        lines.append("")

        keyboard_rows: list[list[dict[str, Any]]] = []

        if name == "telegram":
            lines += [
                "✅ <b>Built-in adapter — always active</b>",
                "",
                "Telegram is your primary interface. No extra configuration needed.",
                "",
                "To send via other adapters:",
                "<code>/send @contact sms Hello!</code>",
                "<code>/sms @contact Your message</code>",
                "<code>/wa @contact Your message</code>",
            ]
        else:
            vault_ok, missing = _adapter_vault_status(name)
            enabled = _get_adapter_enabled(name)
            is_active = _registry_available(name)

            if is_active:
                lines += [
                    "✅ <b>Active and ready to send</b>",
                    "",
                    f"Quick send: <code>/send @contact {name} Your message</code>",
                ]
            elif vault_ok and not enabled:
                lines += [
                    "⚡ <b>Credentials present — adapter not enabled yet</b>",
                    "",
                    "Enable it now:",
                    f"<code>navig config set adapters.{html.escape(name)}.enabled true</code>",
                    "Then restart: <code>navig service restart</code>",
                ]
                keyboard_rows.append([{
                    "text": f"⚡ Enable {label.split('(')[0].strip()}",
                    "callback_data": f"msg:enable:{name}",
                }])
            else:
                setup_steps = _ADAPTER_VAULT_SETUP.get(name, [])
                lines.append("🔒 <b>Setup required</b>")
                lines.append("")
                if setup_steps:
                    lines.append("<b>Step 1 — Store credentials in vault:</b>")
                    for _, cmd in setup_steps:
                        lines.append(f"<code>{html.escape(cmd)}</code>")
                    lines.append("")
                lines += [
                    "<b>Step 2 — Enable adapter:</b>",
                    f"<code>navig config set adapters.{html.escape(name)}.enabled true</code>",
                    "",
                    "<b>Step 3 — Restart service:</b>",
                    "<code>navig service restart</code>",
                    "",
                    "Or run the interactive wizard:",
                    "<code>navig init --messaging</code>",
                ]
                if missing:
                    lines.append("")
                    lines.append("<b>Missing vault keys:</b>")
                    for k in missing:
                        lines.append(f"  • <code>{html.escape(k)}</code>")

        lines.append("")

        keyboard_rows.append([
            {"text": "🔙 Back to Adapters", "callback_data": "msg:back"},
            {"text": "✖ Close", "callback_data": "msg:close"},
        ])

        payload = "\n".join(lines)
        if message_id:
            try:
                await self.edit_message(  # type: ignore[attr-defined]
                    chat_id, message_id, payload,
                    parse_mode="HTML", keyboard=keyboard_rows,
                )
                return
            except Exception:
                pass
        await self.send_message(  # type: ignore[attr-defined]
            chat_id, payload, parse_mode="HTML", keyboard=keyboard_rows,
        )

    # ── Enable adapter in-place ───────────────────────────────────────────────

    async def _enable_messenger_adapter(
        self,
        chat_id: int,
        message_id: int | None,
        user_id: int,
        name: str,
    ) -> None:
        """Write ``adapters.NAME.enabled = true`` to global config."""
        try:
            from navig.config import get_config_manager

            cfg_mgr = get_config_manager()
            adapters = dict(cfg_mgr.global_config.get("adapters", {}))
            adapter_cfg = dict(adapters.get(name, {}))
            adapter_cfg["enabled"] = True
            adapters[name] = adapter_cfg
            cfg_mgr.update_global_config({"adapters": adapters})

            info = _ADAPTER_DISPLAY.get(name, {})
            label = html.escape(info.get("label", name.title()))
            await self.send_message(  # type: ignore[attr-defined]
                chat_id,
                f"✅ <b>{label}</b> enabled in config.\n\n"
                "Restart the service for changes to take effect:\n"
                "<code>navig service restart</code>",
                parse_mode="HTML",
            )
        except Exception as exc:
            await self.send_message(  # type: ignore[attr-defined]
                chat_id,
                f"❌ Could not enable adapter: {html.escape(str(exc))}",
                parse_mode="HTML",
            )
