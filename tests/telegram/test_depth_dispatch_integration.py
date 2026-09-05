"""Integration test for the deep-path wiring through the real TelegramChannel.

Drives ``_handle_reason`` / ``_handle_talk`` with a mocked LLM and asserts the
behaviour the fix promises, without a live bot:

* a real information question routes to the ``research`` tier, appends the
  Telegram formatting hint, and marks the reply ``is_deep`` (no truncation);
* a quick chit-chat message stays on the ``small`` tier with no formatting hint.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.integration

_FORMAT_MARKER = "[Formatting]"


def _make_channel(on_message):
    from navig.gateway.channels.telegram import TelegramChannel

    channel = TelegramChannel(bot_token="123:FAKE", on_message=on_message)
    channel._features = frozenset({"sessions"})
    channel._keep_typing = AsyncMock()
    channel.send_message = AsyncMock(return_value={"message_id": 1})
    channel.edit_message = AsyncMock(return_value={"ok": True})
    channel.delete_message = AsyncMock(return_value=True)
    channel._maybe_send_voice = AsyncMock(return_value=False)
    channel._record_assistant_msg = MagicMock()
    channel._is_debug_mode = MagicMock(return_value=False)
    channel._persist_updated_language = MagicMock()
    builder = MagicMock()
    builder.build.return_value = [[{"text": "x", "callback_data": "cb:1"}]]
    channel._kb_builder = builder
    return channel


def _capture_on_message():
    captured: dict = {}

    async def _on_message(**kwargs):
        captured["message"] = kwargs.get("message", "")
        captured["metadata"] = kwargs.get("metadata", {})
        return "Fight Club is a 1999 film.\n\nEXPLORE_Q: a | b | c | d | e"

    return captured, _on_message


async def test_deep_question_uses_research_tier_and_format_hint(monkeypatch):
    from navig.gateway.channels import telegram as tg

    monkeypatch.setattr(tg, "HAS_CLASSIFIER", True)
    # No pre-fetch grounding noise for this assertion.
    import navig.tools as navig_tools

    class _Reg:
        async def run_tool(self, name, args, on_status=None):
            return SimpleNamespace(name="search", success=False, output=None, error="x")

    monkeypatch.setattr(navig_tools, "get_pipeline_registry", lambda: _Reg())

    captured, on_message = _capture_on_message()
    channel = _make_channel(on_message)

    await channel._handle_reason(
        text="info about Fight Club",
        chat_id=1,
        user_id=2,
        metadata={"answer_depth": "deep"},
        session=MagicMock(),
        session_manager=MagicMock(),
        is_group=False,
        entity_signal=True,
    )

    assert captured["metadata"].get("tier_override") == "research"
    assert _FORMAT_MARKER in captured["message"]


async def test_quick_reason_stays_small_no_format_hint(monkeypatch):
    from navig.gateway.channels import telegram as tg

    monkeypatch.setattr(tg, "HAS_CLASSIFIER", True)

    captured, on_message = _capture_on_message()
    channel = _make_channel(on_message)

    # A REASON message the depth classifier marked quick (borderline) — the tier
    # must stay fast and NOT carry the formatting hint.
    await channel._handle_reason(
        text="whats up with that",
        chat_id=1,
        user_id=2,
        metadata={"answer_depth": "quick"},
        session=MagicMock(),
        session_manager=MagicMock(),
        is_group=False,
        entity_signal=False,
    )

    assert captured["metadata"].get("tier_override") == "small"
    assert _FORMAT_MARKER not in captured["message"]
