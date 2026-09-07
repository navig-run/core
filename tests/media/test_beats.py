"""Finding the beat.

The property that matters is the one in :func:`test_a_click_track_is_found_at_its_own_tempo`:
given audio whose tempo is known by construction, the detector has to return that tempo and
put beats on the clicks. Everything else here guards a specific way this fails quietly — a
grid that drifts, a tempo reported at double, an envelope that mistakes a crescendo for
onsets.

The fixtures are synthesised rather than recorded so the right answer is arithmetic.
"""

from __future__ import annotations

import struct
import wave
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from navig.media.beats import (  # noqa: E402
    BeatError,
    BeatGrid,
    decode,
    detect,
    estimate_tempo,
    onset_envelope,
)

SR = 22050


def click_track(path: Path, bpm: float, seconds: float, *, sample_rate: int = SR,
                swell: bool = False) -> Path:
    """A short percussive click every beat — tempo known by construction."""
    total = int(seconds * sample_rate)
    audio = np.zeros(total, dtype="float32")
    period = 60.0 / bpm
    rng = np.random.default_rng(7)
    click_len = int(0.02 * sample_rate)
    envelope = np.exp(-np.linspace(0, 8, click_len)).astype("float32")
    t = 0.0
    while t < seconds:
        start = int(t * sample_rate)
        end = min(total, start + click_len)
        if end > start:
            burst = rng.normal(0, 1, end - start).astype("float32") * envelope[: end - start]
            audio[start:end] += burst
        t += period
    if swell:
        # A track that simply gets louder must NOT read as a track with more beats.
        audio *= np.linspace(0.15, 1.0, total).astype("float32")
    audio = np.clip(audio, -1.0, 1.0)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"".join(struct.pack("<h", int(s * 32000)) for s in audio))
    return path


@pytest.mark.parametrize("bpm", [90.0, 120.0, 140.0])
def test_a_click_track_is_found_at_its_own_tempo(tmp_path, bpm):
    grid = detect(click_track(tmp_path / f"c{bpm}.wav", bpm, 20.0))
    assert grid.bpm == pytest.approx(bpm, rel=0.06)
    assert grid.confidence > 0.3
    # ...and the beats are ON the clicks, evenly spaced at the period.
    gaps = np.diff(grid.beats)
    assert float(np.median(gaps)) == pytest.approx(60.0 / bpm, rel=0.06)


def test_the_grid_does_not_drift_over_a_long_track(tmp_path):
    """A tracker that follows local peaks accumulates error; the DP penalty is what stops it."""
    grid = detect(click_track(tmp_path / "long.wav", 120.0, 60.0))
    expected = 0.5
    for index, beat in enumerate(grid.beats):
        # Every beat must still be within a fifth of a beat of where the metronome is,
        # sixty seconds in — not just the first few.
        assert abs(beat - (grid.beats[0] + index * expected)) < expected * 0.2


def test_a_crescendo_is_not_mistaken_for_onsets(tmp_path):
    """Rising loudness must not read as rising onset density.

    Spectral flux alone climbs with the volume; subtracting a local mean is what keeps a
    fade-in from producing a cluster of phantom beats at the loud end.
    """
    grid = detect(click_track(tmp_path / "swell.wav", 120.0, 20.0, swell=True))
    assert grid.bpm == pytest.approx(120.0, rel=0.08)
    first_half = [b for b in grid.beats if b < 10]
    second_half = [b for b in grid.beats if b >= 10]
    assert abs(len(first_half) - len(second_half)) <= 3


def test_a_known_tempo_can_be_forced(tmp_path):
    """The escape hatch for short cuts, where autocorrelation returns an exact multiple."""
    path = click_track(tmp_path / "forced.wav", 120.0, 12.0)
    grid = detect(path, bpm=92.3)
    assert grid.bpm == 92.3
    assert grid.confidence == 1.0
    assert float(np.median(np.diff(grid.beats))) == pytest.approx(60.0 / 92.3, rel=0.05)


def test_an_absurd_forced_tempo_is_refused(tmp_path):
    path = click_track(tmp_path / "absurd.wav", 120.0, 8.0)
    with pytest.raises(BeatError, match="bpm must be between"):
        detect(path, bpm=5.0)


def test_silence_says_so_rather_than_returning_a_grid(tmp_path):
    quiet = tmp_path / "silent.wav"
    with wave.open(str(quiet), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(b"\x00\x00" * SR * 5)
    with pytest.raises(BeatError):
        detect(quiet)


def test_a_missing_file_is_named(tmp_path):
    with pytest.raises(BeatError, match="audio not found"):
        decode(tmp_path / "nope.wav")


def test_downbeats_are_a_subset_of_beats_one_bar_apart(tmp_path):
    grid = detect(click_track(tmp_path / "bars.wav", 120.0, 24.0))
    assert set(grid.downbeats) <= set(grid.beats)
    gaps = np.diff(grid.downbeats)
    assert float(np.median(gaps)) == pytest.approx(4 * 60.0 / 120.0, rel=0.1)


def test_the_grid_answers_the_questions_a_renderer_asks():
    grid = BeatGrid(bpm=120.0, beats=[0.0, 0.5, 1.0, 1.5, 2.0], downbeats=[0.0, 2.0])
    assert grid.period == pytest.approx(0.5)
    assert grid.every(2) == [0.0, 1.0, 2.0]
    # A section only knows its own length, so beats inside it come back rebased to zero.
    assert grid.between(1.0, 2.0) == [0.0, 0.5]
    with pytest.raises(ValueError):
        grid.every(0)


def test_tempo_estimation_prefers_a_human_tempo_over_arithmetic(tmp_path):
    """Autocorrelation scores half- and double-time equally; the weighting decides."""
    samples = decode(click_track(tmp_path / "pref.wav", 120.0, 30.0))
    bpm, _ = estimate_tempo(onset_envelope(samples))
    assert 100 <= bpm <= 145, f"locked onto a multiple instead of the tempo: {bpm}"
