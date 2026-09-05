"""Joining clips and mastering them — the assembly half of audio_edit.

Building an episode out of many generated clips depends on three things being exactly
right: nothing is dropped when they are joined, the durations reported back are real
(subtitles are offset by them, so a wrong one desynchronises everything after it), and a
path with an apostrophe in it does not quietly break the concat manifest — which matters
here because the scripts driving this are French.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from navig.media.audio_edit import (
    AudioEditError,
    _concat_list_file,
    concat,
    ffmpeg_available,
    normalize,
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
def clips(tmp_path: Path) -> list[Path]:
    return [
        _make_tone(tmp_path / "a.mp3", 1.0),
        _make_tone(tmp_path / "b.mp3", 2.0),
    ]


# ── the concat manifest (pure) ─────────────────────────────────────────────────


def test_manifest_escapes_an_apostrophe(tmp_path: Path) -> None:
    # The concat demuxer treats ' specially; "l'intro.mp3" otherwise fails with a parse
    # error that says nothing about quoting.
    listing = _concat_list_file([tmp_path / "l'intro.mp3"], tmp_path)
    line = listing.read_text(encoding="utf-8").strip()
    assert line.startswith("file '") and line.endswith("'")
    assert r"'\''" in line


def test_manifest_uses_forward_slashes(tmp_path: Path) -> None:
    listing = _concat_list_file([tmp_path / "a.mp3"], tmp_path)
    assert "\\" not in listing.read_text(encoding="utf-8")


# ── guards (no ffmpeg needed) ──────────────────────────────────────────────────


def test_empty_part_list_is_refused(tmp_path: Path) -> None:
    with pytest.raises(AudioEditError, match="empty"):
        concat([], tmp_path / "out.mp3")


def test_a_missing_input_is_named(tmp_path: Path) -> None:
    with pytest.raises(AudioEditError, match="ghost.mp3"):
        concat([tmp_path / "ghost.mp3"], tmp_path / "out.mp3")


def test_a_negative_gap_is_rejected(tmp_path: Path) -> None:
    # The input has to exist to reach the gap check — missing files are reported first.
    present = tmp_path / "a.mp3"
    present.write_bytes(b"not really audio, but present")
    with pytest.raises(ValueError, match="gap_s"):
        concat([present], tmp_path / "out.mp3", gap_s=-1)


# ── joining and mastering ──────────────────────────────────────────────────────


@requires_ffmpeg
def test_durations_add_up(clips: list[Path], tmp_path: Path) -> None:
    out = tmp_path / "joined.mp3"
    concat(clips, out)
    assert probe_duration(out) == pytest.approx(3.0, abs=0.2)


@requires_ffmpeg
def test_a_gap_lengthens_the_result_by_exactly_that_much(
    clips: list[Path], tmp_path: Path
) -> None:
    # Chapter offsets are computed from this, so a gap that is not really there would
    # walk every later chapter mark forward.
    joined = tmp_path / "tight.mp3"
    spaced = tmp_path / "spaced.mp3"
    concat(clips, joined)
    concat(clips, spaced, gap_s=0.6)
    assert probe_duration(spaced) - probe_duration(joined) == pytest.approx(0.6, abs=0.2)


@requires_ffmpeg
def test_a_single_part_is_copied_verbatim(clips: list[Path], tmp_path: Path) -> None:
    out = tmp_path / "one.mp3"
    result = concat([clips[0]], out)
    assert result.filters == "copy"
    assert out.read_bytes() == clips[0].read_bytes()


@requires_ffmpeg
def test_joining_survives_mixed_inputs(tmp_path: Path) -> None:
    # Speech and a generated music cue do not share a sample rate; the copy path fails
    # and the re-encode fallback has to carry it.
    first = _make_tone(tmp_path / "a.mp3", 1.0)
    second = tmp_path / "b.mp3"
    subprocess.run(
        [
            shutil.which("ffmpeg"), "-nostdin", "-y", "-f", "lavfi",
            "-i", "sine=frequency=220:duration=1", "-ar", "22050", "-ac", "2",
            "-c:a", "libmp3lame", "-q:a", "4", str(second),
        ],
        capture_output=True, check=True,
    )
    out = tmp_path / "mixed.mp3"
    concat([first, second], out)
    assert probe_duration(out) == pytest.approx(2.0, abs=0.3)


@requires_ffmpeg
def test_normalize_keeps_the_length_and_writes_audio(
    clips: list[Path], tmp_path: Path
) -> None:
    out = tmp_path / "mastered.mp3"
    result = normalize(clips[1], out)
    assert "loudnorm" in result.filters
    assert out.stat().st_size > 0
    assert probe_duration(out) == pytest.approx(probe_duration(clips[1]), abs=0.2)


@requires_ffmpeg
def test_probe_duration_rejects_a_non_audio_file(tmp_path: Path) -> None:
    junk = tmp_path / "notaudio.mp3"
    junk.write_text("this is not audio", encoding="utf-8")
    with pytest.raises(AudioEditError):
        probe_duration(junk)
