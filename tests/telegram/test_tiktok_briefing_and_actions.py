"""The briefing header, the language control, and the Transcript / Audio actions.

Operator feedback on the shipped card, point by point:

* the briefing ended with a "Worth watching?" verdict — noise, the reader already
  opened it;
* it was written in English about a Russian video, with no way to change that;
* the header printed a permanent ``🌍 country n/a`` and none of the stats,
  author handle, date or duration that ARE known;
* there was no way to get the spoken content or the audio out of a clip.
"""

from __future__ import annotations

import os
import shutil

import pytest

from navig.telegram import tiktok_actions

_URL = "https://vm.tiktok.com/ZGdxryFmF"


class _Channel:
    def __init__(self, audio_result=None):
        self.messages: list[str] = []
        self.rich: list[str] = []
        self.audio: list[bytes] = []
        self._audio_result = audio_result

    async def send_message(self, chat_id, text, parse_mode=None, **kw):
        self.messages.append(text)
        return {"message_id": 1}

    async def send_rich_message(self, chat_id, markdown=None, **kw):
        self.rich.append(markdown or "")
        return {"message_id": 2}

    async def send_audio(self, chat_id, data, caption=None, **kw):
        self.audio.append(data)
        return self._audio_result


# ── header ────────────────────────────────────────────────────────────────────


class TestBriefHeader:
    def test_shows_stats_author_date_and_duration(self):
        out = tiktok_actions._brief_markdown(
            {
                "uploader": "tilga_photo", "uploader_id": "tilga_photo",
                "view_count": 134_300, "like_count": 8_422,
                "comment_count": 243, "repost_count": 1_981,
                "duration": 17, "upload_date": "20260804",
                "url": "https://vm.tiktok.com/X",
            },
            "BODY",
        )
        assert "tilga_photo" in out
        assert "134,300" in out and "8,422" in out and "243" in out and "1,981" in out
        assert "2026-08-04" in out
        assert "17s" in out
        assert "https://vm.tiktok.com/X" in out
        assert out.endswith("BODY")

    def test_unknown_country_is_omitted_not_printed_as_na(self):
        out = tiktok_actions._brief_markdown({"uploader": "x"}, "BODY")
        assert "n/a" not in out, "a permanent placeholder crowds out real fields"
        assert "🌍" not in out

    def test_known_country_is_shown(self):
        out = tiktok_actions._brief_markdown(
            {"uploader": "x", "country": "Latvia"}, "BODY"
        )
        assert "Latvia" in out

    def test_handle_is_only_added_when_it_differs_from_the_name(self):
        same = tiktok_actions._brief_markdown(
            {"uploader": "tilga_photo", "uploader_id": "tilga_photo"}, "B"
        )
        assert same.count("tilga_photo") == 1
        differs = tiktok_actions._brief_markdown(
            {"uploader": "Tilga Photography", "uploader_id": "tilga_photo"}, "B"
        )
        assert "@tilga_photo" in differs

    def test_duration_over_a_minute_is_mm_ss(self):
        out = tiktok_actions._brief_markdown({"uploader": "x", "duration": 95}, "B")
        assert "1:35" in out

    @pytest.mark.parametrize("bad", [None, "", "n/a", -1])
    def test_unusable_duration_is_dropped(self, bad):
        out = tiktok_actions._brief_markdown({"uploader": "x", "duration": bad}, "B")
        assert "⏱" not in out


# ── language ──────────────────────────────────────────────────────────────────


