"""excerpt(): cutting one passage out of a longer track.

The real risk here is not a crash — it is a cut that is silently the wrong length, because
everything downstream sizes picture to what ffprobe measures. So these assert the measured
duration, not merely that a file appeared.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from navig.media.audio_edit import (
    AudioEditError,
    excerpt,
    ffmpeg_available,
    probe_duration,
)

requires_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")


def _make_tone(path: Path, seconds: float) -> Path:
    subprocess.run(
        [
            shutil.which("ffmpeg"), "-nostdin", "-y", "-f", "lavfi",
            "-i", f"sine=frequency=440:duration={seconds:g}",
            "-c:a", "libmp3lame", "-q:a", "4", str(path),
        ],
        capture_output=True, check=True,
    )
    return path


@pytest.fixture
def track(tmp_path: Path) -> Path:
    return _make_tone(tmp_path / "track.mp3", 10.0)


class TestExcerptValidation:
    def test_a_negative_start_is_refused(self, tmp_path: Path):
        with pytest.raises(ValueError, match="start"):
            excerpt(tmp_path / "x.mp3", tmp_path / "o.wav", -1.0, 5.0)

    def test_an_end_at_or_before_the_start_is_refused(self, tmp_path: Path):
        with pytest.raises(ValueError, match="must be after"):
            excerpt(tmp_path / "x.mp3", tmp_path / "o.wav", 5.0, 5.0)

    def test_a_missing_source_is_an_error(self, tmp_path: Path):
        with pytest.raises(AudioEditError, match="not found"):
            excerpt(tmp_path / "nope.mp3", tmp_path / "o.wav", 0.0, 1.0)


@requires_ffmpeg
class TestExcerptCuts:
    def test_the_cut_is_the_length_that_was_asked_for(self, track: Path, tmp_path: Path):
        dst = tmp_path / "passage.wav"
        excerpt(track, dst, 2.0, 5.0)
        assert probe_duration(dst) == pytest.approx(3.0, abs=0.05)

    def test_an_end_past_the_track_clamps_instead_of_failing(self, track: Path, tmp_path: Path):
        # A hook list can propose a window that runs into the fade-out; clamping is the
        # useful answer, and an error here would make the last passage of every track
        # unusable.
        dst = tmp_path / "tail.wav"
        excerpt(track, dst, 8.0, 30.0)
        assert probe_duration(dst) == pytest.approx(2.0, abs=0.05)

    def test_omitting_the_end_runs_to_the_end_of_the_track(self, track: Path, tmp_path: Path):
        dst = tmp_path / "rest.wav"
        excerpt(track, dst, 6.0)
        assert probe_duration(dst) == pytest.approx(4.0, abs=0.05)

    def test_a_start_past_the_end_of_the_track_is_an_error(self, track: Path, tmp_path: Path):
        with pytest.raises(AudioEditError, match="nothing there"):
            excerpt(track, tmp_path / "o.wav", 30.0, 40.0)

    def test_the_output_is_wav_so_no_encoder_padding_moves_the_last_frame(
        self, track: Path, tmp_path: Path,
    ):
        dst = tmp_path / "passage.wav"
        result = excerpt(track, dst, 1.0, 4.0)
        assert dst.read_bytes()[:4] == b"RIFF"
        assert result.rate == 1.0 and result.pitch_shifted is False

    def test_two_adjacent_cuts_tile_the_source_without_drift(
        self, track: Path, tmp_path: Path,
    ):
        # If -ss landed on a frame boundary instead of a sample, the halves would not add
        # back up -- and every later cut would inherit that drift.
        a, b = tmp_path / "a.wav", tmp_path / "b.wav"
        excerpt(track, a, 0.0, 4.0)
        excerpt(track, b, 4.0, 8.0)
        assert probe_duration(a) + probe_duration(b) == pytest.approx(8.0, abs=0.05)
