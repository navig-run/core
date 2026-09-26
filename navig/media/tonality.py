"""What a track *is* — key, loudness shape, spectral balance — ffmpeg + numpy only.

:mod:`navig.media.beats` answers *when* (tempo, beat times). This answers the questions a
producer asks before writing a beat "like that one": what key is it in, how is the low end
built, where does the arrangement drop out, how loud does it sit. The output is a
:class:`AudioProfile` — numbers first, then a sentence a music model can be handed as a
style brief. Nothing here is taste; every field is measured.

Kept as light as :mod:`beats`: one decode through ffmpeg, an STFT in numpy, and the
Krumhansl–Schmuckler key profiles, which are two lists of twelve numbers. librosa would do
the same in fewer lines and cost a hundred megabytes of wheels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navig.media.beats import BeatError, _numpy, decode, detect

# Full-band decode: the bands below go up to 16 kHz, and a key estimate wants the
# harmonics above the fundamentals, so the 22.05 kHz timing rate of ``beats`` is too low.
SAMPLE_RATE = 44100
# 2.7 Hz bins. Coarser than this and a bin at 55 Hz is wider than a semitone, so the sub
# bass — most of the energy in any rap beat — votes for whichever pitch class its bin
# happens to straddle. Measured on a sub-heavy reference: 8192 said C major, 16384 and
# the 808 itself said E minor.
FRAME = 16384
HOP = 8192
# The bass vote gets twice that again: an 808 a third of a semitone sharp of E1 (42 Hz)
# still rounds to E at 1.35 Hz per bin, and to F at 2.7.
BASS_FRAME = 32768

PITCH_CLASSES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

# Krumhansl & Kessler (1982) probe-tone profiles, index 0 = tonic. Correlating the
# track's pitch-class energy against each of the 24 rotations is the classic
# key-finding algorithm; it is not subtle, but on a beat with a sustained bass it is
# right far more often than a producer's guess.
_MAJOR = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
_MINOR = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)

# Only fundamentals and low harmonics vote on the key: above ~2 kHz the spectrum is
# hi-hats and noise, which have no pitch class and only flatten the chroma. Below 80 Hz
# the sub is heard as a note but smears across bins; it gets its own vote instead
# (:func:`bass_root`) from the frequency it actually peaks at.
KEY_LOW_HZ = 80.0
KEY_HIGH_HZ = 2000.0
BASS_LOW_HZ = 30.0
BASS_HIGH_HZ = 130.0

# Rap tempo is felt in this band. A detector centred on 120 bpm reads a 76 bpm boom-bap
# beat as 152 because the hi-hats are on the eighths; reporting the half as the *felt*
# tempo is what a producer would write on the file.
FELT_MAX_BPM = 110.0
# How far behind the chroma winner a key may score and still take it on the bass vote.
BASS_TIE_MARGIN = 0.12

# The bands a mix engineer talks in. "sub" is the 808 and nothing else.
BANDS: tuple[tuple[str, float, float], ...] = (
    ("sub", 0.0, 100.0),
    ("bass", 100.0, 300.0),
    ("mid", 300.0, 2000.0),
    ("high", 2000.0, 8000.0),
    ("air", 8000.0, 16000.0),
)

# Arrangement sections come from the loudness envelope: a window this long averages
# over a bar at any rap tempo, and a step this big is a part dropping out, not a fill.
SECTION_WINDOW_S = 4.0
SECTION_STEP_DB = 2.0


@dataclass
class KeyEstimate:
    """The most likely key, with how clearly it won."""

    tonic: str
    mode: str  # "major" | "minor"
    confidence: float  # correlation of the winner, 0..1
    chroma: list[float] = field(default_factory=list)  # 12 pitch classes, C first, max 1
    alternatives: list[tuple[str, float]] = field(default_factory=list)  # ("A minor", 0.65)
    bass_root: str | None = None  # the pitch class the sub bass sits on most of the time

    @property
    def name(self) -> str:
        return f"{self.tonic} {self.mode}"

    @property
    def short(self) -> str:
        """``Am`` / ``C`` — the way a producer writes it on a file name."""
        return self.tonic + ("m" if self.mode == "minor" else "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.name,
            "short": self.short,
            "confidence": round(self.confidence, 3),
            "chroma": {pc: round(v, 3) for pc, v in zip(PITCH_CLASSES, self.chroma)},
            "alternatives": [(n, round(c, 3)) for n, c in self.alternatives],
            "bass_root": self.bass_root,
        }


@dataclass
class Section:
    """A stretch of the track at one loudness — intro, main, breakdown, outro."""

    start: float
    end: float
    level_db: float
    label: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 1),
            "end": round(self.end, 1),
            "level_db": round(self.level_db, 1),
            "label": self.label,
        }


@dataclass
class AudioProfile:
    """Everything measured about one track."""

    path: str
    duration_s: float
    bpm: float
    beat_confidence: float
    key: KeyEstimate
    loudness_dbfs: float  # RMS over the whole track
    peak_dbfs: float
    bands: dict[str, float]  # share of energy per band, sums to ~1
    sections: list[Section] = field(default_factory=list)

    @property
    def sub_share(self) -> float:
        return self.bands.get("sub", 0.0)

    @property
    def bpm_felt(self) -> float:
        """The tempo a rapper counts: the detected one, halved when it reads as double-time."""
        return self.bpm / 2.0 if self.bpm > FELT_MAX_BPM else self.bpm

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "duration_s": round(self.duration_s, 2),
            "bpm": round(self.bpm, 2),
            "bpm_felt": round(self.bpm_felt, 2),
            "beat_confidence": round(self.beat_confidence, 3),
            **self.key.to_dict(),
            "loudness_dbfs": round(self.loudness_dbfs, 1),
            "peak_dbfs": round(self.peak_dbfs, 1),
            "bands": {k: round(v, 3) for k, v in self.bands.items()},
            "sections": [s.to_dict() for s in self.sections],
            "brief": self.style_brief(),
        }

    def style_brief(self) -> str:
        """One paragraph a music model can be handed: the numbers, said as a producer would.

        Every adjective is tied to a threshold below, so two runs on the same file say the
        same thing and a different file says something different.
        """
        tempo = f"{round(self.bpm_felt)} bpm"
        if self.bpm_felt != self.bpm:
            tempo += f" (double-time hi-hats at {round(self.bpm)})"
        parts: list[str] = [tempo, self.key.name, "instrumental, no vocals"]
        sub, bass, mid, high = (self.bands.get(k, 0.0) for k in ("sub", "bass", "mid", "high"))
        if sub >= 0.5:
            parts.append("sub-heavy 808 bass carrying the root, huge low end")
        elif sub + bass >= 0.5:
            parts.append("warm bass-led low end")
        else:
            parts.append("light low end")
        if mid + high < 0.1:
            parts.append("muffled low-passed sample, almost nothing above 2 kHz, dusty and dark")
        elif high >= 0.25:
            parts.append("bright, open top end")
        else:
            parts.append("balanced midrange")
        if self.loudness_dbfs >= -9:
            parts.append("dense loud mix")
        elif self.loudness_dbfs <= -18:
            parts.append("sparse quiet mix with space for a voice")
        if self.bpm_felt <= 80:
            parts.append("slow head-nod pulse")
        elif self.bpm_felt >= 130:
            parts.append("fast driving pulse")
        labels = [s.label for s in self.sections]
        if "breakdown" in labels:
            parts.append("with a breakdown where the beat drops out")
        if labels and labels[0] == "intro":
            intro = self.sections[0]
            parts.append(f"{round(intro.end - intro.start)}s intro build")
        return ", ".join(parts)

    def to_markdown(self, title: str | None = None) -> str:
        """The beat card — what gets filed next to a reference track."""
        lines = [f"# {title or Path(self.path).name}", ""]
        lines += [
            f"- **Tempo:** {self.bpm_felt:.1f} bpm felt"
            + (f" · {self.bpm:.1f} detected (double-time)" if self.bpm_felt != self.bpm else "")
            + f" · confidence {self.beat_confidence:.2f}",
            f"- **Key:** {self.key.name} (confidence {self.key.confidence:.2f}; next "
            + ", ".join(f"{n} {c:.2f}" for n, c in self.key.alternatives[:2])
            + (f"; bass sits on {self.key.bass_root}" if self.key.bass_root else "") + ")",
            f"- **Length:** {_mmss(self.duration_s)}",
            f"- **Loudness:** RMS {self.loudness_dbfs:.1f} dBFS · peak {self.peak_dbfs:.1f} dBFS",
            "- **Spectrum:** " + " · ".join(
                f"{k} {v * 100:.0f}%" for k, v in self.bands.items()
            ),
            "",
            "## Structure",
            "",
            "| Section | From | To | Level |",
            "|---|---|---|---|",
        ]
        for s in self.sections:
            lines.append(f"| {s.label} | {_mmss(s.start)} | {_mmss(s.end)} | {s.level_db:.1f} dB |")
        lines += ["", "## Style brief", "", f"> {self.style_brief()}", ""]
        return "\n".join(lines)


def _mmss(seconds: float) -> str:
    m, s = divmod(max(0.0, seconds), 60)
    return f"{int(m)}:{s:04.1f}"


def _spectrum(samples, *, sample_rate: int, frame: int = FRAME, hop: int = HOP):
    """Power spectrogram (frames × bins) and the bin frequencies."""
    np = _numpy()
    if samples.size < frame:
        raise BeatError("the audio is shorter than one analysis frame")
    count = 1 + (samples.size - frame) // hop
    strides = np.lib.stride_tricks.as_strided(
        samples, shape=(count, frame),
        strides=(samples.strides[0] * hop, samples.strides[0]),
        writeable=False,
    )
    window = np.hanning(frame).astype("float32")
    power = np.abs(np.fft.rfft(strides * window, axis=1)) ** 2
    freqs = np.fft.rfftfreq(frame, d=1.0 / sample_rate)
    return power, freqs


def chroma_from_power(power, freqs, *, low_hz: float = KEY_LOW_HZ, high_hz: float = KEY_HIGH_HZ):
    """Energy per pitch class, C first, scaled so the strongest is 1."""
    np = _numpy()
    energy = power.sum(axis=0)
    mask = (freqs >= low_hz) & (freqs <= high_hz)
    if not mask.any():
        raise BeatError("no spectrum inside the key-finding band")
    # MIDI-style pitch class: 440 Hz is A = 9 when C = 0.
    classes = (np.round(12.0 * np.log2(freqs[mask] / 440.0)).astype(int) + 9) % 12
    chroma = np.zeros(12, dtype="float64")
    np.add.at(chroma, classes, energy[mask])
    peak = float(chroma.max())
    if peak <= 0.0:
        raise BeatError("the audio is silent")
    return chroma / peak


def bass_root(power, freqs, *, low_hz: float = BASS_LOW_HZ, high_hz: float = BASS_HIGH_HZ) -> str | None:
    """The pitch class the bass peaks on most often — the note the 808 is tuned to.

    A frame votes with the single loudest bin in the bass band; the class with the most
    frames wins. On a sub-heavy beat this is the least ambiguous evidence of the tonic
    there is: the chroma above can be argued into the relative major, the 808 cannot.
    """
    np = _numpy()
    mask = (freqs >= low_hz) & (freqs <= high_hz)
    if not mask.any() or power.shape[0] == 0:
        return None
    band = power[:, mask]
    # Frames with no real bass energy (the intro, the outro) do not get a vote.
    strength = band.max(axis=1)
    loud = strength >= float(np.percentile(strength, 50))
    if not loud.any() or float(strength[loud].max()) <= 0.0:
        return None
    # Parabolic interpolation around the peak bin: at 2.7 Hz per bin, E1 (41.2 Hz) and
    # F1 (43.7 Hz) share a bin, and the vote would be decided by rounding.
    idx = band[loud].argmax(axis=1)
    rows = np.arange(idx.size)
    lo_i = np.clip(idx - 1, 0, band.shape[1] - 1)
    hi_i = np.clip(idx + 1, 0, band.shape[1] - 1)
    a, b, c = band[loud][rows, lo_i], band[loud][rows, idx], band[loud][rows, hi_i]
    denom = a - 2.0 * b + c
    shift = np.where(denom != 0.0, 0.5 * (a - c) / np.where(denom != 0.0, denom, 1.0), 0.0)
    bin_hz = float(freqs[1] - freqs[0]) if freqs.size > 1 else 0.0
    peaks = freqs[mask][idx] + np.clip(shift, -0.5, 0.5) * bin_hz
    classes = (np.round(12.0 * np.log2(peaks / 440.0)).astype(int) + 9) % 12
    counts = np.bincount(classes, minlength=12)
    return PITCH_CLASSES[int(counts.argmax())]


def estimate_key(chroma, *, bass_root: str | None = None) -> KeyEstimate:
    """Krumhansl–Schmuckler: the key whose profile correlates best with the chroma.

    ``bass_root`` breaks the tie the profiles cannot: a minor key and its relative major
    share every note, so they score within a hair of each other on any chroma. When the
    bass sits on the tonic of a runner-up that is nearly level with the winner, the
    runner-up is the key the track is actually in.
    """
    np = _numpy()
    chroma = np.asarray(chroma, dtype="float64")
    if chroma.shape != (12,):
        raise BeatError("chroma must have twelve pitch classes")
    scored: list[tuple[float, str, str]] = []
    for mode, profile in (("major", _MAJOR), ("minor", _MINOR)):
        prof = np.asarray(profile, dtype="float64")
        for tonic in range(12):
            r = float(np.corrcoef(np.roll(prof, tonic), chroma)[0, 1])
            scored.append((0.0 if np.isnan(r) else r, PITCH_CLASSES[tonic], mode))
    scored.sort(key=lambda t: t[0], reverse=True)
    best = scored[0]
    if bass_root and best[1] != bass_root:
        for cand in scored[1:4]:
            if cand[1] == bass_root and cand[0] >= best[0] - BASS_TIE_MARGIN:
                best = cand
                break
    others = [s for s in scored if s is not best][:3]
    return KeyEstimate(
        tonic=best[1],
        mode=best[2],
        confidence=max(0.0, best[0]),
        chroma=[float(c) for c in chroma],
        alternatives=[(f"{t} {m}", r) for r, t, m in others],
        bass_root=bass_root,
    )


def band_shares(power, freqs, bands=BANDS) -> dict[str, float]:
    """What fraction of the energy sits in each band."""
    energy = power.sum(axis=0)
    total = float(energy.sum())
    if total <= 0.0:
        raise BeatError("the audio is silent")
    out: dict[str, float] = {}
    for name, lo, hi in bands:
        mask = (freqs >= lo) & (freqs < hi)
        out[name] = float(energy[mask].sum()) / total
    return out


def loudness_sections(samples, *, sample_rate: int, window_s: float = SECTION_WINDOW_S,
                      step_db: float = SECTION_STEP_DB) -> list[Section]:
    """Split the track where its loudness steps — the arrangement, read from the envelope.

    Labels are positional and relative: the quieter opening is the ``intro``, a quieter
    stretch in the middle is a ``breakdown``, the quieter tail the ``outro``, and the
    loudest plateau is ``main``. It cannot tell a verse from a hook — both are the full
    beat — but it finds every place the beat drops out, which is what a clip is cut on.
    """
    np = _numpy()
    win = max(1, int(window_s * sample_rate))
    count = samples.size // win
    if count < 2:
        rms = float(np.sqrt(np.mean(samples.astype("float64") ** 2)))
        level = 20.0 * np.log10(rms + 1e-9)
        return [Section(0.0, samples.size / sample_rate, level, "main")]
    frames = samples[: count * win].reshape(count, win).astype("float64")
    levels = 20.0 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
    # Merge consecutive windows while they stay within the step of the run's mean.
    runs: list[list[int]] = [[0]]
    for i in range(1, count):
        current = runs[-1]
        mean = float(np.mean(levels[current]))
        if abs(levels[i] - mean) <= step_db:
            current.append(i)
        else:
            runs.append([i])
    duration = samples.size / sample_rate
    sections = [
        Section(r[0] * window_s, min(duration, (r[-1] + 1) * window_s), float(np.mean(levels[r])), "main")
        for r in runs
    ]
    if sections:
        sections[-1].end = duration
    peak = max(s.level_db for s in sections)
    mains = [i for i, s in enumerate(sections) if s.level_db >= peak - step_db]
    first_main, last_main = (mains[0], mains[-1]) if mains else (0, len(sections) - 1)
    for idx, s in enumerate(sections):
        if idx in mains:
            s.label = "main"
        elif idx < first_main:
            s.label = "intro"
        elif idx > last_main:
            s.label = "outro"
        else:
            s.label = "breakdown"
    # A build-up that steps louder every few bars is one intro, not five.
    merged: list[Section] = []
    for s in sections:
        if merged and merged[-1].label == s.label:
            prev = merged[-1]
            w_prev, w_cur = prev.end - prev.start, s.end - s.start
            prev.level_db = (prev.level_db * w_prev + s.level_db * w_cur) / max(w_prev + w_cur, 1e-9)
            prev.end = s.end
        else:
            merged.append(s)
    return merged


def profile(path: Path | str, *, bpm: float | None = None) -> AudioProfile:
    """Measure a track. ``bpm`` is the same hint :func:`navig.media.beats.detect` takes."""
    np = _numpy()
    src = Path(path)
    samples = decode(src, sample_rate=SAMPLE_RATE)
    if not np.any(samples):
        raise BeatError(f"{src.name} is silent")
    grid = detect(src, bpm=bpm)
    power, freqs = _spectrum(samples, sample_rate=SAMPLE_RATE)
    # Magnitude, not power, for the key vote: power lets one loud bass note outvote the
    # whole harmony; magnitude keeps the sample's chords in the count.
    magnitude = np.sqrt(power)
    bass_power = _spectrum(samples, sample_rate=SAMPLE_RATE, frame=BASS_FRAME, hop=BASS_FRAME // 2)
    rms = float(np.sqrt(np.mean(samples.astype("float64") ** 2)))
    return AudioProfile(
        path=str(src),
        duration_s=samples.size / SAMPLE_RATE,
        bpm=grid.bpm,
        beat_confidence=grid.confidence,
        key=estimate_key(
            chroma_from_power(magnitude, freqs), bass_root=bass_root(*bass_power),
        ),
        loudness_dbfs=20.0 * float(np.log10(rms + 1e-9)),
        peak_dbfs=20.0 * float(np.log10(float(np.abs(samples).max()) + 1e-9)),
        bands=band_shares(power, freqs),
        sections=loudness_sections(samples, sample_rate=SAMPLE_RATE),
    )
