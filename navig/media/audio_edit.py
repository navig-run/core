"""Audio editing — change speed, with or without changing pitch.

Two different things people mean by "slow it down", and conflating them is the whole
reason this module exists rather than one ffmpeg flag:

* **tempo** — play it slower, keep the voices/instruments at the same pitch. ffmpeg's
  ``atempo``. This is what you want for a lecture at 1.5x or a voice note at 0.8x.
* **rate** — play the samples slower, so the pitch drops with it. ffmpeg's ``asetrate``.
  This is the "slowed" aesthetic (and what a tape machine does), and it is NOT what
  ``atempo`` produces.

``speed()`` does the first, ``slowed()`` the second.

The other half of this module is **assembly**: :func:`concat` joins clips into one file
and :func:`normalize` masters the result to the broadcast loudness target. Together they
are what turns a pile of separately-generated clips into something publishable, and
:func:`probe_duration` is how anything downstream (chapter marks, subtitle offsets)
learns where each piece actually landed.

⚠ ``atempo`` accepts a factor of **0.5–2.0 per instance**. A naive
``atempo={rate}`` therefore silently fails (older ffmpeg) or is rejected outright for
anything outside that window — so 0.25x and 4x, the two most obvious things a user
tries first, are exactly the cases a one-liner gets wrong. :func:`tempo_chain` splits
any positive factor into a chain of in-range ones whose product is the request.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from navig.core.proc_text import decode_console_result

# ffmpeg's per-instance atempo limits. The filter has been relaxed upstream over the
# years, but chaining inside the historical window works on every build — and a filter
# graph the user's ffmpeg rejects is a failure they cannot act on.
_ATEMPO_MIN = 0.5
_ATEMPO_MAX = 2.0

# A slowed track with no space around it sounds flat; this is a modest hall, not a cave.
# aecho ships with stock ffmpeg (unlike rubberband/areverb, which need extra libs) —
# an effect the user's build cannot run is worse than a plainer one it can.
_REVERB_FILTER = "aecho=0.8:0.88:60:0.4"

# A conversion that hangs holds a Telegram callback (and a gateway worker) open forever.
DEFAULT_TIMEOUT_S = 300


class AudioEditError(RuntimeError):
    """ffmpeg is missing, refused the graph, or produced nothing usable."""


@dataclass(frozen=True)
class EditResult:
    path: Path
    rate: float
    pitch_shifted: bool
    filters: str


def tempo_chain(rate: float) -> list[float]:
    """Split ``rate`` into atempo factors, each within ffmpeg's 0.5–2.0 window.

    The product of the returned factors is ``rate`` (to floating-point precision), so
    ``0.25`` becomes ``[0.5, 0.5]`` and ``4`` becomes ``[2.0, 2.0]``.
    """
    if rate <= 0:
        raise ValueError(f"rate must be positive, got {rate}")
    factors: list[float] = []
    remaining = float(rate)
    while remaining > _ATEMPO_MAX:
        factors.append(_ATEMPO_MAX)
        remaining /= _ATEMPO_MAX
    while remaining < _ATEMPO_MIN:
        factors.append(_ATEMPO_MIN)
        remaining /= _ATEMPO_MIN
    factors.append(round(remaining, 6))
    return factors


def tempo_filter(rate: float) -> str:
    """The ``atempo`` filter string for ``rate`` (pitch preserved)."""
    return ",".join(f"atempo={f:g}" for f in tempo_chain(rate))


def rate_filter(rate: float, sample_rate: int) -> str:
    """The pitch-shifting slowdown filter — resample, then restore the output rate.

    ``asetrate`` alone leaves the stream claiming a nonstandard sample rate, which some
    encoders reject and some players resample differently; ``aresample`` pins it back so
    the file is ordinary everywhere.
    """
    if rate <= 0:
        raise ValueError(f"rate must be positive, got {rate}")
    if sample_rate <= 0:
        raise ValueError(f"sample_rate must be positive, got {sample_rate}")
    return f"asetrate={int(round(sample_rate * rate))},aresample={sample_rate}"


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _require_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise AudioEditError(
            "ffmpeg is not installed or not on PATH — install it (scoop install ffmpeg / "
            "brew install ffmpeg / apt install ffmpeg) and try again"
        )
    return exe


def probe_sample_rate(src: Path, timeout: int = 30) -> int:
    """The source's sample rate, needed to build a pitch-shifting filter.

    Guessing here is not an option: `asetrate` is relative to the REAL rate, so a wrong
    guess changes the speed as well as the pitch — a plausible-sounding wrong answer,
    which is worse than an error.
    """
    exe = shutil.which("ffprobe")
    if not exe:
        raise AudioEditError("ffprobe is not on PATH (it ships with ffmpeg) — cannot read the sample rate")
    try:
        out = decode_console_result(subprocess.run(
            [exe, "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=sample_rate", "-of", "default=noprint_wrappers=1:nokey=1", str(src)],
            capture_output=True, timeout=timeout, check=False,
        ))
    except subprocess.TimeoutExpired as exc:
        raise AudioEditError(f"ffprobe timed out reading {src.name}") from exc
    value = (out.stdout or "").strip().splitlines()
    if not value or not value[0].isdigit():
        raise AudioEditError(f"could not read a sample rate from {src.name} — is it an audio file?")
    return int(value[0])


def _exec_ffmpeg(cmd: list[str], dst: Path, timeout: int, subject: str) -> None:
    """Run an ffmpeg command and insist it actually produced audio.

    Shared by every writer here so that "it exited 0" is never mistaken for "it worked".
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = decode_console_result(subprocess.run(cmd, capture_output=True, timeout=timeout, check=False))
    except subprocess.TimeoutExpired as exc:
        dst.unlink(missing_ok=True)  # a truncated file is worse than none
        raise AudioEditError(f"ffmpeg timed out after {timeout}s on {subject}") from exc
    if proc.returncode != 0:
        # ffmpeg's last stderr line is the actionable one; the rest is banner noise.
        tail = (proc.stderr or "").strip().splitlines()
        raise AudioEditError(f"ffmpeg failed: {tail[-1] if tail else 'no output'}")
    # Exit 0 with no/empty output is a real ffmpeg outcome (an unreadable stream), and
    # reporting success for a file that is not there is the failure mode we refuse.
    if not dst.exists() or dst.stat().st_size == 0:
        dst.unlink(missing_ok=True)
        raise AudioEditError(f"ffmpeg reported success but wrote no audio for {subject}")


