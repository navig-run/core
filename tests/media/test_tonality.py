"""What a track is — key, bands, loudness shape.

Fixtures are synthesised so the right answer is arithmetic: a chord built from named
frequencies has a key by construction, a sine under 100 Hz is "sub" by definition, and a
track that is quiet-loud-quiet has an intro, a main and an outro wherever the gain
changed. Each test guards one way the profiler could be quietly wrong — the relative
major winning over the minor the bass is in, a sub that rounds to the wrong semitone,
a build-up labelled as a breakdown.
"""

from __future__ import annotations

import struct
import wave
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from navig.media.beats import BeatError  # noqa: E402
from navig.media.tonality import (  # noqa: E402
    AudioProfile,
    KeyEstimate,
    Section,
    band_shares,
    bass_root,
    chroma_from_power,
    estimate_key,
    loudness_sections,
    profile,
)

SR = 44100
NOTE_HZ = {"E1": 41.20, "E2": 82.41, "G2": 98.00, "B2": 123.47, "E3": 164.81, "G3": 196.00,
           "B3": 246.94, "C3": 130.81, "A2": 110.00}


def write_wav(path: Path, audio, sample_rate: int = SR) -> Path:
    audio = np.clip(audio, -1.0, 1.0)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"".join(struct.pack("<h", int(s * 32000)) for s in audio))
    return path


def tone(freq: float, seconds: float, amp: float = 0.2):
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype("float32")


def clicks(bpm: float, seconds: float, amp: float = 0.5):
    out = np.zeros(int(seconds * SR), dtype="float32")
    rng = np.random.default_rng(3)
    length = int(0.02 * SR)
    env = np.exp(-np.linspace(0, 8, length)).astype("float32")
    t = 0.0
    while t < seconds:
        start = int(t * SR)
        end = min(out.size, start + length)
        if end > start:
            out[start:end] += amp * rng.normal(0, 1, end - start).astype("float32") * env[: end - start]
        t += 60.0 / bpm
    return out


def e_minor_beat(seconds: float = 24.0, bpm: float = 90.0):
    """An E-minor chord over an E1 sub with a click on every beat."""
    audio = clicks(bpm, seconds)
    for name in ("E2", "G2", "B2", "E3", "G3", "B3"):
        audio += tone(NOTE_HZ[name], seconds, 0.08)
    audio += tone(NOTE_HZ["E1"] * 1.02, seconds, 0.35)  # an 808 a third of a semitone sharp
    return audio


def test_an_e_minor_chord_is_heard_as_e_minor(tmp_path):
    p = profile(write_wav(tmp_path / "em.wav", e_minor_beat()), bpm=90.0)
    assert p.key.name == "E minor"
    assert p.key.bass_root == "E"
    assert p.key.confidence > 0.5


def test_the_bass_decides_between_a_minor_key_and_its_relative_major():
    """C major and A minor share every note; only the bass can tell them apart."""
    chroma = np.zeros(12)
    for pc in (0, 2, 4, 5, 7, 9, 11):  # the white keys, evenly
        chroma[pc] = 1.0
    chroma[0] = 1.05  # a hair more C, so the chroma alone says C major
    assert estimate_key(chroma).name == "C major"
    assert estimate_key(chroma, bass_root="A").name == "A minor"
    # ...but a bass note the profiles do not rate cannot overturn a clear winner.
    assert estimate_key(chroma, bass_root="F#").name == "C major"


def test_a_sharp_808_still_rounds_to_its_note():
    """An 808 tuned 30 cents sharp of E1 sits in F1's FFT bin at coarse resolution."""
    from navig.media.tonality import BASS_FRAME, _spectrum

    audio = tone(NOTE_HZ["E1"] * 1.02, 12.0, 0.5)
    power, freqs = _spectrum(audio, sample_rate=SR, frame=BASS_FRAME, hop=BASS_FRAME // 2)
    assert bass_root(power, freqs) == "E"


def test_band_shares_put_a_sub_sine_in_the_sub_band():
    from navig.media.tonality import _spectrum

    power, freqs = _spectrum(tone(50.0, 6.0, 0.5), sample_rate=SR)
    shares = band_shares(power, freqs)
    assert shares["sub"] > 0.95
    assert abs(sum(shares.values()) - 1.0) < 0.02
    power, freqs = _spectrum(tone(5000.0, 6.0, 0.5), sample_rate=SR)
    assert band_shares(power, freqs)["high"] > 0.95


def test_chroma_needs_a_signal():
    from navig.media.tonality import _spectrum

    power, freqs = _spectrum(np.zeros(SR * 2, dtype="float32"), sample_rate=SR)
    with pytest.raises(BeatError):
        chroma_from_power(power, freqs)


def test_a_build_up_is_one_intro_not_several_breakdowns():
    """Quiet → louder → loud → dip → loud → quiet reads as intro / main / breakdown / main / outro."""
    beat = clicks(120.0, 4.0, 0.6) + tone(220.0, 4.0, 0.3)
    gains = [0.05, 0.15, 1.0, 1.0, 0.5, 1.0, 1.0, 0.05]  # 4 s windows
    audio = np.concatenate([beat * g for g in gains]).astype("float32")
    sections = loudness_sections(audio, sample_rate=SR)
    assert [s.label for s in sections] == ["intro", "main", "breakdown", "main", "outro"]
    assert sections[0].end == pytest.approx(8.0)
    assert sections[2].start == pytest.approx(16.0)
    assert sections[-1].end == pytest.approx(32.0)


def test_the_style_brief_says_what_the_numbers_say():
    key = KeyEstimate(tonic="E", mode="minor", confidence=0.7, chroma=[0.0] * 12)
    p = AudioProfile(
        path="x.mp3", duration_s=200.0, bpm=152.0, beat_confidence=0.8, key=key,
        loudness_dbfs=-7.5, peak_dbfs=-0.1,
        bands={"sub": 0.72, "bass": 0.24, "mid": 0.03, "high": 0.01, "air": 0.0},
        sections=[Section(0, 24, -28, "intro"), Section(24, 200, -7, "main")],
    )
    assert p.bpm_felt == 76.0
    brief = p.style_brief()
    assert brief.startswith("76 bpm (double-time hi-hats at 152), E minor, instrumental")
    assert "sub-heavy 808" in brief and "muffled" in brief and "24s intro build" in brief
    card = p.to_markdown("Fiends")
    assert "| intro | 0:00.0 | 0:24.0 |" in card
    assert "bass sits on" not in card  # no bass vote recorded → not claimed


def test_profile_refuses_silence(tmp_path):
    with pytest.raises(BeatError):
        profile(write_wav(tmp_path / "silence.wav", np.zeros(SR * 5, dtype="float32")))
