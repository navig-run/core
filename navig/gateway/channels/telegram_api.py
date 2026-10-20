"""
Telegram Bot API transport helpers.

Extracted from `telegram.py` and `telegram_voice.py` so the main channel file
stays focused on orchestration while preserving the existing return contracts:
- `_api_call()` returns the unwrapped `result` value or `None`
- `_api_call_multipart()` returns the raw Bot API JSON dict or `None`
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Cap how long a single call will block waiting out a 429. A ``retry_after``
# longer than this is not worth stalling the turn for — return ``None`` and let
# the caller (e.g. the streamed-edit debounce) skip this edit rather than freeze
# the whole reply behind a long server-mandated cooldown.
#
# Module-level, NOT a class attribute: these mixin methods are bound onto
# TelegramChannel via functools.partial (see telegram.py's _MIXIN_BINDINGS), so
# at runtime ``self`` is the channel, which does not inherit anything defined on
# the mixin class. ``self._MAX_RETRY_AFTER_S`` raised AttributeError on the
# first real 429 — swallowed by the except below, so the backoff silently never
# ran. tests/regression/test_telegram_mixin_siblings_resolve.py catches this.
_MAX_RETRY_AFTER_S = 5.0

aiohttp = None
try:
    import aiohttp
except ImportError:
    pass  # optional dependency not installed; feature disabled


class TelegramApiMixin:
    """Shared Telegram Bot API request helpers."""

    async def _api_call(self, method: str, data: dict | None = None) -> dict | None:
        """POST a JSON request to a Telegram Bot API method.

        Uses the long-lived ``self._session`` connection pool for low latency.
        Returns the unwrapped ``result`` field on success, ``None`` on any
        failure (API error or network exception). Non-fatal — callers must
        handle ``None`` gracefully.

        On a ``429 Too Many Requests`` the call honours Telegram's
        ``parameters.retry_after`` exactly once, within ``_MAX_RETRY_AFTER_S``,
        before giving up. Streaming substantive replies (not just short chat)
        raises edit volume and brushes the ~1 edit/s ``editMessageText`` limit,
        so a single respectful backoff turns a dropped edit into a delivered
        one; a longer mandated wait is skipped so the turn never stalls.

        Note: for file uploads use ``_api_call_multipart`` instead.
        """
        if not self._session:
            return None

        url = f"{self.base_url}/{method}"

        for _attempt in range(2):  # initial try + at most one 429 retry
            try:
                async with self._session.post(url, json=data or {}) as resp:
                    result = await resp.json()
                    if result.get("ok"):
                        return result.get("result")
                    retry_after = None
                    if result.get("error_code") == 429:
                        retry_after = (result.get("parameters") or {}).get("retry_after")
                    if (
                        _attempt == 0
                        and retry_after is not None
                        and 0 < float(retry_after) <= _MAX_RETRY_AFTER_S
                    ):
                        logger.warning(
                            "Telegram API %s rate-limited (429); retrying after %ss",
                            method, retry_after,
                        )
                        await asyncio.sleep(float(retry_after))
                        continue
                    logger.error("Telegram API error in %s: %s", method, result.get("description"))
                    return None
            except Exception as exc:
                logger.error("Telegram API call %s failed: %s", method, exc)
                return None
        return None

    async def _api_call_multipart(
        self,
        method: str,
        data: dict[str, Any],
        files: dict[str, tuple[str, bytes, str]],
    ) -> dict[str, Any] | None:
        """POST a ``multipart/form-data`` request to a Telegram Bot API method.

        Used for binary file uploads (voice, audio, documents). Returns the
        raw ``{"ok": ..., "result": ...}`` dict so callers can distinguish
        API-level errors from network failures.
        """
        if not aiohttp or not self._session:
            return None
        url = f"{self.base_url}/{method}"
        try:
            form = aiohttp.FormData()
            for key, value in data.items():
                form.add_field(key, str(value))
            for field_name, (filename, content, content_type) in files.items():
                form.add_field(field_name, content, filename=filename, content_type=content_type)
            async with self._session.post(
                url, data=form, timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                return await resp.json()
        except Exception as exc:
            logger.warning("Multipart API call %s failed: %s", method, exc)
            return None

    # ------------------------------------------------------------------
    # Convenience wrappers — added for checklist, pin, and inline features
    # ------------------------------------------------------------------

    async def _pin_message(
        self,
        chat_id: int,
        message_id: int,
        *,
        disable_notification: bool = True,
    ) -> bool:
        """Pin *message_id* in *chat_id*.  Returns True on success."""
        result = await self._api_call(
            "pinChatMessage",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "disable_notification": disable_notification,
            },
        )
        return result is not None

    async def _unpin_message(self, chat_id: int, message_id: int) -> bool:
        """Unpin *message_id* in *chat_id*.  Returns True on success."""
        result = await self._api_call(
            "unpinChatMessage",
            {"chat_id": chat_id, "message_id": message_id},
        )
        return result is not None

    async def _answer_inline_query(
        self,
        inline_query_id: str,
        results: list[dict],
        *,
        cache_time: int = 30,
        is_personal: bool = True,
    ) -> bool:
        """Answer an inline query.  Returns True on success."""
        result = await self._api_call(
            "answerInlineQuery",
            {
                "inline_query_id": inline_query_id,
                "results": results,
                "cache_time": cache_time,
                "is_personal": is_personal,
            },
        )
        return result is not None
