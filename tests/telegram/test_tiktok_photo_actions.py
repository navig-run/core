"""The card's four buttons on a TikTok **photo post** (a slideshow).

The operator shared ``https://vm.tiktok.com/ZGdxB8H3o/`` and got a card reading
only "🎵 TikTok link", then "Couldn't extract the audio." from 🎧. The link
resolves to ``/@get.man_/photo/…`` and yt-dlp's extractor matches only
``/video/``, so every action died on ``Unsupported URL``.

These drive the real ``fetch_file`` with a fake ``yt_dlp`` that behaves like the
real one — **rejecting a `/photo/` URL** — so they fail if the canonicalization
is removed rather than merely re-asserting the fake.

A slideshow also has no video stream, which the four buttons must each answer
differently: 🎧 works (there IS an audio track), ⬇️ sends the slides (sending the
audio track as a video hands over an unplayable file), 📝 reads the audio and the
slides, 🔍 briefs off the description as always.
"""
from __future__ import annotations

import sys
import types

import pytest

from navig.telegram import tiktok_actions

engine = tiktok_actions.engine

_SHORT = "https://vm.tiktok.com/ZGdxB8H3o/"
_PHOTO = "https://www.tiktok.com/@get.man_/photo/7652338755679964436"
_VIDEO = "https://www.tiktok.com/@get.man_/video/7652338755679964436"

_PHOTO_INFO = {
    "id": "7652338755679964436",
    "description": "Мы привыкли думать, что интуиция — это магия",
    "uploader": "get.man_",
    "webpage_url": _VIDEO,
    # A slideshow's ONLY format is its audio track.
    "formats": [{"format_id": "audio", "ext": "m4a", "vcodec": "none", "acodec": "aac"}],
}


#: `None` is a MEANINGFUL result here — it is how Telegram reports a rejected
#: send — so "not supplied" needs its own value or the rejection case silently
#: tests the success path.
_UNSET = object()


class _Channel:
    def __init__(self, *, photo_result=_UNSET, audio_result=_UNSET):
        self.messages: list[str] = []
        self.rich: list[str] = []
        self.photos: list[bytes] = []
        self.videos: list[bytes] = []
        self.audio: list[bytes] = []
        self._photo_result = {"message_id": 3} if photo_result is _UNSET else photo_result
        self._audio_result = {"message_id": 4} if audio_result is _UNSET else audio_result

    async def send_message(self, chat_id, text, parse_mode=None, **kw):
        self.messages.append(text)
        return {"message_id": 1}

    async def send_rich_message(self, chat_id, markdown=None, **kw):
        self.rich.append(markdown or "")
        return {"message_id": 2}

    async def send_photo(self, chat_id, data, caption=None, **kw):
        self.photos.append(data)
        return self._photo_result

    async def send_video(self, chat_id, data, caption=None, **kw):
        self.videos.append(data)
        return {"message_id": 5}

    async def send_audio(self, chat_id, data, caption=None, **kw):
        self.audio.append(data)
        return self._audio_result

    @property
    def text(self) -> str:
        return "\n".join(self.messages + self.rich)


