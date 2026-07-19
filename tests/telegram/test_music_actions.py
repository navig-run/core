"""Tests for the Telegram music-link auto-reply (navig.telegram.music_actions).

Covers the conservative UX contract: fires only on a bare music link, owns the
message, stays silent on non-music / conversational / disabled / unresolvable.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from navig.telegram import music_actions

_SPOTIFY = "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC"
_FAKE = {
    "title": "Never Gonna Give You Up",
    "artist": "Rick Astley",
    "links": [
        {"platform": "spotify", "label": "Spotify", "url": _SPOTIFY},
        {"platform": "appleMusic", "label": "Apple Music", "url": "https://music.apple.com/x"},
    ],
}


def _channel():
    ch = MagicMock()
    ch.send_message = AsyncMock(return_value={"message_id": 1})
    return ch


async def test_bare_link_replies_with_platforms(monkeypatch):
    monkeypatch.setattr(music_actions, "enabled", lambda: True)
    monkeypatch.setattr(music_actions, "resolve_links", lambda url: _FAKE)
    ch = _channel()

    handled = await music_actions.offer_links(ch, 100, 5, _SPOTIFY)

    assert handled is True
    ch.send_message.assert_awaited_once()
    args, kwargs = ch.send_message.call_args
    body = args[1]
    assert "Spotify" in body and "Apple Music" in body
    assert "Rick Astley" in body
    assert kwargs.get("reply_to_message_id") == 5
    assert kwargs.get("parse_mode") == "HTML"


async def test_non_music_text_is_noop(monkeypatch):
    monkeypatch.setattr(music_actions, "enabled", lambda: True)
    monkeypatch.setattr(music_actions, "resolve_links", lambda url: _FAKE)
    ch = _channel()

    assert await music_actions.offer_links(ch, 100, 5, "hey how are you") is False
    ch.send_message.assert_not_awaited()


async def test_link_inside_conversation_is_left_to_agent(monkeypatch):
    # A sentence that merely mentions a link belongs to the chat agent, not to us.
    monkeypatch.setattr(music_actions, "enabled", lambda: True)
    monkeypatch.setattr(music_actions, "resolve_links", lambda url: _FAKE)
    ch = _channel()

    text = f"hey what do you think of this {_SPOTIFY} pretty good right"
    assert await music_actions.offer_links(ch, 100, 5, text) is False
    ch.send_message.assert_not_awaited()


async def test_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(music_actions, "enabled", lambda: False)
    monkeypatch.setattr(music_actions, "resolve_links", lambda url: _FAKE)
    ch = _channel()

    assert await music_actions.offer_links(ch, 100, 5, _SPOTIFY) is False
    ch.send_message.assert_not_awaited()


async def test_unresolvable_track_stays_silent(monkeypatch):
    from navig_download.music_links import MusicResolveError

    def _boom(url):
        raise MusicResolveError("not on song.link")

    monkeypatch.setattr(music_actions, "enabled", lambda: True)
    monkeypatch.setattr(music_actions, "resolve_links", _boom)
    ch = _channel()

    assert await music_actions.offer_links(ch, 100, 5, _SPOTIFY) is False
    ch.send_message.assert_not_awaited()


def test_coerce_bool_handles_config_strings():
    # `navig config set` stores raw strings — "false"/"off" must read as False.
    assert music_actions._coerce_bool("false", True) is False
    assert music_actions._coerce_bool("off", True) is False
    assert music_actions._coerce_bool("", True) is False
    assert music_actions._coerce_bool("true", False) is True
    assert music_actions._coerce_bool(True, False) is True
    assert music_actions._coerce_bool(None, True) is True


async def test_resolve_reply_ignores_passive_gates(monkeypatch):
    # The explicit 'music' reply-keyword fires even when the passive toggle is OFF
    # (the user named it) — unlike the passive offer_links.
    monkeypatch.setattr(music_actions, "enabled", lambda: False)
    monkeypatch.setattr(music_actions, "resolve_links", lambda url: _FAKE)
    ch = _channel()

    ok = await music_actions.resolve_reply(ch, 100, 7, _SPOTIFY)

    assert ok is True
    ch.send_message.assert_awaited_once()
    assert ch.send_message.call_args.kwargs.get("reply_to_message_id") == 7


async def test_resolve_reply_false_when_unresolvable(monkeypatch):
    from navig_download.music_links import MusicResolveError

    def _boom(url):
        raise MusicResolveError("nope")

    monkeypatch.setattr(music_actions, "resolve_links", _boom)
    ch = _channel()

    assert await music_actions.resolve_reply(ch, 100, 7, _SPOTIFY) is False
    ch.send_message.assert_not_awaited()


async def test_music_reply_keyword_end_to_end(monkeypatch):
    # Owner replies "music" to a message containing a Spotify link → links sent.
    # Works in a GROUP (is_group=True), where the DM-only passive never fires.
    from navig.telegram import reply_actions

    monkeypatch.setattr(music_actions, "resolve_links", lambda url: _FAKE)
    ch = _channel()
    ch.allowed_users = {42}

    handled = await reply_actions.run_bot_reply(
        ch, action="music", chat_id=100, user_id=42,
        reply_to_msg={"text": f"check this {_SPOTIFY}"}, reply_to_message_id=7, is_group=True,
    )

    assert handled is True
    ch.send_message.assert_awaited_once()
    assert "Spotify" in ch.send_message.call_args[0][1]


async def test_music_reply_keyword_no_link_owns_message():
    from navig.telegram import reply_actions

    ch = _channel()
    ch.allowed_users = {42}

    handled = await reply_actions.run_bot_reply(
        ch, action="music", chat_id=100, user_id=42,
        reply_to_msg={"text": "no link at all"}, reply_to_message_id=7, is_group=False,
    )

    assert handled is True  # owns the message rather than leaking "music" to the agent
    assert "No music link" in ch.send_message.call_args[0][1]
