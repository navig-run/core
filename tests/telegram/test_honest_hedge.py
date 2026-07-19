"""Honest-hedge: when a query needs live web grounding but the search yields nothing,
the model must be told to admit the gap instead of confabulating (the "Not much on
Cybesis Studios here" failure). Covers both REASON enrichment and the ACT pipeline.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.integration

_HEDGE_MARKER = "[Grounding note]"


class _FakeSessionManager:
    def __init__(self, session):
        self._session = session

    def get_or_create_session(self, chat_id: int, user_id: int, is_group: bool = False):
        return self._session


def _make_channel(on_message):
    from navig.gateway.channels.telegram import TelegramChannel

    channel = TelegramChannel(bot_token="123:FAKE", on_message=on_message)
    channel._features = frozenset({"sessions"})
    channel._keep_typing = AsyncMock()
    channel.send_message = AsyncMock(return_value={"message_id": 1})
    channel.edit_message = AsyncMock(return_value={"ok": True})
    channel.delete_message = AsyncMock(return_value=True)
    channel.send_photo = AsyncMock(return_value={"ok": True})
    channel._maybe_send_voice = AsyncMock(return_value=False)
    channel._record_assistant_msg = MagicMock()
    channel._is_debug_mode = MagicMock(return_value=False)
    channel._persist_updated_language = MagicMock()
    channel._resolve_model_name = MagicMock(return_value="test-model")
    builder = MagicMock()
    builder.build.return_value = [[{"text": "x", "callback_data": "cb:1"}]]
    channel._kb_builder = builder
    return channel


def _patch_search(monkeypatch, *, success: bool, results=None):
    """Point the pipeline registry's `search` tool at a canned result."""
    import navig.tools as navig_tools

    class _Reg:
        async def run_tool(self, name, args, on_status=None):
            return SimpleNamespace(
                name="search",
                success=success,
                output={"results": results or []} if success else None,
                error=None if success else "blocked",
            )

    monkeypatch.setattr(navig_tools, "get_pipeline_registry", lambda: _Reg())


# ---------------------------------------------------------------------------
# REASON mode
# ---------------------------------------------------------------------------


async def test_reason_hedges_when_search_returns_nothing(monkeypatch):
    from navig.gateway.channels import telegram as tg

    captured: dict[str, str] = {}

    async def _on_message(**kwargs):
        captured["message"] = kwargs.get("message", "")
        return "Answer.\n\nEXPLORE_Q: a | b | c | d | e"

    monkeypatch.setattr(tg, "HAS_CLASSIFIER", True)
    monkeypatch.setattr(tg, "get_session_manager", lambda: _FakeSessionManager(
        SimpleNamespace(action_cards_enabled=True, voice_response_to_text="text")
    ))
    _patch_search(monkeypatch, success=False)

    channel = _make_channel(_on_message)
    await channel._handle_reason(
        text="tell me about Cybesis Studios",
        chat_id=1,
        user_id=2,
        metadata={},
        session=MagicMock(),
        session_manager=MagicMock(),
        is_group=False,
        entity_signal=True,
    )

    assert _HEDGE_MARKER in captured["message"]


async def test_reason_no_hedge_when_search_grounds(monkeypatch):
    from navig.gateway.channels import telegram as tg

    captured: dict[str, str] = {}

    async def _on_message(**kwargs):
        captured["message"] = kwargs.get("message", "")
        return "Answer.\n\nEXPLORE_Q: a | b | c | d | e"

    monkeypatch.setattr(tg, "HAS_CLASSIFIER", True)
    monkeypatch.setattr(tg, "get_session_manager", lambda: _FakeSessionManager(
        SimpleNamespace(action_cards_enabled=True, voice_response_to_text="text")
    ))
    _patch_search(
        monkeypatch,
        success=True,
        results=[{"title": "Cybesis", "snippet": "studio", "url": "https://cybesis.com"}],
    )

    channel = _make_channel(_on_message)
    await channel._handle_reason(
        text="tell me about Cybesis Studios",
        chat_id=1,
        user_id=2,
        metadata={},
        session=MagicMock(),
        session_manager=MagicMock(),
        is_group=False,
        entity_signal=True,
    )

    # Grounded → the web context is injected and the hedge is NOT.
    assert _HEDGE_MARKER not in captured["message"]
    assert "[Web context]" in captured["message"]