class TestLanguage:
    def test_default_is_auto(self, monkeypatch):
        monkeypatch.setattr(
            "navig.core.Config", lambda: type("C", (), {"get": lambda s, k, d=None: d})()
        )
        assert tiktok_actions._language() == ""

    def test_a_configured_language_is_returned(self, monkeypatch):
        monkeypatch.setattr(
            "navig.core.Config",
            lambda: type("C", (), {"get": lambda s, k, d=None: "Russian"})(),
        )
        assert tiktok_actions._language() == "Russian"

    async def test_the_configured_language_reaches_the_engine(self, monkeypatch):
        """Core's job is to READ the setting and pass it down. What the engine
        then DOES with it is asserted in the plugin's own suite, which runs
        against the plugin source — asserting it from here would only ever test
        whichever copy of navig_download happens to be installed."""
        monkeypatch.setattr(
            "navig.core.Config",
            lambda: type("C", (), {"get": lambda s, k, d=None: "Russian"})(),
        )
        seen: dict = {}

        async def _fake_analyse(url, **kw):
            seen.update(kw)
            return {"meta": {"uploader": "x"}, "brief": "body"}

        monkeypatch.setattr(tiktok_actions.engine, "analyse", _fake_analyse)

        await tiktok_actions._do_analyse(_Channel(), 1, _URL)

        assert seen.get("language") == "Russian"

    async def test_auto_passes_no_language_so_the_engine_mirrors_the_source(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            "navig.core.Config",
            lambda: type("C", (), {"get": lambda s, k, d=None: "auto"})(),
        )
        seen: dict = {}

        async def _fake_analyse(url, **kw):
            seen.update(kw)
            return {"meta": {"uploader": "x"}, "brief": "body"}

        monkeypatch.setattr(tiktok_actions.engine, "analyse", _fake_analyse)

        await tiktok_actions._do_analyse(_Channel(), 1, _URL)

        assert seen.get("language") is None


# ── buttons ───────────────────────────────────────────────────────────────────


async def test_card_offers_all_four_actions(monkeypatch):
    monkeypatch.setattr(tiktok_actions, "enabled", lambda: True)
    monkeypatch.setattr(tiktok_actions.engine, "info", lambda url: {"title": "c"})
    monkeypatch.setattr(tiktok_actions.engine, "render_card", lambda meta: "card")

    captured = {}

    class _Ch:
        allowed_users = {42}

        async def send_message(self, chat_id, text, parse_mode=None, **kw):
            captured.update(kw)
            return {"message_id": 1}

    await tiktok_actions.offer_card_dm(_Ch(), 1, 2, _URL, user_id=42)

    labels = [b["text"] for row in captured["keyboard"] for b in row]
    assert labels == ["⬇️ Download", "🔍 Analyse", "📝 Transcript", "🎧 Audio"]
    data = [b["callback_data"] for row in captured["keyboard"] for b in row]
    assert all(d.startswith("tk:") for d in data)


# ── audio ─────────────────────────────────────────────────────────────────────


def _path_from(messages) -> str:
    """The path we printed to the user, out of the <code>…</code> in the reply."""
    import html as _h
    import re

    for m in messages:
        found = re.search(r"<code>(.+?)</code>", m)
        if found:
            return _h.unescape(found.group(1))
    raise AssertionError(f"no path in any message: {messages}")


@pytest.fixture
def keep_dir(monkeypatch, tmp_path):
    """`clip.keep()` moves into `media_dir(...)`; never touch the real one."""
    dest = tmp_path / "kept"
    monkeypatch.setattr("navig.platform.paths.media_dir", lambda kind: dest / kind)
    return dest


@pytest.fixture
def audio_file(tmp_path):
    p = tmp_path / "clip.m4a"
    p.write_bytes(b"a" * 512)
    return p


def _patch_audio_fetch(monkeypatch, path):
    """Write INTO dest_dir, as the real engine does — the caller owns that
    directory now and removes it, so a fake that ignores it tests nothing."""
    seen: dict = {}

    async def _fetch(url, *, dest_dir=None, audio_only=False, **kw):
        assert audio_only is True, "the audio action must not pull the whole video"
        seen["dest_dir"] = dest_dir
        target = os.path.join(dest_dir, os.path.basename(str(path)))
        shutil.copyfile(str(path), target)
        return target

    monkeypatch.setattr(tiktok_actions.engine, "fetch_file_async", _fetch)
    return seen


