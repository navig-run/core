"""
Tests for AI response Markdown normalisation and Telegram V1 rendering.

Covers:
  - TelegramChannel._normalize_md delegates to MarkdownFormatter
  - **bold** → *bold* conversion
  - ## Heading → symbol conversion
  - _send_md_with_fallback delegates to _send_html_with_fallback (HTML mode)
    - _send_md_with_fallback performs a single send (fallback is inside send_message)
  - _send_response applies _normalize_md before sending
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# _normalize_md unit tests
# ---------------------------------------------------------------------------


def test_normalize_md_converts_double_star_bold():
    from navig.gateway.channels.telegram import TelegramChannel

    result = TelegramChannel._normalize_md("**Fight Club**")
    assert "**" not in result, "double-star bold must be converted"
    assert "Fight" in result


def test_normalize_md_converts_heading_to_symbol():
    from navig.gateway.channels.telegram import TelegramChannel

    result = TelegramChannel._normalize_md("## Plot")
    assert "##" not in result, "Markdown heading marker must be removed"
    assert "Plot" in result


def test_normalize_md_preserves_code_blocks():
    from navig.gateway.channels.telegram import TelegramChannel

    src = "```python\nprint('**hello**')\n```"
    result = TelegramChannel._normalize_md(src)
    # The literal **hello** inside the code fence must survive unchanged
    assert "**hello**" in result


def test_normalize_md_fight_club_response():
    """Regression: the screenshot scenario — section headers must not show as **text**."""
    from navig.gateway.channels.telegram import TelegramChannel

    raw = (
        "**Plot**\nFight Club is about...\n\n"
        "**Themes**\n1. **Toxic masculinity**: blah\n\n"
        "**Impact**\nThe film had a lasting...\n\n"
        "**Trivia**\nBrad Pitt..."
    )
    result = TelegramChannel._normalize_md(raw)
    assert "**Plot**" not in result
    assert "**Themes**" not in result
    assert "**Impact**" not in result
    assert "**Trivia**" not in result
    # Content must survive
    assert "Plot" in result
    assert "Themes" in result


# ---------------------------------------------------------------------------
# _send_md_with_fallback tests
# ---------------------------------------------------------------------------


async def test_send_md_with_fallback_uses_markdown_parse_mode():
    from navig.gateway.channels.telegram import TelegramChannel

    ch = TelegramChannel(bot_token="123:FAKE")
    ch.send_message = AsyncMock(return_value={"message_id": 1})

    await ch._send_md_with_fallback(999, "hello *world*")

    ch.send_message.assert_awaited_once()
    _, kwargs = ch.send_message.await_args
    # _send_md_with_fallback delegates to _send_html_with_fallback (HTML mode)
    assert kwargs.get("parse_mode") == "HTML"


async def test_send_md_with_fallback_retries_plain_on_api_failure():
    """Helper performs one send call; parse fallback is owned by send_message."""
    from navig.gateway.channels.telegram import TelegramChannel

    ch = TelegramChannel(bot_token="123:FAKE")
    ch.send_message = AsyncMock(return_value=None)

    await ch._send_md_with_fallback(999, "some *broken markup")

    assert ch.send_message.await_count == 1
    _, kwargs = ch.send_message.await_args
    assert kwargs.get("parse_mode") == "HTML"


# ---------------------------------------------------------------------------
# _send_response integration — verify normalization is applied
# ---------------------------------------------------------------------------


async def test_send_response_normalizes_double_star_bold(monkeypatch):
    """_send_response must convert **bold** before it reaches send_message."""
    from navig.gateway.channels import telegram as tg
    from navig.gateway.channels.telegram import TelegramChannel

    ch = TelegramChannel(bot_token="123:FAKE")
    ch._features = frozenset()
    ch._maybe_send_voice = AsyncMock(return_value=False)

    sent_texts: list[str] = []

    async def _capture(chat_id, text, *, parse_mode=None, keyboard=None):
        sent_texts.append(text)
        return {"message_id": 1}

    ch.send_message = _capture

    monkeypatch.setattr(tg, "HAS_TEMPLATES", False)

    await ch._send_response(chat_id=1, response="**Bold Header**\nSome content here.")

    assert sent_texts, "at least one message must be sent"
    assert "**Bold Header**" not in sent_texts[0], (
        "_send_response must normalise **bold** before sending"
    )
    assert "Bold Header" in sent_texts[0]
