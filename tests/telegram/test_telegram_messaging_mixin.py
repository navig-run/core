from types import SimpleNamespace

import pytest

from navig.gateway.channels.telegram_messaging_mixin import TelegramMessagingMixin


class _FakeChannel(TelegramMessagingMixin):
    def __init__(self):
        self.sent_messages = []
        self.reply_calls = []

    async def send_message(self, chat_id, text, parse_mode=None, **kwargs):
        self.sent_messages.append(
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "kwargs": kwargs,
            }
        )
        return {"ok": True}

    async def _messaging_reply_to_thread(self, thread, body):
        self.reply_calls.append({"thread": thread, "body": body})
        return SimpleNamespace(ok=True, error=None)


@pytest.mark.integration
async def test_reply_without_id_rejects_ambiguous_open_threads(monkeypatch):
    channel = _FakeChannel()

    t1 = SimpleNamespace(id=101, adapter="telegram")
    t2 = SimpleNamespace(id=102, adapter="whatsapp")

    class _Store:
        def list_threads(self, **kwargs):
            assert kwargs.get("status") == "open"
            return [t1, t2]

        def get_by_id(self, thread_id):
            if thread_id == 101:
                return t1
            if thread_id == 102:
                return t2
            return None

    monkeypatch.setattr("navig.store.threads.get_thread_store", lambda: _Store())

    await channel._handle_messaging_reply(chat_id=1, user_id=7, text="/reply hello team")

    assert channel.reply_calls == []
    assert channel.sent_messages
    assert "Multiple open threads found" in channel.sent_messages[-1]["text"]


@pytest.mark.integration
async def test_reply_without_id_uses_single_open_thread(monkeypatch):
    channel = _FakeChannel()

    thread = SimpleNamespace(id=42, adapter="telegram")

    class _Store:
        def list_threads(self, **kwargs):
            assert kwargs.get("status") == "open"
            return [thread]

        def get_by_id(self, thread_id):
            return thread if thread_id == 42 else None

    monkeypatch.setattr("navig.store.threads.get_thread_store", lambda: _Store())

    await channel._handle_messaging_reply(chat_id=1, user_id=7, text="/reply hello there")

    assert len(channel.reply_calls) == 1
    assert channel.reply_calls[0]["thread"].id == 42
    assert channel.reply_calls[0]["body"] == "hello there"


@pytest.mark.integration
async def test_reply_success_escapes_adapter_in_html_message(monkeypatch):
    channel = _FakeChannel()

    thread = SimpleNamespace(id=42, adapter="<bad-adapter>")

    class _Store:
        def list_threads(self, **kwargs):
            return [thread]

        def get_by_id(self, thread_id):
            return thread if thread_id == 42 else None

    monkeypatch.setattr("navig.store.threads.get_thread_store", lambda: _Store())

    await channel._handle_messaging_reply(chat_id=1, user_id=7, text="/reply 42 shipped")

    assert channel.sent_messages
    text = channel.sent_messages[-1]["text"]
    assert "&lt;bad-adapter&gt;" in text
