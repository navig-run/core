"""
Telegram Inline Mode
======================
Handles ``inline_query`` updates so users can type ``@navigbot <query>``
from any Telegram chat and get instant AI-powered answers.

Features:
- Auth gate: non-allowlisted users receive a single locked result
- Per-user 2 s debounce to avoid hammering the AI on every keystroke
- 30 s server-side cache (personal, so different users get separate results)
- Graceful degradation: if AI is unavailable, returns a helpful fallback
- Results are rendered as articles with formatted text previews

Integration point:
  ``TelegramChannel._process_update`` calls ``self._on_inline_query(iq)``
  after adding ``"inline_query"`` to ``allowed_updates``.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------
_INLINE_CACHE_TTL_SECONDS: int = 30      # Telegram server-side cache TTL
_INLINE_DEBOUNCE_SECONDS: float = 2.0    # minimum gap between AI calls per user
_INLINE_MAX_RESULT_LEN: int = 512        # preview text max length in results
_INLINE_RESULT_COUNT: int = 1            # number of article results returned


class TelegramInlineMixin:
    """Mixin — serve ``inline_query`` updates from any chat.

    Requires ``TelegramChannel`` to provide:
    - ``self._is_user_authorized(user_id, chat_id, is_group)``
    - ``self._api_call(method, data)``
    - ``self.on_message`` callable (may be None)
    """

    # {user_id: last_query_time}  — in-memory debounce tracker
    _inline_last_call: dict[int, float]

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def _on_inline_query(self, iq: dict) -> None:
        """Handle an incoming inline query."""
        cfg = self._get_inline_config()
        if not cfg.get("inline_mode_enabled", True):
            return

        query_id: str = iq.get("id", "")
        if not query_id:
            return

        from_user: dict = iq.get("from") or {}
        user_id: int = int(from_user.get("id") or 0)
        query_text: str = (iq.get("query") or "").strip()

        # Auth gate — show a locked result for unauthorised users
        if user_id and not self._is_user_authorized(user_id, 0, False):
            await self._answer_inline_locked(query_id)
            return

        # Empty query — return a usage hint
        if not query_text:
            await self._answer_inline_hint(query_id)
            return

        # Debounce — if the same user typed again within the quiet period, skip
        now = time.monotonic()
        cache: dict[int, float] = getattr(self, "_inline_last_call", {})
        if not hasattr(self, "_inline_last_call"):
            self._inline_last_call = cache
        last = cache.get(user_id, 0.0)
        if now - last < _INLINE_DEBOUNCE_SECONDS:
            return
        cache[user_id] = now

        # Call AI (or return a timeout result)
        answer_text = await self._inline_ask_ai(user_id, query_text)
        if not answer_text:
            await self._answer_inline_error(query_id, query_text)
            return

        results = self._build_inline_results(query_text, answer_text)
        await self._api_call(
            "answerInlineQuery",
            {
                "inline_query_id": query_id,
                "results": results,
                "cache_time": _INLINE_CACHE_TTL_SECONDS,
                "is_personal": True,
            },
        )

    # ------------------------------------------------------------------
    # AI call
    # ------------------------------------------------------------------

    async def _inline_ask_ai(self, user_id: int, query: str) -> str:
        """Ask the AI for a concise response suitable for inline display."""
        on_message = getattr(self, "on_message", None)
        if not on_message:
            return ""
        try:
            response = await asyncio.wait_for(
                on_message(
                    channel="telegram_inline",
                    user_id=str(user_id),
                    message=query,
                    metadata={
                        "tier_override": "small",
                        "inline_mode": True,
                        "max_length": 800,
                    },
                ),
                timeout=8.0,
            )
            return (response or "").strip()
        except asyncio.TimeoutError:
            logger.debug("Inline AI call timed out for query %r", query[:40])
            return ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("Inline AI call failed: %s", exc)
            return ""

    # ------------------------------------------------------------------
    # Result builders
    # ------------------------------------------------------------------

    def _build_inline_results(self, query: str, answer: str) -> list[dict]:
        """Build the InlineQueryResult list for a successful answer."""
        preview = answer[:_INLINE_MAX_RESULT_LEN].rstrip()
        if len(answer) > _INLINE_MAX_RESULT_LEN:
            preview += "…"

        # Primary result: full answer as Article
        return [
            {
                "type": "article",
                "id": str(uuid.uuid4()),
                "title": f"navig: {query[:60]}",
                "description": preview[:100],
                "input_message_content": {
                    "message_text": answer[:4096],
                    "parse_mode": "HTML",
                },
                "thumb_url": "https://navig.ai/icon-64.png",
            }
        ]

    async def _answer_inline_locked(self, query_id: str) -> None:
        """Return a single 'access restricted' result for unauthorised users."""
        await self._api_call(
            "answerInlineQuery",
            {
                "inline_query_id": query_id,
                "results": [
                    {
                        "type": "article",
                        "id": "locked",
                        "title": "🔒 Access restricted",
                        "description": "You are not authorised to use this bot inline.",
                        "input_message_content": {
                            "message_text": "🔒 This bot requires authorisation.",
                        },
                    }
                ],
                "cache_time": 300,  # long cache — access won't change quickly
                "is_personal": True,
            },
        )

    async def _answer_inline_hint(self, query_id: str) -> None:
        """Return a usage hint when the query is empty."""
        await self._api_call(
            "answerInlineQuery",
            {
                "inline_query_id": query_id,
                "results": [
                    {
                        "type": "article",
                        "id": "hint",
                        "title": "Type your question…",
                        "description": "e.g. @navigbot what is a blue-green deployment?",
                        "input_message_content": {
                            "message_text": "💡 Type <code>@navigbot &lt;your question&gt;</code> to get instant answers.",
                            "parse_mode": "HTML",
                        },
                    }
                ],
                "cache_time": 10,
                "is_personal": False,
            },
        )

    async def _answer_inline_error(self, query_id: str, query: str) -> None:
        """Return a graceful error result when the AI is unavailable."""
        await self._api_call(
            "answerInlineQuery",
            {
                "inline_query_id": query_id,
                "results": [
                    {
                        "type": "article",
                        "id": "error",
                        "title": "⚠️ Unable to answer right now",
                        "description": "Try again in a moment.",
                        "input_message_content": {
                            "message_text": (
                                f"⚠️ Could not answer <i>{query[:80]}</i> right now. "
                                "Please try again in a moment."
                            ),
                            "parse_mode": "HTML",
                        },
                    }
                ],
                "cache_time": 5,
                "is_personal": True,
            },
        )

    # ------------------------------------------------------------------
    # Config helper
    # ------------------------------------------------------------------

    def _get_inline_config(self) -> dict:
        """Return inline-mode config (best-effort)."""
        try:
            from navig.config import get_config_manager

            cm = get_config_manager()
            tg = cm.get("telegram") or {}
            return {"inline_mode_enabled": tg.get("inline_mode_enabled", True)}
        except Exception:  # noqa: BLE001
            return {"inline_mode_enabled": True}
