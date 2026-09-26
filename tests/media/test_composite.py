"""composite(): real footage laid over the picture.

The failure this guards against is not a crash — it is an overlay that runs out partway
through and leaves the second half of a clip visibly bare, which is exactly what happens
if the base rather than the overlay is allowed to decide the loop.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from navig.media.video_edit import (
    BLEND_MODES,
    DEFAULT_OVERLAY_OPACITY,
    VideoEditError,
    composite,
    ffmpeg_available,
    probe,
)

requires_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")


def _clip(path: Path, seconds: float, *, colour: str = "black", size: str = "320x568") -> Path:
    subprocess.run(
        [shutil.which("ffmpeg"), "-nostdin", "-y", "-f", "lavfi",
         "-i", f"color=c={colour}:s={size}:d={seconds:g}:r=30",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, check=True,
    )
    return path


class TestValidation:
    def test_an_unknown_blend_mode_is_refused_and_names_the_real_ones(self, tmp_path: Path):
        with pytest.raises(VideoEditError, match="unknown blend mode"):
            composite(tmp_path / "a.mp4", tmp_path / "b.mp4", tmp_path / "o.mp4", mode="sparkle")

    def test_opacity_outside_zero_to_one_is_refused(self, tmp_path: Path):
        with pytest.raises(ValueError, match="opacity"):
            composite(tmp_path / "a.mp4", tmp_path / "b.mp4", tmp_path / "o.mp4", opacity=1.4)

    def test_a_missing_base_is_named(self, tmp_path: Path):
        with pytest.raises(VideoEditError, match="base clip not found"):
            composite(tmp_path / "nope.mp4", tmp_path / "b.mp4", tmp_path / "o.mp4")

    def test_a_missing_overlay_is_named_separately(self, tmp_path: Path):
        # Two different mistakes; one message for both would send you to the wrong file.
        base = tmp_path / "a.mp4"
        base.write_bytes(b"x")
        with pytest.raises(VideoEditError, match="overlay footage not found"):
            composite(base, tmp_path / "nope.mp4", tmp_path / "o.mp4")

    def test_screen_is_available_because_black_backed_stock_needs_it(self):
        assert "screen" in BLEND_MODES
        assert 0.0 < DEFAULT_OVERLAY_OPACITY < 1.0


@requires_ffmpeg
class TestComposite:
    def test_the_base_decides_the_length_when_the_overlay_is_shorter(self, tmp_path: Path):
        # The regression that matters: a 2s overlay under an 8s shot must loop, not leave
        # six seconds of bare picture.
        base = _clip(tmp_path / "base.mp4", 8.0, colour="gray")
        ov = _clip(tmp_path / "ov.mp4", 2.0, colour="black")
        out = composite(base, ov, tmp_path / "out.mp4")
        assert out.duration_s == pytest.approx(8.0, abs=0.2)

    def test_the_base_decides_the_length_when_the_overlay_is_longer(self, tmp_path: Path):
        base = _clip(tmp_path / "base.mp4", 3.0, colour="gray")
        ov = _clip(tmp_path / "ov.mp4", 20.0, colour="black")
        out = composite(base, ov, tmp_path / "out.mp4")
        assert out.duration_s == pytest.approx(3.0, abs=0.2)

    def test_the_output_keeps_the_bases_frame_size(self, tmp_path: Path):
        # An overlay of a different aspect must not letterbox the picture: hard black bars
        # across a frame whose whole point is invisible black.
        base = _clip(tmp_path / "base.mp4", 2.0, colour="gray", size="320x568")
        ov = _clip(tmp_path / "ov.mp4", 2.0, colour="black", size="640x360")
        out = composite(base, ov, tmp_path / "out.mp4")
        assert (out.width, out.height) == (320, 568)

    def test_screening_pure_black_leaves_the_picture_alone(self, tmp_path: Path):
        # The property that makes black-backed stock work at all.
        base = _clip(tmp_path / "base.mp4", 2.0, colour="gray")
        ov = _clip(tmp_path / "ov.mp4", 2.0, colour="black")
        out = composite(base, ov, tmp_path / "out.mp4", mode="screen", opacity=1.0)
        assert out.duration_s > 0
        assert probe(out.path)["width"] == 320

    def test_a_start_offset_still_produces_a_full_length_clip(self, tmp_path: Path):
        # Seeking in so two clips of the same stock do not open on the same frame must not
        # shorten the result.
        base = _clip(tmp_path / "base.mp4", 5.0, colour="gray")
        ov = _clip(tmp_path / "ov.mp4", 10.0, colour="black")
        out = composite(base, ov, tmp_path / "out.mp4", start=6.0)
        assert out.duration_s == pytest.approx(5.0, abs=0.2)

    def test_the_filter_used_is_reported(self, tmp_path: Path):
        base = _clip(tmp_path / "base.mp4", 1.0)
        ov = _clip(tmp_path / "ov.mp4", 1.0)
        out = composite(base, ov, tmp_path / "out.mp4", mode="lighten", opacity=0.4)
        assert "lighten" in out.filters and "0.4" in out.filters


class TestTint:
    def test_a_hex_colour_becomes_a_luminance_recolour(self):
        from navig.media.video_edit import tint_filter

        f = tint_filter("0xD62828")
        # Desaturate FIRST: the mixer's diagonal only means "scale by luminance" on a
        # frame where R=G=B.
        assert f.startswith("hue=s=0,")
        assert "rr=0.8392" in f and "gg=0.1569" in f and "bb=0.1569" in f

    def test_hash_and_0x_prefixes_are_both_accepted(self):
        from navig.media.video_edit import tint_filter

        assert tint_filter("#D62828") == tint_filter("0xD62828") == tint_filter("D62828")

    def test_a_short_colour_is_refused(self):
        from navig.media.video_edit import tint_filter

        with pytest.raises(VideoEditError, match="six-digit hex"):
            tint_filter("F00")

    def test_a_non_hex_colour_is_refused(self):
        from navig.media.video_edit import tint_filter

        with pytest.raises(VideoEditError, match="hex colour"):
            tint_filter("ZZZZZZ")

    @requires_ffmpeg
    def test_tinting_survives_the_render_and_is_reported(self, tmp_path: Path):
        base = _clip(tmp_path / "base.mp4", 2.0, colour="gray")
        ov = _clip(tmp_path / "ov.mp4", 2.0, colour="blue")
        out = composite(base, ov, tmp_path / "out.mp4", tint="0xD62828")
        assert out.duration_s == pytest.approx(2.0, abs=0.2)
        assert "tint=0xD62828" in out.filters


class TestWindow:
    def test_a_backwards_window_is_refused(self, tmp_path: Path):
        with pytest.raises(VideoEditError, match="ends at or before"):
            composite(tmp_path / "a.mp4", tmp_path / "b.mp4", tmp_path / "o.mp4",
                      window=(8.0, 3.0))

    @requires_ffmpeg
    def test_a_windowed_overlay_still_yields_a_full_length_clip(self, tmp_path: Path):
        # The gate fades the overlay to black, it does not shorten anything.
        base = _clip(tmp_path / "base.mp4", 10.0, colour="gray")
        ov = _clip(tmp_path / "ov.mp4", 3.0, colour="black")
        out = composite(base, ov, tmp_path / "out.mp4", window=(2.0, 6.0))
        assert out.duration_s == pytest.approx(10.0, abs=0.2)

    @requires_ffmpeg
    def test_the_window_is_reported_so_a_render_can_be_read_back(self, tmp_path: Path):
        base = _clip(tmp_path / "base.mp4", 5.0)
        ov = _clip(tmp_path / "ov.mp4", 2.0)
        out = composite(base, ov, tmp_path / "out.mp4", window=(1.0, 4.0))
        assert "window=1-4" in out.filters

    @requires_ffmpeg
    def test_a_window_shorter_than_two_fades_still_renders(self, tmp_path: Path):
        # A 0.5s window cannot hold two 0.7s fades; the fade shortens rather than
        # producing a negative start time that ffmpeg would reject.
        base = _clip(tmp_path / "base.mp4", 4.0)
        ov = _clip(tmp_path / "ov.mp4", 2.0)
        out = composite(base, ov, tmp_path / "out.mp4", window=(1.0, 1.5))
        assert out.duration_s == pytest.approx(4.0, abs=0.2)
