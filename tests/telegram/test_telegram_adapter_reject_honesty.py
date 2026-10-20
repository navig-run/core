"""A Telegram send the transport REJECTED must not come back as a delivery receipt.

The Telegram transport is the odd one out among the messaging adapters: it returns
``None`` from a rejected send **without raising** — a 429 whose retry budget ran out, an
API error, a timeout (``TelegramChannel._api_call``). Discord raises, WhatsApp Cloud
checks ``resp.status``, Vonage checks its status field; only this one signals failure by
a falsy return.

``_msg_id(None)`` is ``""``, and ``""`` is not ``None``, so:

* ``send_message`` built ``DeliveryReceipt.success(message_id="")`` for a message that
  never went out, and
* ``_dispatch_attachment`` returned ``""``, which the caller's ``if mid is not None``
  counted as delivered — so ``sent == len(attachments)`` and a batch the Bot API refused
  reported full success, the precise outcome that caller's docstring promises cannot
  happen.

This was unreachable until the adapter was given a bot at all (it shipped with
``_bot = None``, so every send failed early). Wiring the bot made it live, and
``messaging/send.py`` hands the receipt straight to ``tracker.apply_receipt`` — so a
phantom success PERSISTS a delivered row for a message that does not exist.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

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


# ── plain text ─────────────────────────────────────────────────────────────────


async def test_rejected_text_send_is_not_reported_as_delivered():
    adapter, bot = _adapter_with_bot()
    bot.send_message = AsyncMock(return_value=None)  # rejected, no exception

    receipt = await adapter.send_message("42", "hello")

    bot.send_message.assert_awaited_once()  # it DID try
    assert receipt.ok is False, (
        "a rejected send reported as delivered writes a phantom row into the "
        "delivery tracker"
    )
    assert "reject" in (receipt.error or "").lower()


async def test_delivered_text_send_reports_the_id():
    adapter, bot = _adapter_with_bot()
    bot.send_message = AsyncMock(return_value={"message_id": 7})

    receipt = await adapter.send_message("42", "hello")

    assert receipt.ok is True
    assert receipt.message_id == "7"


# ── attachments ────────────────────────────────────────────────────────────────


async def test_rejected_attachment_is_not_counted_as_delivered():
    adapter, bot = _adapter_with_bot()
    bot.send_photo = AsyncMock(return_value=None)  # rejected

    receipt = await adapter.send_message("42", "cap", [_photo()])

    bot.send_photo.assert_awaited_once()
    assert receipt.ok is False
    assert "0 of 1" in (receipt.error or "")


async def test_partial_attachment_rejection_reports_incomplete():
    adapter, bot = _adapter_with_bot()
    bot.send_photo = AsyncMock(side_effect=[{"message_id": 1}, None])

    receipt = await adapter.send_message("42", "cap", [_photo(), _photo(b"\x89PNG2")])

    assert receipt.ok is False
    assert "1 of 2" in (receipt.error or "")


async def test_attachment_delivered_without_an_id_is_still_a_success():
    """The other direction, and the reason this is not just `if mid:`.

    A send that returned an object we cannot read an id from DID go out. Reporting
    failure would make the caller retry and duplicate the media — exactly the hazard
    `_send_with_attachments` was written to avoid. Only a None return means rejected.
    """
    adapter, bot = _adapter_with_bot()
    bot.send_photo = AsyncMock(return_value={})  # delivered; id not readable

    receipt = await adapter.send_message("42", "cap", [_photo()])

    assert receipt.ok is True, "an unreadable id is not a rejection"


# ── the distinction itself ─────────────────────────────────────────────────────


def test_sent_id_separates_rejection_from_an_unreadable_id():
    # Imported HERE, not at module scope: `_sent_id` is introduced by this fix, and a
    # top-level import would make the whole file error on collection against unfixed
    # code — turning the behavioural failures above into a collection error, which
    # proves nothing about behaviour.
    from navig.messaging.adapters.telegram_adapter import _sent_id

    assert _sent_id(None) is None, "None means the transport refused it"
    assert _sent_id({}) == "", "delivered, id unreadable — NOT a rejection"
    assert _sent_id({"message_id": 7}) == "7"
    assert _sent_id(MagicMock(message_id=7)) == "7"


# ── generated audio arrives as a PLAYER, not a file ────────────────────────────


def _audio(filename="dark-drone.mp3"):
    return {"data": b"ID3fake", "kind": "audio", "filename": filename}


async def test_an_mp3_track_goes_to_send_audio_with_a_title():
    """`navig audio gen` output used to land as an inert document; a track should be
    Telegram's in-chat player (title, scrubber)."""
    adapter, bot = _adapter_with_bot()
    bot.send_audio = AsyncMock(return_value={"message_id": 9})
    bot.send_document = AsyncMock(return_value={"message_id": 10})

    receipt = await adapter.send_message("42", "horror drone", [_audio()])

    assert receipt.ok is True and receipt.message_id == "9"
    bot.send_audio.assert_awaited_once()
    assert bot.send_audio.await_args.kwargs["title"] == "dark-drone"
    assert bot.send_audio.await_args.kwargs["filename"] == "dark-drone.mp3"
    bot.send_document.assert_not_awaited()


async def test_m4a_is_also_a_player_track():
    adapter, bot = _adapter_with_bot()
    bot.send_audio = AsyncMock(return_value={"message_id": 1})
    bot.send_document = AsyncMock()

    receipt = await adapter.send_message("42", "", [_audio("take.m4a")])

    assert receipt.ok is True
    bot.send_audio.assert_awaited_once()


async def test_wav_stays_a_document_because_send_audio_would_refuse_it():
    """The Bot API only plays MP3/M4A. A .wav sent through sendAudio is rejected, so
    the render would FAIL over its container — it must go out as a document instead."""
    adapter, bot = _adapter_with_bot()
    bot.send_audio = AsyncMock(return_value={"message_id": 1})
    bot.send_document = AsyncMock(return_value={"message_id": 2})

    receipt = await adapter.send_message("42", "", [_audio("master.wav")])

    assert receipt.ok is True and receipt.message_id == "2"
    bot.send_audio.assert_not_awaited()
    bot.send_document.assert_awaited_once()


async def test_a_rejected_audio_send_is_still_reported_as_a_failure():
    adapter, bot = _adapter_with_bot()
    bot.send_audio = AsyncMock(return_value=None)  # rejected, no exception

    receipt = await adapter.send_message("42", "", [_audio()])

    assert receipt.ok is False, "the player route must keep the honest-receipt contract"
