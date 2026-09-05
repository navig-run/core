"""On-screen text needs more than one frame — but only where someone asked for it.

The Transcript button read a single `-vf thumbnail` frame, so it caught at most
one caption. TikTok captions change through the clip, so most of the on-screen
text was simply never read.

The cost constraint shapes the design: `analyze_video_file` is ALSO the catalog's
fire-and-forget background analyser, which runs on **every** video the bot sees.
Raising the frame count for everyone would multiply that background work
silently, so the default stays at 1 and the explicit user action opts in.
"""

from __future__ import annotations

import pytest

from navig.gateway.channels import telegram_catalog_analyzer as an

# ── caption de-duplication ────────────────────────────────────────────────────


class TestMergeOcrFrames:
    def test_identical_frames_collapse(self):
        assert an._merge_ocr_frames(["BUY NOW", "BUY NOW", "BUY NOW"]) == "BUY NOW"

    def test_a_caption_that_builds_up_keeps_only_the_full_line(self):
        """The common case: a caption typed on progressively across frames."""
        out = an._merge_ocr_frames(["Hello", "Hello world", "Hello world!"])
        assert out == "Hello world!"

    def test_distinct_captions_are_all_kept_in_reading_order(self):
        out = an._merge_ocr_frames(["First caption", "Second caption", "Third"])
        assert out == "First caption\nSecond caption\nThird"

    def test_case_and_whitespace_differences_are_not_new_captions(self):
        out = an._merge_ocr_frames(["BUY   NOW", "buy now", " Buy Now "])
        assert out == "BUY NOW"

    def test_blank_and_missing_frames_are_ignored(self):
        assert an._merge_ocr_frames([None, "", "   ", "real"]) == "real"

    def test_nothing_readable_is_none_not_an_empty_string(self):
        """`None` means 'no text'; '' would read as a successful empty result."""
        assert an._merge_ocr_frames([]) is None
        assert an._merge_ocr_frames([None, "  "]) is None


# ── frame sampling: opt-in, with a safe default ───────────────────────────────


@pytest.fixture
def _stub_ffmpeg(monkeypatch, tmp_path):
    """Make the audio leg a no-op so the tests isolate the OCR path."""
    monkeypatch.setattr(an.shutil, "which", lambda name: "ffmpeg")

    async def _run(cmd):
        # Emulate the single-thumbnail branch producing a file.
        for i, part in enumerate(cmd):
            if str(part).endswith("frame.jpg"):
                from pathlib import Path

                Path(cmd[i]).write_bytes(b"jpg")
                return True
        return False

    monkeypatch.setattr(an, "_run_ffmpeg", _run)

    async def _no_transcript(path, language=None):
        return None

    monkeypatch.setattr(an, "_transcribe_path", _no_transcript)


async def test_default_stays_on_one_thumbnail(monkeypatch, tmp_path, _stub_ffmpeg):
    """The catalog's background analyser must not silently get 4x the work."""
    called = {"n": 0}

    def _extract(*a, **kw):
        called["n"] += 1
        return []

    monkeypatch.setattr("navig.media.frames.extract_frames", _extract)

    async def _ocr(paths):
        return "thumb text"

    monkeypatch.setattr(an, "_ocr_frames", _ocr)

    _, ocr, note = await an.analyze_video_file(tmp_path / "clip.mp4")

    assert called["n"] == 0, "the default must not invoke frame sampling"
    assert ocr == "thumb text"
    assert note is None


async def test_asking_for_more_frames_samples_scene_changes(
    monkeypatch, tmp_path, _stub_ffmpeg
):
    seen: dict = {}
    frames = [tmp_path / "f_001.jpg", tmp_path / "f_002.jpg"]
    for f in frames:
        f.write_bytes(b"jpg")

    def _extract(video, out_dir, **kw):
        seen.update(kw)
        return frames

    monkeypatch.setattr("navig.media.frames.extract_frames", _extract)

    captured: dict = {}

    async def _ocr(paths):
        captured["count"] = len(paths)
        return "A\nB"

    monkeypatch.setattr(an, "_ocr_frames", _ocr)

    _, ocr, _note = await an.analyze_video_file(tmp_path / "clip.mp4", max_ocr_frames=4)

    assert seen.get("mode") == "scene"
    assert seen.get("max_frames") == 4
    # The library default is 600s — a ten-minute wait behind a button tap.
    assert 0 < seen.get("timeout", 0) <= 120
    assert captured["count"] == 2
    assert ocr == "A\nB"


