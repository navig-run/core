"""
Telegram Forum Topic Auto-Router
===================================
When a Telegram supergroup has Topics (forum mode) enabled, automatically
routes bot commands and responses to appropriate forum topics so that
different categories of output stay neatly organised.

  /briefing, /status, /plans  →  "Status & Briefing"
  /db, /query                 →  "Database"
  /run, /shell, /act          →  "Commands"
  /docker, /compose           →  "Docker"
  /ask, /reason, /code        →  "AI Responses"
  anything else               →  General (no thread)

Topics are created on demand and cached in memory.  The full forum group
detection result is also cached so we only call ``getChat`` once per group.

Configuration:
  ``telegram.forum_routing_enabled: false`` (opt-in — off by default)

For production use, enable in ``.navig/config.yaml``:
  telegram:
    forum_routing_enabled: true
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants — single source of truth for routing categories
# ---------------------------------------------------------------------------

# Maps slash-command verb (without /) → topic name
_COMMAND_TOPIC_MAP: dict[str, str] = {
    "briefing": "Status & Briefing",
    "status": "Status & Briefing",
    "plans": "Status & Briefing",
    "plan": "Status & Briefing",
    "db": "Database",
    "query": "Database",
    "sql": "Database",
    "run": "Commands",
    "shell": "Commands",
    "act": "Commands",
    "exec": "Commands",
    "docker": "Docker",
    "compose": "Docker",
    "ask": "AI Responses",
    "reason": "AI Responses",
    "code": "AI Responses",
    "think": "AI Responses",
}

# Icon emojis for each topic (used when creating topics)
_TOPIC_EMOJI_IDS: dict[str, str] = {
    "Status & Briefing": "5417915203100613993",
    "Database": "5282843764451195532",
    "Commands": "5350978914221082782",
    "Docker": "5472354042316922083",
    "AI Responses": "5312536423851630001",
}


class TelegramForumMixin:
    """Mixin — route messages to forum topics in supergroups.

    Requires ``TelegramChannel`` to provide:
    - ``self._api_call(method, data)``

    Usage in handlers::

        thread_id = await self._get_thread_for_command("/briefing", chat_id)
        await self.send_message(chat_id, text, message_thread_id=thread_id)
    """

    # {chat_id: bool} — whether chat is a forum (cached after first getChat)
    _forum_group_cache: dict[int, bool]

    # {(chat_id, topic_name): thread_id} — topic id cache
    _forum_topic_cache: dict[tuple[int, str], int]

    def _ensure_forum_caches(self) -> None:
        """Lazily initialise the cache dicts on first use."""
        if not hasattr(self, "_forum_group_cache"):
            self._forum_group_cache = {}
        if not hasattr(self, "_forum_topic_cache"):
            self._forum_topic_cache = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def _get_thread_for_command(self, command: str, chat_id: int) -> int | None:
        """Return the ``message_thread_id`` for *command* in *chat_id*.

        Returns ``None`` if:
        - Forum routing is disabled in config
        - *chat_id* is not a forum supergroup
        - Topic creation fails

        The returned value can be passed directly as ``message_thread_id`` to
        any send API call.
        """
        cfg = self._get_forum_config()
        if not cfg.get("forum_routing_enabled", False):
            return None

        if not await self._is_forum_group(chat_id):
            return None

        verb = command.lstrip("/").split()[0].lower() if command else ""
        topic_name = _COMMAND_TOPIC_MAP.get(verb)
        if not topic_name:
            return None  # goes to General

        return await self._ensure_topic(chat_id, topic_name)

    async def _is_forum_group(self, chat_id: int) -> bool:
        """Return True if *chat_id* is a supergroup with forum topics enabled."""
        self._ensure_forum_caches()
        cached = self._forum_group_cache.get(chat_id)
        if cached is not None:
            return cached

        try:
            chat_info = await self._api_call("getChat", {"chat_id": chat_id})
            is_forum = bool(
                chat_info
                and chat_info.get("type") == "supergroup"
                and chat_info.get("is_forum")
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("getChat failed for forum check (chat=%s): %s", chat_id, exc)
            is_forum = False

        self._forum_group_cache[chat_id] = is_forum
        return is_forum

    async def _ensure_topic(self, chat_id: int, topic_name: str) -> int | None:
        """Return the thread_id for *topic_name*, creating the topic if needed.

        Returns ``None`` if the topic cannot be found or created.
        """
        self._ensure_forum_caches()
        cache_key = (chat_id, topic_name)
        cached_id = self._forum_topic_cache.get(cache_key)
        if cached_id is not None:
            return cached_id

        # Try to find an existing topic by listing forum topics
        thread_id = await self._find_existing_topic(chat_id, topic_name)
        if thread_id is None:
            thread_id = await self._create_topic(chat_id, topic_name)

        if thread_id is not None:
            self._forum_topic_cache[cache_key] = thread_id

        return thread_id

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _find_existing_topic(self, chat_id: int, topic_name: str) -> int | None:
        """List forum topics and return the thread_id of a matching one."""
        try:
            result = await self._api_call(
                "getForumTopics",
                {"chat_id": chat_id, "limit": 100},
            )
            if not result:
                return None
            topics = result.get("topics") or []
            for topic in topics:
                if topic.get("name", "").strip().lower() == topic_name.lower():
                    return int(topic["message_thread_id"])
        except Exception as exc:  # noqa: BLE001
            logger.debug("getForumTopics failed for chat=%s: %s", chat_id, exc)
        return None

    async def _create_topic(self, chat_id: int, topic_name: str) -> int | None:
        """Create a new forum topic and return its thread_id."""
        payload: dict = {"chat_id": chat_id, "name": topic_name}
        emoji_id = _TOPIC_EMOJI_IDS.get(topic_name)
        if emoji_id:
            payload["icon_custom_emoji_id"] = emoji_id

        try:
            result = await self._api_call("createForumTopic", payload)
            if result and isinstance(result, dict):
                tid = result.get("message_thread_id")
                if tid:
                    logger.info(
                        "Created forum topic %r (thread_id=%s) in chat %s",
                        topic_name,
                        tid,
                        chat_id,
                    )
                    return int(tid)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "createForumTopic %r failed for chat=%s: %s", topic_name, chat_id, exc
            )
        return None

    def _invalidate_forum_cache(self, chat_id: int) -> None:
        """Purge cached data for *chat_id* (call when bot is added/removed from group)."""
        self._ensure_forum_caches()
        self._forum_group_cache.pop(chat_id, None)
        stale = [k for k in self._forum_topic_cache if k[0] == chat_id]
        for k in stale:
            del self._forum_topic_cache[k]

    def _get_forum_config(self) -> dict:
        """Return forum routing config (best-effort)."""
        try:
            from navig.config import get_config_manager

            cm = get_config_manager()
            tg = cm.get("telegram") or {}
            return {"forum_routing_enabled": tg.get("forum_routing_enabled", False)}
        except Exception:  # noqa: BLE001
            return {"forum_routing_enabled": False}
