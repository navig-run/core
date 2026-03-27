"""
Telegram Bot API transport helpers.

Extracted from `telegram.py` and `telegram_voice.py` so the main channel file
stays focused on orchestration while preserving the existing return contracts:
- `_api_call()` returns the unwrapped `result` value or `None`
- `_api_call_multipart()` returns the raw Bot API JSON dict or `None`
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

aiohttp = None
try:
    import aiohttp
except ImportError:
    pass  # optional dependency not installed; feature disabled


class TelegramApiMixin:
    """Shared Telegram Bot API request helpers."""

    async def _api_call(
        self, method: str, data: Optional[Dict] = None
    ) -> Optional[Dict]:
        """POST a JSON request to a Telegram Bot API method.

        Uses the long-lived ``self._session`` connection pool for low latency.
        Returns the unwrapped ``result`` field on success, ``None`` on any
        failure (API error or network exception). Non-fatal — callers must
        handle ``None`` gracefully.

        Note: for file uploads use ``_api_call_multipart`` instead.
        """
        if not self._session:
            return None

        url = f"{self.base_url}/{method}"

        try:
            async with self._session.post(url, json=data or {}) as resp:
                result = await resp.json()
                if result.get("ok"):
                    return result.get("result")
                logger.error(
                    "Telegram API error in %s: %s", method, result.get("description")
                )
                return None
        except Exception as exc:
            logger.error("Telegram API call %s failed: %s", method, exc)
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
                form.add_field(
                    field_name, content, filename=filename, content_type=content_type
                )
            async with self._session.post(
                url, data=form, timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                return await resp.json()
        except Exception as exc:
            logger.warning("Multipart API call %s failed: %s", method, exc)
            return None