def _run_ffmpeg(src: Path, dst: Path, afilter: str, timeout: int) -> None:
    exe = _require_ffmpeg()
    cmd = [exe, "-nostdin", "-y", "-i", str(src), "-filter:a", afilter, "-vn", str(dst)]
    _exec_ffmpeg(cmd, dst, timeout, src.name)


def speed(src: Path, dst: Path, rate: float, *, timeout: int = DEFAULT_TIMEOUT_S) -> EditResult:
    """Change tempo, keeping pitch — 1.5 is faster, 0.75 slower, voices unchanged."""
    afilter = tempo_filter(rate)
    _run_ffmpeg(src, dst, afilter, timeout)
    return EditResult(path=dst, rate=rate, pitch_shifted=False, filters=afilter)


def slowed(
    src: Path, dst: Path, rate: float = 0.85, *, reverb: bool = True,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> EditResult:
    """The "slowed" sound — pitch drops with the speed, optionally with reverb."""
    sample_rate = probe_sample_rate(src)
    parts = [rate_filter(rate, sample_rate)]
    if reverb:
        parts.append(_REVERB_FILTER)
    afilter = ",".join(parts)
    _run_ffmpeg(src, dst, afilter, timeout)
    return EditResult(path=dst, rate=rate, pitch_shifted=True, filters=afilter)


# ── assembly: joining clips and mastering the result ──────────────────────────


def probe_duration(src: Path, timeout: int = 30) -> float:
    """Length of ``src`` in seconds.

    Everything downstream positions itself with this — chapter marks, subtitle offsets,
    the shot a caption belongs to — so a wrong answer does not fail loudly, it quietly
    desynchronises every later piece. A file ffprobe cannot read is therefore an error,
    never a 0.0.
    """
    exe = shutil.which("ffprobe")
    if not exe:
        raise AudioEditError("ffprobe is not on PATH (it ships with ffmpeg) — cannot read the duration")
    try:
        out = decode_console_result(subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(src)],
            capture_output=True, timeout=timeout, check=False,
        ))
    except subprocess.TimeoutExpired as exc:
        raise AudioEditError(f"ffprobe timed out reading {src.name}") from exc
    raw = (out.stdout or "").strip().splitlines()
    try:
        # ffprobe prints "N/A" for a stream it opened but could not measure.
        return float(raw[0])
    except (IndexError, ValueError) as exc:
        raise AudioEditError(f"could not read a duration from {src.name} — is it an audio file?") from exc


