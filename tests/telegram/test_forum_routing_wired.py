"""Forum topic routing must actually reach Telegram -- when the operator opts in.

The feature was unreachable from the day it was written: `HAS_FORUM` computed and never
read, `TelegramForumMixin` with zero methods on `TelegramChannel`, `_get_thread_for_command`
with zero call sites. The Deck rendered a toggle for it.

Wired through ONE injection point: `_process_update` resolves the thread once per slash
command into a contextvar, and `_api_call` applies it as ``message_thread_id`` to every
send-family method that did not set its own. That is why no send surface had to change --
handlers call send_message / send_photo / sendChecklist from dozens of sites and would each
have needed the thread carried by hand.

These tests drive `_process_update` on a REAL channel with only `_api_call` stubbed, and
assert what the Telegram payload contains. No forum group is needed: `getChat` is what says
"this is a forum", and it is stubbed to say so.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration

FORUM_CHAT = -1001234
TOPIC_ID = 777


class _Resp:
    """The two things _api_call reads off an aiohttp response."""

    def __init__(self, payload):
        self.status = 200
        self._payload = payload

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    """Stub the NETWORK, not `_api_call`.

    The first draft replaced `_api_call` and the main test failed with a fully rendered status
    card and no thread -- because the thread is injected INSIDE the real `_api_call`, and the
    stub had simply skipped it. Instrumenting showed the contextvar was 777 at send time. A
    test that stubs the method under test is a test of the stub.
    """

    def __init__(self, ch, is_forum=True):
        self.ch, self.is_forum = ch, is_forum

    def post(self, url, json=None):
        method = url.rsplit("/", 1)[-1]
        self.ch.api.append((method, dict(json or {})))
        if method == "getChat":
            body = {"id": FORUM_CHAT, "type": "supergroup", "is_forum": self.is_forum}
        elif method == "getForumTopics":
            body = {"topics": [{"name": "Status & Briefing", "message_thread_id": TOPIC_ID}]}
        else:
            body = {"message_id": 1}
        return _Resp({"ok": True, "result": body})


def _channel(is_forum=True):
    from navig.gateway.channels.telegram import TelegramChannel

    ch = TelegramChannel(bot_token="123:FAKE", allowed_users=[42], on_message=lambda *a, **k: None)
    ch.api: list[tuple[str, dict]] = []
    ch._session = _Session(ch, is_forum=is_forum)
    return ch


def _slash(text, chat=FORUM_CHAT):
    return {"message": {"message_id": 1, "text": text, "chat": {"id": chat, "type": "supergroup"},
                        "from": {"id": 42, "username": "user42"}}}


def _run(ch, text, *, enabled):
    cfg = {"forum_routing_enabled": enabled}
    # Patch on the CHANNEL, not the mixin: _MIXIN_BINDINGS copies the function object onto
    # TelegramChannel at import, so a patch on TelegramForumMixin never reaches the copy that
    # self._get_forum_config() resolves to. (The first draft patched the mixin and the main
    # test failed with a fully rendered status card and no thread -- a useful failure.)
    with patch("navig.gateway.channels.telegram.TelegramChannel._get_forum_config",
               return_value=cfg):
        asyncio.run(ch._process_update(_slash(text)))
    return ch.api


def _sends(api):
    return [(m, p) for m, p in api if m.startswith("send")]


def test_a_mapped_command_lands_in_its_topic_when_enabled():
    """/status maps to 'Status & Briefing'; every send for it must carry that thread."""
    ch = _channel()
    api = _run(ch, "/status", enabled=True)
    sends = _sends(api)
    assert sends, f"no send happened at all: {[m for m, _ in api]}"
    missing = [(m, p) for m, p in sends if p.get("message_thread_id") != TOPIC_ID]
    assert not missing, f"sends without the topic thread: {missing}"


def test_it_is_off_unless_the_operator_turned_it_on():
    """Opt-in: with the toggle off nothing is routed and getChat is not even asked."""
    ch = _channel()
    api = _run(ch, "/status", enabled=False)
    assert "getChat" not in [m for m, _ in api], "disabled routing must not probe the chat"
    assert all("message_thread_id" not in p for m, p in _sends(api))


def test_an_unmapped_command_goes_to_general():
    """/help is not in the topic map -> no thread, i.e. exactly today's behaviour."""
    ch = _channel()
    api = _run(ch, "/help", enabled=True)
    assert all("message_thread_id" not in p for m, p in _sends(api))


def test_a_plain_group_is_left_alone():
    """getChat says is_forum=False -> nothing routed, nothing created."""
    ch = _channel(is_forum=False)
    api = _run(ch, "/status", enabled=True)
    assert "createForumTopic" not in [m for m, _ in api]
    assert all("message_thread_id" not in p for m, p in _sends(api))


def test_an_existing_topic_is_reused_never_recreated():
    """getForumTopics already lists the topic -> createForumTopic must not be called."""
    ch = _channel()
    api = _run(ch, "/status", enabled=True)
    assert "createForumTopic" not in [m for m, _ in api], "would have created a duplicate topic"


def test_the_thread_does_not_leak_into_the_next_message():
    """A contextvar set for one update must be clean for the next.

    Two things the first draft got wrong, both of which made it pass with the reset removed:
    the second message was another slash command, whose own resolution resets the var anyway;
    and each asyncio.run() is a fresh context, so nothing could leak between two of them. The
    real gateway processes updates in ONE long-lived task. So: one loop, two updates, and the
    second is PLAIN TEXT -- the path that never reaches the resolver and can only be clean if
    the top-of-message reset ran.
    """
    ch = _channel()
    cfg = {"forum_routing_enabled": True}

    async def _two_updates():
        await ch._process_update(_slash("/status"))
        ch.api.clear()
        await ch._process_update(_slash("hello there"))   # plain text, same context

    with patch("navig.gateway.channels.telegram.TelegramChannel._get_forum_config", return_value=cfg),          patch.object(ch, "_dispatch_by_mode", new=_no_ai_reply(ch)):
        asyncio.run(_two_updates())
    sends = _sends(ch.api)
    assert sends, "the plain message produced no send to inspect"
    assert all("message_thread_id" not in p for m, p in sends), (
        f"the previous command's thread leaked into a plain message: {sends}"
    )


def _no_ai_reply(ch):
    """Stand in for the AI turn: send one plain reply so there is a payload to inspect."""
    async def _dispatch(*a, **kw):
        await ch.send_message(FORUM_CHAT, "ok")
    return _dispatch


def test_an_explicit_thread_in_a_payload_wins_over_the_contextvar():
    """setdefault, not overwrite: a caller that chose a thread keeps it."""
    from navig.gateway.channels import telegram as T
    ch = _channel()
    token = T._FORUM_THREAD.set(TOPIC_ID)
    try:
        asyncio.run(ch._api_call("sendMessage", {"chat_id": 1, "text": "x", "message_thread_id": 5}))
    finally:
        T._FORUM_THREAD.reset(token)
    assert ch.api[-1][1]["message_thread_id"] == 5


def test_non_send_methods_are_never_touched():
    from navig.gateway.channels import telegram as T
    ch = _channel()
    token = T._FORUM_THREAD.set(TOPIC_ID)
    try:
        asyncio.run(ch._api_call("getMe", {}))
    finally:
        T._FORUM_THREAD.reset(token)
    assert "message_thread_id" not in ch.api[-1][1]