def _clear_caches() -> None:
    """Every per-process cache in the TikTok path.

    Four of them, and each one is a channel through which one case can answer the
    next: the clip cache and its locks, resolved share links, and post metadata.
    `_info_cache` is the newest and it bit immediately — a meta cached by an
    earlier case has no `thumbnail`, so the cover tests saw no picture.
    """
    tiktok_actions._cache.clear()
    tiktok_actions._cache_locks.clear()
    tiktok_actions._IN_FLIGHT.clear()
    engine._resolved.clear()
    engine._info_cache.clear()


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Clear the per-process caches so one case cannot answer the next."""
    _clear_caches()
    # The share link resolves to a PHOTO post — the whole premise of the report.
    monkeypatch.setattr(engine, "_final_url", lambda url, **kw: _PHOTO)
    yield
    _clear_caches()


@pytest.fixture
def real_ytdlp_shape(monkeypatch):
    """A fake ``yt_dlp`` that rejects `/photo/` exactly as the real one does.

    This is the load-bearing half: a fake that accepted every URL would keep
    these tests green with the canonicalization deleted.
    """
    import os

    class _DL:
        def __init__(self, opts):
            self._outtmpl = opts.get("outtmpl", "")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            if "/photo/" in url:
                raise ValueError(f"ERROR: Unsupported URL: {url}")
            if download:
                path = self.prepare_filename(_PHOTO_INFO)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as fh:
                    fh.write(b"\x00audio-bytes")
            return _PHOTO_INFO

        def prepare_filename(self, data):
            return (self._outtmpl.replace("%(id)s", data["id"])
                                 .replace("%(ext)s", "m4a"))

    fake = types.ModuleType("yt_dlp")
    fake.YoutubeDL = _DL
    monkeypatch.setitem(sys.modules, "yt_dlp", fake)
    monkeypatch.setattr("navig_download.tiktok.ytdlp_available", lambda: True,
                        raising=False)
    return fake


# ── 🎧 Audio — the reported failure ───────────────────────────────────────────


class TestAudioButton:
    async def test_a_photo_post_yields_its_audio(self, real_ytdlp_shape):
        """The exact report: "Couldn't extract the audio." on a shared link."""
        ch = _Channel()
        await tiktok_actions._do_audio(ch, 1, _SHORT)
        assert ch.audio, f"no audio was sent; channel said: {ch.text}"
        assert "Couldn't extract the audio" not in ch.text

    async def test_it_asks_ytdlp_for_the_video_form(self, real_ytdlp_shape, monkeypatch):
        seen: list[str] = []
        real = engine.as_video_url
        monkeypatch.setattr(engine, "as_video_url",
                            lambda u: seen.append(real(u)) or real(u))
        await tiktok_actions._do_audio(_Channel(), 1, _SHORT)
        assert _VIDEO in seen


# ── ⬇️ Download — a slideshow has no video ────────────────────────────────────


class TestDownloadButton:
    async def test_a_photo_post_sends_slides_not_a_video(self, real_ytdlp_shape,
                                                         monkeypatch):
        """The format ladder ends in a bare `best`, so without this the AUDIO
        track would be uploaded as a video — an unplayable file, reported as a
        successful download."""
        monkeypatch.setattr(
            engine, "download_post_images",
            lambda url, *, dest_dir, limit=10: _write_slides(dest_dir, 2))
        ch = _Channel()
        await tiktok_actions._do_download(ch, 1, _SHORT)
        assert len(ch.photos) == 2
        assert ch.videos == [], "a slideshow must never be uploaded as a video"

    async def test_a_video_link_that_is_really_a_slideshow_also_routes(
            self, real_ytdlp_shape, monkeypatch):
        """People share `/video/` URLs for photo posts too — only the format list
        gives that away, so the URL check alone is not enough."""
        monkeypatch.setattr(engine, "_final_url", lambda url, **kw: _VIDEO)
        monkeypatch.setattr(
            engine, "download_post_images",
            lambda url, *, dest_dir, limit=10: _write_slides(dest_dir, 1))
        ch = _Channel()
        await tiktok_actions._do_download(ch, 1, _VIDEO)
        assert len(ch.photos) == 1
        assert ch.videos == []

    async def test_a_known_photo_post_moves_no_bytes_first(self, real_ytdlp_shape,
                                                           monkeypatch):
        """TikTokNoVideo would catch this anyway — but only AFTER pulling the
        audio track down. When the URL itself says `/photo/`, that download is a
        wasted request at exactly the moment TikTok is deciding whether we look
        like a bot, which is why the cheap URL check comes first."""
        fetched: list = []
        real = engine.fetch_file_async

        async def _spy(*a, **kw):
            fetched.append(kw)
            return await real(*a, **kw)

        monkeypatch.setattr(engine, "fetch_file_async", _spy)
        monkeypatch.setattr(
            engine, "download_post_images",
            lambda url, *, dest_dir, limit=10: _write_slides(dest_dir, 1))
        ch = _Channel()
        await tiktok_actions._do_download(ch, 1, _SHORT)
        assert fetched == [], "a /photo/ URL is known to be a slideshow for free"
        assert len(ch.photos) == 1

    async def test_a_capped_carousel_says_it_was_capped(self, real_ytdlp_shape,
                                                        monkeypatch):
        """Showing 10 of 30 slides silently presents part of a post as the whole."""
        monkeypatch.setattr(
            engine, "download_post_images",
            lambda url, *, dest_dir, limit=10: (_write_slides(dest_dir, 2)[0], 30))
        ch = _Channel()
        await tiktok_actions._do_download(ch, 1, _SHORT)
        assert "of 30" in ch.text

    async def test_unreadable_images_name_what_still_works(self, real_ytdlp_shape,
                                                           monkeypatch):
        monkeypatch.setattr(engine, "download_post_images",
                            lambda url, *, dest_dir, limit=10: ([], 0))
        ch = _Channel()
        await tiktok_actions._do_download(ch, 1, _SHORT)
        assert "photo post" in ch.text
        assert "Audio" in ch.text, "say what the operator can still do"

    async def test_a_rejected_upload_is_counted_not_assumed(self, real_ytdlp_shape,
                                                            monkeypatch):
        """send_photo returns None on a rejected send WITHOUT raising."""
        monkeypatch.setattr(
            engine, "download_post_images",
            lambda url, *, dest_dir, limit=10: _write_slides(dest_dir, 2))
        ch = _Channel(photo_result=None)
        await tiktok_actions._do_download(ch, 1, _SHORT)
        assert "Sent 0 of 2" in ch.text


# ── 📝 Transcript — the words are in the audio and on the slides ──────────────


class TestTranscriptButton:
    async def test_a_photo_post_reads_audio_and_slides(self, real_ytdlp_shape,
                                                       monkeypatch):
        monkeypatch.setattr(tiktok_actions, "_spoken_text",
                            _async_returning("what was said"))
        monkeypatch.setattr(tiktok_actions, "_ocr_slides", _slides("what was printed"))
        ch = _Channel()
        await tiktok_actions._do_transcript(ch, 1, _SHORT)
        assert "what was said" in ch.text
        assert "what was printed" in ch.text
        assert "Couldn't read that video" not in ch.text

    async def test_speech_alone_is_still_a_transcript(self, real_ytdlp_shape,
                                                      monkeypatch):
        monkeypatch.setattr(tiktok_actions, "_spoken_text",
                            _async_returning("only speech"))
        monkeypatch.setattr(tiktok_actions, "_ocr_slides", _async_returning(None))
        ch = _Channel()
        await tiktok_actions._do_transcript(ch, 1, _SHORT)
        assert "only speech" in ch.text

    async def test_a_missing_ocr_install_is_named_not_reported_as_empty(
            self, real_ytdlp_shape, monkeypatch):
        """'Nothing there' and 'I had no tool to look with' are different answers."""
        monkeypatch.setattr(tiktok_actions, "_spoken_text", _async_returning(None))
        monkeypatch.setattr(tiktok_actions, "_ocr_slides", _async_returning(None))
        monkeypatch.setattr("navig.core.ocr.ocr_unavailable_reason",
                            lambda: "the Tesseract binary isn't installed")
        ch = _Channel()
        await tiktok_actions._do_transcript(ch, 1, _SHORT)
        assert "Tesseract" in ch.text

    async def test_a_genuinely_empty_post_is_not_dressed_up_as_a_failure(
            self, real_ytdlp_shape, monkeypatch):
        monkeypatch.setattr(tiktok_actions, "_spoken_text", _async_returning(None))
        monkeypatch.setattr(tiktok_actions, "_ocr_slides", _async_returning(None))
        monkeypatch.setattr("navig.core.ocr.ocr_unavailable_reason", lambda: None)
        ch = _Channel()
        await tiktok_actions._do_transcript(ch, 1, _SHORT)
        assert "Nothing to read" in ch.text
        assert "Couldn't" not in ch.text


# ── the card ──────────────────────────────────────────────────────────────────


class TestCard:
    async def test_the_card_carries_the_description(self, real_ytdlp_shape):
        """The whole report: the card said '🎵 TikTok link' and nothing else."""
        ch = _Channel()
        sent = await tiktok_actions.offer_card(
            ch, 1, 99, f"look at this {_SHORT}", is_owner=True)
        assert sent is True
        card = ch.messages[0]
        assert "интуиция" in card, f"description missing from the card: {card!r}"
        assert card != "🎵 <b>TikTok link</b>"

    async def _keyboard_for(self, monkeypatch, description):
        """Offer a card for a post with *description* and return its actions."""
        monkeypatch.setitem(_PHOTO_INFO, "description", description)
        ch = _Channel()
        captured: dict = {}

        async def _send(chat_id, text, parse_mode=None, keyboard=None, **kw):
            captured["keyboard"] = keyboard
            ch.messages.append(text)
            return {"message_id": 1}

        ch.send_message = _send
        await tiktok_actions.offer_card(ch, 1, 99, _SHORT, is_owner=True)
        return {b["callback_data"].split(":")[1]
                for row in captured["keyboard"] for b in row}, ch

    async def test_the_four_core_buttons_are_always_offered(
            self, real_ytdlp_shape, monkeypatch):
        actions, _ = await self._keyboard_for(monkeypatch, "a short caption")
        assert {"dl", "an", "tr", "au"} <= actions

    async def test_a_caption_that_fits_gets_no_full_text_button(
            self, real_ytdlp_shape, monkeypatch):
        """A button that would reveal nothing teaches the operator to ignore it."""
        actions, _ = await self._keyboard_for(monkeypatch, "a short caption")
        assert "tx" not in actions

    async def test_a_caption_too_long_for_one_message_gets_one(
            self, real_ytdlp_shape, monkeypatch):
        """Telegram's ceiling is 4096; past it the card must cut, so there has to
        be a way to read the rest."""
        actions, ch = await self._keyboard_for(monkeypatch, "слово " * 1200)
        assert "tx" in actions
        assert ch.messages[0].endswith("…") or "…" in ch.messages[0]

    async def test_the_reported_caption_now_lands_whole(
            self, real_ytdlp_shape, monkeypatch):
        """1939 characters — the post from the report. It fits in one message, so
        it must arrive complete and WITHOUT a full-text button."""
        caption = "и" * 1939
        actions, ch = await self._keyboard_for(monkeypatch, caption)
        assert "tx" not in actions
        assert caption in ch.messages[0], "the caption was cut despite fitting"


# ── core and the plugin ship separately ──────────────────────────────────────


class TestPluginVersionSkew:
    """`except engine.TikTokNoVideo:` on a plugin that predates the class raises
    AttributeError *while handling* an exception — a failed download becomes a
    crash with no reply at all. Found for real: the local CI gate resolves
    `navig` from a worktree and `navig_download` from the main checkout, which is
    exactly this pairing, and it reddened a pre-existing download-cleanup test.
    """

    def test_the_real_classes_are_used_when_the_plugin_has_them(self):
        assert tiktok_actions._NO_VIDEO is engine.TikTokNoVideo
        assert tiktok_actions._UNAVAILABLE is engine.TikTokUnavailable
        assert tiktok_actions._BLOCKED is engine.TikTokBlocked

    def test_an_older_plugin_still_yields_a_valid_except_clause(self, monkeypatch):
        class _OldEngine:  # a plugin from before the class existed
            pass

        monkeypatch.setattr(tiktok_actions, "engine", _OldEngine)
        resolved = tiktok_actions._engine_error("TikTokNoVideo")
        assert resolved is tiktok_actions._EngineErrorUnsupported
        assert issubclass(resolved, BaseException), "an except clause needs a class"

    def test_a_name_that_is_not_an_exception_is_refused(self, monkeypatch):
        """`except <non-class>:` raises TypeError — a renamed symbol must not
        turn one crash into a different one."""
        class _OddEngine:
            TikTokNoVideo = "not a class"

        monkeypatch.setattr(tiktok_actions, "engine", _OddEngine)
        assert tiktok_actions._engine_error("TikTokNoVideo") is \
            tiktok_actions._EngineErrorUnsupported

    def test_the_placeholder_never_swallows_a_real_error(self):
        """It must be inert: catching anything would hide the failure it stands in
        for, which is worse than the crash it prevents."""
        with pytest.raises(ValueError):
            try:
                raise ValueError("a real failure")
            except tiktok_actions._EngineErrorUnsupported:  # noqa: BLE001
                raise AssertionError("the placeholder matched something")

    def test_no_except_clause_reads_the_engine_directly(self):
        """The durable half: every `except engine.<Name>:` is a latent crash on a
        skewed install, and this module holds all of them in the repo (measured:
        8 sites, 1 file, 2 symbols — which is why this is a focused source check
        rather than a repo-wide guard with an allowlist of the oldest names).

        A new engine exception caught here must go through `_engine_error`.
        """
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(tiktok_actions))
        offenders = [
            f"line {node.lineno}: except engine.{sub.attr}"
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler) and node.type is not None
            for sub in ast.walk(node.type)
            if isinstance(sub, ast.Attribute)
            and isinstance(sub.value, ast.Name) and sub.value.id == "engine"
        ]
        assert not offenders, (
            "resolve these through _engine_error(...) — reading the plugin at "
            f"except time crashes a version-skewed install: {offenders}"
        )


# ── helpers ───────────────────────────────────────────────────────────────────


def _write_slides(dest_dir: str, n: int) -> tuple[list[str], int]:
    from pathlib import Path

    paths = []
    for i in range(1, n + 1):
        p = Path(dest_dir) / f"{i:02d}.jpg"
        p.write_bytes(b"jpeg-bytes")
        paths.append(str(p))
    return paths, n


def _async_returning(value):
    async def _fn(*a, **kw):
        return value

    return _fn

def _slides(text, read=1, total=1):
    """The `_ocr_slides` contract: text PLUS how much of the post it covers."""
    return _async_returning(tiktok_actions._Slides(text, read, total))


async def _refused(coro):
    """Await a call that the in-flight guard must refuse *immediately*.

    Bounded on purpose. Without the guard the second caller runs the same worker
    and blocks on an Event released only afterwards — so an unbounded await turns
    "the guard is gone" into a DEADLOCK, and a hung test reports nothing while a
    failed one names the bug.
    """
    import asyncio

    try:
        return await asyncio.wait_for(coro, timeout=5)
    except TimeoutError:
        raise AssertionError(
            "the second caller was not refused — it started the work instead, "
            "which means it bypassed run_action's _IN_FLIGHT guard"
        ) from None


# ── 🎧 the audio message says what it IS ──────────────────────────────────────
#
# It arrived as `7652338755679964436.m4a`, `00:00`, captioned "🎧 via NAVIG" —
# a file with no name, no length and no way back to the post. Telegram's
# sendAudio takes `title`/`performer`/`duration` and every one was left unset.


_TRACK_META = {
    "track": "Veins of Sand", "artists": ["GTMN"], "uploader": "get.man_",
    "duration": 60, "url": _PHOTO,
}


class TestAudioLabels:
    def test_the_track_and_artist_become_title_and_performer(self):
        labels = tiktok_actions._audio_labels(_TRACK_META, "7652338755679964436.m4a")
        assert labels["title"] == "Veins of Sand"
        assert labels["performer"] == "GTMN"

    def test_the_duration_is_sent_so_telegram_stops_showing_00_00(self):
        assert tiktok_actions._audio_labels(_TRACK_META, "x.m4a")["duration"] == 60

    def test_the_filename_becomes_readable(self):
        labels = tiktok_actions._audio_labels(_TRACK_META, "7652338755679964436.m4a")
        assert labels["filename"] == "GTMN - Veins of Sand.m4a"

    def test_the_extension_is_preserved(self):
        assert tiktok_actions._audio_labels(_TRACK_META, "x.mp3")["filename"].endswith(".mp3")

    def test_a_filesystem_hostile_name_is_sanitised(self):
        labels = tiktok_actions._audio_labels(
            {"track": 'a/b:c*?"<>|', "uploader": "u"}, "x.m4a")
        assert not set(labels["filename"]) & set(r'\/:*?"<>|')

    def test_no_metadata_falls_back_to_the_original_name(self):
        """A nameless track still has to arrive."""
        labels = tiktok_actions._audio_labels(None, "765233.m4a")
        assert labels["filename"] == "765233.m4a"
        assert labels["title"] and labels["performer"] is None

    def test_a_track_with_no_artist_still_gets_a_title(self):
        labels = tiktok_actions._audio_labels({"track": "original sound"}, "x.m4a")
        assert labels["title"] == "original sound"

    def test_a_nonsense_duration_is_dropped_not_forwarded(self):
        assert tiktok_actions._audio_labels(
            {"duration": "not a number"}, "x.m4a")["duration"] is None


class TestAudioCaption:
    def test_it_names_the_track_the_creator_and_links_home(self):
        cap = tiktok_actions._audio_caption(_TRACK_META)
        assert "Veins of Sand" in cap and "GTMN" in cap
        assert "get.man_" in cap
        assert "1:00" in cap, "the length a listener wants before pressing play"
        assert _PHOTO in cap, "no way back to the post is the original complaint"

    def test_it_escapes_a_hostile_track_name(self):
        cap = tiktok_actions._audio_caption({"track": "<b>x</b>", "uploader": "u"})
        assert "<b>x</b>" not in cap and "&lt;b&gt;" in cap

    def test_it_degrades_to_something_rather_than_nothing(self):
        assert tiktok_actions._audio_caption(None).strip()


class TestAudioSendCarriesTheLabels:
    async def test_send_audio_receives_title_performer_and_duration(self):
        seen: dict = {}

        class _Ch:
            async def send_audio(self, chat_id, data, **kw):
                seen.update(kw)
                return {"message_id": 1}

        await tiktok_actions._send_audio(_Ch(), 1, b"bytes", "765.m4a", _TRACK_META)
        assert seen["title"] == "Veins of Sand"
        assert seen["performer"] == "GTMN"
        assert seen["duration"] == 60
        assert seen["filename"] == "GTMN - Veins of Sand.m4a"
        assert _PHOTO in seen["caption"]

    async def test_the_document_fallback_carries_them_too(self):
        """A channel without sendAudio must not silently lose the labelling."""
        seen: dict = {}

        class _Ch:
            async def send_document(self, chat_id, data, **kw):
                seen.update(kw)
                return {"message_id": 1}

        await tiktok_actions._send_audio(_Ch(), 1, b"bytes", "765.m4a", _TRACK_META)
        assert seen["filename"] == "GTMN - Veins of Sand.m4a"
        assert "Veins of Sand" in seen["caption"]


# ── 📄 the full caption ───────────────────────────────────────────────────────


class TestFullTextButton:
    async def test_it_sends_the_whole_caption(self, real_ytdlp_shape, monkeypatch):
        monkeypatch.setitem(_PHOTO_INFO, "description", "п" * 6000)
        ch = _Channel()
        await tiktok_actions._do_full_text(ch, 1, _SHORT)
        assert len(ch.text) > 5000, "the point of the button is the REST of it"

    async def test_it_links_back_to_the_post(self, real_ytdlp_shape, monkeypatch):
        monkeypatch.setitem(_PHOTO_INFO, "description", "some caption")
        ch = _Channel()
        await tiktok_actions._do_full_text(ch, 1, _SHORT)
        assert _PHOTO in ch.text

    async def test_an_empty_caption_is_a_real_answer(self, real_ytdlp_shape, monkeypatch):
        monkeypatch.setitem(_PHOTO_INFO, "description", "")
        ch = _Channel()
        await tiktok_actions._do_full_text(ch, 1, _SHORT)
        assert "no caption" in ch.text.lower()
        assert "couldn't" not in ch.text.lower()

    async def test_the_worker_is_registered(self):
        """A button whose action is missing from the table does nothing at all."""
        assert tiktok_actions._worker_for("tx") is tiktok_actions._do_full_text


# ── 🖼 the cover image ────────────────────────────────────────────────────────
#
# The operator's second report: "photo is missing". A slideshow's whole point is
# the picture, and the card showed none.
#
# It is a SEPARATE message on purpose: Telegram caps a photo caption at 1024
# characters against a message's 4096, so putting the card in the caption would
# put the description back behind a truncation — the thing this feature just
# stopped doing.


class _CoverChannel(_Channel):
    def __init__(self, *, photo_raises=False):
        super().__init__()
        self.order: list[str] = []
        self.captions: list = []
        self._photo_raises = photo_raises

    async def send_message(self, chat_id, text, parse_mode=None, **kw):
        self.order.append("text")
        return await super().send_message(chat_id, text, parse_mode=parse_mode, **kw)

    async def send_photo(self, chat_id, data, caption=None, **kw):
        if self._photo_raises:
            raise RuntimeError("telegram said no")
        self.order.append("photo")
        self.captions.append(caption)
        return await super().send_photo(chat_id, data, caption=caption, **kw)


@pytest.fixture
def with_cover(monkeypatch):
    monkeypatch.setitem(_PHOTO_INFO, "thumbnail", "https://cdn/cover.jpg")
    monkeypatch.setattr(engine, "fetch_image", lambda url, **kw: b"jpeg-bytes")
    monkeypatch.setattr(tiktok_actions, "_cover_enabled", lambda: True)


class TestCoverImage:
    async def test_the_picture_arrives_before_the_card(self, real_ytdlp_shape, with_cover):
        ch = _CoverChannel()
        await tiktok_actions.offer_card(ch, 1, 99, _SHORT, is_owner=True)
        assert ch.order == ["photo", "text"], ch.order
        assert ch.photos == [b"jpeg-bytes"]

    async def test_a_slideshow_says_so_on_the_picture(self, real_ytdlp_shape, with_cover):
        ch = _CoverChannel()
        await tiktok_actions.offer_card(ch, 1, 99, _SHORT, is_owner=True)
        assert ch.captions == ["🖼 photo post"]

    async def test_a_video_cover_carries_no_caption(self, real_ytdlp_shape, with_cover,
                                                    monkeypatch):
        """The card underneath already names the creator; a caption here would
        just be said twice."""
        monkeypatch.setattr(engine, "_final_url", lambda url, **kw: _VIDEO)
        monkeypatch.setitem(
            _PHOTO_INFO, "formats",
            [{"format_id": "h264", "ext": "mp4", "vcodec": "h264", "acodec": "aac"}])
        ch = _CoverChannel()
        await tiktok_actions.offer_card(ch, 1, 99, _VIDEO, is_owner=True)
        assert ch.captions == [None]

    async def test_a_post_with_no_cover_still_gets_its_card(self, real_ytdlp_shape,
                                                            monkeypatch):
        monkeypatch.setitem(_PHOTO_INFO, "thumbnail", "")
        ch = _CoverChannel()
        assert await tiktok_actions.offer_card(ch, 1, 99, _SHORT, is_owner=True) is True
        assert ch.order == ["text"]

    async def test_a_cover_that_will_not_fetch_never_costs_the_card(
            self, real_ytdlp_shape, monkeypatch):
        """The card is the answer; the picture is decoration."""
        monkeypatch.setitem(_PHOTO_INFO, "thumbnail", "https://cdn/cover.jpg")
        monkeypatch.setattr(tiktok_actions, "_cover_enabled", lambda: True)

        def _boom(url, **kw):
            raise OSError("cdn 403 — signed URL expired")

        monkeypatch.setattr(engine, "fetch_image", _boom)
        ch = _CoverChannel()
        assert await tiktok_actions.offer_card(ch, 1, 99, _SHORT, is_owner=True) is True
        assert ch.order == ["text"]

    async def test_a_cover_telegram_rejects_never_costs_the_card(
            self, real_ytdlp_shape, with_cover):
        ch = _CoverChannel(photo_raises=True)
        assert await tiktok_actions.offer_card(ch, 1, 99, _SHORT, is_owner=True) is True
        assert ch.order == ["text"]

    async def test_it_can_be_turned_off(self, real_ytdlp_shape, with_cover, monkeypatch):
        monkeypatch.setattr(tiktok_actions, "_cover_enabled", lambda: False)
        ch = _CoverChannel()
        await tiktok_actions.offer_card(ch, 1, 99, _SHORT, is_owner=True)
        assert ch.order == ["text"]

    async def test_the_toggle_survives_a_string_false(self, monkeypatch):
        """`navig config set … photo false` stores the STRING "false"."""
        class _Cfg:
            def get(self, key, default=None):
                return "false" if key == tiktok_actions._CFG_PHOTO else default

        monkeypatch.setattr("navig.core.Config", lambda: _Cfg())
        assert tiktok_actions._cover_enabled() is False

    async def test_the_card_still_carries_the_whole_caption(self, real_ytdlp_shape,
                                                            with_cover, monkeypatch):
        """The picture must not have quietly moved the text into a 1024 caption."""
        caption = "и" * 1939
        monkeypatch.setitem(_PHOTO_INFO, "description", caption)
        ch = _CoverChannel()
        await tiktok_actions.offer_card(ch, 1, 99, _SHORT, is_owner=True)
        assert caption in ch.messages[0]


# ── 🔍 Analyse — the briefing must read the SLIDES, not the backing track ─────
#
# A slideshow has no speech of its own. When the caption is too thin to brief
# from, the engine asks core for the post's words — and core answered by
# downloading the audio and transcribing it, which on a photo post is the
# licensed song playing behind the slides. The briefing then presented song
# lyrics as what the post says, labelled "📝 from speech". The words a slideshow
# carries are printed on its slides.


class TestBriefingReadsAPhotoPost:
    async def test_it_reads_the_slides_not_the_song(self, real_ytdlp_shape,
                                                    monkeypatch):
        monkeypatch.setattr(tiktok_actions, "_ocr_slides", _slides("what the slides say"))
        monkeypatch.setattr(tiktok_actions, "_spoken_text",
                            _async_returning("song lyrics"))

        read = await tiktok_actions._post_text(_SHORT)

        assert read is not None
        assert read.text == "what the slides say"
        assert read.label == "slide text"

    async def test_a_voiceover_is_still_read_when_no_slide_has_text(
            self, real_ytdlp_shape, monkeypatch):
        """Slides first, but a slideshow with a voiceover and no printed text
        must not come back empty."""
        monkeypatch.setattr(tiktok_actions, "_ocr_slides", _async_returning(None))
        monkeypatch.setattr(tiktok_actions, "_spoken_text",
                            _async_returning("the voiceover"))

        read = await tiktok_actions._post_text(_SHORT)

        assert read is not None
        assert read.text == "the voiceover"
        # …and it says so: not "speech", because on a slideshow the audio track
        # may equally well be a song.
        assert read.label == "the audio track"

    async def test_nothing_readable_stays_none(self, real_ytdlp_shape, monkeypatch):
        """None is what the engine's contract means by 'no enrichment' — an empty
        string would be handed to the model as the post's content."""
        monkeypatch.setattr(tiktok_actions, "_ocr_slides", _async_returning(None))
        monkeypatch.setattr(tiktok_actions, "_spoken_text", _async_returning("  \n "))

        assert await tiktok_actions._post_text(_SHORT) is None

    async def test_a_video_still_takes_the_speech_path(self, monkeypatch):
        monkeypatch.setattr(engine, "_final_url", lambda url, **kw: _VIDEO)
        tiktok_actions.engine._resolved.clear()

        async def _no_ocr(*a, **kw):
            raise AssertionError("a video must not be OCR'd as slides")

        monkeypatch.setattr(tiktok_actions, "_ocr_slides", _no_ocr)
        monkeypatch.setattr(tiktok_actions, "_spoken_text",
                            _async_returning("what was said"))

        read = await tiktok_actions._post_text(_VIDEO)

        assert read is not None
        assert read.label == "speech"

    async def test_the_slow_path_announces_itself(self, real_ytdlp_shape, monkeypatch):
        """Downloading the slides and OCR'ing them is seconds of silence."""
        monkeypatch.setattr(tiktok_actions, "_ocr_slides", _slides("text"))
        ch = _Channel()

        await tiktok_actions._post_text(_SHORT, channel=ch, chat_id=1)

        assert any("slides" in m for m in ch.messages), ch.messages

    async def test_analyse_labels_the_briefing_with_the_real_source(
            self, real_ytdlp_shape, monkeypatch):
        """End to end: the header over a slideshow's briefing must not claim
        speech."""
        monkeypatch.setattr(tiktok_actions, "_ocr_slides", _slides("what the slides say"))
        monkeypatch.setattr(tiktok_actions, "_spoken_text",
                            _async_returning("song lyrics"))

        async def _fake_analyse(url, **kw):
            transcript = await kw["get_transcript"]()
            return {"meta": {"uploader": "x"}, "brief": f"[{transcript}]",
                    "used_transcript": bool(transcript)}

        monkeypatch.setattr(engine, "analyse", _fake_analyse)
        ch = _Channel()

        await tiktok_actions._do_analyse(ch, 1, _SHORT)

        assert "what the slides say" in ch.text
        assert "from slide text" in ch.text
        assert "from speech" not in ch.text

    async def test_an_unenriched_briefing_claims_no_source(self, real_ytdlp_shape,
                                                           monkeypatch):
        """A caption fat enough to brief from never calls the getter — the header
        must not then claim the post's words were read."""
        async def _fake_analyse(url, **kw):
            return {"meta": {"uploader": "x"}, "brief": "b", "used_transcript": False}

        monkeypatch.setattr(engine, "analyse", _fake_analyse)
        ch = _Channel()

        await tiktok_actions._do_analyse(ch, 1, _SHORT)

        assert "📝 from" not in ch.text

    async def test_a_briefing_built_on_unreadable_slides_says_so(
            self, real_ytdlp_shape, monkeypatch):
        """OCR missing means the briefing is about to summarise the SONG. The
        label alone ("from the audio track") does not say why."""
        monkeypatch.setattr(tiktok_actions, "_ocr_slides", _async_returning(None))
        monkeypatch.setattr(tiktok_actions, "_spoken_text",
                            _async_returning("song lyrics"))
        monkeypatch.setattr("navig.core.ocr.ocr_unavailable_reason",
                            lambda: "the Tesseract binary isn't installed")

        async def _fake_analyse(url, **kw):
            t = await kw["get_transcript"]()
            return {"meta": {"uploader": "x"}, "brief": "b", "used_transcript": bool(t)}

        monkeypatch.setattr(engine, "analyse", _fake_analyse)
        ch = _Channel()

        await tiktok_actions._do_analyse(ch, 1, _SHORT)

        assert "Tesseract" in ch.text

    async def test_slides_read_with_the_wrong_language_pack_are_flagged(
            self, real_ytdlp_shape, monkeypatch):
        """The worse case: Tesseract does not decline, it returns nonsense — and
        the briefing summarises it as fact."""
        monkeypatch.setattr(tiktok_actions, "_ocr_slides", _slides("Mbi npnBbIKnn"))
        monkeypatch.setattr("navig.core.ocr.ocr_unavailable_reason", lambda: None)
        monkeypatch.setattr("navig.core.ocr.ocr_language_gap",
                            lambda: "no Russian pack is installed")

        read = await tiktok_actions._post_text(_SHORT)

        assert read is not None and read.label == "slide text"
        assert "Russian" in read.caveat

    async def test_a_healthy_ocr_install_adds_no_note(self, real_ytdlp_shape,
                                                      monkeypatch):
        """A caveat on every briefing is noise that trains the operator to skim."""
        monkeypatch.setattr(tiktok_actions, "_ocr_slides", _slides("clean"))
        monkeypatch.setattr("navig.core.ocr.ocr_unavailable_reason", lambda: None)
        monkeypatch.setattr("navig.core.ocr.ocr_language_gap", lambda: None)

        read = await tiktok_actions._post_text(_SHORT)

        assert read is not None and read.caveat == ""


