"""A business-chat role answers photos, videos and voice notes — in their language.

Asked 2026-09-28: can a role like `tyler` "auto-detect the language … so I don't
write `tyler fr`", and "detect images, videos and voice"? Two gaps:

* Media was invisible. A role reply to a photo or voice note said "that message has
  no text", and pro mode skipped media-only messages without a word. Every
  primitive needed already existed in the catalog analyzer (vision description,
  OCR, speech-to-text, video audio + frame OCR); nothing connected them to a reply.
* Speech was transcribed in the OPERATOR's language. The analyzer resolves
  `user.language` for transcription — right for the operator's own library, wrong
  for someone else's voice: a French voice note would be read as Russian.

Language without a code already worked for text ("reply in the language the other
person is using"); the rule now keys on the message being answered, so a
transcript in French is answered in French.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import navig.gateway.channels.telegram_catalog_analyzer as an
import navig.telegram.autoreply as ar

VOICE = {"message_id": 80, "from": {"id": 555}, "voice": {"file_id": "VOICE-1", "file_size": 9000}}
PHOTO = {
    "message_id": 81,
    "from": {"id": 555},
    "caption": "look",
    "photo": [
        {"file_id": "SMALL", "file_size": 1000, "width": 90},
        {"file_id": "BIG", "file_size": 90000, "width": 1280},
    ],
}
VIDEO = {"message_id": 82, "from": {"id": 555}, "video": {"file_id": "VID", "file_size": 50000}}


def _channel():
    ch = MagicMock()
    ch._api_call = AsyncMock(return_value={"ok": True, "message_id": 1})
    ch.send_message = AsyncMock()
    return ch


async def _drain():
    await asyncio.gather(*list(ar._bg_tasks))


# ── which file a message carries ─────────────────────────────────────────────


def test_the_largest_photo_size_is_the_one_read():
    assert an.message_media(PHOTO)["file_id"] == "BIG"


def test_a_pdf_is_not_media_to_answer_but_an_image_document_is():
    pdf = {"document": {"file_id": "D", "mime_type": "application/pdf"}}
    img = {"document": {"file_id": "I", "mime_type": "image/jpeg"}}
    assert an.message_media(pdf) is None
    assert an.message_media(img)["kind"] == "document"
    assert an.message_media({"text": "hi"}) is None


# ── reading it ───────────────────────────────────────────────────────────────


async def test_a_photo_becomes_what_it_shows_and_the_text_in_it(monkeypatch):
    monkeypatch.setattr(an, "_download", AsyncMock(return_value=b"jpeg"))
    monkeypatch.setattr(
        an, "_analyze_image", AsyncMock(return_value=("SALE -50%", "a shop window"))
    )
    out = await an.understand_message_media(_channel(), PHOTO)
    assert out == "a photo showing: a shop window; text in it: SALE -50%"


async def test_a_voice_note_is_transcribed_in_the_language_it_was_spoken(monkeypatch):
    """The operator reads Russian; the person may speak French. `auto` reaches the
    speech-to-text provider as "detect", whatever `user.language` says."""
    import navig.voice.stt as stt

    seen = {}

    async def fake_transcribe(path, **kw):
        seen.update(kw, suffix=Path(path).suffix)
        return "On se voit demain ?"

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)
    monkeypatch.setattr(an, "_download", AsyncMock(return_value=b"ogg"))
    monkeypatch.setattr("navig.core.language.resolve_language", lambda *_a, **_k: "Russian")

    out = await an.understand_message_media(_channel(), VOICE)

    assert out == "a voice message saying: On se voit demain ?"
    assert seen["language"] == "auto", f"transcribed with {seen['language']!r}, not detection"
    assert seen["suffix"] == ".ogg"


async def test_a_video_gives_speech_and_on_screen_text(monkeypatch):
    calls = {}

    async def fake_video(path, *a, **kw):
        calls.update(kw)
        return "bonjour à tous", "PROMO", None

    monkeypatch.setattr(an, "_download", AsyncMock(return_value=b"mp4"))
    monkeypatch.setattr(an, "analyze_video_file", fake_video)
    out = await an.understand_message_media(_channel(), VIDEO)
    assert out == "a video — said in it: bonjour à tous; text on screen: PROMO"
    assert calls.get("auto_detect") is True


async def test_a_file_over_the_download_cap_is_not_fetched(monkeypatch):
    dl = AsyncMock(return_value=b"x")
    monkeypatch.setattr(an, "_download", dl)
    huge = {"video": {"file_id": "V", "file_size": 999 * 1024 * 1024}}
    assert await an.understand_message_media(_channel(), huge) is None
    dl.assert_not_awaited()


# ── the role reply uses it ───────────────────────────────────────────────────


def _reply_to(target, text="tyler"):
    return {
        "chat": {"id": 555},
        "message_id": 90,
        "business_connection_id": "bc1",
        "from": {"id": 777},
        "text": text,
        "reply_to_message": target,
    }


async def test_a_role_reply_to_a_voice_note_answers_what_was_said(monkeypatch):
    monkeypatch.setattr(ar.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(ar, "_recent_context", lambda cid, oid, n=12: [])
    monkeypatch.setattr(
        an,
        "understand_message_media",
        AsyncMock(return_value="a voice message saying: On se voit demain ?"),
    )
    seen = {}

    async def fake_generate(role, lang, ctx, *, target=None):
        seen.update(role=role, lang=lang, target=target)
        return "Demain 18h, ça marche."

    monkeypatch.setattr(ar, "_generate", fake_generate)
    ch = _channel()

    assert await ar.maybe_role_reply(ch, _reply_to(VOICE), is_owner=True, owner_id=777)
    await _drain()

    assert seen["lang"] == "", "no language named — it follows the message"
    assert "On se voit demain ?" in seen["target"]
    edits = [c.args[1] for c in ch._api_call.call_args_list if c.args[0] == "editMessageText"]
    assert edits[0]["text"] == "👀", "reading a voice note shows as reading"
    assert edits[-1]["text"] == "Demain 18h, ça marche.", "the answer lands in the trigger"
    assert all(e["message_id"] == 90 for e in edits), "the trigger IS the answer — already a reply to 80"


async def test_a_photo_with_a_caption_keeps_both(monkeypatch):
    monkeypatch.setattr(
        an, "understand_message_media", AsyncMock(return_value="a photo showing: a cat")
    )
    assert await ar._message_as_text(_channel(), PHOTO) == "look\n[a photo showing: a cat]"


async def test_media_that_cannot_be_read_is_explained_not_silent(monkeypatch):
    monkeypatch.setattr(an, "understand_message_media", AsyncMock(return_value=None))
    gen = AsyncMock(return_value="x")
    monkeypatch.setattr(ar, "_generate", gen)
    ch = _channel()

    await ar.maybe_role_reply(ch, _reply_to(VOICE), is_owner=True, owner_id=777)
    await _drain()

    gen.assert_not_awaited()
    assert "Couldn't read" in ch.send_message.await_args.args[1]


async def test_pro_mode_now_answers_a_voice_note_too(monkeypatch):
    """It used to return False for any message without words."""
    monkeypatch.setattr(ar.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(ar, "get_active", lambda cid: {"role": "tyler", "lang": ""})
    monkeypatch.setattr(ar, "_recent_context", lambda cid, oid, n=12: [])
    monkeypatch.setattr(
        an, "understand_message_media", AsyncMock(return_value="a voice message saying: ça va ?")
    )
    monkeypatch.setattr(ar, "_generate", AsyncMock(return_value="Ça va."))
    ch = _channel()
    msg = {"chat": {"id": 555}, "business_connection_id": "bc1", **VOICE}

    assert await ar.maybe_autoreply(ch, msg, is_owner=False, owner_id=777) is True
    await _drain()
    assert [c for c in ch._api_call.call_args_list if c.args[0] == "sendMessage"]


# ── the language rule ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("lang", "expected"),
    [("", "language of the message you are answering"), ("fr", "Always write your reply in fr")],
)
async def test_the_language_follows_the_message_unless_one_is_named(monkeypatch, lang, expected):
    captured = {}

    def fake_llm(messages, **_):
        captured["system"] = messages[0]["content"]
        return "ok"

    import navig.llm.generate as gen_mod

    monkeypatch.setattr(gen_mod, "llm_generate", fake_llm)
    await ar._generate("tyler", lang, [], target="[a voice message saying: hola]")
    assert expected in captured["system"]


# ── what a GIF, a round video or a sticker SHOWS ─────────────────────────────
#
# Reported 2026-09-30: `tyler` on their GIF showed 👀 and then "couldn't read that
# message". A clip was read only for speech and on-screen text, so a silent GIF —
# most of what people send — came back as nothing. Measured on two real GIFs from
# the chat: None before; "a GIF — showing: a man in a suit clapping…" after.

GIF = {
    "message_id": 83,
    "from": {"id": 555},
    "animation": {"file_id": "GIF", "mime_type": "video/mp4", "file_size": 90000},
}


async def test_a_silent_gif_is_read_for_what_it_shows(monkeypatch):
    monkeypatch.setattr(an, "_download", AsyncMock(return_value=b"mp4"))
    monkeypatch.setattr(an, "analyze_video_file", AsyncMock(return_value=(None, None, None)))
    monkeypatch.setattr(an, "_describe_frame", AsyncMock(return_value="a man clapping"))
    out = await an.understand_message_media(_channel(), GIF)
    assert out == "a GIF — showing: a man clapping"


async def test_a_video_says_what_it_shows_as_well_as_what_is_said(monkeypatch):
    monkeypatch.setattr(an, "_download", AsyncMock(return_value=b"mp4"))
    monkeypatch.setattr(an, "analyze_video_file", AsyncMock(return_value=("salut", None, None)))
    monkeypatch.setattr(an, "_describe_frame", AsyncMock(return_value="a beach"))
    out = await an.understand_message_media(_channel(), VIDEO)
    assert out == "a video — showing: a beach; said in it: salut"


async def test_a_round_video_message_is_seen_and_heard(monkeypatch):
    import navig.voice.stt as stt

    monkeypatch.setattr(stt, "transcribe", AsyncMock(return_value="on y va"))
    monkeypatch.setattr(an, "_download", AsyncMock(return_value=b"mp4"))
    monkeypatch.setattr(an, "_describe_frame", AsyncMock(return_value="a kitchen"))
    note = {"message_id": 84, "from": {"id": 555}, "video_note": {"file_id": "VN", "file_size": 9}}
    out = await an.understand_message_media(_channel(), note)
    assert out == "a round video message — showing: a kitchen; saying: on y va"


async def test_an_animated_sticker_falls_back_to_its_emoji(monkeypatch):
    """A .tgs sticker is Lottie JSON — no image model can open it."""
    monkeypatch.setattr(an, "_download", AsyncMock(return_value=b"{lottie}"))
    image = AsyncMock()
    monkeypatch.setattr(an, "_analyze_image", image)
    sticker = {
        "message_id": 85,
        "from": {"id": 555},
        "sticker": {"file_id": "S", "emoji": "😂", "is_animated": True},
    }
    assert await an.understand_message_media(_channel(), sticker) == "a sticker (😂)"
    image.assert_not_awaited()


async def test_a_video_sticker_is_read_from_a_frame(monkeypatch):
    monkeypatch.setattr(an, "_download", AsyncMock(return_value=b"webm"))
    monkeypatch.setattr(an, "_describe_frame", AsyncMock(return_value="a dancing cat"))
    sticker = {
        "message_id": 86,
        "from": {"id": 555},
        "sticker": {"file_id": "S", "emoji": "🐱", "is_video": True},
    }
    out = await an.understand_message_media(_channel(), sticker)
    assert out == "a sticker showing: a dancing cat"


async def test_a_real_frame_is_extracted_and_described(monkeypatch, tmp_path):
    """The frame step itself, with a real ffmpeg when there is one."""
    import shutil
    import subprocess

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not installed")
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1", str(clip)],
        check=True,
        capture_output=True,
    )
    seen = {}

    async def fake_image(data):
        seen["bytes"] = len(data)
        return None, "a red square"

    monkeypatch.setattr(an, "_analyze_image", fake_image)
    assert await an._describe_frame(clip) == "a red square"
    assert seen["bytes"] > 0
