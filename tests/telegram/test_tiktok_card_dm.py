"""Tests for the passive TikTok card in 1:1 chats (offer_card_dm).

Sharing a TikTok link with the bot used to reach the chat model, which has no
fetch tool for it and could only answer "Can't open external links." The card —
title/author/stats plus ⬇️ Download and 🔍 Analyse — already existed and was
wired ONLY to the Telegram Business layer, so it never fired in the owner's own
chat. This pins the same conservative contract the music-link handler uses:
fires on a bare link, owns the message, stays silent otherwise.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from navig.telegram import tiktok_actions

_LINK = "https://vm.tiktok.com/ZGdxryFmF"


def _channel(sent=None):
    ch = MagicMock()
    ch.send_message = AsyncMock(return_value={"message_id": 1} if sent is None else sent)
    ch.allowed_users = {42}
    return ch


def _patch(monkeypatch, *, enabled=True, info=None):
    monkeypatch.setattr(tiktok_actions, "enabled", lambda: enabled)
    monkeypatch.setattr(
        tiktok_actions.engine, "info", lambda url: info or {"title": "clip"}
    )
    monkeypatch.setattr(
        tiktok_actions.engine, "render_card", lambda meta: "🎵 <b>clip</b>"
    )


async def test_bare_link_sends_card_with_both_buttons(monkeypatch):
    _patch(monkeypatch)
    ch = _channel()

    handled = await tiktok_actions.offer_card_dm(ch, 100, 5, _LINK, user_id=42)

    assert handled is True, "a bare TikTok link in a DM must produce the card"
    ch.send_message.assert_awaited_once()
    _args, kwargs = ch.send_message.call_args
    buttons = [b["text"] for row in kwargs["keyboard"] for b in row]
    assert "⬇️ Download" in buttons
    assert "🔍 Analyse" in buttons
    assert kwargs.get("reply_to_message_id") == 5


async def test_a_question_about_a_link_still_goes_to_the_agent(monkeypatch):
    """A sentence that merely mentions a link is a question for the agent — the
    card must not swallow it (that would replace an answer with a card)."""
    _patch(monkeypatch)
    ch = _channel()

    handled = await tiktok_actions.offer_card_dm(
        ch, 100, 5, f"what do you make of {_LINK} — is it satire?", user_id=42
    )

    assert handled is False
    ch.send_message.assert_not_awaited()


async def test_non_tiktok_text_is_a_silent_noop(monkeypatch):
    _patch(monkeypatch)
    ch = _channel()

    assert await tiktok_actions.offer_card_dm(ch, 100, 5, "hey", user_id=42) is False
    assert (
        await tiktok_actions.offer_card_dm(
            ch, 100, 5, "https://example.com/x", user_id=42
        )
        is False
    )
    ch.send_message.assert_not_awaited()


async def test_disabled_by_config_falls_through_to_the_agent(monkeypatch):
    _patch(monkeypatch, enabled=False)
    ch = _channel()

    assert await tiktok_actions.offer_card_dm(ch, 100, 5, _LINK, user_id=42) is False
    ch.send_message.assert_not_awaited()


async def test_denied_by_download_policy_falls_through(monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setattr(
        tiktok_actions.permissions, "can_use", lambda tool, *, is_owner: False
    )
    ch = _channel()

    assert await tiktok_actions.offer_card_dm(ch, 100, 5, _LINK, user_id=42) is False
    ch.send_message.assert_not_awaited()


async def test_rejected_send_returns_false_so_the_user_is_not_left_silent(monkeypatch):
    """send_message returns None on a rejected send WITHOUT raising. Claiming the
    message then would skip the agent reply too — the user would get nothing."""
    _patch(monkeypatch)
    ch = _channel(sent=False)
    ch.send_message = AsyncMock(return_value=None)

    assert await tiktok_actions.offer_card_dm(ch, 100, 5, _LINK, user_id=42) is False
    ch.send_message.assert_awaited_once()  # it DID try


async def test_slow_metadata_still_sends_a_card_with_buttons(monkeypatch):
    """A hung tiktok.com lookup must not hold the reply: the card degrades to the
    plain header and still carries both buttons."""
    import time

    monkeypatch.setattr(tiktok_actions, "enabled", lambda: True)
    monkeypatch.setattr(tiktok_actions, "_INFO_TIMEOUT", 0.05)
    monkeypatch.setattr(tiktok_actions.engine, "info", lambda url: time.sleep(5))
    ch = _channel()

    handled = await tiktok_actions.offer_card_dm(ch, 100, 5, _LINK, user_id=42)

    assert handled is True
    _args, kwargs = ch.send_message.call_args
    buttons = [b["text"] for row in kwargs["keyboard"] for b in row]
    assert "⬇️ Download" in buttons and "🔍 Analyse" in buttons


async def test_owner_flag_comes_from_the_channel_allowlist(monkeypatch):
    """A user not on allowed_users is not the owner, so the 'owner' default policy
    denies them — the card is an owner-facing action."""
    _patch(monkeypatch)
    seen = {}
    monkeypatch.setattr(
        tiktok_actions.permissions,
        "can_use",
        lambda tool, *, is_owner: seen.setdefault("is_owner", is_owner),
    )
    ch = _channel()

    await tiktok_actions.offer_card_dm(ch, 100, 5, _LINK, user_id=999)

    assert seen["is_owner"] is False


# ── button callbacks: the URL must survive without the catalog ────────────────


async def test_button_resolves_the_url_from_the_callback_payload(monkeypatch):
    """A callback carries the message the card replied to, so the link is right
    there. The catalog is a fallback, not a dependency — an operator with
    telegram.catalog.enabled off must still get a working Download button."""
    analysed = {}

    async def _fake_analyse(channel, chat_id, url):
        analysed["url"] = url

    def _no_catalog(chat, msg):
        raise AssertionError("catalog must not be needed when the payload has the link")

    monkeypatch.setattr(tiktok_actions, "_url_from_ref", _no_catalog)
    monkeypatch.setattr(tiktok_actions, "_do_analyse", _fake_analyse)
    ch = _channel()

    await tiktok_actions.handle_callback(
        ch, "tk:an:100:5", 100, 9, 42, source_text=f"look at this {_LINK}"
    )

    assert analysed["url"] == _LINK


async def test_button_falls_back_to_the_catalog_when_payload_has_no_link(monkeypatch):
    downloaded = {}

    async def _fake_download(channel, chat_id, url):
        downloaded["url"] = url

    monkeypatch.setattr(tiktok_actions, "_url_from_ref", lambda c, m: _LINK)
    monkeypatch.setattr(tiktok_actions, "_do_download", _fake_download)
    ch = _channel()

    await tiktok_actions.handle_callback(ch, "tk:dl:100:5", 100, 9, 42, source_text="")

    assert downloaded["url"] == _LINK


async def test_button_says_so_when_neither_route_has_the_link(monkeypatch):
    """Honesty: no silent no-op on a tap."""
    monkeypatch.setattr(tiktok_actions, "_url_from_ref", lambda c, m: None)
    ch = _channel()

    await tiktok_actions.handle_callback(ch, "tk:dl:100:5", 100, 9, 42, source_text="hi")

    ch.send_message.assert_awaited_once()
    assert "Couldn't find" in ch.send_message.call_args[0][1]
