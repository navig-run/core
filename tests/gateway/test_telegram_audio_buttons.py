"""The ⏩ / 🐢 buttons on an audio card must survive the whole round trip.

``CallbackHandler`` calls ``self.channel._edit_audio_and_reply(...)`` and ``self.channel``
IS the ``TelegramChannel`` -- which does not inherit ``TelegramVoiceMixin`` (runtime MRO is
``[TelegramChannel, object]``). So the method, and everything it reaches for, has to be bound
onto the class explicitly. Four names, across two mixins:

    _edit_audio_and_reply     the entry point the button calls
    _download_telegram_file   -> which needs _get_file_path + _build_file_url (already bound)
    _send_audio_document      -> which needs _api_call_multipart, from a THIRD mixin
    TELEGRAM_DOWNLOAD_LIMIT   the ceiling it refuses above, read before it downloads

Binding only the entry point moves the AttributeError one frame deeper -- inside a mixin
method ``self._sibling()`` resolves against the channel too. These tests drive the real chain
rather than asserting ``hasattr``, because resolution is not the same as working.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from unittest.mock import patch

import pytest

from navig.gateway.channels.telegram import TelegramChannel


def _bare_channel() -> TelegramChannel:
    ch = TelegramChannel.__new__(TelegramChannel)
    ch.bot_token = "TOK"
    return ch


class _Result:
    def __init__(self, path):
        self.path = path


@pytest.fixture
def src_file():
    fh = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    fh.write(b"\x00" * 32)
    fh.close()
    yield fh.name
    try:
        os.unlink(fh.name)
    except OSError:
        pass


def test_the_whole_audio_chain_is_bound_on_the_channel():
    """Every name the button reaches, in one place, so a partial binding is visible."""
    ch = _bare_channel()
    for name in (
        "_edit_audio_and_reply",
        "_download_telegram_file",
        "_send_audio_document",
        "_api_call_multipart",
        "TELEGRAM_DOWNLOAD_LIMIT",
    ):
        assert hasattr(ch, name), (
            f"TelegramChannel has no {name}; the ⏩/🐢 buttons raise AttributeError when pressed"
        )
    assert [c.__name__ for c in TelegramChannel.__mro__] == ["TelegramChannel", "object"], (
        "the binding must not have introduced inheritance -- that would change dispatch"
    )


def test_pressing_speed_uploads_the_converted_track(src_file):
    """The path the button actually takes: download -> ffmpeg -> sendAudio."""
    ch = _bare_channel()
    seen = {}

    async def fake_multipart(method, data, files):
        seen["upload"] = (method, dict(data), sorted(files))
        return {"ok": True, "result": {"message_id": 7}}

    async def fake_download(file_id):
        seen["download"] = file_id
        return src_file

    def fake_speed(source, dest, rate):
        seen["rate"] = rate
        open(dest, "wb").write(b"converted")
        return _Result(str(dest))

    ch._api_call_multipart = fake_multipart
    ch._download_telegram_file = fake_download

    with patch("navig.media.audio_edit.speed", fake_speed):
        ok, detail = asyncio.run(
            ch._edit_audio_and_reply(
                123, "FILEID", "speed",
                meta={"title": "Song", "file_size": 1000},
                reply_to_message_id=5,
            )
        )

    assert ok is True
    assert detail.startswith("⏩")
    assert seen["download"] == "FILEID"
    method, data, files = seen["upload"]
    assert method == "sendAudio"
    assert files == ["audio"], "an edited song must go up as audio, not a voice note"
    assert data["chat_id"] == "123"
    assert data["reply_to_message_id"] == "5", "the reply must thread onto the original card"
    assert "1.5x" in data["title"]


def test_an_oversize_file_is_refused_before_anything_is_downloaded():
    """The ceiling is read off the channel, so a broken binding would download 2 GB."""
    ch = _bare_channel()
    touched = []
    ch._download_telegram_file = lambda *a, **k: touched.append(a)  # must never be reached

    ok, detail = asyncio.run(
        ch._edit_audio_and_reply(
            123, "FILEID", "speed",
            meta={"file_size": ch.TELEGRAM_DOWNLOAD_LIMIT + 1},
        )
    )
    assert ok is False
    assert "20 MB" in detail, f"the message must name the real ceiling, got {detail!r}"
    assert touched == [], "it refused the size but downloaded the file anyway"


def test_a_rejected_upload_is_reported_as_a_failure_not_a_success(src_file):
    """A falsy Telegram response must not be reported to the user as delivered."""
    ch = _bare_channel()

    async def refuse(method, data, files):
        return {"ok": False, "description": "Bad Request"}

    async def fake_download(file_id):
        return src_file

    def fake_speed(source, dest, rate):
        open(dest, "wb").write(b"converted")
        return _Result(str(dest))

    ch._api_call_multipart = refuse
    ch._download_telegram_file = fake_download

    with patch("navig.media.audio_edit.speed", fake_speed):
        ok, detail = asyncio.run(
            ch._edit_audio_and_reply(123, "F", "speed", meta={"title": "S"})
        )
    assert ok is False
    assert "refused" in detail.lower()


def test_the_source_and_converted_files_are_both_cleaned_up(src_file):
    """Two temp files per press; a leak here fills the disk one button at a time."""
    ch = _bare_channel()
    converted = {}

    async def fake_multipart(method, data, files):
        return {"ok": True}

    async def fake_download(file_id):
        return src_file

    def fake_speed(source, dest, rate):
        open(dest, "wb").write(b"converted")
        converted["path"] = str(dest)
        return _Result(str(dest))

    ch._api_call_multipart = fake_multipart
    ch._download_telegram_file = fake_download

    with patch("navig.media.audio_edit.speed", fake_speed):
        asyncio.run(ch._edit_audio_and_reply(123, "F", "speed", meta={"title": "S"}))

    assert not os.path.exists(src_file), "the downloaded source was left behind"
    assert not os.path.exists(converted["path"]), "the converted file was left behind"


def test_a_failed_conversion_still_cleans_up_and_explains(src_file):
    """ffmpeg missing is the commonest real failure; the user needs the reason."""
    from navig.media.audio_edit import AudioEditError

    ch = _bare_channel()

    async def fake_download(file_id):
        return src_file

    def boom(source, dest, rate):
        raise AudioEditError("ffmpeg is not installed")

    ch._download_telegram_file = fake_download

    with patch("navig.media.audio_edit.speed", boom):
        ok, detail = asyncio.run(
            ch._edit_audio_and_reply(123, "F", "speed", meta={"title": "S"})
        )
    assert ok is False
    assert "ffmpeg" in detail
    assert not os.path.exists(src_file), "a failed conversion leaked the downloaded source"