# ── the 10-slide cap must not present part of a post as the whole ─────────────
#
# `download_post_images` returns (paths, total) "so a caller forced to cap can say
# it showed part of a post instead of presenting the part as the whole" — and ⬇️
# Download does exactly that. The OCR reader threw the total away, so 📝 on a
# 30-slide post read 10 and said so nowhere, and 🔍 briefed off a third of it.


class TestSlideCapIsReported:
    @staticmethod
    def _capped(monkeypatch, *, read=10, total=30):
        monkeypatch.setattr(
            engine, "download_post_images",
            lambda url, *, dest_dir, limit=10: (_write_slides(dest_dir, read)[0], total))
        monkeypatch.setattr("navig.core.ocr.extract_ocr_text_from_image_bytes",
                            lambda data: "slide words")
        monkeypatch.setattr("navig.core.ocr.ocr_unavailable_reason", lambda: None)
        monkeypatch.setattr("navig.core.ocr.ocr_language_gap", lambda: None)

    async def test_ocr_slides_carries_the_counts(self, real_ytdlp_shape, monkeypatch):
        self._capped(monkeypatch)

        slides = await tiktok_actions._ocr_slides(_SHORT)

        assert slides is not None
        assert (slides.read, slides.total) == (10, 30)
        assert "10 of 30" in slides.capped

    async def test_a_whole_post_says_nothing(self, real_ytdlp_shape, monkeypatch):
        """A note on every transcript is noise; it must fire only when capped."""
        self._capped(monkeypatch, read=3, total=3)

        slides = await tiktok_actions._ocr_slides(_SHORT)

        assert slides is not None and slides.capped == ""

    async def test_the_transcript_says_it_read_part(self, real_ytdlp_shape, monkeypatch):
        self._capped(monkeypatch)
        monkeypatch.setattr(tiktok_actions, "_spoken_text", _async_returning(None))
        ch = _Channel()

        await tiktok_actions._do_transcript(ch, 1, _SHORT)

        assert "slide words" in ch.text
        assert "10 of 30" in ch.text

    async def test_the_briefing_says_it_read_part(self, real_ytdlp_shape, monkeypatch):
        self._capped(monkeypatch)

        async def _fake_analyse(url, **kw):
            t = await kw["get_transcript"]()
            return {"meta": {"uploader": "x"}, "brief": "b", "used_transcript": bool(t)}

        monkeypatch.setattr(engine, "analyse", _fake_analyse)
        ch = _Channel()

        await tiktok_actions._do_analyse(ch, 1, _SHORT)

        assert "10 of 30" in ch.text


