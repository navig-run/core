"""
Telegram Reaction Intelligence
================================
Translates Telegram ``message_reaction`` updates into semantic signals:

  👍  positive feedback  → memory taxonomy (FEEDBACK)
  👎  negative feedback  → trigger /refine on the original query
  🔥  bookmark          → save bot reply to wiki inbox
  🤔  explain more      → AI elaborates on the bot reply
  💯  pin               → pin the message in group chats

Module-level constants are the single source of truth for all emoji→action
mappings.  Only emojis from the Telegram-approved reaction set are used here.
⭐ and 🔁 are NOT in Telegram's allowed reaction set and must never be added.

Integration points:
- ``TelegramChannel._process_update`` calls ``self._on_message_reaction(upd)``
- ``TelegramChannel._send_response`` calls ``self._record_bot_reply(…)`` after
  every AI response so the ring buffer can resolve reactions to their queries.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dispatch table — emoji → bound-method name (single source of truth)
# ---------------------------------------------------------------------------
# All entries MUST be verified Telegram reaction emojis.
# See: https://core.telegram.org/bots/api#reactiontypeemoji
_REACTION_DISPATCH: dict[str, str] = {
    "👍": "_reaction_positive_feedback",
    "👎": "_reaction_request_refine",
    "🔥": "_reaction_bookmark_wiki",
    "🤔": "_reaction_explain_more",
    "💯": "_reaction_pin_message",
}

# Ack reactions sent back on the same message after a handler runs.
# Empty string means the ack is a chat message instead (see per-handler).
_REACTION_ACKS: dict[str, str] = {
    "👍": "🫡",
    "👎": "✍️",
    "🔥": "✅",
    "🤔": "🤔",
    "💯": "📌",
}

# Guard: only dispatch on added reactions, not removals
_MIN_NEW_REACTION_COUNT: int = 1


class TelegramReactionsMixin:
    """Mixin — translate incoming ``message_reaction`` events into actions.

    Must be mixed into ``TelegramChannel`` which already provides:
    - ``self._api_call(method, data)``
    - ``self.send_message(chat_id, text, …)``
    - ``self._is_user_authorized(user_id, chat_id, is_group)``
    - ``self._is_group_chat_id(chat_id)``
    - ``self._keep_typing(chat_id)``
    - ``self._send_response(chat_id, response, original_text, user_id, …)``
    - ``self.on_message`` callable
    """

    # ------------------------------------------------------------------
    # Public entry point — called by _process_update
    # ------------------------------------------------------------------

    async def _on_message_reaction(self, reaction_update: dict) -> None:
        """Dispatch a ``message_reaction`` event to the appropriate handler."""
        cfg = self._get_reactions_config()
        if not cfg.get("reactions_enabled", True):
            return

        new_reactions: list[dict] = reaction_update.get("new_reaction") or []
        if len(new_reactions) < _MIN_NEW_REACTION_COUNT:
            return  # user removed a reaction — nothing to do

        chat: dict = reaction_update.get("chat") or {}
        chat_id: int = int(chat.get("id") or 0)
        if not chat_id:
            return

        msg_id: int = int(reaction_update.get("message_id") or 0)
        if not msg_id:
            return

        user_info: dict = reaction_update.get("user") or {}
        user_id: int = int(user_info.get("id") or 0)

        # Auth check — only configured users may trigger reaction actions.
        # Anonymous reactions in large groups (actor_chat) are silently skipped.
        if user_id:
            is_group = chat.get("type") in ("group", "supergroup")
            if not self._is_user_authorized(user_id, chat_id, is_group):
                return

        # Dispatch on the first recognised emoji; ignore multiple per update.
        for rxn in new_reactions:
            if rxn.get("type") != "emoji":
                continue
            emoji: str = rxn.get("emoji", "")
            handler_name = _REACTION_DISPATCH.get(emoji)
            if handler_name:
                handler = getattr(self, handler_name, None)
                if handler:
                    try:
                        await handler(
                            chat_id=chat_id,
                            msg_id=msg_id,
                            user_id=user_id,
                            emoji=emoji,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "Reaction handler %r failed for chat=%s msg=%s: %s",
                            handler_name,
                            chat_id,
                            msg_id,
                            exc,
                        )
            break  # only the first recognised emoji per update

    # ------------------------------------------------------------------
    # Individual handlers
    # ------------------------------------------------------------------

    async def _reaction_positive_feedback(
        self, chat_id: int, msg_id: int, user_id: int, emoji: str
    ) -> None:
        """👍 → save positive memory signal (FEEDBACK) and ack with 🫡."""
        try:
            from navig.memory.store import get_memory_store

            store = get_memory_store()
            store.add(
                content=f"User reacted 👍 to bot message {msg_id} in chat {chat_id}.",
                memory_type="FEEDBACK",
                metadata={"emoji": emoji, "msg_id": msg_id, "valence": "positive"},
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not save positive reaction to memory store: %s", exc)

        await self._safe_set_reaction(chat_id, msg_id, _REACTION_ACKS["👍"])

    async def _reaction_request_refine(
        self, chat_id: int, msg_id: int, user_id: int, emoji: str
    ) -> None:
        """👎 → trigger /refine on the original query that produced this reply."""
        original_query: str = self._lookup_query_for_reply(chat_id, msg_id) or ""

        # Ack immediately so user knows the bot is composing
        await self._safe_set_reaction(chat_id, msg_id, _REACTION_ACKS["👎"])

        if not original_query:
            await self.send_message(
                chat_id,
                "🔄 Got it — reply to my message <i>or</i> ask me again and I'll try harder.",
                parse_mode="HTML",
            )
            return

        # Delegate to the full refinement engine
        try:
            await self._handle_refine_cmd(
                chat_id=chat_id,
                user_id=user_id,
                text=f"/refine {original_query}",
            )
        except AttributeError:
            # Fallback: re-run the original query with an explicit "improve" hint
            if getattr(self, "on_message", None):
                typing_task = asyncio.create_task(self._keep_typing(chat_id))
                try:
                    response = await self.on_message(
                        channel="telegram",
                        user_id=str(user_id),
                        message=original_query,
                        metadata={"tier_override": "big", "refine_signal": True},
                    )
                finally:
                    typing_task.cancel()
                    try:
                        await typing_task
                    except asyncio.CancelledError:
                        pass
                if response:
                    await self._send_response(
                        chat_id, response, original_query, user_id=user_id
                    )

    async def _reaction_bookmark_wiki(
        self, chat_id: int, msg_id: int, user_id: int, emoji: str
    ) -> None:
        """🔥 → save the bot reply text to the wiki inbox."""
        reply_text: str = self._lookup_reply_text(chat_id, msg_id) or ""

        if reply_text:
            try:
                import time
                from pathlib import Path

                from navig.platform.paths import config_dir as _config_dir

                wiki_inbox = _config_dir() / "wiki" / "inbox"
                wiki_inbox.mkdir(parents=True, exist_ok=True)
                ts = int(time.time())
                note_path = wiki_inbox / f"reaction_bookmark_{ts}.md"
                note_path.write_text(
                    "---\n"
                    "source: telegram_reaction\n"
                    f"timestamp: {ts}\n"
                    f"chat_id: {chat_id}\n"
                    "---\n\n"
                    f"{reply_text}\n",
                    encoding="utf-8",
                )
                await self.send_message(chat_id, "🔥 Added to wiki inbox.", parse_mode=None)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Wiki bookmark failed: %s", exc)
                await self.send_message(chat_id, "🔥 noted.", parse_mode=None)
        else:
            await self.send_message(
                chat_id,
                "🔥 Noted — reply to my message if you'd like to save specific text.",
                parse_mode=None,
            )

        await self._safe_set_reaction(chat_id, msg_id, _REACTION_ACKS["🔥"])

    async def _reaction_explain_more(
        self, chat_id: int, msg_id: int, user_id: int, emoji: str
    ) -> None:
        """🤔 → ask the AI to elaborate on the bot reply."""
        reply_text: str = self._lookup_reply_text(chat_id, msg_id) or ""

        # Mirror 🤔 back so user sees the bot is thinking
        await self._safe_set_reaction(chat_id, msg_id, _REACTION_ACKS["🤔"])

        if not reply_text and not getattr(self, "on_message", None):
            await self.send_message(
                chat_id,
                "🤔 Could not find that message — please reply to it and ask me to expand.",
                parse_mode=None,
            )
            return

        prompt = (
            "Please elaborate further on this response. "
            "Add depth, concrete examples, and any relevant context:\n\n"
            f"{reply_text}"
            if reply_text
            else "Please elaborate on your previous response with more depth and examples."
        )

        if getattr(self, "on_message", None):
            typing_task = asyncio.create_task(self._keep_typing(chat_id))
            try:
                response = await self.on_message(
                    channel="telegram",
                    user_id=str(user_id),
                    message=prompt,
                    metadata={"tier_override": "big"},
                )
            finally:
                typing_task.cancel()
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass
            if response:
                await self._send_response(chat_id, response, prompt, user_id=user_id)

    async def _reaction_pin_message(
        self, chat_id: int, msg_id: int, user_id: int, emoji: str
    ) -> None:
        """💯 → pin the message (group chats only)."""
        if not self._is_group_chat_id(chat_id):
            await self.send_message(
                chat_id,
                "📌 Pinning a message works in group or supergroup chats.",
                parse_mode=None,
            )
            return

        try:
            result = await self._api_call(
                "pinChatMessage",
                {
                    "chat_id": chat_id,
                    "message_id": msg_id,
                    "disable_notification": True,
                },
            )
            if result is not None:
                await self._safe_set_reaction(chat_id, msg_id, "📌")
            else:
                await self.send_message(
                    chat_id,
                    "📌 Could not pin — make sure I have admin rights in this group.",
                    parse_mode=None,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Reaction pin failed for msg=%s chat=%s: %s", msg_id, chat_id, exc)
            await self.send_message(
                chat_id,
                "📌 Pin failed — check my admin permissions.",
                parse_mode=None,
            )

    # ------------------------------------------------------------------
    # Ring-buffer helpers (delegates to SessionManager)
    # ------------------------------------------------------------------

    def _record_bot_reply(
        self, chat_id: int, msg_id: int, original_query: str, reply_text: str
    ) -> None:
        """Record a bot reply → (original_query, reply_text) for later reaction lookups."""
        try:
            from navig.gateway.channels.telegram_sessions import get_session_manager

            sm = get_session_manager()
            sm.record_bot_reply(chat_id, msg_id, original_query, reply_text)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not record bot reply in ring buffer: %s", exc)

    def _lookup_query_for_reply(self, chat_id: int, msg_id: int) -> str | None:
        """Return the original user query that produced bot message *msg_id*."""
        try:
            from navig.gateway.channels.telegram_sessions import get_session_manager

            sm = get_session_manager()
            return sm.get_query_for_bot_reply(chat_id, msg_id)
        except Exception:  # noqa: BLE001
            return None

    def _lookup_reply_text(self, chat_id: int, msg_id: int) -> str | None:
        """Return the stored text of bot message *msg_id*."""
        try:
            from navig.gateway.channels.telegram_sessions import get_session_manager

            sm = get_session_manager()
            return sm.get_reply_text_for_msg(chat_id, msg_id)
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _safe_set_reaction(self, chat_id: int, msg_id: int, emoji: str) -> None:
        """Set a reaction on a message; swallow all errors (non-critical)."""
        if not emoji:
            return
        try:
            await self._api_call(
                "setMessageReaction",
                {
                    "chat_id": chat_id,
                    "message_id": msg_id,
                    "reaction": [{"type": "emoji", "emoji": emoji}],
                    "is_big": False,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Reaction ack failed (non-fatal) for msg=%s: %s", msg_id, exc)

    def _get_reactions_config(self) -> dict:
        """Return the reactions sub-config from the config manager (best-effort)."""
        try:
            from navig.config import get_config_manager

            cm = get_config_manager()
            tg = cm.get("telegram") or {}
            return {
                "reactions_enabled": tg.get("reactions_enabled", True),
            }
        except Exception:  # noqa: BLE001
            return {"reactions_enabled": True}