# ---------------------------------------------------------------------------
# ACT mode (the screenshot's exact path)
# ---------------------------------------------------------------------------


class _FakeRenderer:
    def __init__(self, *a, **k):
        pass

    async def update(self, *a, **k):
        pass

    async def warn(self, *a, **k):
        pass

    async def finalize(self, *a, **k):
        pass


async def test_act_hedges_when_search_fails(monkeypatch):
    from navig.gateway.channels import telegram as tg

    captured: dict[str, str] = {}

    async def _on_message(**kwargs):
        captured["message"] = kwargs.get("message", "")
        return "answer"

    monkeypatch.setattr(tg, "StatusRenderer", _FakeRenderer)
    monkeypatch.setattr(tg, "select_tools_for_text", lambda text: ["search"])
    monkeypatch.setattr(tg, "extract_url", lambda text: None)
    _patch_search(monkeypatch, success=False)

    channel = _make_channel(_on_message)
    await channel._handle_act(
        text="find info about Cybesis Studios",
        chat_id=1,
        user_id=2,
        metadata={},
        session=MagicMock(),
        session_manager=MagicMock(),
        is_group=False,
    )

    assert _HEDGE_MARKER in captured["message"]


async def test_act_no_hedge_when_search_grounds(monkeypatch):
    from navig.gateway.channels import telegram as tg

    captured: dict[str, str] = {}

    async def _on_message(**kwargs):
        captured["message"] = kwargs.get("message", "")
        return "answer"

    monkeypatch.setattr(tg, "StatusRenderer", _FakeRenderer)
    monkeypatch.setattr(tg, "select_tools_for_text", lambda text: ["search"])
    monkeypatch.setattr(tg, "extract_url", lambda text: None)
    _patch_search(
        monkeypatch,
        success=True,
        results=[{"title": "Cybesis", "snippet": "studio", "url": "https://cybesis.com"}],
    )

    channel = _make_channel(_on_message)
    await channel._handle_act(
        text="find info about Cybesis Studios",
        chat_id=1,
        user_id=2,
        metadata={},
        session=MagicMock(),
        session_manager=MagicMock(),
        is_group=False,
    )

    assert _HEDGE_MARKER not in captured["message"]


async def test_reason_attaches_read_site_buttons(monkeypatch):
    """REASON's silent enrichment search now also yields '🔎 Read' buttons, passed
    to the streaming reply via extra_krow."""
    from navig.gateway.channels import telegram as tg

    captured: dict = {}

    async def _fake_stream_reply(chat_id, user_id, message, metadata, **kw):
        captured["extra_krow"] = kw.get("extra_krow")
        captured["message"] = message
        return "Answer."

    monkeypatch.setattr(tg, "HAS_CLASSIFIER", True)
    monkeypatch.setattr(tg, "get_session_manager", lambda: _FakeSessionManager(
        SimpleNamespace(action_cards_enabled=True, voice_response_to_text="text")
    ))
    _patch_search(
        monkeypatch,
        success=True,
        results=[
            {"title": "Cybesis", "snippet": "studio", "url": "https://cybesis.com/about"},
            {"title": "Wiki", "snippet": "", "url": "https://en.wikipedia.org/wiki/Cybesis"},
        ],
    )

    async def _unused_on_message(**kwargs):
        return None

    channel = _make_channel(_unused_on_message)
    channel._stream_reply = _fake_stream_reply

    await channel._handle_reason(
        text="tell me about Cybesis Studios",
        chat_id=1,
        user_id=2,
        metadata={},
        session=MagicMock(),
        session_manager=MagicMock(),
        is_group=False,
        entity_signal=True,
    )

    krow = captured.get("extra_krow") or []
    assert any(str(b.get("callback_data", "")).startswith("wf:") for b in krow)
    # And the model is told not to offer to fetch the site itself.
    assert "Do NOT offer to fetch" in captured.get("message", "")