# ── every entry point is de-duplicated, not just the buttons ──────────────────
#
# `_IN_FLIGHT` lived inside handle_callback, so it covered ⬇️🔍📝🎧 taps and
# NEITHER of the other two ways to start the same work: an emoji reaction and the
# reply-menu action both called `_do_analyse` directly. Re-reacting ran a second
# full analysis — now slides + OCR + a paid AI briefing.


class TestEveryEntryPointIsGuarded:
    @staticmethod
    def _slow_worker(started, release):
        async def _worker(channel, chat_id, url):
            started.append(url)
            await release.wait()

        return _worker

    async def test_a_reaction_while_one_runs_is_refused(self, monkeypatch):
        import asyncio

        started, release = [], asyncio.Event()
        monkeypatch.setattr(tiktok_actions, "_do_analyse",
                            self._slow_worker(started, release))
        monkeypatch.setattr(tiktok_actions.permissions, "can_use",
                            lambda *a, **kw: True)
        monkeypatch.setattr(tiktok_actions, "_url_from_ref", lambda c, m: _SHORT)
        ch = _Channel()

        first = asyncio.create_task(
            tiktok_actions.handle_reaction(ch, 1, 9, 7, "🎵"))
        await asyncio.sleep(0)
        second = await _refused(tiktok_actions.handle_reaction(ch, 1, 9, 7, "🎵"))
        release.set()
        await first

        assert second is True, "the reaction was still handled"
        assert len(started) == 1, f"the worker ran {len(started)} times"
        assert any("Already working" in m for m in ch.messages), ch.messages

    async def test_the_reply_menu_action_shares_the_same_guard(self, monkeypatch):
        import asyncio

        started, release = [], asyncio.Event()
        monkeypatch.setattr(tiktok_actions, "_do_analyse",
                            self._slow_worker(started, release))
        ch = _Channel()

        first = asyncio.create_task(tiktok_actions.analyse_link(ch, 1, _SHORT))
        await asyncio.sleep(0)
        await _refused(tiktok_actions.analyse_link(ch, 1, _SHORT))
        release.set()
        await first

        assert len(started) == 1

    async def test_a_reaction_and_a_button_tap_collide(self, monkeypatch):
        """Same chat, same link, same action — through two different doors."""
        import asyncio

        started, release = [], asyncio.Event()
        monkeypatch.setattr(tiktok_actions, "_do_analyse",
                            self._slow_worker(started, release))
        monkeypatch.setattr(tiktok_actions.permissions, "can_use",
                            lambda *a, **kw: True)
        monkeypatch.setattr(tiktok_actions, "_url_from_ref", lambda c, m: _SHORT)
        ch = _Channel()

        first = asyncio.create_task(tiktok_actions.analyse_link(ch, 1, _SHORT))
        await asyncio.sleep(0)
        await _refused(tiktok_actions.handle_callback(ch, "tk:an:1:9", 1, 9, 7))
        release.set()
        await first

        assert len(started) == 1

    async def test_the_key_releases_even_when_the_worker_raises(self, monkeypatch):
        """A crashed worker must not wedge the action forever."""
        async def _boom(channel, chat_id, url):
            raise RuntimeError("worker died")

        monkeypatch.setattr(tiktok_actions, "_do_analyse", _boom)
        ch = _Channel()

        with pytest.raises(RuntimeError):
            await tiktok_actions.analyse_link(ch, 1, _SHORT)

        assert (1, "an", _SHORT) not in tiktok_actions._IN_FLIGHT

    async def test_a_different_link_is_not_blocked(self, monkeypatch):
        import asyncio

        started, release = [], asyncio.Event()
        monkeypatch.setattr(tiktok_actions, "_do_analyse",
                            self._slow_worker(started, release))
        ch = _Channel()

        first = asyncio.create_task(tiktok_actions.analyse_link(ch, 1, _SHORT))
        await asyncio.sleep(0)
        second = asyncio.create_task(tiktok_actions.analyse_link(ch, 1, _VIDEO))
        await asyncio.sleep(0)
        release.set()
        await first
        await second

        assert len(started) == 2, "an unrelated link must not be refused"