def _concat_list_file(parts: list[Path], work_dir: Path) -> Path:
    """Write the concat demuxer's manifest.

    Two escaping rules, both of which cost real debugging time when missed: the demuxer
    treats ``'`` as a quote delimiter (so ``l'intro.mp3`` is a parse error that never
    mentions quoting), and a Windows ``\\`` is an escape character to it. The scripts
    driving this are French, so the apostrophe case is routine, not hypothetical.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    listing = work_dir / "concat.txt"
    lines = []
    for part in parts:
        literal = str(part.resolve()).replace("\\", "/").replace("'", r"'\''")
        lines.append(f"file '{literal}'")
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return listing


def _silence(seconds: float, like: Path, dst: Path, timeout: int) -> Path:
    """A silent clip shaped like ``like`` so it can join the stream-copy path."""
    exe = _require_ffmpeg()
    rate = probe_sample_rate(like)
    cmd = [
        exe, "-nostdin", "-y", "-f", "lavfi",
        "-i", f"anullsrc=r={rate}:cl=stereo", "-t", f"{seconds:g}",
        "-c:a", "libmp3lame", "-q:a", "4", str(dst),
    ]
    _exec_ffmpeg(cmd, dst, timeout, f"{seconds:g}s of silence")
    return dst


def concat(
    parts: list[Path], dst: Path, *, gap_s: float = 0.0,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> EditResult:
    """Join ``parts`` into ``dst``, optionally with ``gap_s`` of silence between them.

    Stream-copies when the inputs allow it and re-encodes when they do not — speech and a
    generated music cue rarely share a sample rate, and a slightly re-compressed episode
    beats no episode. The copy path is *verified by duration* rather than trusted: mixing
    sample rates is a case where ffmpeg exits 0 and writes a file that plays at the wrong
    length, which is precisely the silent corruption this function exists to prevent.
    """
    if not parts:
        raise AudioEditError("nothing to join — the part list is empty")
    missing = [p for p in parts if not p.exists()]
    if missing:
        raise AudioEditError(f"missing input(s): {', '.join(p.name for p in missing)}")
    if gap_s < 0:
        raise ValueError(f"gap_s must be zero or positive, got {gap_s}")

    # One part is already the answer; re-muxing it would only lose a generation of
    # quality and change the bytes for no benefit.
    if len(parts) == 1:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(parts[0], dst)
        return EditResult(path=dst, rate=1.0, pitch_shifted=False, filters="copy")

    exe = _require_ffmpeg()
    with tempfile.TemporaryDirectory(prefix="navig-concat-") as tmp:
        work = Path(tmp)
        sequence = list(parts)
        expected = sum(probe_duration(p) for p in parts)
        if gap_s > 0:
            pad = _silence(gap_s, parts[0], work / "gap.mp3", timeout)
            spaced: list[Path] = []
            for i, part in enumerate(sequence):
                if i:
                    spaced.append(pad)
                spaced.append(part)
            sequence = spaced
            expected += gap_s * (len(parts) - 1)

        listing = _concat_list_file(sequence, work)
        base = [exe, "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", str(listing)]

        try:
            _exec_ffmpeg([*base, "-c", "copy", str(dst)], dst, timeout, dst.name)
            # Tolerance covers mp3 frame quantisation, not a wrong sample rate.
            if abs(probe_duration(dst) - expected) <= max(0.15, expected * 0.02):
                return EditResult(path=dst, rate=1.0, pitch_shifted=False, filters="concat:copy")
        except AudioEditError:
            pass  # the inputs differ; the re-encode below is the fallback, not a failure

        _exec_ffmpeg([*base, "-c:a", "libmp3lame", "-q:a", "2", str(dst)], dst, timeout, dst.name)
        return EditResult(path=dst, rate=1.0, pitch_shifted=False, filters="concat:encode")


def normalize(
    src: Path, dst: Path, *, i: float = -16.0, tp: float = -1.5, lra: float = 11.0,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> EditResult:
    """Master to the EBU R128 loudness target (-16 LUFS: the podcast/spoken standard).

    Platforms normalise toward a target anyway; doing it here means the result sounds the
    same everywhere instead of being quietly turned up or down by each one.
    """
    afilter = f"loudnorm=I={i:g}:TP={tp:g}:LRA={lra:g}"
    _run_ffmpeg(src, dst, afilter, timeout)
    return EditResult(path=dst, rate=1.0, pitch_shifted=False, filters=afilter)