class TestAudioAction:
    async def test_sends_the_audio_and_cleans_up(self, monkeypatch, audio_file):
        seen = _patch_audio_fetch(monkeypatch, audio_file)
        ch = _Channel(audio_result={"message_id": 3})

        await tiktok_actions._do_audio(ch, 1, _URL)

        assert ch.audio, "no audio was sent"
        # The DIRECTORY, not just the file: removing only the file is what left
        # 25 empty `navig_tiktok_*` dirs behind, one per action ever run.
        assert not os.path.exists(seen["dest_dir"]), "the fetch directory leaked"

    async def test_rejected_upload_keeps_the_file(self, monkeypatch, audio_file, keep_dir):
        seen = _patch_audio_fetch(monkeypatch, audio_file)
        ch = _Channel(audio_result=None)

        await tiktok_actions._do_audio(ch, 1, _URL)

        assert any("rejected the upload" in m for m in ch.messages), ch.messages
        saved = _path_from(ch.messages)
        assert os.path.exists(saved), "the only copy was discarded"
        assert not os.path.exists(seen["dest_dir"]), "the fetch directory leaked"


# ── transcript ────────────────────────────────────────────────────────────────


def _patch_video_fetch(monkeypatch, path):
    seen: dict = {}

    async def _fetch(url, *, dest_dir=None, audio_only=False, **kw):
        assert not audio_only, (
            "on-screen text lives in the pixels — the transcript needs the video"
        )
        seen["dest_dir"] = dest_dir
        target = os.path.join(dest_dir, os.path.basename(str(path)))
        shutil.copyfile(str(path), target)
        return target

    monkeypatch.setattr(tiktok_actions.engine, "fetch_file_async", _fetch)
    return seen


def _patch_analyzer(monkeypatch, result, *, seen: dict | None = None):
    # Mirror the real signature: a fake that accepts **kwargs would keep passing
    # while the caller sent something the analyzer never reads.
    async def _analyze(path, language=None, max_ocr_frames=1):
        if seen is not None:
            seen["language"] = language
            seen["max_ocr_frames"] = max_ocr_frames
        return result

    monkeypatch.setattr(
        "navig.gateway.channels.telegram_catalog_analyzer.analyze_video_file", _analyze
    )


class TestTranscriptAction:
    async def test_posts_both_spoken_words_and_on_screen_text(
        self, monkeypatch, audio_file
    ):
        seen = _patch_video_fetch(monkeypatch, audio_file)
        _patch_analyzer(monkeypatch, ("hello there", "BUY NOW", None))
        ch = _Channel()

        await tiktok_actions._do_transcript(ch, 1, _URL)

        body = "\n".join(ch.rich)
        assert "hello there" in body
        assert "BUY NOW" in body, "on-screen text was requested and must be shown"
        assert not os.path.exists(seen["dest_dir"]), "the fetch directory leaked"

    async def test_speech_only_clip_omits_the_empty_section(
        self, monkeypatch, audio_file
    ):
        _patch_video_fetch(monkeypatch, audio_file)
        _patch_analyzer(monkeypatch, ("just talking", "", None))
        ch = _Channel()

        await tiktok_actions._do_transcript(ch, 1, _URL)

        body = "\n".join(ch.rich)
        assert "just talking" in body
        assert "On-screen text" not in body

    async def test_nothing_found_is_reported_as_nothing_not_as_failure(
        self, monkeypatch, audio_file
    ):
        """A silent clip with no captions is a real answer; calling it an error
        sends the operator hunting a configuration problem that does not exist."""
        _patch_video_fetch(monkeypatch, audio_file)
        _patch_analyzer(monkeypatch, (None, None, None))
        ch = _Channel()

        await tiktok_actions._do_transcript(ch, 1, _URL)

        assert any("Nothing to read" in m for m in ch.messages), ch.messages
        assert not any("Couldn't" in m for m in ch.messages)

    async def test_missing_ffmpeg_names_the_dependency(self, monkeypatch, audio_file):
        """'nothing found' and 'I had no tool to look with' are different problems."""
        _patch_video_fetch(monkeypatch, audio_file)
        _patch_analyzer(monkeypatch, (None, None, "ffmpeg_unavailable"))
        ch = _Channel()

        await tiktok_actions._do_transcript(ch, 1, _URL)

        assert any("ffmpeg" in m for m in ch.messages), ch.messages