# ── "no speech" must not be claimed by an install that cannot listen ──────────
#
# `transcribe_audio` returns `text if success else None`, throwing away
# TranscriptionResult.error — so "faster-whisper is not installed" and "this clip
# is silent" arrive as the same empty answer. 📝 replied "no speech and no text
# detected" about a post nothing ever listened to, and 🔍 quietly briefed off a
# caption it had already judged too thin. OCR has had `_ocr_caveat` for this the
# whole time; the speech half three lines away had nothing.


def _no_stt(monkeypatch):
    monkeypatch.setattr("navig.agent.voice_input.stt_unavailable_reason",
                        lambda: "no transcription backend is installed")


def _stt_fine(monkeypatch):
    monkeypatch.setattr("navig.agent.voice_input.stt_unavailable_reason", lambda: None)


class TestSilentSttAbsenceIsNamed:
    async def test_the_photo_transcript_says_it_could_not_listen(
            self, real_ytdlp_shape, monkeypatch):
        _no_stt(monkeypatch)
        monkeypatch.setattr("navig.core.ocr.ocr_unavailable_reason", lambda: None)
        monkeypatch.setattr("navig.core.ocr.ocr_language_gap", lambda: None)
        monkeypatch.setattr(tiktok_actions, "_spoken_text", _async_returning(None))
        monkeypatch.setattr(tiktok_actions, "_ocr_slides", _slides("what the slides say"))
        ch = _Channel()

        await tiktok_actions._do_transcript(ch, 1, _SHORT)

        assert "what the slides say" in ch.text
        assert "faster-whisper" in ch.text, ch.text

    async def test_a_healthy_install_adds_no_speech_note(self, real_ytdlp_shape,
                                                         monkeypatch):
        _stt_fine(monkeypatch)
        monkeypatch.setattr("navig.core.ocr.ocr_unavailable_reason", lambda: None)
        monkeypatch.setattr("navig.core.ocr.ocr_language_gap", lambda: None)
        monkeypatch.setattr(tiktok_actions, "_spoken_text", _async_returning(None))
        monkeypatch.setattr(tiktok_actions, "_ocr_slides", _slides("printed"))
        ch = _Channel()

        await tiktok_actions._do_transcript(ch, 1, _SHORT)

        assert "faster-whisper" not in ch.text

    async def test_speech_that_WAS_read_never_carries_the_note(
            self, real_ytdlp_shape, monkeypatch):
        """The backend can be absent for the *briefing* and present here only in
        a contrived state — but the rule is simple: text was read, so no excuse."""
        _no_stt(monkeypatch)
        monkeypatch.setattr("navig.core.ocr.ocr_unavailable_reason", lambda: None)
        monkeypatch.setattr("navig.core.ocr.ocr_language_gap", lambda: None)
        monkeypatch.setattr(tiktok_actions, "_spoken_text", _async_returning("heard it"))
        monkeypatch.setattr(tiktok_actions, "_ocr_slides", _async_returning(None))
        ch = _Channel()

        await tiktok_actions._do_transcript(ch, 1, _SHORT)

        assert "heard it" in ch.text
        assert "faster-whisper" not in ch.text

    async def test_the_briefing_says_it_had_no_way_to_read_the_post(
            self, real_ytdlp_shape, monkeypatch):
        """The engine asked for the post's words because the caption was too thin,
        and got nothing — the user should know that is fixable."""
        _no_stt(monkeypatch)
        monkeypatch.setattr("navig.core.ocr.ocr_unavailable_reason",
                            lambda: "the Tesseract binary isn't installed")
        monkeypatch.setattr(tiktok_actions, "_ocr_slides", _async_returning(None))
        monkeypatch.setattr(tiktok_actions, "_spoken_text", _async_returning(None))

        async def _fake_analyse(url, **kw):
            t = await kw["get_transcript"]()
            return {"meta": {"uploader": "x"}, "brief": "b", "used_transcript": bool(t)}

        monkeypatch.setattr(engine, "analyse", _fake_analyse)
        ch = _Channel()

        await tiktok_actions._do_analyse(ch, 1, _SHORT)

        assert "Tesseract" in ch.text
        assert "faster-whisper" in ch.text

    async def test_a_fat_caption_never_triggers_the_note(self, real_ytdlp_shape,
                                                         monkeypatch):
        """The getter is never called, so there is nothing to apologise for."""
        _no_stt(monkeypatch)

        async def _fake_analyse(url, **kw):
            return {"meta": {"uploader": "x"}, "brief": "b", "used_transcript": False}

        monkeypatch.setattr(engine, "analyse", _fake_analyse)
        ch = _Channel()

        await tiktok_actions._do_analyse(ch, 1, _SHORT)

        assert "faster-whisper" not in ch.text

    async def test_a_video_post_only_gets_the_speech_note(self, monkeypatch):
        """No slides to apologise for on a video."""
        monkeypatch.setattr(engine, "_final_url", lambda url, **kw: _VIDEO)
        engine._resolved.clear()
        _no_stt(monkeypatch)
        monkeypatch.setattr("navig.core.ocr.ocr_unavailable_reason",
                            lambda: "the Tesseract binary isn't installed")

        note = await tiktok_actions._nothing_read_caveat(_VIDEO)

        assert "faster-whisper" in note
        assert "Tesseract" not in note


