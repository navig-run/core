import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Callable

# Setup standard logger
logger = logging.getLogger(__name__)


class RateLimiter:
    """Token-bucket/sliding window rate limiter per user."""

    def __init__(self, max_requests: int = 30, window_minutes: int = 1):
        self.max_requests = max_requests
        self.window = timedelta(minutes=window_minutes)
        self.requests = defaultdict(list)

    def is_allowed(self, user_id: int) -> bool:
        now = datetime.now()
        user_requests = self.requests[user_id]

        # Clean old requests
        self.requests[user_id] = [
            req_time for req_time in user_requests if now - req_time < self.window
        ]

        if len(self.requests[user_id]) >= self.max_requests:
            return False

        self.requests[user_id].append(now)
        return True


# Global rate limiter — lazy-initialised from config on first use.
# This avoids evaluating config at import time (keeps cold-start fast).
_global_limiter: RateLimiter | None = None


def _get_global_limiter() -> RateLimiter:
    """Return (and lazily create) the process-wide rate limiter."""
    global _global_limiter
    if _global_limiter is None:
        from navig.config import get_config_manager

        rl_cfg = (
            get_config_manager().global_config.get("gateway", {}).get("rate_limit", {})
        )
        _global_limiter = RateLimiter(
            max_requests=rl_cfg.get("max_requests_per_minute", 20),
            window_minutes=rl_cfg.get("window_minutes", 1),
        )
    return _global_limiter


def rate_limited(func: Callable) -> Callable:
    """Decorator to enforce rate limiting on a Telegram command handler."""

    async def wrapper(self, update_or_chat_id: Any, *args, **kwargs):
        # We need to extract user_id. In our architecture, it might be the second argument
        # We'll just look for 'user_id' in kwargs or assume args[0] is chat_id and args[1] is user_id
        user_id = kwargs.get("user_id")
        if user_id is None and len(args) >= 1 and isinstance(args[0], int):
            user_id = args[0]  # user_id is often passed as a positional arg

        if user_id and not _get_global_limiter().is_allowed(user_id):
            logger.warning("Rate limit exceeded", extra={"user_id": user_id})
            # Try to send a warning if we have chat_id
            chat_id = kwargs.get("chat_id")
            if chat_id is None and isinstance(update_or_chat_id, int):
                chat_id = update_or_chat_id
            if chat_id and hasattr(self, "send_message"):
                await self.send_message(
                    chat_id,
                    "🚫 Please wait a moment between requests. Try again shortly.",
                )
            return

        return await func(self, update_or_chat_id, *args, **kwargs)

    return wrapper


def error_handled(func: Callable) -> Callable:
    """Decorator to gracefully handle errors in command handlers."""

    async def wrapper(self, update_or_chat_id: Any, *args, **kwargs):
        try:
            return await func(self, update_or_chat_id, *args, **kwargs)
        except Exception as e:
            logger.error(
                "Unhandled error in %s: %s",
                func.__name__,
                e,
                exc_info=True,
                extra={"handler": func.__name__, "args": args, "kwargs": kwargs},
            )
            chat_id = kwargs.get("chat_id")
            if chat_id is None and isinstance(update_or_chat_id, int):
                chat_id = update_or_chat_id

            if chat_id and hasattr(self, "send_message"):
                await self.send_message(
                    chat_id,
                    "⚠️ Something went wrong. Our team has been notified. Please try again in a moment.",
                )

    return wrapper


def typing_context(func: Callable) -> Callable:
    """Decorator that sends a continuous typing indicator if the operation is slow."""

    async def wrapper(self, update_or_chat_id: Any, *args, **kwargs):
        chat_id = kwargs.get("chat_id")
        if chat_id is None and isinstance(update_or_chat_id, int):
            chat_id = update_or_chat_id

        typing_task = None
        if chat_id and hasattr(self, "_api_call"):

            async def keep_typing():
                while True:
                    try:
                        await self._api_call(
                            "sendChatAction", {"chat_id": chat_id, "action": "typing"}
                        )
                        await asyncio.sleep(4)  # Telegram clears typing after 5s
                    except asyncio.CancelledError:
                        break
                    except Exception:
                        await asyncio.sleep(4)

            typing_task = asyncio.create_task(keep_typing())

        try:
            return await func(self, update_or_chat_id, *args, **kwargs)
        finally:
            if typing_task:
                typing_task.cancel()

    return wrapper
