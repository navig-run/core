"""Vertical assembly — reframing, joining, captioning and scoring a short-form clip.

Two things here are pure and therefore tested without ffmpeg, because both are the kind
of bug that produces an error message pointing somewhere else entirely:

* :func:`escape_filter_path` — ffmpeg's ``subtitles`` filter parses its own argument, so
  an unescaped Windows path ends the argument at ``C:`` and fails complaining about an
  option it never received.
* :func:`vertical_filter` — it must *fill* the frame. A ``force_original_aspect_ratio``
  of ``decrease`` letterboxes instead, which on a phone reads as a broken upload and is
  invisible in any unit test that only checks the output resolution.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from navig.media.video_edit import (
    VideoEditError,
    escape_filter_path,
    ffmpeg_available,
    join,
    mix,
    probe,
    still,
    to_vertical,
    vertical_filter,
)

requires_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")


def _clip(path: Path, seconds: float, *, w: int = 640, h: int = 360, fps: int = 30) -> Path:
    subprocess.run(
        [shutil.which("ffmpeg"), "-nostdin", "-y", "-f", "lavfi",
         "-i", f"testsrc=size={w}x{h}:rate={fps}:duration={seconds:g}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, check=True,
    )
    return path


def _tone(path: Path, seconds: float) -> Path:
    subprocess.run(
        [shutil.which("ffmpeg"), "-nostdin", "-y", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={seconds:g}",
         "-c:a", "libmp3lame", "-q:a", "4", str(path)],
        capture_output=True, check=True,
    )
    return path


# ── pure: filter-string construction ──────────────────────────────────────────


def test_a_windows_path_is_escaped_for_the_filter_parser() -> None:
    escaped = escape_filter_path(r"C:\work\ép 01.ass")
    assert "\\" not in escaped.replace(r"\:", ""), "backslashes must not survive"
    assert r"C\:" in escaped, "the drive colon must be escaped or the argument ends there"


def test_escaping_leaves_a_posix_path_usable() -> None:
    assert escape_filter_path("/tmp/a.ass") == "/tmp/a.ass"


def test_an_apostrophe_in_a_path_is_escaped() -> None:
    assert r"\'" in escape_filter_path("/tmp/l'intro.ass")


def test_the_vertical_filter_fills_rather_than_letterboxes() -> None:
    # `decrease` would fit-inside and pad; on a phone that is black bars.
    vf = vertical_filter()
    assert "force_original_aspect_ratio=increase" in vf
    assert "crop=1080:1920" in vf
    assert "decrease" not in vf and "pad=" not in vf


# ── guards (no ffmpeg needed) ─────────────────────────────────────────────────


def test_joining_nothing_is_refused(tmp_path: Path) -> None:
    with pytest.raises(VideoEditError, match="empty"):
        join([], tmp_path / "out.mp4")


def test_a_missing_part_is_named(tmp_path: Path) -> None:
    with pytest.raises(VideoEditError, match="ghost.mp4"):
        join([tmp_path / "ghost.mp4"], tmp_path / "out.mp4")


def test_a_still_of_no_length_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="secs"):
        still(tmp_path / "x.png", tmp_path / "out.mp4", secs=0)


def test_mixing_nothing_onto_a_video_is_refused(tmp_path: Path) -> None:
    with pytest.raises(VideoEditError, match="voice, a bed, or both"):
        mix(tmp_path / "v.mp4", tmp_path / "out.mp4")


def test_missing_subtitles_are_reported_before_ffmpeg_runs(tmp_path: Path) -> None:
    from navig.media.video_edit import burn_captions

    with pytest.raises(VideoEditError, match="subtitle file not found"):
        burn_captions(tmp_path / "v.mp4", tmp_path / "nope.ass", tmp_path / "out.mp4")


# ── real renders ──────────────────────────────────────────────────────────────


@requires_ffmpeg
def test_probe_refuses_a_file_that_is_not_video(tmp_path: Path) -> None:
    junk = tmp_path / "notvideo.mp4"
    junk.write_text("nope", encoding="utf-8")
    with pytest.raises(VideoEditError):
        probe(junk)


@requires_ffmpeg
def test_reframing_a_landscape_clip_produces_exact_vertical(tmp_path: Path) -> None:
    src = _clip(tmp_path / "wide.mp4", 1.0, w=640, h=360)
    result = to_vertical(src, tmp_path / "tall.mp4")
    assert (result.width, result.height) == (1080, 1920)
    assert result.duration_s == pytest.approx(1.0, abs=0.2)


@requires_ffmpeg
def test_joined_durations_add_up(tmp_path: Path) -> None:
    parts = [_clip(tmp_path / "a.mp4", 1.0), _clip(tmp_path / "b.mp4", 2.0)]
    result = join(parts, tmp_path / "joined.mp4")
    assert result.duration_s == pytest.approx(3.0, abs=0.3)


@requires_ffmpeg
def test_a_single_part_is_copied_verbatim(tmp_path: Path) -> None:
    part = _clip(tmp_path / "a.mp4", 1.0)
    out = tmp_path / "one.mp4"
    result = join([part], out)
    assert result.filters == "copy"
    assert out.read_bytes() == part.read_bytes()


@requires_ffmpeg
def test_joining_survives_mismatched_clips(tmp_path: Path) -> None:
    # Captured b-roll and a rendered still rarely agree on resolution; the copy path
    # cannot carry that, and the duration check is what catches it.
    parts = [
        _clip(tmp_path / "a.mp4", 1.0, w=640, h=360),
        _clip(tmp_path / "b.mp4", 1.0, w=320, h=240, fps=25),
    ]
    result = join(parts, tmp_path / "mixed.mp4")
    assert result.duration_s == pytest.approx(2.0, abs=0.4)


@requires_ffmpeg
def test_a_crossfade_is_shorter_than_a_hard_cut(tmp_path: Path) -> None:
    # The overlap has to actually remove time — an xfade that just concatenates is the
    # failure this catches, and it looks fine frame by frame.
    parts = [_clip(tmp_path / "a.mp4", 2.0), _clip(tmp_path / "b.mp4", 2.0)]
    cut = join(parts, tmp_path / "cut.mp4")
    faded = join(parts, tmp_path / "fade.mp4", transition="xfade", transition_s=0.5)
    assert faded.duration_s == pytest.approx(cut.duration_s - 0.5, abs=0.3)


@requires_ffmpeg
def test_a_bed_shorter_than_the_picture_is_looped_not_truncated(tmp_path: Path) -> None:
    # A 1s loop under a 3s clip must cover all 3 seconds; -shortest without the loop
    # would silently cut the video down to the music.
    video = _clip(tmp_path / "v.mp4", 3.0)
    bed = _tone(tmp_path / "bed.mp3", 1.0)
    result = mix(video, tmp_path / "scored.mp4", bed=bed)
    assert result.duration_s == pytest.approx(3.0, abs=0.3)


@requires_ffmpeg
def test_variable_rate_frames_keep_their_real_timing(tmp_path: Path) -> None:
    """The screencast case: frames do not arrive evenly, and must not be evened out.

    Ten frames spanning 2s of wall clock — but bunched, the way a browser emits them
    (bursts while animating, near-nothing while static). Encoding at a flat rate would
    yield 10/30s ≈ 0.33s; honouring the per-frame durations yields the real 2s.
    """
    from navig.media.video_edit import from_frames

    frames = []
    for i in range(10):
        png = tmp_path / f"f{i:03d}.png"
        subprocess.run(
            [shutil.which("ffmpeg"), "-nostdin", "-y", "-f", "lavfi",
             "-i", f"color=c=0x{i*20:02x}0000:size=320x568", "-frames:v", "1", str(png)],
            capture_output=True, check=True,
        )
        # Bursty: eight quick frames, then two long holds.
        frames.append((png, 0.05 if i < 8 else 0.8))

    result = from_frames(frames, tmp_path / "cast.mp4", fps=30)
    # Tight on purpose: cumulative frame accounting makes this exact, so a loose bound
    # would let per-frame rounding drift back in unnoticed.
    assert result.duration_s == pytest.approx(2.0, abs=0.05), (
        "per-frame durations were ignored — the capture would drift against the voiceover"
    )


@requires_ffmpeg
def test_a_60fps_capture_encoded_at_30_stays_the_right_length(tmp_path: Path) -> None:
    """The real screencast case: the browser paints faster than we encode.

    Chrome pushes ~60 frames a second. At fps=30 half of them fall into an output tick
    that is already taken and must be DROPPED. Giving each input frame a tick of its own
    instead yields a video exactly the ratio of the two rates too long — a 2s capture
    played back over 4s — which looks fine until it is cut against a voiceover.
    """
    from navig.media.video_edit import from_frames

    png = tmp_path / "f.png"
    subprocess.run(
        [shutil.which("ffmpeg"), "-nostdin", "-y", "-f", "lavfi",
         "-i", "color=c=green:size=320x568", "-frames:v", "1", str(png)],
        capture_output=True, check=True,
    )
    # 120 frames, 1/60s apart == 2.0 seconds of real time.
    frames = [(png, 1 / 60) for _ in range(120)]
    result = from_frames(frames, tmp_path / "fast.mp4", fps=30)
    assert result.duration_s == pytest.approx(2.0, abs=0.1), (
        "a 60fps capture was stretched — input frames were not dropped to fit 30fps"
    )


@requires_ffmpeg
def test_a_held_last_frame_carries_a_static_page_to_full_length(tmp_path: Path) -> None:
    """A page that stops painting must still fill the requested window.

    The browser only emits a frame when it paints, so a static page goes quiet after its
    first burst. If the final frame is given a single tick, a multi-second capture comes
    back as a fraction of a second — measured live at 0.27s of picture against 6.69s of
    narration, which desynchronises everything cut after it.
    """
    from navig.media.video_edit import from_frames

    png = tmp_path / "f.png"
    subprocess.run(
        [shutil.which("ffmpeg"), "-nostdin", "-y", "-f", "lavfi",
         "-i", "color=c=navy:size=320x568", "-frames:v", "1", str(png)],
        capture_output=True, check=True,
    )
    # Three frames in a 0.1s burst, then the page goes quiet — the last frame is held
    # for the remaining 4.9s of a 5s request.
    frames = [(png, 0.05), (png, 0.05), (png, 4.9)]
    result = from_frames(frames, tmp_path / "static.mp4", fps=30)
    assert result.duration_s == pytest.approx(5.0, abs=0.1)


@requires_ffmpeg
def test_a_capture_shorter_than_one_output_tick_still_produces_a_file(tmp_path: Path) -> None:
    from navig.media.video_edit import from_frames

    png = tmp_path / "f.png"
    subprocess.run(
        [shutil.which("ffmpeg"), "-nostdin", "-y", "-f", "lavfi",
         "-i", "color=c=white:size=320x568", "-frames:v", "1", str(png)],
        capture_output=True, check=True,
    )
    result = from_frames([(png, 0.001)], tmp_path / "blink.mp4", fps=30)
    assert result.duration_s > 0


@requires_ffmpeg
def test_frames_can_be_reframed_to_vertical_while_assembling(tmp_path: Path) -> None:
    from navig.media.video_edit import from_frames

    png = tmp_path / "f.png"
    subprocess.run(
        [shutil.which("ffmpeg"), "-nostdin", "-y", "-f", "lavfi",
         "-i", "color=c=blue:size=800x600", "-frames:v", "1", str(png)],
        capture_output=True, check=True,
    )
    result = from_frames([(png, 0.5), (png, 0.5)], tmp_path / "v.mp4", width=1080, height=1920)
    assert (result.width, result.height) == (1080, 1920)


def test_assembling_no_frames_is_refused(tmp_path: Path) -> None:
    from navig.media.video_edit import from_frames

    with pytest.raises(VideoEditError, match="no frames"):
        from_frames([], tmp_path / "out.mp4")


@requires_ffmpeg
def test_a_still_becomes_a_clip_of_the_requested_length(tmp_path: Path) -> None:
    png = tmp_path / "card.png"
    subprocess.run(
        [shutil.which("ffmpeg"), "-nostdin", "-y", "-f", "lavfi",
         "-i", "color=c=red:size=800x600", "-frames:v", "1", str(png)],
        capture_output=True, check=True,
    )
    result = still(png, tmp_path / "still.mp4", secs=2.0, motion="kenburns")
    assert (result.width, result.height) == (1080, 1920)
    assert result.duration_s == pytest.approx(2.0, abs=0.3)


@requires_ffmpeg
def test_captions_are_lifted_clear_of_the_platform_ui(tmp_path: Path) -> None:
    """A caption in libass's default margin lands under TikTok's own chrome.

    Renders white text on a black clip with and without the default style, and compares
    where the ink actually is. The styled one must sit higher — this is checked on
    pixels rather than on the filter string, because a `force_style` that libass silently
    ignores would still look correct in the command line.
    """
    from navig.media.video_edit import CAPTION_STYLE, SAFE_BOTTOM_PX, burn_captions

    clip = tmp_path / "black.mp4"
    subprocess.run(
        [shutil.which("ffmpeg"), "-nostdin", "-y", "-f", "lavfi",
         "-i", "color=c=black:size=1080x1920:d=1", "-r", "10",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
        capture_output=True, check=True,
    )
    srt = tmp_path / "c.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nCAPTION\n\n", encoding="utf-8"
    )

    def ink_bottom(video: Path) -> int:
        """The lowest row containing any non-black pixel."""
        frame = tmp_path / f"{video.stem}.pgm"
        subprocess.run(
            [shutil.which("ffmpeg"), "-nostdin", "-y", "-i", str(video),
             "-vf", "format=gray,crop=1080:1920:0:0", "-frames:v", "1", str(frame)],
            capture_output=True, check=True,
        )
        raw = frame.read_bytes()
        header_end = 0
        for _ in range(3):  # P5, dimensions, maxval
            header_end = raw.index(b"\n", header_end) + 1
        pixels = raw[header_end:]
        for row in range(1919, -1, -1):
            line = pixels[row * 1080:(row + 1) * 1080]
            if any(b > 40 for b in line):
                return row
        return 0

    styled = tmp_path / "styled.mp4"
    burn_captions(clip, srt, styled)
    plain = tmp_path / "plain.mp4"
    burn_captions(clip, srt, plain, style="Fontsize=52,MarginV=10")

    assert ink_bottom(styled) < ink_bottom(plain), "the default style did not lift the caption"
    assert ink_bottom(styled) < 1920 - SAFE_BOTTOM_PX + 120, (
        f"caption sits within the bottom {SAFE_BOTTOM_PX}px the app covers"
    )
    assert "MarginV" in CAPTION_STYLE


@requires_ffmpeg
def test_a_long_clip_is_trimmed_to_the_edit(tmp_path: Path) -> None:
    from navig.media.video_edit import fit_duration

    src = _clip(tmp_path / "long.mp4", 4.0)
    result = fit_duration(src, tmp_path / "cut.mp4", secs=1.5)
    assert result.duration_s == pytest.approx(1.5, abs=0.15)
    assert result.filters == "trim"


@requires_ffmpeg
def test_a_short_clip_is_looped_to_fill_not_frozen(tmp_path: Path) -> None:
    """Generated footage comes back at the model's chosen length, not the edit's.

    A held frame under continuing narration reads as a stall; a loop of ambient footage
    does not. Either way the duration must be exact, or picture and voice diverge.
    """
    from navig.media.video_edit import fit_duration

    src = _clip(tmp_path / "short.mp4", 1.0)
    result = fit_duration(src, tmp_path / "filled.mp4", secs=3.0)
    assert result.duration_s == pytest.approx(3.0, abs=0.2)
    assert result.filters == "loop"


def test_fitting_to_no_duration_is_refused(tmp_path: Path) -> None:
    from navig.media.video_edit import fit_duration

    with pytest.raises(ValueError, match="secs"):
        fit_duration(tmp_path / "x.mp4", tmp_path / "o.mp4", secs=0)


@requires_ffmpeg
def test_an_empty_filter_chain_copies_rather_than_re_encodes(tmp_path: Path) -> None:
    """A look that disables every effect must cost nothing and lose no quality."""
    from navig.media.video_edit import apply_filter

    src = _clip(tmp_path / "src.mp4", 1.0)
    out = tmp_path / "same.mp4"
    result = apply_filter(src, out, "")
    assert result.filters == "copy"
    assert out.read_bytes() == src.read_bytes()


@requires_ffmpeg
def test_a_filter_chain_actually_changes_the_picture(tmp_path: Path) -> None:
    from navig.media.video_edit import apply_filter

    src = _clip(tmp_path / "src.mp4", 1.0)
    out = tmp_path / "graded.mp4"
    apply_filter(src, out, "eq=saturation=0")
    assert out.read_bytes() != src.read_bytes()
    assert probe(out)["width"] == probe(src)["width"]


# ── motion: names that actually mean different things ─────────────────────────


def test_every_motion_produces_a_different_move() -> None:
    """The bug this pins: "kenburns", "zoom" and "drift" were three names for ONE filter.

    A shotlist that carefully alternated them rendered a reel where every single shot
    pushed in identically — which is exactly what "the images always look the same" means.
    """
    from navig.media.video_edit import MOTIONS, _zoompan

    built = {
        motion: _zoompan(motion, frames=90, zoom=1.12, width=1080, height=1920, fps=30)
        for motion in MOTIONS - {"none"}
    }
    assert len(set(built.values())) == len(built), "two motions render the same filter"


def test_the_old_motion_names_still_work_and_now_differ() -> None:
    from navig.media.video_edit import MOTION_ALIASES, MOTIONS, _zoompan

    assert MOTION_ALIASES["zoom"] == "zoom-in"
    assert MOTION_ALIASES["drift"] == "pan-right"
    for alias, real in MOTION_ALIASES.items():
        assert real in MOTIONS, alias
    push = _zoompan("zoom-in", frames=60, zoom=1.12, width=1080, height=1920, fps=30)
    drift = _zoompan("pan-right", frames=60, zoom=1.12, width=1080, height=1920, fps=30)
    assert push != drift


def test_a_pan_is_given_room_to_travel() -> None:
    """At zoom 1.0 the visible region IS the frame, so a pan has nowhere to go."""
    from navig.media.video_edit import PAN_ZOOM, _zoompan

    tight = _zoompan("pan-left", frames=60, zoom=1.0, width=1080, height=1920, fps=30)
    assert f"z='{PAN_ZOOM:g}'" in tight


def test_a_zoom_out_starts_wide_and_a_zoom_in_starts_tight() -> None:
    from navig.media.video_edit import _zoompan

    assert _zoompan("zoom-in", frames=60, zoom=1.2, width=1080, height=1920, fps=30).startswith(
        "zoompan=z='1+"
    )
    assert "z='1.2-" in _zoompan("zoom-out", frames=60, zoom=1.2, width=1080, height=1920, fps=30)


def test_an_unknown_motion_is_refused_rather_than_silently_static(tmp_path) -> None:
    # Silently falling back to a still is how a typo becomes "why is this shot dead?".
    from navig.media.video_edit import still

    art = tmp_path / "a.png"
    art.write_bytes(b"\x89PNG")
    with pytest.raises(ValueError, match="unknown motion"):
        still(art, tmp_path / "o.mp4", secs=1.0, motion="ken-burns")