# ── the VIDEO branch of 📝 — reached by none of the above ─────────────────────
#
# Every case in this file resolves to a photo post, so `_do_transcript` routes
# straight to `_do_transcript_photo` and its own body — the one that calls
# `analyze_video_file` and composes the caveat — ran in NO test. Mutation testing
# found it: deleting the speech note from the video branch changed nothing.


class TestVideoTranscript:
    @staticmethod
    def _as_video(monkeypatch, *, spoken, on_screen, note=None):
        monkeypatch.setattr(engine, "_final_url", lambda url, **kw: _VIDEO)
        engine._resolved.clear()
        # The shared fixture's post is a SLIDESHOW — audio-only formats — so the
        # engine rightly raises TikTokNoVideo and `_do_transcript` falls back to
        # the photo reader. A real video has a video stream; without this the
        # branch under test is still never entered.
        monkeypatch.setitem(
            _PHOTO_INFO, "formats",
            [{"format_id": "h264", "ext": "mp4", "vcodec": "h264", "acodec": "aac"}],
        )

        async def _analyze(path, language=None, max_ocr_frames=1):
            return spoken, on_screen, note

        monkeypatch.setitem(
            sys.modules, "navig.gateway.channels.telegram_catalog_analyzer",
            types.SimpleNamespace(analyze_video_file=_analyze),
        )

    async def test_a_video_with_no_speech_and_no_stt_says_so(
            self, real_ytdlp_shape, monkeypatch):
        self._as_video(monkeypatch, spoken=None, on_screen="captions")
        _no_stt(monkeypatch)
        monkeypatch.setattr("navig.core.ocr.ocr_unavailable_reason", lambda: None)
        monkeypatch.setattr("navig.core.ocr.ocr_language_gap", lambda: None)
        ch = _Channel()

        await tiktok_actions._do_transcript(ch, 1, _VIDEO)

        assert "captions" in ch.text
        assert "faster-whisper" in ch.text, ch.text

    async def test_a_video_whose_speech_WAS_read_carries_no_excuse(
            self, real_ytdlp_shape, monkeypatch):
        self._as_video(monkeypatch, spoken="what was said", on_screen=None)
        _no_stt(monkeypatch)
        monkeypatch.setattr("navig.core.ocr.ocr_unavailable_reason", lambda: None)
        monkeypatch.setattr("navig.core.ocr.ocr_language_gap", lambda: None)
        ch = _Channel()

        await tiktok_actions._do_transcript(ch, 1, _VIDEO)

        assert "what was said" in ch.text
        assert "faster-whisper" not in ch.text

    async def test_a_healthy_install_reports_a_silent_clip_plainly(
            self, real_ytdlp_shape, monkeypatch):
        """'Nothing to read' is a REAL answer when we could actually look."""
        self._as_video(monkeypatch, spoken=None, on_screen=None)
        _stt_fine(monkeypatch)
        monkeypatch.setattr("navig.core.ocr.ocr_unavailable_reason", lambda: None)
        monkeypatch.setattr("navig.core.ocr.ocr_language_gap", lambda: None)
        ch = _Channel()

        await tiktok_actions._do_transcript(ch, 1, _VIDEO)

        assert "Nothing to read" in ch.text
        assert "faster-whisper" not in ch.text

    async def test_ffmpeg_absence_still_wins_and_says_only_that(
            self, real_ytdlp_shape, monkeypatch):
        """One actionable message, not two — without ffmpeg nothing was attempted."""
        self._as_video(monkeypatch, spoken=None, on_screen=None,
                       note="ffmpeg_unavailable")
        _no_stt(monkeypatch)
        ch = _Channel()

        await tiktok_actions._do_transcript(ch, 1, _VIDEO)

        assert "ffmpeg" in ch.text
        assert "faster-whisper" not in ch.text