def _async_value(v):
    async def _inner():
        return v

    return _inner()


# ── the briefing is transcript-aware ──────────────────────────────────────────


class TestBriefingUsesSpeech:
    async def test_analyse_supplies_a_transcript_getter(self, monkeypatch):
        """The engine only asks for speech when the caption is thin — but it can
        only ask if core actually hands it a way to get one. This is the wiring
        that makes `transcript=` reachable rather than a dead parameter."""
        seen: dict = {}

        async def _fake_analyse(url, **kw):
            seen.update(kw)
            return {"meta": {"uploader": "x"}, "brief": "b", "used_transcript": False}

        monkeypatch.setattr(tiktok_actions.engine, "analyse", _fake_analyse)

        await tiktok_actions._do_analyse(_Channel(), 1, _URL)

        getter = seen.get("get_transcript")
        assert callable(getter), f"no transcript getter passed: {sorted(seen)}"

    async def test_the_getter_pulls_audio_only_and_cleans_up(
        self, monkeypatch, audio_file
    ):
        seen = _patch_audio_fetch(monkeypatch, audio_file)
        monkeypatch.setattr(
            "navig.agent.voice_input.transcribe_audio",
            lambda p, language=None: _async_value("spoken words"),
        )

        assert await tiktok_actions._spoken_text(_URL) == "spoken words"
        assert not os.path.exists(seen["dest_dir"]), "the fetch directory leaked"

    async def test_the_getter_returns_none_instead_of_raising(
        self, monkeypatch, audio_file
    ):
        """Enrichment must never break the briefing it is enriching."""
        async def _boom(url, **kw):
            raise RuntimeError("network down")

        monkeypatch.setattr(tiktok_actions.engine, "fetch_file_async", _boom)

        assert await tiktok_actions._spoken_text(_URL) is None

    async def test_the_header_says_when_the_briefing_came_from_speech(self):
        with_speech = tiktok_actions._brief_markdown(
            {"uploader": "x"}, "BODY", source="speech"
        )
        without = tiktok_actions._brief_markdown({"uploader": "x"}, "BODY")
        assert "from speech" in with_speech
        assert "from speech" not in without

    async def test_the_header_names_the_source_it_was_given(self):
        """Not a fixed word: a briefing read off a slideshow's slides must not
        claim it came from speech."""
        out = tiktok_actions._brief_markdown(
            {"uploader": "x"}, "BODY", source="slide text"
        )
        assert "from slide text" in out
        assert "from speech" not in out

    async def test_the_slow_path_announces_itself(self, monkeypatch, audio_file):
        """Transcribing adds a download plus STT to a tap that otherwise sits
        silent. The notice fires only here, so it cannot appear on the fast path."""
        _patch_audio_fetch(monkeypatch, audio_file)
        monkeypatch.setattr(
            "navig.agent.voice_input.transcribe_audio",
            lambda p, language=None: _async_value("words"),
        )
        ch = _Channel()

        await tiktok_actions._spoken_text(_URL, channel=ch, chat_id=1)

        assert any("listening to the audio" in m for m in ch.messages), ch.messages

    async def test_no_notice_without_a_channel(self, monkeypatch, audio_file):
        """The getter stays usable from the CLI, where there is nobody to tell."""
        _patch_audio_fetch(monkeypatch, audio_file)
        monkeypatch.setattr(
            "navig.agent.voice_input.transcribe_audio",
            lambda p, language=None: _async_value("words"),
        )

        assert await tiktok_actions._spoken_text(_URL) == "words"

    async def test_a_failing_notice_does_not_stop_the_transcription(
        self, monkeypatch, audio_file
    ):
        _patch_audio_fetch(monkeypatch, audio_file)
        monkeypatch.setattr(
            "navig.agent.voice_input.transcribe_audio",
            lambda p, language=None: _async_value("words"),
        )

        class _Broken:
            async def send_message(self, *a, **kw):
                raise RuntimeError("telegram down")

        assert await tiktok_actions._spoken_text(_URL, channel=_Broken(), chat_id=1) == "words"


