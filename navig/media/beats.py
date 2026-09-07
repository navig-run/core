"""Where the beat is — onset detection and beat tracking, ffmpeg + numpy only.

:mod:`navig.media.fx` has taken beat times since it was written: :func:`~navig.media.fx.punch_in`
pulses the frame on them and :func:`~navig.media.fx.glitch` tears on them. Nothing in navig
ever computed any. Every caller passed the *cut* boundaries instead and called them beats,
which lands the effects on the edit rather than on the music — the one thing short-form
viewers read instantly as "this was not cut to the song".

This closes that. It is deliberately dependency-light: ffmpeg to decode and numpy to
analyse, both of which navig already requires. librosa would be the obvious import and a
heavy one for three hundred lines of DSP.

The chain is the standard one, and each step is here because the previous one is not
enough on its own:

1. **Decode** to mono float32 at a low rate — pitch is irrelevant, timing is not.
2. **Spectral flux** — sum only the frames where energy *rose*. Total energy peaks at the
   loudest moment, which in dense music is nowhere near the transient.
3. **Normalise and subtract a local mean** — a track that gets louder must not read as a
   track with more beats.
4. **Tempo** by autocorrelating that envelope, weighted toward human tempi so a strong
   half- or double-time lag does not win on arithmetic alone.
5. **Beat tracking** by dynamic programming (Ellis 2007): every beat is scored against the
   onset envelope *and* penalised for deviating from the tempo period, so the grid stays
   steady across a bar the drummer left empty. Picking local peaks instead drifts on any
   syncopation, which is most of the music this will ever see.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navig.media.video_edit import _require

# Timing, not timbre. 22.05 kHz keeps every transient a listener can hear and quarters the
# sample count against 44.1; the hop then puts the onset envelope at ~43 frames a second,
# which resolves beats up to well past any tempo a person dances to.
SAMPLE_RATE = 22050
FRAME = 1024
HOP = 512

# The tempo range worth searching. Below 60 the grid is slower than a heartbeat; above 200
# every detector starts locking onto the hi-hat instead of the beat.
MIN_BPM = 60.0
MAX_BPM = 200.0
# Human tempo perception centres near 120bpm; this is the width of that preference in
# octaves. Without it, half-time and double-time score the same and the answer flips
# between runs on nearly identical audio.
TEMPO_SIGMA = 0.9
PREFERRED_BPM = 120.0
# How hard the beat tracker resists straying from the period. Ellis's value; lower drifts,
# higher ignores a genuine tempo change.
TIGHTNESS = 400.0
# How many envelope frames each onset is widened to before the tempo autocorrelation. Odd,
# and wide enough to cover the sub-frame drift a fractional period produces.
TEMPO_SMOOTHING = 7


class BeatError(RuntimeError):
    """The audio could not be analysed — unreadable file, no ffmpeg, or silence."""


@dataclass
class BeatGrid:
    """Where the beats are, and how sure we are about the tempo."""

    bpm: float
    beats: list[float] = field(default_factory=list)
    downbeats: list[float] = field(default_factory=list)
    onsets: list[float] = field(default_factory=list)
    duration_s: float = 0.0
    confidence: float = 0.0

    @property
    def period(self) -> float:
        """Seconds per beat."""
        return 60.0 / self.bpm if self.bpm > 0 else 0.0

    def every(self, n: int, *, offset: int = 0) -> list[float]:
        """Every ``n``-th beat — the usual way to keep an effect off every single hit."""
        if n < 1:
            raise ValueError(f"n must be at least 1, got {n}")
        return self.beats[offset::n]

    def between(self, start: float, end: float) -> list[float]:
        """The beats inside a section, as offsets from its start.

        A clip applies its look to one assembled timeline, but a shot only knows its own
        length — so a section asking "when do I pulse?" needs the answer rebased to zero.
        """
        return [b - start for b in self.beats if start <= b < end]

    def to_dict(self) -> dict[str, Any]:
        return {
            "bpm": round(self.bpm, 2),
            "beats": [round(b, 3) for b in self.beats],
            "downbeats": [round(b, 3) for b in self.downbeats],
            "duration_s": round(self.duration_s, 3),
            "confidence": round(self.confidence, 3),
        }


def _numpy():
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - numpy is a declared dependency
        raise BeatError(
            "beat detection needs numpy — install it with `pip install numpy`"
        ) from exc
    return np


def decode(path: Path | str, *, sample_rate: int = SAMPLE_RATE):
    """Decode any audio ffmpeg understands into a mono float32 array.

    Mono and low-rate on purpose: this measures *when*, and stereo detail costs memory
    without moving a single onset.
    """
    exe = _require("ffmpeg")
    src = Path(path)
    if not src.exists():
        raise BeatError(f"audio not found: {src}")
    np = _numpy()
    proc = subprocess.run(
        [exe, "-nostdin", "-v", "quiet", "-i", str(src),
         "-map", "0:a:0", "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "-"],
        capture_output=True, check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        detail = (proc.stderr or b"").decode("utf-8", "replace").strip()[:200]
        raise BeatError(f"could not decode {src.name}{': ' + detail if detail else ''}")
    samples = np.frombuffer(proc.stdout, dtype="<f4").astype("float32")
    if samples.size == 0:
        raise BeatError(f"{src.name} decoded to no audio at all")
    return samples


def onset_envelope(samples, *, sample_rate: int = SAMPLE_RATE, hop: int = HOP,
                   frame: int = FRAME):
    """Spectral flux: how much energy ROSE in each frame.

    Only the rises are summed. Falling energy is the tail of the note before, and counting
    it turns every decay into a second, phantom onset half a beat late.
    """
    np = _numpy()
    if samples.size < frame:
        raise BeatError("the audio is shorter than one analysis frame")
    count = 1 + (samples.size - frame) // hop
    if count < 4:
        raise BeatError("the audio is too short to find a beat in")
    # One strided view over the signal rather than a Python loop per frame — at ~43 frames
    # a second a three-minute track is eight thousand of them.
    strides = np.lib.stride_tricks.as_strided(
        samples, shape=(count, frame),
        strides=(samples.strides[0] * hop, samples.strides[0]),
        writeable=False,
    )
    window = np.hanning(frame).astype("float32")
    spectrum = np.abs(np.fft.rfft(strides * window, axis=1))
    # Log scaling before differencing: loudness is perceived multiplicatively, so a quiet
    # verse and a loud chorus otherwise produce wildly different flux for the same hit.
    spectrum = np.log1p(spectrum * 1000.0)
    flux = np.diff(spectrum, axis=0)
    envelope = np.maximum(flux, 0.0).sum(axis=1)
    envelope = np.concatenate([[0.0], envelope]).astype("float32")

    # Subtract a moving average so a track that simply gets louder does not read as a
    # track with more beats, then clip the negatives the subtraction creates.
    span = max(3, int(round(0.35 * sample_rate / hop)))
    kernel = np.ones(span, dtype="float32") / span
    local = np.convolve(envelope, kernel, mode="same")
    envelope = np.maximum(envelope - local, 0.0)
    peak = float(envelope.max())
    if peak <= 0:
        raise BeatError("no onsets found — the audio may be silence")
    return envelope / peak


def estimate_tempo(envelope, *, sample_rate: int = SAMPLE_RATE, hop: int = HOP) -> tuple[float, float]:
    """Best tempo in BPM, and how strongly the envelope agrees with it (0..1).

    Autocorrelation finds every period the music repeats on, which includes half and
    double the real tempo. The Gaussian weighting toward ~120bpm is what decides between
    them the way a listener would, rather than by whichever peak is arithmetically larger.
    """
    np = _numpy()
    rate = sample_rate / hop
    # Smooth the envelope BEFORE correlating — for the tempo only, never for placing the
    # beats. A hop of 512 puts one frame every 23ms, so a period of 18.46 frames (140bpm)
    # lands on a different sub-frame phase every beat and only realigns every second one.
    # The raw correlation therefore peaks at DOUBLE the period and the detector confidently
    # returns half the tempo. Widening each onset to a few frames removes the phase
    # sensitivity; the sharp envelope is still what the tracker places beats against.
    width = TEMPO_SMOOTHING
    kernel = np.hanning(width)
    smoothed = np.convolve(envelope, kernel / kernel.sum(), mode="same")
    centred = smoothed - smoothed.mean()
    correlation = np.correlate(centred, centred, mode="full")[len(centred) - 1:]
    if correlation[0] > 0:
        correlation = correlation / correlation[0]

    # Score a dense grid of TEMPI at fractional lags, rather than the integer lags the
    # frame rate happens to offer. This is not a refinement — it is the difference between
    # right and half-right. At ~43 frames a second, 140bpm falls at lag 18.46: sampled at
    # the integers it scores 0.58, while its own double lands near 36.9 and scores 0.94,
    # so the detector confidently returns half the tempo. Measured, then fixed.
    grid_bpm = np.arange(MIN_BPM, MAX_BPM + 0.25, 0.25)
    lags = 60.0 * rate / grid_bpm
    usable = (lags >= 1.0) & (lags <= len(correlation) - 2)
    if not usable.any():
        raise BeatError("the audio is too short to estimate a tempo")
    grid_bpm, lags = grid_bpm[usable], lags[usable]
    raw = np.interp(lags, np.arange(len(correlation)), correlation)
    weight = np.exp(-0.5 * (np.log2(grid_bpm / PREFERRED_BPM) / TEMPO_SIGMA) ** 2)
    scored = raw * weight
    best = int(np.argmax(scored))
    return float(grid_bpm[best]), float(max(0.0, min(1.0, scored[best])))


def track_beats(envelope, bpm: float, *, sample_rate: int = SAMPLE_RATE, hop: int = HOP) -> list[float]:
    """Lay a steady grid of beats over the envelope (Ellis 2007 dynamic programming).

    Each candidate beat is scored on the onset strength underneath it MINUS a penalty for
    sitting the wrong distance from the previous beat. The penalty is what makes this hold
    a tempo through a bar the drummer left empty — picking local peaks instead follows the
    syncopation and drifts, which is audible as picture landing a semiquaver late.
    """
    np = _numpy()
    rate = sample_rate / hop
    period = 60.0 / bpm * rate
    if period < 2 or period >= len(envelope):
        raise BeatError(f"a tempo of {bpm:.0f}bpm does not fit this audio")

    # Search a window around one period back. Wider than this and the tracker can skip a
    # beat entirely; narrower and it cannot recover from a missed one.
    lo = max(1, int(round(period * 0.5)))
    hi = max(lo + 1, int(round(period * 2.0)))
    offsets = np.arange(lo, hi + 1)
    penalty = -TIGHTNESS * (np.log(offsets / period) ** 2)

    score = envelope.astype("float64").copy()
    previous = np.full(len(envelope), -1, dtype="int64")
    for i in range(lo, len(envelope)):
        starts = i - offsets
        valid = starts >= 0
        if not valid.any():
            continue
        candidates = score[starts[valid]] + penalty[valid]
        best = int(np.argmax(candidates))
        score[i] += candidates[best]
        previous[i] = starts[valid][best]

    # Start the backtrace from a strong ENDING, not the global maximum: score accumulates,
    # so the raw argmax is almost always the final frame regardless of the music.
    tail = score[int(len(score) * 0.5):]
    cutoff = float(np.median(tail)) if tail.size else 0.0
    ends = np.nonzero(score >= max(cutoff, float(score.max()) * 0.5))[0]
    cursor = int(ends[-1]) if ends.size else int(np.argmax(score))

    frames: list[int] = []
    while cursor >= 0:
        frames.append(cursor)
        cursor = int(previous[cursor])
    frames.reverse()
    return [f / rate for f in frames]


def _downbeats(envelope, beats: list[float], *, sample_rate: int = SAMPLE_RATE,
               hop: int = HOP, per_bar: int = 4) -> list[float]:
    """Which beats start a bar — assumed 4/4, phase chosen by onset strength.

    Nothing here infers a time signature. It picks, among the ``per_bar`` possible
    phases, the one whose beats carry the most onset energy, which in practice is the one
    with the kick on it.
    """
    np = _numpy()
    if len(beats) < per_bar:
        return list(beats)
    rate = sample_rate / hop
    strength = []
    for phase in range(per_bar):
        idx = [min(len(envelope) - 1, int(round(b * rate))) for b in beats[phase::per_bar]]
        strength.append(float(np.sum(envelope[idx])) if idx else 0.0)
    return beats[int(np.argmax(strength))::per_bar]


# Below this, the tempo is a guess and the caller should be told so. Measured on real
# material: a full track settles around 0.7, while a twelve-second fragment lands near 0.2
# and can come back at double time — there simply are not enough bars to autocorrelate.
LOW_CONFIDENCE = 0.45


def detect(path: Path | str, *, per_bar: int = 4, bpm: float | None = None) -> BeatGrid:
    """Find the tempo and the beats in an audio file.

    This is the whole point of the module and the one function callers need.

    ``bpm`` skips estimation and lays the grid at a tempo you already know. That is not a
    convenience — a short fragment does not contain enough bars for autocorrelation to be
    sure, and it fails by returning an exact multiple: a 17-second cut of a 92bpm session
    comes back at 184.6. Half-time and double-time both "fit", so the answer is only
    recoverable from outside the fragment. When a longer mix of the same session exists,
    detect that and pass its tempo here.
    """
    np = _numpy()
    samples = decode(path)
    envelope = onset_envelope(samples)
    if bpm is not None:
        if not MIN_BPM / 2 <= bpm <= MAX_BPM * 2:
            raise BeatError(f"bpm must be between {MIN_BPM / 2:g} and {MAX_BPM * 2:g}, got {bpm}")
        confidence = 1.0
    else:
        bpm, confidence = estimate_tempo(envelope)
    beats = track_beats(envelope, bpm)
    rate = SAMPLE_RATE / HOP
    # Onsets are reported separately from beats: an effect that wants to hit the actual
    # transients (a stab, a vocal chop) wants these, not the grid.
    threshold = float(np.percentile(envelope, 92))
    peaks = [
        i / rate for i in range(1, len(envelope) - 1)
        if envelope[i] >= threshold and envelope[i] >= envelope[i - 1] and envelope[i] > envelope[i + 1]
    ]
    return BeatGrid(
        bpm=bpm,
        beats=beats,
        downbeats=_downbeats(envelope, beats, per_bar=per_bar),
        onsets=peaks,
        duration_s=samples.size / SAMPLE_RATE,
        confidence=confidence,
    )
