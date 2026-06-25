"""
Telegram Messaging Commands Mixin — Handler implementations for /send, /sms,
/wa, /thread, /threads, /contact, /contacts, /reply slash commands.

Mixed into the Telegram channel adapter alongside ``TelegramCommandsMixin``.
Each handler method corresponds to a :data:`SlashCommandEntry` registered in
``_SLASH_REGISTRY`` with ``category="messaging"``.

Expects ``self`` to be a :class:`TelegramChannel` instance providing:
- ``send_message(chat_id, text, parse_mode=...)``
- ``allowed_users: set[int]``

Handlers follow the existing dispatch convention: individual ``**kwargs``
injected by the ``_SLASH_REGISTRY`` dispatcher (``chat_id``, ``user_id``,
``text``, ``metadata``, ``session``, ``is_group``, ``username``).
"""

from __future__ import annotations

import html
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Known network aliases accepted by /send
_NETWORK_ALIASES: dict[str, str] = {
    "sms": "sms",
    "whatsapp": "whatsapp",
    "wa": "whatsapp",
    "whatsapp_cloud": "whatsapp",
    "discord": "discord",
    "telegram": "telegram",
    "tg": "telegram",
    "matrix": "matrix",
    "mx": "matrix",
    "signal": "signal",
}


def _extract_json(raw: str) -> dict | None:
    """Pull the first JSON object out of an LLM reply (tolerates code fences)."""
    if not raw:
        return None
    import json as _json

    s = raw.strip()
    a, b = s.find("{"), s.rfind("}")
    if a == -1 or b == -1 or b < a:
        return None
    try:
        return _json.loads(s[a : b + 1])
    except Exception:
        return None


