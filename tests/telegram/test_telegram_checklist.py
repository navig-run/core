"""Tests for Telegram native checklist detection and formatting."""

import pytest

from navig.gateway.channels.telegram_checklist import (
    _CHECKLIST_MIN_ITEMS,
    _CHECKLIST_MIN_LIST_RATIO,
    TelegramChecklistMixin,
    extract_task_list,
    should_send_as_checklist,
)


# ---------------------------------------------------------------------------
# extract_task_list
# ---------------------------------------------------------------------------


def test_returns_none_for_empty_string():
    assert extract_task_list("") is None


def test_returns_none_for_prose():
    prose = (
        "This is a normal paragraph of text. "
        "It has no list items whatsoever. "
        "Nothing should trigger checklist mode here."
    )
    assert extract_task_list(prose) is None


def test_unordered_bullets():
    text = "- item one\n- item two\n- item three\n- item four"
    tasks = extract_task_list(text)
    assert tasks is not None
    assert len(tasks) == 4
    assert "item one" in tasks


def test_ordered_numbers():
    text = "1. First task\n2. Second task\n3. Third task"
    tasks = extract_task_list(text)
    assert tasks is not None
    assert len(tasks) == 3


def test_plus_bullets():
    text = "+ Alpha\n+ Beta\n+ Gamma\n+ Delta"
    assert extract_task_list(text) is not None


def test_returns_none_when_too_few_items():
    text = "- only one\n- two"
    assert extract_task_list(text) is None  # 2 < _CHECKLIST_MIN_ITEMS


def test_returns_none_when_ratio_too_low():
    # 30 % list items, 70 % prose — should NOT trigger
    text = "\n".join(
        ["Long prose line that is definitely not a list item."] * 7 + ["- task a", "- task b", "- task c"]
    )
    result = extract_task_list(text)
    assert result is None


def test_triggers_when_majority_are_list_items():
    lines = ["Some prose header."] + [f"- task {i}" for i in range(4)]
    text = "\n".join(lines)
    # 4 list / 5 total = 80 % ≥ 50 %
    tasks = extract_task_list(text)
    assert tasks is not None
    assert len(tasks) == 4


def test_bullet_stripped_from_result():
    tasks = extract_task_list("- alpha\n- beta\n- gamma")
    assert all(not t.startswith("-") for t in tasks)


def test_asterisk_bullet_stripped():
    tasks = extract_task_list("* x\n* y\n* z")
    assert tasks == ["x", "y", "z"]


def test_existing_checkbox_stripped():
    text = "☐ Task 1\n☐ Task 2\n☐ Task 3"
    tasks = extract_task_list(text)
    assert tasks is not None
    assert all("☐" not in t for t in tasks)


def test_long_task_capped():
    long_item = "x" * 200
    tasks = extract_task_list(f"- {long_item}\n- short\n- another")
    from navig.gateway.channels.telegram_checklist import _CHECKLIST_MAX_TASK_LEN
    assert len(tasks[0]) <= _CHECKLIST_MAX_TASK_LEN


# ---------------------------------------------------------------------------
# should_send_as_checklist
# ---------------------------------------------------------------------------


def test_should_send_for_valid_list():
    assert should_send_as_checklist("- a\n- b\n- c\n- d") is True


def test_should_not_send_for_prose():
    assert should_send_as_checklist("Hello world, this is prose.") is False


def test_should_not_send_for_empty():
    assert should_send_as_checklist("") is False


# ---------------------------------------------------------------------------
# TelegramChecklistMixin._send_smart_reply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_smart_reply_passthrough_for_prose():
    """Prose text is passed straight to send_message."""
    from unittest.mock import AsyncMock, MagicMock, patch

    ch = MagicMock()
    ch.send_message = AsyncMock(return_value={"message_id": 1})

    with patch.object(
        TelegramChecklistMixin,
        "_get_checklist_config",
        return_value={"checklist_enabled": True},
    ):
        await TelegramChecklistMixin._send_smart_reply(
            ch, chat_id=1, text="Just ordinary text."
        )

    ch.send_message.assert_awaited_once()
    args, kwargs = ch.send_message.call_args
    assert "Just ordinary text." in args or "Just ordinary text." == kwargs.get("text", "")


@pytest.mark.asyncio
async def test_send_smart_reply_attempts_checklist_api():
    """A valid task list should call _try_send_checklist first."""
    from unittest.mock import AsyncMock, MagicMock

    ch = MagicMock()
    ch.send_message = AsyncMock(return_value={"message_id": 1})
    # Set mixin methods directly on the instance so self.method() works correctly
    mock_cl = AsyncMock(return_value={"message_id": 5})
    ch._try_send_checklist = mock_cl
    ch._get_checklist_config = MagicMock(return_value={"checklist_enabled": True})

    task_text = "- Task alpha\n- Task beta\n- Task gamma\n- Task delta"
    result = await TelegramChecklistMixin._send_smart_reply(ch, chat_id=1, text=task_text)

    mock_cl.assert_awaited_once()
    # send_message should NOT be called when native checklist succeeds
    ch.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_smart_reply_falls_back_to_html_on_api_failure():
    """When _try_send_checklist returns None, send_message is called."""
    from unittest.mock import AsyncMock, MagicMock

    ch = MagicMock()
    ch.send_message = AsyncMock(return_value={"message_id": 1})
    ch._get_checklist_config = MagicMock(return_value={"checklist_enabled": True})
    ch._try_send_checklist = AsyncMock(return_value=None)  # API not available

    task_text = "- Task x\n- Task y\n- Task z\n- Task w"
    await TelegramChecklistMixin._send_smart_reply(ch, chat_id=1, text=task_text)

    # Fallback should use HTML send_message
    ch.send_message.assert_awaited_once()
    _, kwargs = ch.send_message.call_args
    assert kwargs.get("parse_mode") == "HTML"


@pytest.mark.asyncio
async def test_send_smart_reply_checklist_disabled():
    """When checklist_enabled=False, send_message is always called directly."""
    from unittest.mock import AsyncMock, MagicMock

    ch = MagicMock()
    ch.send_message = AsyncMock(return_value={"message_id": 1})
    mock_cl = AsyncMock()
    ch._try_send_checklist = mock_cl
    ch._get_checklist_config = MagicMock(return_value={"checklist_enabled": False})

    await TelegramChecklistMixin._send_smart_reply(
        ch, chat_id=1, text="- a\n- b\n- c\n- d"
    )

    mock_cl.assert_not_awaited()
    ch.send_message.assert_awaited_once()
