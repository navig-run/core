"""Tests for the plugin→Telegram command seam.

The seam's whole job is to let a plugin answer one chat command *without* the ability to
shadow the platform, reach the model when it breaks, or leak between tests.
"""

from __future__ import annotations

import pytest

from navig.telegram import plugin_commands


@pytest.fixture(autouse=True)
def _clean():
    plugin_commands.clear()
    yield
    plugin_commands.clear()


async def _handler(**kwargs):
    _handler.calls.append(kwargs)


_handler.calls = []


def test_a_plugin_command_is_registered_under_its_bare_name() -> None:
    assert plugin_commands.register("/Mail", _handler, plugin="email") is True
    assert plugin_commands.names() == ["mail"]
    cmd = plugin_commands.get("mail")
    assert cmd is not None and cmd.plugin == "email"
    assert cmd.usage == "/mail"
    assert plugin_commands.get("/mail") is not None  # the slash is tolerated


def test_a_core_command_cannot_be_shadowed() -> None:
    assert plugin_commands.register("status", _handler, plugin="email") is False
    assert plugin_commands.names() == []


def test_another_plugin_cannot_steal_a_name() -> None:
    assert plugin_commands.register("mail", _handler, plugin="email") is True
    assert plugin_commands.register("mail", _handler, plugin="rss") is False
    assert plugin_commands.get("mail").plugin == "email"


def test_the_same_plugin_may_replace_its_own_command() -> None:
    assert plugin_commands.register("mail", _handler, plugin="email") is True
    other = _handler
    assert plugin_commands.register("mail", other, plugin="email") is True
    assert plugin_commands.get("mail").handler is other


def test_an_empty_name_or_a_non_callable_is_refused() -> None:
    assert plugin_commands.register("", _handler, plugin="x") is False
    assert plugin_commands.register("mail", "not a function", plugin="x") is False  # type: ignore[arg-type]
    assert plugin_commands.names() == []


def test_unregister_and_clear_are_scoped() -> None:
    plugin_commands.register("mail", _handler, plugin="email")
    plugin_commands.register("feed", _handler, plugin="rss")
    assert plugin_commands.unregister("mail") is True
    assert plugin_commands.unregister("mail") is False
    assert plugin_commands.clear("rss") == 1
    assert plugin_commands.names() == []
    plugin_commands.register("a", _handler, plugin="p1")
    plugin_commands.register("b", _handler, plugin="p2")
    assert plugin_commands.clear() == 2


# ── the dispatcher ────────────────────────────────────────────────────────────


def _channel():
    from navig.gateway.channels.telegram import TelegramChannel

    channel = TelegramChannel(bot_token="123:FAKE", allowed_users=[1], on_message=lambda *a: None)
    sent: list[str] = []

    async def fake_send(chat_id, text, parse_mode=None, **kw):
        sent.append(text)
        return {"message_id": 1}

    channel.send_message = fake_send  # type: ignore[assignment]
    return channel, sent


@pytest.mark.asyncio
async def test_a_registered_command_is_dispatched_with_the_arguments_it_declares() -> None:
    _handler.calls.clear()

    async def handler(channel, chat_id, text):
        _handler.calls.append({"channel": channel, "chat_id": chat_id, "text": text})

    plugin_commands.register("mail", handler, plugin="email")
    channel, sent = _channel()

    handled = await channel._dispatch_registry_command(
        chat_id=5, user_id=1, text="/mail unread"
    )

    assert handled is True
    assert _handler.calls[0]["chat_id"] == 5
    assert _handler.calls[0]["text"] == "/mail unread"
    assert _handler.calls[0]["channel"] is channel
    assert sent == []  # no warning, no fallback message


@pytest.mark.asyncio
async def test_an_unknown_command_still_falls_through_to_the_model() -> None:
    channel, sent = _channel()
    handled = await channel._dispatch_registry_command(
        chat_id=5, user_id=1, text="/nonsense"
    )
    assert handled is False
    assert sent == []


@pytest.mark.asyncio
async def test_a_failing_plugin_command_answers_instead_of_reaching_the_model() -> None:
    async def boom(**kwargs):
        raise RuntimeError("plugin exploded")

    plugin_commands.register("mail", boom, plugin="email")
    channel, sent = _channel()

    handled = await channel._dispatch_registry_command(chat_id=5, user_id=1, text="/mail")

    assert handled is True  # contained: the model must not improvise an answer
    assert sent and "/mail failed" in sent[0]


@pytest.mark.asyncio
async def test_a_core_command_is_not_diverted_by_a_plugin_registration() -> None:
    async def handler(**kwargs):  # pragma: no cover - must never run
        raise AssertionError("a plugin must not shadow a core command")

    plugin_commands.register("status", handler, plugin="email")  # refused
    channel, sent = _channel()

    handled = await channel._dispatch_registry_command(chat_id=5, user_id=1, text="/status")

    # /status is a real registry command; whatever it does, the plugin did not answer it.
    assert handled is True
    assert not any("plugin" in s.lower() for s in sent)