class TelegramMessagingMixin:
    """
    Mixin providing messaging slash-command handlers for TelegramChannel.

    All public ``_handle_messaging_*`` methods match :data:`SlashCommandEntry`
    ``handler`` names in the *messaging* category of ``_SLASH_REGISTRY``.
    """

    # ── /send @alias [network] message ────────────────────────

    async def _handle_messaging_send(
        self, chat_id: int, text: str = "", user_id: int = 0, **_: Any
    ) -> None:
        """Route a message through the unified messaging layer."""
        args = text.split() if text else []
        # Strip leading "/send" if the dispatcher passed the full text
        if args and args[0].lower() in ("/send",):
            args = args[1:]

        if len(args) < 2:
            await self.send_message(
                chat_id,
                "Usage: <code>/send @alias [network] message</code>\n"
                "Example: <code>/send @alice Hello!</code>\n"
                "Example: <code>/send @alice whatsapp Hello!</code>",
                parse_mode="HTML",
            )
            return

        target = args[0]
        network: str | None = None

        if len(args) >= 3 and args[1].lower() in _NETWORK_ALIASES:
            network = _NETWORK_ALIASES[args[1].lower()]
            body = " ".join(args[2:])
        else:
            body = " ".join(args[1:])

        if not body.strip():
            await self.send_message(chat_id, "Message body cannot be empty.")
            return

        try:
            receipt = await self._messaging_dispatch(target, body, network=network)
            if receipt.ok:
                status = f"✅ Sent via <b>{html.escape(receipt.adapter or 'adapter')}</b>"
                if receipt.message_id:
                    status += f" (<code>{html.escape(str(receipt.message_id))}</code>)"
                await self.send_message(chat_id, status, parse_mode="HTML")
            else:
                await self.send_message(chat_id, f"❌ Send failed: {receipt.error}")
        except Exception as exc:
            logger.error("messaging_send_error | target=%s | %s", target, exc)
            await self.send_message(chat_id, f"❌ Error: {exc}")

    # ── /sms @alias message ───────────────────────────────────

    async def _handle_messaging_sms(
        self, chat_id: int, text: str = "", user_id: int = 0, **_: Any
    ) -> None:
        """Shortcut for ``/send @alias sms message``."""
        args = text.split() if text else []
        if args and args[0].lower() in ("/sms",):
            args = args[1:]

        if len(args) < 2:
            await self.send_message(chat_id, "Usage: <code>/sms @alias message</code>", parse_mode="HTML")
            return

        target = args[0]
        body = " ".join(args[1:])
        try:
            receipt = await self._messaging_dispatch(target, body, network="sms")
            if receipt.ok:
                await self.send_message(
                    chat_id,
                    f"✅ SMS sent (<code>{receipt.message_id or 'ok'}</code>)",
                    parse_mode="HTML",
                )
            else:
                await self.send_message(chat_id, f"❌ SMS failed: {receipt.error}")
        except Exception as exc:
            await self.send_message(chat_id, f"❌ Error: {exc}")

    # ── Natural-language messaging (voice or text) ────────────
    async def _try_nl_messaging_intent(
        self, chat_id: int, text: str, metadata: dict | None = None
    ) -> bool:
        """Detect a free-form 'send SMS/message to <contact>' instruction (any
        language, incl. a requested translation) and dispatch it. Returns True
        when handled. Powers voice commands like
        «Навиг отправь смс Алексу на французском "Привет бро"»: the transcribed
        text is parsed by the LLM, translated into the requested language, the
        contact is resolved to a phone number, and the SMS is sent."""
        low = (text or "").lower()
        # Cheap gate: only spend an LLM call when a messaging channel is named.
        if not any(g in low for g in ("sms", "смс", "whatsapp", "ватсап", "envoie", "manda")):
            return False

        # Give the model the known contacts so it can map "Алексу"/"Alex" → alias.
        try:
            from navig.store.contacts import get_contact_store

            rows = get_contact_store().list_contacts(limit=200)
            contacts_blurb = ", ".join(
                f"{c.alias} ({c.display_name})" if getattr(c, "display_name", "") else c.alias
                for c in rows
            ) or "(none)"
        except Exception:
            contacts_blurb = "(unavailable)"

        from navig.llm_generate import llm_generate

        sys = (
            "Convert the user's instruction into a messaging action. The user may "
            "speak any language. Reply with ONE JSON object and nothing else:\n"
            '{"is_send": bool, "contact": str, "channel": "sms|whatsapp|telegram", '
            '"language": str|null, "message": str}\n'
            "- is_send: true ONLY if they clearly want to send a message to someone.\n"
            "- contact: the recipient; match one of the known contact aliases if possible.\n"
            "- channel: the network to use (default \"sms\").\n"
            "- language: the language they asked to send IN (e.g. \"French\"), else null.\n"
            "- message: the body, TRANSLATED into `language` if given, else the original. "
            "Output only the exact words to send."
        )
        user = f"Instruction: {text}\nKnown contacts (alias — name): {contacts_blurb}"
        try:
            raw = llm_generate(
                messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
                mode="big_tasks", temperature=0.2, max_tokens=400,
            )
        except Exception:
            return False

        data = _extract_json(raw)
        if not data or not data.get("is_send"):
            return False
        contact = str(data.get("contact") or "").strip()
        message = str(data.get("message") or "").strip()
        channel = _NETWORK_ALIASES.get((data.get("channel") or "sms").strip().lower(), "sms")
        if not contact or not message:
            return False

        try:
            receipt = await self._messaging_dispatch(contact, message, network=channel)
        except Exception as exc:
            await self.send_message(chat_id, f"❌ Couldn't send to {html.escape(contact)}: {exc}")
            return True
        if getattr(receipt, "ok", False):
            lang = data.get("language")
            tail = f" (in {lang})" if lang else ""
            await self.send_message(
                chat_id,
                f"✅ {channel.upper()} sent to <b>{html.escape(contact)}</b>{html.escape(tail)}:\n{html.escape(message)}",
                parse_mode="HTML",
            )
        else:
            await self.send_message(
                chat_id,
                f"❌ Couldn't send to {html.escape(contact)}: {getattr(receipt, 'error', 'failed')}",
            )
        return True

    # ── /wa @alias message ────────────────────────────────────

    async def _handle_messaging_wa(
        self, chat_id: int, text: str = "", user_id: int = 0, **_: Any
    ) -> None:
        """Shortcut for ``/send @alias whatsapp message``."""
        args = text.split() if text else []
        if args and args[0].lower() in ("/wa",):
            args = args[1:]

        if len(args) < 2:
            await self.send_message(chat_id, "Usage: <code>/wa @alias message</code>", parse_mode="HTML")
            return

        target = args[0]
        body = " ".join(args[1:])
        try:
            receipt = await self._messaging_dispatch(target, body, network="whatsapp")
            if receipt.ok:
                await self.send_message(
                    chat_id,
                    f"✅ WhatsApp sent (<code>{receipt.message_id or 'ok'}</code>)",
                    parse_mode="HTML",
                )
            else:
                await self.send_message(chat_id, f"❌ WhatsApp failed: {receipt.error}")
        except Exception as exc:
            await self.send_message(chat_id, f"❌ Error: {exc}")

    # ── /thread [id] ──────────────────────────────────────────

    async def _handle_messaging_thread(self, chat_id: int, text: str = "", **_: Any) -> None:
        """Show thread details by ID, or fall through to ``/threads``."""
        args = text.split() if text else []
        if args and args[0].lower() in ("/thread",):
            args = args[1:]

        from navig.store.threads import get_thread_store

        store = get_thread_store()

        if args:
            try:
                thread_id = int(args[0])
            except ValueError:
                await self.send_message(chat_id, "Usage: <code>/thread [id]</code>", parse_mode="HTML")
                return

            thread = store.get_by_id(thread_id)
            if thread is None:
                await self.send_message(chat_id, f"Thread #{thread_id} not found.")
                return

            lines = [
                f"🧵 <b>Thread #{thread.id}</b>",
                f"  Adapter: <code>{html.escape(str(thread.adapter))}</code>",
                f"  Remote: <code>{html.escape(str(thread.remote_conversation_id))}</code>",
                f"  Contact: {html.escape(str(thread.contact_alias or '(none)'))}",
                f"  Status: {html.escape(str(thread.status))}",
                f"  Last active: {html.escape(str(thread.last_active))}",
            ]
            await self.send_message(chat_id, "\n".join(lines), parse_mode="HTML")
        else:
            await self._handle_messaging_threads(chat_id=chat_id, text="")

    # ── /threads [adapter] ────────────────────────────────────

    async def _handle_messaging_threads(self, chat_id: int, text: str = "", **_: Any) -> None:
        """List active conversation threads, optionally filtered by adapter."""
        args = text.split() if text else []
        if args and args[0].lower() in ("/threads",):
            args = args[1:]

        from navig.store.threads import get_thread_store

        store = get_thread_store()
        adapter_filter = args[0] if args else None
        threads = store.list_threads(adapter=adapter_filter, limit=20)

        if not threads:
            await self.send_message(chat_id, "No active threads.")
            return

        lines = ["🧵 <b>Active Threads</b>\n"]
        for t in threads:
            alias_str = f"@{html.escape(t.contact_alias)}" if t.contact_alias else "(unknown)"
            lines.append(
                f"  <code>#{t.id}</code> [{html.escape(str(t.adapter))}] {alias_str} — {html.escape(str(t.status))}"
            )
        await self.send_message(chat_id, "\n".join(lines), parse_mode="HTML")

    # ── /contact @alias ───────────────────────────────────────

    async def _handle_messaging_contact(self, chat_id: int, text: str = "", **_: Any) -> None:
        """Show contact details by alias."""
        args = text.split() if text else []
        if args and args[0].lower() in ("/contact",):
            args = args[1:]

        if not args:
            await self.send_message(chat_id, "Usage: <code>/contact @alias</code>", parse_mode="HTML")
            return

        alias = args[0].lstrip("@")
        from navig.store.contacts import get_contact_store

        store = get_contact_store()
        contact = store.resolve_alias(alias)

        if contact is None:
            await self.send_message(chat_id, f"Contact @{alias} not found.")
            return

        lines = [
            f"👤 <b>@{html.escape(contact.alias)}</b>",
            f"  Name: {html.escape(str(contact.display_name or '(none)'))}",
            f"  Default: {html.escape(str(contact.default_network or '(auto)'))}",
        ]
        if contact.routes:
            lines.append("  Routes:")
            for r in contact.routes:
                lines.append(
                    f"    • {html.escape(str(r.network))}: <code>{html.escape(str(r.address))}</code> (pri={html.escape(str(r.priority))})"
                )
        if contact.fallbacks:
            lines.append(f"  Fallbacks: {html.escape(', '.join(contact.fallbacks))}")
        await self.send_message(chat_id, "\n".join(lines), parse_mode="HTML")

    # ── /contacts ─────────────────────────────────────────────

    async def _handle_messaging_contacts(self, chat_id: int, text: str = "", **_: Any) -> None:
        """List all contacts in the address book."""
        from navig.store.contacts import get_contact_store

        store = get_contact_store()
        contacts = store.list_contacts(limit=50)

        if not contacts:
            await self.send_message(
                chat_id,
                "👥 <b>No contacts yet.</b>\n\n"
                "Contacts let you send messages across any configured adapter "
                "(WhatsApp, SMS, Discord, Telegram) without typing the full address each time.\n\n"
                "<b>Add your first contact:</b>\n"
                "<code>navig contacts add --alias alice --name 'Alice Smith' --route 'whatsapp:+33612345678'</code>\n\n"
                "<b>More examples:</b>\n"
                "<code>navig contacts add --alias bob   --name 'Bob'   --route 'sms:+447911123456'</code>\n"
                "<code>navig contacts add --alias carol --name 'Carol' --route 'telegram:987654321'</code>\n"
                "<code>navig contacts add --alias team  --name 'Team'  --route 'discord:channel:123456'</code>\n\n"
                "<b>Then message them with:</b>\n"
                "<code>/msg alice Hey, how are you?</code>\n"
                "<code>/msg bob   Meeting at 3pm today</code>\n\n"
                "Run <code>navig contacts --help</code> to see all options.",
                parse_mode="HTML",
            )
            return

        lines = ["👥 <b>Contacts</b>\n"]
        for c in contacts:
            nets = ", ".join(r.network for r in c.routes) or "no routes"
            lines.append(
                f"  @{html.escape(c.alias)} — {html.escape(str(c.display_name or '(unnamed)'))} [{html.escape(nets)}]"
            )
        await self.send_message(chat_id, "\n".join(lines), parse_mode="HTML")

    # ── /reply [thread_id] message ────────────────────────────

    async def _handle_messaging_reply(
        self, chat_id: int, text: str = "", user_id: int = 0, **_: Any
    ) -> None:
        """Reply to an existing conversation thread."""
        args = text.split() if text else []
        if args and args[0].lower() in ("/reply",):
            args = args[1:]

        if not args:
            await self.send_message(
                chat_id,
                "Usage: <code>/reply [thread_id] message</code>\nExample: <code>/reply 42 Thanks for the update!</code>",
                parse_mode="HTML",
            )
            return

        from navig.store.threads import get_thread_store

        store = get_thread_store()

        # Try to parse first arg as thread_id
        try:
            thread_id = int(args[0])
            body = " ".join(args[1:])
            if not body.strip():
                await self.send_message(chat_id, "Message body cannot be empty.")
                return
        except ValueError:
            # No explicit thread_id — only auto-select when exactly one open thread exists
            recent = store.list_threads(status="open", limit=2)
            if not recent:
                await self.send_message(chat_id, "No open threads to reply to.")
                return
            if len(recent) > 1:
                await self.send_message(
                    chat_id,
                    "Multiple open threads found. Use <code>/threads</code> then reply with an explicit ID, e.g. <code>/reply 42 your message</code>.",
                    parse_mode="HTML",
                )
                return
            thread_id = recent[0].id
            body = " ".join(args)

        thread = store.get_by_id(thread_id)
        if thread is None:
            await self.send_message(chat_id, f"Thread <code>#{thread_id}</code> not found.")
            return

        try:
            receipt = await self._messaging_reply_to_thread(thread, body)
            if receipt.ok:
                await self.send_message(
                    chat_id,
                    f"✅ Reply sent to <code>#{thread_id}</code> via <b>{html.escape(str(thread.adapter))}</b>",
                    parse_mode="HTML",
                )
            else:
                await self.send_message(chat_id, f"❌ Reply failed: {receipt.error}")
        except Exception as exc:
            await self.send_message(chat_id, f"❌ Error: {exc}")

    # ── Internal dispatch helpers ─────────────────────────────

    async def _messaging_dispatch(
        self, target: str, body: str, *, network: str | None = None
    ) -> Any:
        """
        Resolve *target*, select adapter, send *body*, track delivery.

        Returns a :class:`~navig.messaging.adapter.DeliveryReceipt`.
        """
        from navig.messaging.adapter import DeliveryReceipt
        from navig.messaging.adapter_registry import get_adapter_registry
        from navig.messaging.delivery import get_delivery_tracker
        from navig.messaging.routing import RoutingEngine
        from navig.store.contacts import get_contact_store
        from navig.store.threads import get_thread_store

        engine = RoutingEngine(get_contact_store(), get_thread_store(), get_adapter_registry())
        decision = engine.resolve(target, network=network)
        adapter = get_adapter_registry().get(decision.adapter_name)
        if adapter is None:
            return DeliveryReceipt.failure(f"Adapter '{decision.adapter_name}' not available")

        tracker = get_delivery_tracker()
        delivery_id = tracker.record_send(
            adapter=decision.adapter_name,
            target=decision.resolved_target.address,
            contact_alias=decision.resolved_target.display_hint or None,
            compliance=decision.compliance_mode,
        )

        thread = await adapter.get_or_create_thread(
            f"{decision.adapter_name}:{decision.resolved_target.address}"
        )
        receipt = await adapter.send_message(thread.remote_conversation_id, body)
        tracker.apply_receipt(delivery_id, receipt)
        return receipt

    async def _messaging_reply_to_thread(self, thread: Any, body: str) -> Any:
        """Send *body* through an existing *thread*'s adapter."""
        from navig.messaging.adapter import DeliveryReceipt
        from navig.messaging.adapter_registry import get_adapter_registry
        from navig.messaging.delivery import get_delivery_tracker

        adapter = get_adapter_registry().get(thread.adapter)
        if adapter is None:
            return DeliveryReceipt.failure(f"Adapter '{thread.adapter}' not available")

        tracker = get_delivery_tracker()
        delivery_id = tracker.record_send(
            adapter=thread.adapter,
            target=thread.remote_conversation_id,
            contact_alias=thread.contact_alias,
            thread_id=thread.id,
        )

        receipt = await adapter.send_message(thread.remote_conversation_id, body)
        tracker.apply_receipt(delivery_id, receipt)

        from navig.store.threads import get_thread_store

        get_thread_store().touch(thread.id)
        return receipt
