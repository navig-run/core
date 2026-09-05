"""Discord adapter must not report phantom success after silently dropping media.

`DiscordMessagingAdapter._build_files` resolves attachment bytes with
``getattr(self, "_session", None)`` — but the class never sets ``_session``, so it is
ALWAYS ``None`` and every url-only attachment resolves to ``None`` (see
``messaging/attachments.py``: the ``url`` branch needs a session). The old
``send_message`` then delivered the text alone and returned ``DeliveryReceipt.success``
— a silent partial delivery that Studio's dispatcher recorded as "delivered".

The fix resolves attachments BEFORE sending and returns ``failure`` (without sending)
if any were dropped, so the loss is surfaced and a retry can't duplicate. Under the old
code the first two tests would FAIL (success returned + text sent).

discord.py isn't a test dependency, so a minimal fake ``discord`` module is injected.
"""

from __future__ import annotations

import sys
import types

import pytest

from navig.messaging.adapter import DeliveryStatus
from navig.messaging.adapters import discord_adapter as da


class _FakeFile:
    def __init__(self, fp, filename=None):
        self.filename = filename


class _FakeChannel:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, content=None, files=None):
        self.sent.append({"content": content, "files": files})
        return types.SimpleNamespace(id=999)


class _FakeClient:
    def __init__(self, channel):
        self._channel = channel

    def get_channel(self, _cid):
        return self._channel

    async def fetch_channel(self, _cid):
        return self._channel


@pytest.fixture
def adapter(monkeypatch):
    """A DiscordMessagingAdapter wired to a fake channel, with discord.py faked in."""
    mod = types.ModuleType("discord")
    mod.File = _FakeFile
    monkeypatch.setitem(sys.modules, "discord", mod)
    monkeypatch.setattr(da, "DISCORD_AVAILABLE", True)
    channel = _FakeChannel()
    ad = da.DiscordMessagingAdapter()
    ad.set_client(_FakeClient(channel))
    ad._test_channel = channel  # expose for assertions
    return ad


async def test_unresolvable_url_attachment_fails_instead_of_phantom_success(adapter):
    receipt = await adapter.send_message(
        "123", "New drop!", [{"url": "https://cdn.example/img.png", "kind": "photo"}]
    )
    assert receipt.ok is False
    assert receipt.status == DeliveryStatus.FAILED
    # nothing was sent — no text-only partial, retry-safe
    assert adapter._test_channel.sent == []


async def test_partial_resolution_fails_without_sending_anything(adapter):
    # One resolvable (data:) + one not (url with no session) -> refuse the whole send.
    receipt = await adapter.send_message(
        "123",
        "caption",
        [
            {"data": b"\x89PNG\r\n", "filename": "ok.png"},
            {"url": "https://cdn.example/broken.png"},
        ],
    )
    assert receipt.ok is False
    assert adapter._test_channel.sent == []  # not even the resolvable one leaks out


async def test_resolvable_attachment_sends_with_media_and_succeeds(adapter):
    receipt = await adapter.send_message(
        "123", "hi", [{"data": b"\x89PNG\r\n", "filename": "x.png"}]
    )
    assert receipt.ok is True
    assert receipt.message_id == "999"
    assert len(adapter._test_channel.sent) == 1
    assert len(adapter._test_channel.sent[0]["files"]) == 1  # media actually attached


async def test_text_only_message_still_succeeds(adapter):
    receipt = await adapter.send_message("123", "just text", None)
    assert receipt.ok is True
    assert adapter._test_channel.sent[0]["content"] == "just text"
    assert adapter._test_channel.sent[0]["files"] is None