# ── the slides arrive as ONE gallery, not N messages ─────────────────────────
#
# Reported from a real post (vm.tiktok.com/ZGdxSWX2E, 4 slides): "i want it
# download all images in one post media gallery". Four separate photo messages
# bury the chat and lose the fact that the images are a single post.


class _AlbumChannel(_Channel):
    def __init__(self, *, group_result=_UNSET, **kw):
        super().__init__(**kw)
        self.groups: list[list[bytes]] = []
        self._group_result = ([{"message_id": 7}] * 4) if group_result is _UNSET else group_result

    async def send_media_group(self, chat_id, photos, caption=None, **kw):
        self.groups.append(list(photos))
        self.messages.append(caption or "")
        return self._group_result


class TestSlidesGoOutAsAnAlbum:
    @staticmethod
    def _slides(monkeypatch, n, total=None):
        monkeypatch.setattr(
            engine, "download_post_images",
            lambda url, *, dest_dir, limit=10: (_write_slides(dest_dir, n)[0], total or n))

    async def test_four_slides_are_one_group(self, real_ytdlp_shape, monkeypatch):
        self._slides(monkeypatch, 4)
        ch = _AlbumChannel()

        await tiktok_actions._do_download(ch, 1, _SHORT)

        assert len(ch.groups) == 1, "the slides did not go out as one album"
        assert len(ch.groups[0]) == 4
        assert ch.photos == [], "an album must not ALSO send singles"

    async def test_a_rejected_group_still_delivers_the_slides(self, real_ytdlp_shape,
                                                              monkeypatch):
        """sendMediaGroup returns None on a rejected send without raising —
        treating that as delivered would lose every slide silently."""
        self._slides(monkeypatch, 3)
        ch = _AlbumChannel(group_result=None)

        await tiktok_actions._do_download(ch, 1, _SHORT)

        assert len(ch.groups) == 1
        assert len(ch.photos) == 3, "the fallback did not run"

    async def test_a_channel_without_albums_still_works(self, real_ytdlp_shape,
                                                        monkeypatch):
        """Every older channel and every test double lacks the method."""
        self._slides(monkeypatch, 3)
        ch = _Channel()

        await tiktok_actions._do_download(ch, 1, _SHORT)

        assert len(ch.photos) == 3

    async def test_a_single_slide_is_not_a_group(self, real_ytdlp_shape, monkeypatch):
        """Telegram rejects a one-item media group; a lone photo stays a photo."""
        self._slides(monkeypatch, 1)
        ch = _AlbumChannel()

        await tiktok_actions._do_download(ch, 1, _SHORT)

        assert ch.groups == []
        assert len(ch.photos) == 1

    async def test_the_cap_notice_survives_the_album(self, real_ytdlp_shape, monkeypatch):
        """A capped post still says so — the album must not swallow that."""
        self._slides(monkeypatch, 10, total=30)
        ch = _AlbumChannel(group_result=[{"message_id": 7}] * 10)

        await tiktok_actions._do_download(ch, 1, _SHORT)

        assert len(ch.groups) == 1
        assert "first 10 of 30" in ch.text, ch.text


