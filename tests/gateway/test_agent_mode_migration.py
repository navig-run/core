"""Agent-mode migration — cross-surface contract tests (owner decision 2026-10-20).

The retired focus modes must be gone from every active surface while the stored
values keep working, the mode must actually reach the agent runtime, and the two
modes must never differ in anything but behaviour gates:

* fresh install defaults to ``standard``;
* ``/mode`` (Telegram) switches, lists only the two modes, and answers retired
  requests with a "retired" notice — never the obsolete choices;
* the mode syncs across the deck routes (mode → settings → status → bot payload);
* session metadata (per-chat) diverges from the global preference on purpose;
* the live prompt builder shows the mode for any provider (it runs before the
  provider is chosen).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

pytest.importorskip("aiohttp")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


def _app() -> web.Application:
    app = web.Application()

    from navig.gateway.deck.routes.core import (
        handle_deck_mode,
        handle_deck_settings_get,
        handle_deck_status,
    )

    app.router.add_post("/api/deck/mode", handle_deck_mode)
    app.router.add_get("/api/deck/settings", handle_deck_settings_get)
    app.router.add_get("/api/deck/status", handle_deck_status)
    return app


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """Fresh config/data dirs, a fresh tracker singleton and a fresh session store."""
    from navig.agent.proactive import user_state
    from navig.config import reset_config_manager
    from navig.core.shared_config import ConfigSingleton
    from navig.gateway.channels import telegram_sessions

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    reset_config_manager()
    ConfigSingleton._instance = None
    # Force a fresh singleton built under the isolated config dir; monkeypatch
    # restores whatever instance (or None) the worker had afterwards.
    monkeypatch.setattr(user_state, "_tracker_instance", None)
    sm = telegram_sessions.SessionManager(storage_dir=tmp_path / "sessions")
    monkeypatch.setattr(telegram_sessions, "_session_manager", sm)
    yield sm
    reset_config_manager()
    ConfigSingleton._instance = None


def _tracker():
    from navig.agent.proactive.user_state import get_user_state_tracker

    return get_user_state_tracker()


async def _post(client, url, payload):
    resp = await client.post(url, json=payload)
    body = await resp.json()
    return resp.status, body


async def _get(client, url):
    resp = await client.get(url)
    body = await resp.json()
    return resp.status, body


# ── defaults ─────────────────────────────────────────────────────────────────


async def test_fresh_install_defaults_to_standard(isolated):
    from navig.agent.proactive.user_state import UserPreferences
    from navig.gateway.channels.focus_modes import DEFAULT_MODE

    assert UserPreferences().chat_mode == "standard"
    assert DEFAULT_MODE == "standard"
    async with TestClient(TestServer(_app())) as client:
        status, body = await _get(client, "/api/deck/settings")
        assert status == 200
        assert body["chat_mode"] == "standard"
        status, body = await _get(client, "/api/deck/status")
        assert status == 200
        assert body["current_mode"] == "standard"


# ── cross-surface sync ───────────────────────────────────────────────────────


async def test_mode_syncs_across_surfaces(isolated):
    from navig.gateway.deck.routes.telegram_bot import _focus_payload

    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(client, "/api/deck/mode", {"mode": "creative"})
        assert status == 200 and body == {"mode": "creative"}

        # The same preference every other surface reads — one source of truth.
        status, settings = await _get(client, "/api/deck/settings")
        assert settings["chat_mode"] == "creative"
        status, status_body = await _get(client, "/api/deck/status")
        assert status_body["current_mode"] == "creative"
        assert status_body["avatar_state"] == "learning"  # creative → learning

    payload = _focus_payload()
    assert payload["current"] == "creative"
    assert [m["id"] for m in payload["modes"]] == ["standard", "creative"]


async def test_standard_maps_to_focused_avatar(isolated):
    async with TestClient(TestServer(_app())) as client:
        await _post(client, "/api/deck/mode", {"mode": "standard"})
        _, body = await _get(client, "/api/deck/status")
    assert body["avatar_state"] == "focused"


# ── Telegram /mode ───────────────────────────────────────────────────────────


async def test_telegram_mode_switches_and_lists_only_two(isolated):
    from unittest.mock import AsyncMock

    from navig.gateway.channels.telegram import TelegramChannel

    ch = TelegramChannel(bot_token="123:TEST", allowed_users=[4242])
    ch.send_message = AsyncMock()

    await ch._handle_mode(4242, "")  # list view
    text = "".join(str(c.args[1]) for c in ch.send_message.call_args_list)
    assert "Agent Modes" in text
    assert "deep-focus" not in text and "relax" not in text  # obsolete choices gone
    assert "standard" in text and "creative" in text

    ch.send_message.reset_mock()
    await ch._handle_mode(4242, "creative")
    text = str(ch.send_message.call_args.args[1])
    assert "Creative" in text
    assert _tracker().get_preference("chat_mode") == "creative"
    # Session metadata written under the new key.
    sm = isolated
    assert sm.get_session_metadata(4242, 4242, "agent_mode") == "creative"


async def test_telegram_legacy_request_gets_retired_notice(isolated):
    from unittest.mock import AsyncMock

    from navig.gateway.channels.telegram import TelegramChannel

    ch = TelegramChannel(bot_token="123:TEST", allowed_users=[4242])
    ch.send_message = AsyncMock()

    await ch._handle_mode(4242, "deep focus")
    text = str(ch.send_message.call_args.args[1])
    assert "retired" in text
    assert "Standard and Creative" in text
    # Behaves as Standard, and the obsolete id never appears as an offer.
    assert _tracker().get_preference("chat_mode") == "standard"


async def test_telegram_unknown_mode_lists_the_two(isolated):
    from unittest.mock import AsyncMock

    from navig.gateway.channels.telegram import TelegramChannel

    ch = TelegramChannel(bot_token="123:TEST", allowed_users=[4242])
    ch.send_message = AsyncMock()
    await ch._handle_mode(4242, "banana")
    text = str(ch.send_message.call_args.args[1])
    assert "Unknown mode" in text
    assert "standard" in text and "creative" in text
    assert "sleep" not in text


# ── session vs global ────────────────────────────────────────────────────────


def test_session_metadata_is_scoped_and_falls_back(isolated):
    """Per-chat session mode (display) diverges from the global preference on
    purpose; readers fall back agent_mode → focus_mode → global."""
    sm = isolated
    tracker = _tracker()
    assert tracker.get_preference("chat_mode") == "standard"

    sm.set_session_metadata(1, 1, "agent_mode", "creative")
    assert sm.get_session_metadata(1, 1, "agent_mode") == "creative"
    # Other chat untouched; global untouched.
    assert sm.get_session_metadata(2, 2, "agent_mode") is None
    assert tracker.get_preference("chat_mode") == "standard"

    # Legacy fallback: an old session only has focus_mode.
    sm.set_session_metadata(3, 3, "focus_mode", "work")
    mode = sm.get_session_metadata(3, 3, "agent_mode") or sm.get_session_metadata(
        3, 3, "focus_mode"
    )
    assert mode == "work"  # raw fallback; surface validation coerces to standard


# ── the mode reaches the live runtime ────────────────────────────────────────


def test_prompt_context_carries_the_agent_mode(isolated):
    from unittest.mock import MagicMock

    from navig.agent.conv.agent import ConversationalAgent

    def _make_agent():
        history = MagicMock()
        history.get_messages.return_value = []
        history.messages.return_value = []
        return ConversationalAgent(soul_content="test soul", history=history)

    agent = _make_agent()
    ctx = agent._build_session_context()
    assert "Current agent mode: standard" in ctx
    assert "No proactive suggestions" in ctx  # Standard's behavioural contract

    assert _tracker().set_preference("chat_mode", "creative") is True
    ctx = agent._build_session_context()
    assert "Current agent mode: creative" in ctx
    assert "proactive capability intelligence is ON" in ctx
    # One source of truth: what the prompt says is what the gates read.
    from navig.creative.mode import get_agent_mode

    assert get_agent_mode() == "creative"
