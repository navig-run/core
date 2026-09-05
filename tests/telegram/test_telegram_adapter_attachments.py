"""Telegram adapter must report a receipt that reflects ALL attachments, never just
the first — and must not silently drop unresolvable media then report success.

The old ``send_message`` set the receipt's success/failure from attachment[0] only:
- att[0] sent but att[1..n] dropped  -> success (silent media loss), and
- att[0] failed but att[1..n] already sent -> failure (retry duplicates them).

The fix resolves every attachment's bytes UP FRONT (side-effect-free) and fails the
whole send before dispatching anything if any can't resolve — then requires every item
to actually send before reporting success. Under the old code the two "phantom" tests
below would pass with ok=True (verified).

Fake bot: a MagicMock whose send_* methods are AsyncMocks. ``data:`` attachments resolve
without an aiohttp session; a ``url`` attachment with ``bot._session = None`` cannot.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from navig.messaging.adapter import DeliveryStatus
from navig.messaging.adapters.telegram_adapter import TelegramMessagingAdapter

pytestmark = pytest.mark.asyncio


def _adapter_with_bot():
    adapter = TelegramMessagingAdapter()
    bot = MagicMock()
    bot._session = None  # no aiohttp session -> url attachments can't be fetched
    adapter._bot = bot
    return adapter, bot


def _photo(data=b"\x89PNG\r\n", filename="x.png"):
    return {"data": data, "kind": "photo", "filename": filename}


async def test_single_resolvable_attachment_succeeds():
    adapter, bot = _adapter_with_bot()
    bot.send_photo = AsyncMock(return_value=MagicMock(message_id=100))
    receipt = await adapter.send_message("42", "cap", [_photo()])
    assert receipt.ok is True
    assert receipt.message_id == "100"
    bot.send_photo.assert_awaited_once()


async def test_all_attachments_delivered_returns_success():
    adapter, bot = _adapter_with_bot()
    bot.send_photo = AsyncMock(side_effect=[MagicMock(message_id=1), MagicMock(message_id=2)])
    receipt = await adapter.send_message("42", "cap", [_photo(), _photo(filename="y.png")])
    assert receipt.ok is True
    assert receipt.message_id == "1"  # first item's id
    assert bot.send_photo.await_count == 2  # BOTH sent


async def test_later_attachment_send_failure_is_reported_not_phantom_success():
    # att[0] sends; att[1] raises at the Bot API -> old code returned success (att[1]
    # silently dropped). Now the receipt must be a failure.
    adapter, bot = _adapter_with_bot()
    bot.send_photo = AsyncMock(side_effect=[MagicMock(message_id=1), Exception("boom")])
    receipt = await adapter.send_message("42", "cap", [_photo(), _photo(filename="y.png")])
    assert receipt.ok is False
    assert receipt.status == DeliveryStatus.FAILED
    assert "1 of 2" in receipt.error


async def test_unresolvable_attachment_fails_before_sending_anything():
    # att[0] resolvable, att[1] a url with no session -> must fail on resolution BEFORE
    # dispatching, so nothing goes out (retry-safe). Old code sent att[0] then returned
    # success with att[1] dropped.
    adapter, bot = _adapter_with_bot()
    bot.send_photo = AsyncMock(return_value=MagicMock(message_id=1))
    receipt = await adapter.send_message(
        "42",
        "cap",
        [_photo(), {"url": "https://cdn.example/broken.png", "kind": "photo"}],
    )
    assert receipt.ok is False
    bot.send_photo.assert_not_awaited()  # nothing sent — no partial, no leak


async def test_text_only_message_still_succeeds():
    adapter, bot = _adapter_with_bot()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=7))
    receipt = await adapter.send_message("42", "just text")
    assert receipt.ok is True
    assert receipt.message_id == "7"
    bot.send_message.assert_awaited_once()
