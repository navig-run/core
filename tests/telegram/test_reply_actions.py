"""Tests for reply-to-message keyword actions (navig.telegram.reply_actions).

The keyword trigger that replaced emoji reactions: reply to a message with a bare
keyword → run the action on the replied-to message. Bot chats reply in-chat;
business chats run a sandboxed subset and DM the owner privately.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from navig.telegram import reply_actions as ra

# ── parser ───────────────────────────────────────────────────────────────────


def test_resolve_matches_keywords_and_aliases():
    assert ra.resolve("summarize") == "summarize"
    assert ra.resolve("/tldr") == "summarize"
    assert ra.resolve("Translate.") == "translate"
    assert ra.resolve("eli5") == "explain"
    assert ra.resolve("ctx") == "context"
    assert ra.resolve("bookmark") == "save"
    assert ra.resolve("pin") == "pin"
    assert ra.resolve("refine") == "refine"
    assert ra.resolve("analyse") == "tiktok"


def test_resolve_is_strict_and_excludes_system_tools():
    assert ra.resolve("summarize this for the board") is None  # not hijacked
    assert ra.resolve("thanks!") is None
    assert ra.resolve("") is None
    assert ra.resolve("download") is None  # system tools are not keyword-triggerable


def test_parse_extracts_translate_argument():
    assert ra.parse("translate fr") == ("translate", "fr")
    assert ra.parse("tr es") == ("translate", "es")
    assert ra.parse("translate") == ("translate", "")
    # only arg-accepting actions take a trailing word; others stay strict
    assert ra.parse("summarize this for the board") == (None, "")


async def test_translate_argument_threads_to_run_text_action(monkeypatch):
    ch = _channel()
    captured = {}

    async def fake_run(tool, content, *, is_owner, arg=""):
        captured["arg"] = arg
        return {"ok": True, "tool": tool, "result": "Hola"}

    monkeypatch.setattr("navig.telegram.ai_actions.run_text_action", fake_run)
    handled = await ra.run_bot_reply(
        ch, action="translate", chat_id=555, user_id=777,
        reply_to_msg=_reply("Hello"), reply_to_message_id=9, is_group=False, arg="es",
    )
    assert handled is True and captured["arg"] == "es"


# ── helpers / fixtures ───────────────────────────────────────────────────────


def _channel(owner: int = 777):
    ch = MagicMock()
    ch.allowed_users = {owner}
    ch.send_message = AsyncMock(return_value={"message_id": 1})
    ch.send_rich_message = AsyncMock(return_value={"message_id": 2})
    ch._api_call = AsyncMock(return_value={"ok": True})
    ch._keep_typing = AsyncMock()
    ch._send_response = AsyncMock()
    ch.on_message = AsyncMock(return_value="a refined, deeper answer")
    return ch


def _reply(text="original text", mid=9):
    return {"message_id": mid, "text": text}


# ── run_bot_reply ────────────────────────────────────────────────────────────


async def test_bot_llm_action_replies_in_chat(monkeypatch):
    ch = _channel()

    async def fake_run(tool, content, *, is_owner, arg=""):
        return {"ok": True, "tool": tool, "result": f"[{tool}] {content}"}

    monkeypatch.setattr("navig.telegram.ai_actions.run_text_action", fake_run)
    handled = await ra.run_bot_reply(
        ch, action="summarize", chat_id=555, user_id=777,
        reply_to_msg=_reply(), reply_to_message_id=9, is_group=False,
    )
    assert handled is True
    ch.send_rich_message.assert_awaited()  # rich reply in-chat
    args, kwargs = ch.send_rich_message.call_args
    assert kwargs.get("reply_to_message_id") == 9


async def test_bot_save_action(monkeypatch):
    ch = _channel()
    monkeypatch.setattr(ra, "_save_to_wiki", lambda chat_id, text: True)
    handled = await ra.run_bot_reply(
        ch, action="save", chat_id=555, user_id=777,
        reply_to_msg=_reply(), reply_to_message_id=9, is_group=False,
    )
    assert handled is True
    assert "wiki" in ch.send_message.call_args.args[1].lower()


async def test_bot_pin_action_calls_api():
    ch = _channel()
    handled = await ra.run_bot_reply(
        ch, action="pin", chat_id=555, user_id=777,
        reply_to_msg=_reply(), reply_to_message_id=9, is_group=True,
    )
    assert handled is True
    pin_calls = [c for c in ch._api_call.call_args_list if c.args and c.args[0] == "pinChatMessage"]
    assert pin_calls


async def test_bot_non_owner_cannot_run_local_action(monkeypatch):
    ch = _channel(owner=777)
    monkeypatch.setattr(ra, "_save_to_wiki", lambda *a: True)
    # user 999 is not in allowed_users → save (owner-only) is refused.
    handled = await ra.run_bot_reply(
        ch, action="save", chat_id=555, user_id=999,
        reply_to_msg=_reply(), reply_to_message_id=9, is_group=True,
    )
    assert handled is False
    ch.send_message.assert_not_called()


async def test_bot_not_permitted_falls_through(monkeypatch):
    # A genuinely disallowed action falls through to normal dispatch.
    ch = _channel()
    monkeypatch.setattr(
        "navig.telegram.ai_actions.run_text_action",
        AsyncMock(return_value={"ok": False, "reason": "not_permitted"}),
    )
    handled = await ra.run_bot_reply(
        ch, action="translate", chat_id=555, user_id=777,
        reply_to_msg=_reply(), reply_to_message_id=9, is_group=False,
    )
    assert handled is False
    ch.send_message.assert_not_called()


async def test_new_writing_action_routes_through_llm(monkeypatch):
    # An AHK-derived action (e.g. 'improve') is a sandboxed LLM op and replies in-chat.
    ch = _channel()
    assert ra.resolve("improve") == "improve" and "improve" in ra.LLM_ACTIONS

    async def fake_run(tool, content, *, is_owner, arg=""):
        return {"ok": True, "tool": tool, "result": f"[{tool}] {content}"}

    monkeypatch.setattr("navig.telegram.ai_actions.run_text_action", fake_run)
    handled = await ra.run_bot_reply(
        ch, action="improve", chat_id=555, user_id=777,
        reply_to_msg=_reply(), reply_to_message_id=9, is_group=False,
    )
    assert handled is True
    ch.send_rich_message.assert_awaited()


async def test_empty_target_owns_message_not_fallthrough(monkeypatch):
    # A resolved keyword on a text-less message must NOT leak to the chat agent.
    ch = _channel()
    monkeypatch.setattr("navig.telegram.ai_actions.run_text_action", AsyncMock())
    handled = await ra.run_bot_reply(
        ch, action="summarize", chat_id=555, user_id=777,
        reply_to_msg={"message_id": 9},  # no text/caption
        reply_to_message_id=9, is_group=False,
    )
    assert handled is True  # owned, not fallthrough
    assert "couldn't read" in ch.send_message.call_args.args[1].lower()


async def test_output_chaining_recovers_rich_reply_text(monkeypatch):
    # Chaining: a rich AI reply returns empty text, but remember_output lets a
    # follow-up keyword re-read it. Simulate the bot having sent output id=42.
    ra.remember_output(42, "the translated paragraph")
    captured = {}

    async def fake_run(tool, content, *, is_owner, arg=""):
        captured["content"] = content
        return {"ok": True, "tool": tool, "result": "SUMMARY"}

    monkeypatch.setattr("navig.telegram.ai_actions.run_text_action", fake_run)
    ch = _channel()
    handled = await ra.run_bot_reply(
        ch, action="summarize", chat_id=555, user_id=777,
        reply_to_msg={"message_id": 42},  # empty text → falls back to cache
        reply_to_message_id=42, is_group=False,
    )
    assert handled is True
    assert captured["content"] == "the translated paragraph"


async def test_bot_llm_error_owns_message_with_notice(monkeypatch):
    # An LLM error must NOT leak the bare keyword to the chat agent — the action
    # owns the message and reports a clear error instead.
    ch = _channel()
    monkeypatch.setattr(
        "navig.telegram.ai_actions.run_text_action",
        AsyncMock(return_value={"ok": False, "reason": "llm_error"}),
    )
    handled = await ra.run_bot_reply(
        ch, action="translate", chat_id=555, user_id=777,
        reply_to_msg=_reply(), reply_to_message_id=9, is_group=False,
    )
    assert handled is True
    assert "couldn't" in ch.send_message.call_args.args[1].lower()


# ── run_business_reply ───────────────────────────────────────────────────────


async def test_business_llm_dms_owner_privately(monkeypatch):
    ch = _channel()

    async def fake_run(tool, content, *, is_owner, arg=""):
        assert is_owner is True
        return {"ok": True, "tool": tool, "result": f"[{tool}] {content}"}

    monkeypatch.setattr("navig.telegram.ai_actions.run_text_action", fake_run)
    msg = {
        "chat": {"id": 555}, "message_id": 10, "business_connection_id": "bc1",
        "text": "summarize", "reply_to_message": _reply("a long original message"),
    }
    handled = await ra.run_business_reply(ch, msg, is_owner=True, owner_id=777)
    assert handled is True
    # DM to the OWNER (777) as a rich message, never into the business chat (555).
    ch.send_rich_message.assert_awaited()
    assert ch.send_rich_message.call_args.args[0] == 777
    assert "**" in ch.send_rich_message.call_args.kwargs["markdown"]  # bold label
    # keyword message best-effort deleted from the business chat.
    assert any(c.args and c.args[0] == "deleteBusinessMessages" for c in ch._api_call.call_args_list)


async def test_business_refine_not_allowed():
    # refine is bot-chat only — not in BUSINESS_ACTIONS.
    ch = _channel()
    msg = {"chat": {"id": 555}, "message_id": 10, "text": "refine",
           "reply_to_message": _reply()}
    assert await ra.run_business_reply(ch, msg, is_owner=True, owner_id=777) is False


async def test_business_non_owner_and_non_reply_noop(monkeypatch):
    ch = _channel()
    monkeypatch.setattr("navig.telegram.ai_actions.run_text_action", AsyncMock())
    # non-owner
    msg1 = {"chat": {"id": 555}, "message_id": 10, "text": "summarize",
            "reply_to_message": _reply()}
    assert await ra.run_business_reply(ch, msg1, is_owner=False, owner_id=777) is False
    # keyword but no reply
    msg2 = {"chat": {"id": 555}, "message_id": 10, "text": "summarize"}
    assert await ra.run_business_reply(ch, msg2, is_owner=True, owner_id=777) is False
    ch.send_message.assert_not_called()