# ── an age-gated post says what actually fixes it ────────────────────────────
#
# vm.tiktok.com/ZGdxAM5DG resolves to a /video/ TikTok serves only to a logged-in
# account: "This post may not be comfortable for some audiences. Log in for
# access." The card came back bare and 📝 said "Couldn't read that video" — no
# hint that a login is the difference. It is NOT a bot-wall: waiting never clears
# an age gate, so the bot-wall answer ("try again shortly") is actively wrong.


class TestLoginGatedPost:
    """⚠ The remedy is the ENV VAR, not `navig tt login`. Measured: the vaulted
    session reaches only the browser tier — `_download_mixed` hands `session_host`
    to the photo path and gives the yt-dlp video path no session and no cookies —
    so for an age-gated /video/ post, logging in changes nothing. An earlier
    version of this hint said "run navig tt login", which sent the operator into a
    login flow that could not have fixed their download.
    """
    @staticmethod
    def _gated(monkeypatch):
        # Built from the ENGINE's class, not from `tiktok_actions._LOGIN`. Using
        # the module's own binding makes these tests self-consistent with whatever
        # it resolved to — including the inert placeholder a broken name yields —
        # so the wiring that matters would go untested. (Mutation-proved: renaming
        # the resolved class changed nothing until this line did.)
        exc = engine.TikTokLoginRequired(
            "This post may not be comfortable for some audiences. Log in for access.")

        def _raise(*a, **kw):
            raise exc

        monkeypatch.setattr(engine, "info", _raise)
        return exc

    async def test_the_card_says_why_it_is_empty(self, real_ytdlp_shape, monkeypatch):
        self._gated(monkeypatch)
        ch = _Channel()

        assert await tiktok_actions.offer_card(ch, 1, 42, _SHORT, is_owner=True) is True

        assert "logged-in" in ch.text, ch.text
        assert "NAVIG_TIKTOK_COOKIES_FROM_BROWSER" not in ch.text
        assert "navig tt login" in ch.text

    async def test_the_transcript_names_the_remedy(self, real_ytdlp_shape, monkeypatch):
        exc = self._gated(monkeypatch)

        # `_fetched` is an async CONTEXT MANAGER — `async with _fetched(url)` calls
        # it first, so a plain function that raises propagates before the `with`.
        # An `async def` here yields a coroutine and fails differently.
        def _raise(*a, **kw):
            raise exc

        monkeypatch.setattr(tiktok_actions, "_fetched", _raise)
        ch = _Channel()

        await tiktok_actions._do_transcript(ch, 1, _VIDEO)

        assert "navig tt login" in ch.text, ch.text
        assert "Couldn't read that video" not in ch.text

    async def test_it_is_not_reported_as_a_bot_wall(self, real_ytdlp_shape, monkeypatch):
        """"Try again shortly" sends the operator off retrying something that can
        never succeed."""
        exc = self._gated(monkeypatch)

        async def _raise(*a, **kw):
            raise exc

        monkeypatch.setattr(engine, "analyse", _raise)
        ch = _Channel()

        await tiktok_actions._do_analyse(ch, 1, _SHORT)

        assert "navig tt login" in ch.text
        assert "bot-wall" not in ch.text
        assert "Try again shortly" not in ch.text


# ── an unreadable response is a third cause, and it is AMBIGUOUS ─────────────
#
# Found during the closure pass: every download failed with "Unexpected response
# from webpage request" while metadata still resolved, so ⬇️🎧📝 all answered
# "Couldn't …" with nothing actionable.
#
# ⚠ The cause is AMBIGUOUS and the tests say so. The same yt-dlp build fetched
# this post successfully and then failed on it repeatedly twenty minutes later
# with no version, option, cache or proxy change — so "your downloader is out of
# date" would be confidently wrong most of the time. The message leads with the
# likely cause (transient) and keeps the update as the fallback.


class TestUnreadableResponse:
    _MSG = ("ERROR: [TikTok] 7652338755679964436: Unexpected response from webpage "
            "request; please report this issue on https://github.com/yt-dlp/yt-dlp/"
            "issues?q= , filling out the appropriate issue template. Confirm you are "
            "on the latest version using yt-dlp -U")

    @staticmethod
    def _raising(exc):
        def _raise(*a, **kw):
            raise exc
        return _raise

    async def test_the_card_explains_instead_of_going_bare(
            self, real_ytdlp_shape, monkeypatch):
        monkeypatch.setattr(engine, "info",
                            self._raising(engine.TikTokUnreadableResponse(self._MSG)))
        ch = _Channel()

        assert await tiktok_actions.offer_card(ch, 1, 42, _SHORT, is_owner=True) is True

        assert "could not read" in ch.text, ch.text
        # …and the card still carries its buttons: offer_card OWNS the message, so
        # bailing out would leave the user with nothing at all.
        assert ch.messages, "no card was sent"

    async def test_the_audio_button_names_the_remedy(self, real_ytdlp_shape,
                                                     monkeypatch):
        monkeypatch.setattr(tiktok_actions, "_fetched",
                            self._raising(engine.TikTokUnreadableResponse(self._MSG)))
        ch = _Channel()

        await tiktok_actions._do_audio(ch, 1, _SHORT)

        assert "could not read" in ch.text
        assert "Couldn't extract the audio" not in ch.text

    async def test_it_is_not_reported_as_a_bot_wall(self, real_ytdlp_shape,
                                                    monkeypatch):
        """It must not be dressed as a bot-wall: `TikTokBlocked` escalates to the
        browser tier, and this failure has already exhausted that page."""
        monkeypatch.setattr(tiktok_actions, "_fetched",
                            self._raising(engine.TikTokUnreadableResponse(self._MSG)))
        ch = _Channel()

        await tiktok_actions._do_download(ch, 1, _VIDEO)

        assert "could not read" in ch.text
        assert "bot-wall" not in ch.text
        # Both remedies are offered, in the order the evidence supports.
        assert "temporary" in ch.text and "yt-dlp" in ch.text
