"""Tests for Telegram Forum topic auto-router."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.gateway.channels.telegram_forum import (
    TelegramForumMixin,
    _COMMAND_TOPIC_MAP,
)


# ---------------------------------------------------------------------------
# Helpers — real subclass so internal helpers call through to real impl
# ---------------------------------------------------------------------------


class _FakeForumChannel(TelegramForumMixin):
    """Minimal fake channel: mocks only _api_call (the infrastructure boundary)."""

    def __init__(self):
        self._api_call = AsyncMock(return_value=None)
        # Cache dicts normally init via _ensure_forum_caches(); pre-create here
        self._forum_group_cache: dict = {}
        self._forum_topic_cache: dict = {}
        # Default config — override per test
        self._get_forum_config = MagicMock(return_value={"forum_routing_enabled": True})


def _make_channel():
    """Legacy helper kept for tests that only need basic caches (non-async tests)."""
    ch = _FakeForumChannel()
    return ch


# ---------------------------------------------------------------------------
# Category routing table
# ---------------------------------------------------------------------------


def test_command_topic_map_known_entries():
    """Key routing entries must be present and map to expected topic names."""
    assert _COMMAND_TOPIC_MAP["briefing"] == "Status & Briefing"
    assert _COMMAND_TOPIC_MAP["db"] == "Database"
    assert _COMMAND_TOPIC_MAP["run"] == "Commands"
    assert _COMMAND_TOPIC_MAP["docker"] == "Docker"
    assert _COMMAND_TOPIC_MAP["ask"] == "AI Responses"


def test_all_topic_names_are_strings():
    for cmd, topic in _COMMAND_TOPIC_MAP.items():
        assert isinstance(topic, str) and topic, f"Empty topic for command {cmd!r}"


# ---------------------------------------------------------------------------
# _get_thread_for_command when forum routing is disabled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_none_when_routing_disabled():
    ch = _make_channel()
    ch._get_forum_config = MagicMock(return_value={"forum_routing_enabled": False})
    result = await TelegramForumMixin._get_thread_for_command(ch, "/briefing", -100)
    assert result is None


# ---------------------------------------------------------------------------
# _is_forum_group
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_forum_group_returns_false():
    ch = _make_channel()
    ch._api_call = AsyncMock(
        return_value={"type": "supergroup", "is_forum": False, "id": -100}
    )
    result = await TelegramForumMixin._is_forum_group(ch, -100)
    assert result is False


@pytest.mark.asyncio
async def test_forum_supergroup_returns_true():
    ch = _make_channel()
    ch._api_call = AsyncMock(
        return_value={"type": "supergroup", "is_forum": True, "id": -200}
    )
    result = await TelegramForumMixin._is_forum_group(ch, -200)
    assert result is True


@pytest.mark.asyncio
async def test_regular_group_returns_false():
    ch = _make_channel()
    ch._api_call = AsyncMock(
        return_value={"type": "group", "id": -300}
    )
    result = await TelegramForumMixin._is_forum_group(ch, -300)
    assert result is False


@pytest.mark.asyncio
async def test_is_forum_result_is_cached():
    ch = _make_channel()
    ch._api_call = AsyncMock(return_value={"type": "supergroup", "is_forum": True})
    await TelegramForumMixin._is_forum_group(ch, -400)
    await TelegramForumMixin._is_forum_group(ch, -400)
    # getChat should only be called once
    assert ch._api_call.await_count == 1


# ---------------------------------------------------------------------------
# _ensure_topic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_topic_creates_when_not_found():
    ch = _make_channel()
    # _find_existing_topic: getForumTopics returns empty list
    # _create_topic: createForumTopic returns new thread
    ch._api_call = AsyncMock(side_effect=[
        {"topics": []},                           # getForumTopics
        {"message_thread_id": 7, "name": "DB"},   # createForumTopic
    ])
    tid = await TelegramForumMixin._ensure_topic(ch, -100, "Database")
    assert tid == 7


@pytest.mark.asyncio
async def test_ensure_topic_reuses_existing():
    ch = _make_channel()
    ch._api_call = AsyncMock(return_value={
        "topics": [{"name": "Database", "message_thread_id": 5}]
    })
    tid = await TelegramForumMixin._ensure_topic(ch, -100, "Database")
    assert tid == 5


@pytest.mark.asyncio
async def test_ensure_topic_cached_on_second_call():
    ch = _make_channel()
    ch._api_call = AsyncMock(return_value={
        "topics": [{"name": "Commands", "message_thread_id": 9}]
    })
    await TelegramForumMixin._ensure_topic(ch, -100, "Commands")
    await TelegramForumMixin._ensure_topic(ch, -100, "Commands")
    # getForumTopics called only once (second call hits cache)
    assert ch._api_call.await_count == 1


# ---------------------------------------------------------------------------
# Cache invalidation
# ---------------------------------------------------------------------------


def test_invalidate_forum_cache():
    ch = _make_channel()
    ch._forum_group_cache[-100] = True
    ch._forum_topic_cache[(-100, "Database")] = 5
    ch._forum_topic_cache[(-100, "Commands")] = 9
    ch._forum_topic_cache[(-200, "Docker")] = 12  # different chat, should survive

    TelegramForumMixin._invalidate_forum_cache(ch, -100)

    assert -100 not in ch._forum_group_cache
    assert (-100, "Database") not in ch._forum_topic_cache
    assert (-100, "Commands") not in ch._forum_topic_cache
    assert (-200, "Docker") in ch._forum_topic_cache


# ---------------------------------------------------------------------------
# Unknown command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_command_returns_none_thread():
    ch = _make_channel()
    ch._get_forum_config = MagicMock(return_value={"forum_routing_enabled": True})
    # Simulate forum group check returning True
    ch._is_forum_group = AsyncMock(return_value=True)
    result = await TelegramForumMixin._get_thread_for_command(
        ch, "/unknowncmd", -100
    )
    assert result is None
