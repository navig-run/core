"""Tests for navig.telegram.autoreply — business "pro mode" persona auto-reply.

Owner-only activation, command deletion, counterparty auto-reply with human-like
sending, and the owner/inactive no-op guards.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import navig.telegram.autoreply as ar


def _channel():
    ch = MagicMock()
    ch._api_call = AsyncMock(return_value={"ok": True, "message_id": 1})
    ch.send_message = AsyncMock()
    return ch


# ── command parsing ──────────────────────────────────────────────────────────


def test_parse_command_forms():
    assert ar.parse_command("role tyler fr on") == {"toggle": True, "role": "tyler", "lang": "fr"}
    assert ar.parse_command("role support on")["role"] == "support"
    assert ar.parse_command("role tyler fr")["toggle"] is True  # naming implies ON
    assert ar.parse_command("role off")["toggle"] is False
    assert ar.parse_command("role")["toggle"] == "status"
    assert ar.parse_command("hello there") is None


# ── owner control ────────────────────────────────────────────────────────────


async def test_handle_command_activates_and_deletes(monkeypatch):
    state: dict = {}
    monkeypatch.setattr(ar, "_set_active",
                        lambda cid, role, lang: state.__setitem__(str(cid), {"role": role, "lang": lang}))
    ch = _channel()
    msg = {"chat": {"id": 555}, "message_id": 10, "business_connection_id": "bc1",
           "text": "role tyler fr on"}

    handled = await ar.handle_command(ch, msg, is_owner=True, owner_id=777)
    assert handled is True
    # The control message is deleted from the business chat.
    assert any(c.args[0] == "deleteBusinessMessages" for c in ch._api_call.call_args_list)
    # Confirmation goes to the OWNER privately (777), never into the business chat.
    assert ch.send_message.call_args.args[0] == 777
    assert state["555"] == {"role": "tyler", "lang": "fr"}


async def test_handle_command_non_owner_is_noop():
    ch = _channel()
    msg = {"chat": {"id": 555}, "message_id": 10, "text": "role tyler fr on"}
    assert await ar.handle_command(ch, msg, is_owner=False, owner_id=777) is False
    ch.send_message.assert_not_called()


# ── counterparty auto-reply ──────────────────────────────────────────────────


async def test_maybe_autoreply_generates_and_sends(monkeypatch):
    monkeypatch.setattr(ar.asyncio, "sleep", AsyncMock())  # skip the typing delays
    monkeypatch.setattr(ar, "get_active", lambda cid: {"role": "sales", "lang": "fr"})
    monkeypatch.setattr(ar, "_recent_context", lambda cid, oid, n=12: [("Them", "Bonjour")])

    async def fake_generate(role, lang, ctx):
        assert role == "sales" and lang == "fr"
        return "Bonjour ! Comment puis-je vous aider ?"

    monkeypatch.setattr(ar, "_generate", fake_generate)
    ch = _channel()
    msg = {"chat": {"id": 555}, "message_id": 11, "business_connection_id": "bc1", "text": "Bonjour"}

    replied = await ar.maybe_autoreply(ch, msg, is_owner=False, owner_id=777)
    assert replied is True
    # showed a typing indicator and sent AS the business account
    assert any(c.args[0] == "sendChatAction" for c in ch._api_call.call_args_list)
    sent = [c for c in ch._api_call.call_args_list if c.args[0] == "sendMessage"]
    assert sent and sent[0].args[1]["business_connection_id"] == "bc1"
    assert "Bonjour" in sent[0].args[1]["text"]


async def test_maybe_autoreply_never_replies_to_owner(monkeypatch):
    monkeypatch.setattr(ar, "get_active", lambda cid: {"role": "sales", "lang": ""})
    ch = _channel()
    msg = {"chat": {"id": 555}, "text": "note to self"}
    assert await ar.maybe_autoreply(ch, msg, is_owner=True, owner_id=777) is False


async def test_maybe_autoreply_inactive_is_noop(monkeypatch):
    monkeypatch.setattr(ar, "get_active", lambda cid: None)
    ch = _channel()
    msg = {"chat": {"id": 555}, "text": "hi"}
    assert await ar.maybe_autoreply(ch, msg, is_owner=False, owner_id=777) is False