async def test_act_attaches_read_site_button_for_top_result(monkeypatch):
    """When ACT search grounds with a URL, a '🔎 Read <site>' button is attached and
    the model is told not to offer to fetch the site itself."""
    from navig.gateway.channels import telegram as tg

    captured: dict = {}

    async def _on_message(**kwargs):
        captured["message"] = kwargs.get("message", "")
        return "Cybesis is a studio."

    monkeypatch.setattr(tg, "StatusRenderer", _FakeRenderer)
    monkeypatch.setattr(tg, "select_tools_for_text", lambda text: ["search"])
    monkeypatch.setattr(tg, "extract_url", lambda text: None)
    _patch_search(
        monkeypatch,
        success=True,
        results=[{"title": "Cybesis", "snippet": "studio", "url": "https://cybesis.com/about"}],
    )

    channel = _make_channel(_on_message)
    channel._send_html_with_fallback = AsyncMock(return_value={"message_id": 1})

    await channel._handle_act(
        text="find info about Cybesis Studios",
        chat_id=1,
        user_id=2,
        metadata={},
        session=MagicMock(),
        session_manager=MagicMock(),
        is_group=False,
    )

    # Prompt-guard note tells the model not to offer to fetch/dig-in itself.
    assert "Do NOT offer to fetch" in captured["message"]
    # The read-site button (wf:) reached the sent keyboard.
    kw = channel._send_html_with_fallback.await_args.kwargs
    keyboard = kw.get("keyboard") or {}
    rows = keyboard.get("inline_keyboard", []) if isinstance(keyboard, dict) else keyboard
    flat = [b for row in rows for b in row]
    assert any(str(b.get("callback_data", "")).startswith("wf:") for b in flat)


async def test_act_explore_q_becomes_buttons_not_text(monkeypatch):
    """An EXPLORE_Q: line the model appends in ACT mode must be stripped from the
    visible text and handed to the keyboard builder as explore buttons — the old
    direct send_message leaked it as raw text with no buttons."""
    from navig.gateway.channels import telegram as tg

    async def _on_message(**kwargs):
        return (
            "Here's what I found.\n\n"
            "EXPLORE_Q: What games do they make | Who founded it | "
            "Explore their website | Compare to other studios | Latest news"
        )

    monkeypatch.setattr(tg, "StatusRenderer", _FakeRenderer)
    monkeypatch.setattr(tg, "select_tools_for_text", lambda text: ["search"])
    monkeypatch.setattr(tg, "extract_url", lambda text: None)
    _patch_search(
        monkeypatch,
        success=True,
        results=[{"title": "Cybesis", "snippet": "studio", "url": "https://cybesis.com"}],
    )

    channel = _make_channel(_on_message)
    channel._send_html_with_fallback = AsyncMock(return_value={"message_id": 1})

    await channel._handle_act(
        text="find info about Cybesis Studios",
        chat_id=1,
        user_id=2,
        metadata={},
        session=MagicMock(),
        session_manager=MagicMock(),
        is_group=False,
    )

    # The keyboard builder received the cleaned text + extracted explore questions.
    build_kwargs = channel._kb_builder.build.call_args.kwargs
    assert "EXPLORE_Q" not in build_kwargs["ai_response"]
    assert build_kwargs["explore_questions"], "explore questions must be extracted for buttons"
    assert any("games" in q.lower() for q in build_kwargs["explore_questions"])
    # And whatever text was actually sent carries no raw marker.
    sent_text = channel._send_html_with_fallback.await_args.args[1]
    assert "EXPLORE_Q" not in sent_text