class TestTranscriptLanguage:
    async def test_the_configured_language_reaches_the_transcriber(
        self, monkeypatch, audio_file
    ):
        """Passing nothing used to inherit STTConfig's hard-coded "en", so a
        Russian clip was transcribed as if it were English."""
        seen: dict = {}
        _patch_video_fetch(monkeypatch, audio_file)
        _patch_analyzer(monkeypatch, ("x", None, None), seen=seen)
        monkeypatch.setattr(
            "navig.core.Config",
            lambda: type("C", (), {"get": lambda s, k, d=None: "Russian"})(),
        )

        await tiktok_actions._do_transcript(_Channel(), 1, _URL)

        assert seen["language"] == "Russian"

    async def test_auto_passes_none_so_the_provider_detects(
        self, monkeypatch, audio_file
    ):
        seen: dict = {}
        _patch_video_fetch(monkeypatch, audio_file)
        _patch_analyzer(monkeypatch, ("x", None, None), seen=seen)
        monkeypatch.setattr(
            "navig.core.Config",
            lambda: type("C", (), {"get": lambda s, k, d=None: ""})(),
        )

        await tiktok_actions._do_transcript(_Channel(), 1, _URL)

        assert seen["language"] is None, "auto must reach the provider as detect"


class TestTranscriptOcrDepth:
    async def test_the_button_asks_for_more_than_one_frame(
        self, monkeypatch, audio_file
    ):
        """TikTok captions change through the clip. One `-vf thumbnail` frame —
        what the catalog's background pass uses — reads at most one of them."""
        seen: dict = {}
        _patch_video_fetch(monkeypatch, audio_file)
        _patch_analyzer(monkeypatch, ("x", None, None), seen=seen)

        await tiktok_actions._do_transcript(_Channel(), 1, _URL)

        assert seen["max_ocr_frames"] > 1, "the explicit action must opt into depth"


class TestTranscriptOcrUnavailable:
    """Unlike ffmpeg, a missing OCR backend costs only half the answer — so the
    reply is additive: show the speech, then say the other half wasn't attempted.
    Reporting "no on-screen text detected" would be a claim nobody verified."""

    async def test_speech_is_still_delivered_and_the_gap_is_named(
        self, monkeypatch, audio_file
    ):
        _patch_video_fetch(monkeypatch, audio_file)
        _patch_analyzer(monkeypatch, ("hello there", None, "ocr_unavailable"))
        monkeypatch.setattr(
            "navig.core.ocr.ocr_unavailable_reason", lambda: "pytesseract isn't installed"
        )
        ch = _Channel()

        await tiktok_actions._do_transcript(ch, 1, _URL)

        assert any("hello there" in m for m in ch.rich), "speech must not be lost"
        gap = "\n".join(ch.messages)
        assert "pytesseract" in gap, "the reason must be named"
        assert "navig[ocr]" in gap, "and it must say how to fix it"
        assert "no on-screen text detected" not in gap.lower()

    async def test_a_silent_clip_with_no_ocr_does_not_claim_emptiness(
        self, monkeypatch, audio_file
    ):
        _patch_video_fetch(monkeypatch, audio_file)
        _patch_analyzer(monkeypatch, (None, None, "ocr_unavailable"))
        monkeypatch.setattr(
            "navig.core.ocr.ocr_unavailable_reason", lambda: "pytesseract isn't installed"
        )
        ch = _Channel()

        await tiktok_actions._do_transcript(ch, 1, _URL)

        body = "\n".join(ch.messages)
        assert "Nothing to read" not in body, "that would claim a check nobody ran"
        assert "pytesseract" in body