async def test_sampling_failure_falls_back_to_the_thumbnail(
    monkeypatch, tmp_path, _stub_ffmpeg
):
    """A missing/failing sampler must degrade, not lose the OCR entirely."""

    def _boom(*a, **kw):
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr("navig.media.frames.extract_frames", _boom)

    async def _ocr(paths):
        return "thumb text"

    monkeypatch.setattr(an, "_ocr_frames", _ocr)

    _, ocr, _note = await an.analyze_video_file(tmp_path / "clip.mp4", max_ocr_frames=4)

    assert ocr == "thumb text"


async def test_static_clip_yielding_no_frames_falls_back(
    monkeypatch, tmp_path, _stub_ffmpeg
):
    monkeypatch.setattr("navig.media.frames.extract_frames", lambda *a, **kw: [])

    async def _ocr(paths):
        return "thumb text"

    monkeypatch.setattr(an, "_ocr_frames", _ocr)

    _, ocr, _note = await an.analyze_video_file(tmp_path / "clip.mp4", max_ocr_frames=4)

    assert ocr == "thumb text"


def test_the_transcript_button_opts_in():
    """The whole point: the explicit action asks for depth.

    That the value actually reaches the analyzer is asserted end-to-end by
    `test_tiktok_briefing_and_actions.py::TestTranscriptOcrDepth`.
    """
    from navig.telegram import tiktok_actions as t

    assert t._OCR_FRAMES > 1


class TestOcrNoiseInTheMiddle:
    """A word appearing MID-caption defeats substring containment.

    Reported from a live clip: the transcript printed the same sentence twice
    because one frame's OCR read a number the other missed, in the middle —
    `что я осознал? семья и дети` vs `что я осознал? 414 семья и дети`
    ("44" misread as "414"). Neither contains the other.
    """

    RU_CLEAN = "что я осознал? семья и дети"
    RU_NOISY = "что я осознал? 414 семья и дети"

    def test_the_reported_pair_collapses_to_one_line(self):
        out = an._merge_ocr_frames([self.RU_CLEAN, self.RU_NOISY])

        assert out is not None
        assert out.count("\n") == 0, f"still duplicated:\n{out}"
        # The fuller line wins, exactly as a build-up does — we cannot know that
        # "414" is noise rather than content, so we must not throw it away.
        assert out == self.RU_NOISY

    def test_it_works_in_either_frame_order(self):
        """Scene sampling does not guarantee the clean frame comes first."""
        out = an._merge_ocr_frames([self.RU_NOISY, self.RU_CLEAN])

        assert out == self.RU_NOISY, f"order-dependent:\n{out}"

    def test_a_word_inserted_mid_caption_is_covered(self):
        out = an._merge_ocr_frames(["the quick fox", "the quick brown fox"])

        assert out == "the quick brown fox"


class TestCoverageDoesNotEatRealCaptions:
    """The rule must not merge two captions that differ in any token.

    This is why it is ordered-subsequence and not digit-normalisation plus a
    similarity threshold: normalising digits makes these pairs identical, so the
    briefing would silently lose half a slideshow.
    """

    def test_numbered_steps_all_survive(self):
        out = an._merge_ocr_frames(["Step 1", "Step 2", "Step 3"])

        assert out is not None
        assert out.splitlines() == ["Step 1", "Step 2", "Step 3"]

    def test_different_prices_survive(self):
        out = an._merge_ocr_frames(["Was $10", "Now $20"])

        assert out is not None and len(out.splitlines()) == 2

    def test_counters_survive(self):
        out = an._merge_ocr_frames(["1 of 5", "2 of 5", "3 of 5"])

        assert out is not None and len(out.splitlines()) == 3

    def test_a_reordered_sentence_is_not_treated_as_a_fragment(self):
        """Order is part of the test, so a different arrangement of the same
        words is a different caption — not a fragment of one."""
        out = an._merge_ocr_frames(["dogs chase cats", "cats chase dogs"])

        assert out is not None and len(out.splitlines()) == 2
