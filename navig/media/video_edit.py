"""Video assembly — turn a pile of clips, stills and a soundtrack into one deliverable.

The sibling of :mod:`navig.media.audio_edit`, and deliberately built the same way:
subprocess ffmpeg (no binding to go stale), an explicit timeout on every call, and a
refusal to report success for a file that is not there or is the wrong length.

What it is for is vertical short-form — 1080x1920 for TikTok / Shorts / Reels — so two
opinions are baked in:

* **Fill and crop, never letterbox.** Black bars on a phone read as an upload mistake,
  and on TikTok they collide with the UI chrome. :func:`to_vertical` always fills.
* **Hard cuts by default.** Fast cutting holds attention on a feed; crossfades are
  available via ``transition="xfade"`` but they are not the default for a reason.

⚠ The single sharpest edge in here is :func:`burn_captions`. ffmpeg's ``subtitles``
filter parses its own argument, so a Windows path (``C:\\x``) is read as a filter
option separator plus an escape sequence, and it fails with a message about neither.
:func:`escape_filter_path` is the fix and is tested on its own.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from navig.core.proc_text import decode_console_result

# Encoding a minute of 1080x1920 takes appreciably longer than any audio filter, and a
# capture-plus-encode chain is slower still.
DEFAULT_TIMEOUT_S = 600

# TikTok / Shorts / Reels. Anything else is a crop away.
VERTICAL_W = 1080
VERTICAL_H = 1920
DEFAULT_FPS = 30

# yuv420p is not a preference: without it, an even slightly unusual pixel format
# produces a file that plays in VLC and is black in every browser and on every phone.
_PIX_FMT = "yuv420p"


class VideoEditError(RuntimeError):
    """ffmpeg is missing, refused the graph, or produced nothing usable."""


@dataclass(frozen=True)
class RenderResult:
    path: Path
    width: int
    height: int
    duration_s: float
    filters: str


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _require(tool: str) -> str:
    exe = shutil.which(tool)
    if not exe:
        raise VideoEditError(
            f"{tool} is not installed or not on PATH — install ffmpeg "
            "(scoop install ffmpeg / brew install ffmpeg / apt install ffmpeg) and try again"
        )
    return exe


def _exec(cmd: list[str], dst: Path, timeout: int, subject: str) -> None:
    """Run ffmpeg and insist it actually wrote something."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = decode_console_result(
            subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
        )
    except subprocess.TimeoutExpired as exc:
        dst.unlink(missing_ok=True)  # a truncated video is worse than none
        raise VideoEditError(f"ffmpeg timed out after {timeout}s on {subject}") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()
        raise VideoEditError(f"ffmpeg failed: {tail[-1] if tail else 'no output'}")
    if not dst.exists() or dst.stat().st_size == 0:
        dst.unlink(missing_ok=True)
        raise VideoEditError(f"ffmpeg reported success but wrote no video for {subject}")


def probe(src: Path, timeout: int = 60) -> dict[str, float | int]:
    """``{duration, width, height, fps}``, raising rather than guessing.

    :func:`navig.media.frames.probe` returns ``{}`` on failure, which is right for a
    best-effort briefing and wrong here: every timing decision downstream is computed
    from these numbers, so an unreadable file has to stop the render, not become a zero.
    """
    exe = _require("ffprobe")
    try:
        out = decode_console_result(subprocess.run(
            [exe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1", str(src)],
            capture_output=True, timeout=timeout, check=False,
        ))
    except subprocess.TimeoutExpired as exc:
        raise VideoEditError(f"ffprobe timed out reading {src.name}") from exc
    fields: dict[str, str] = {}
    for line in (out.stdout or "").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            fields[key.strip()] = value.strip()
    # ffprobe prints nothing to stdout for a file it cannot open, so without this the
    # parse below succeeds on an empty dict and hands back a plausible-looking set of
    # zeros — the exact "wrong answer instead of an error" this function exists to avoid.
    if not fields.get("width") or not fields.get("height"):
        raise VideoEditError(f"no video stream found in {src.name} — is it a video file?")
    try:
        num, _, den = (fields.get("r_frame_rate") or "0/1").partition("/")
        fps = float(num) / float(den) if float(den or 0) else 0.0
        return {
            "duration": float(fields.get("duration") or 0.0),
            "width": int(fields.get("width") or 0),
            "height": int(fields.get("height") or 0),
            "fps": round(fps, 3),
        }
    except (TypeError, ValueError) as exc:
        raise VideoEditError(f"could not read video properties from {src.name}") from exc


def escape_filter_path(path: Path | str) -> str:
    """Make a path safe to embed inside an ffmpeg filter argument.

    Three separate escapes, and every one of them is load-bearing on Windows:
    ``\\`` is an escape character to the filter parser, ``:`` separates filter options
    (so ``C:`` ends the argument early), and ``'`` closes the quoting. Getting this wrong
    produces an error that mentions none of the above.
    """
    return str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def vertical_filter(width: int = VERTICAL_W, height: int = VERTICAL_H) -> str:
    """Scale to cover, then crop to size — fills the frame, never letterboxes."""
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1"
    )


def to_vertical(
    src: Path, dst: Path, *, width: int = VERTICAL_W, height: int = VERTICAL_H,
    fps: int = DEFAULT_FPS, timeout: int = DEFAULT_TIMEOUT_S,
) -> RenderResult:
    """Reframe any clip to vertical, filling the frame."""
    exe = _require("ffmpeg")
    vf = f"{vertical_filter(width, height)},format={_PIX_FMT}"
    _exec(
        [exe, "-nostdin", "-y", "-i", str(src), "-vf", vf, "-r", str(fps),
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-an", str(dst)],
        dst, timeout, src.name,
    )
    info = probe(dst)
    return RenderResult(dst, int(info["width"]), int(info["height"]), float(info["duration"]), vf)


def still(
    image: Path, dst: Path, *, secs: float, width: int = VERTICAL_W,
    height: int = VERTICAL_H, fps: int = DEFAULT_FPS, motion: str = "none",
    zoom: float = 1.12, timeout: int = DEFAULT_TIMEOUT_S,
) -> RenderResult:
    """Turn a still into a clip, optionally with a slow push in (``motion="kenburns"``).

    The image is scaled up before ``zoompan`` runs: zoompan computes its crop on the
    input resolution, so panning a frame-sized image produces visible per-frame jitter.
    Oversampling first is the documented workaround.
    """
    if secs <= 0:
        raise ValueError(f"secs must be positive, got {secs}")
    exe = _require("ffmpeg")
    frames = max(1, int(round(secs * fps)))
    if motion in {"kenburns", "zoom", "drift"}:
        step = (zoom - 1.0) / frames
        vf = (
            f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
            f"crop={width * 2}:{height * 2},"
            f"zoompan=z='min(1+{step:.6f}*on,{zoom:g})'"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={frames}:s={width}x{height}:fps={fps},"
            f"setsar=1,format={_PIX_FMT}"
        )
    else:
        vf = f"{vertical_filter(width, height)},format={_PIX_FMT}"
    _exec(
        [exe, "-nostdin", "-y", "-loop", "1", "-i", str(image), "-t", f"{secs:g}",
         "-vf", vf, "-r", str(fps), "-c:v", "libx264", "-preset", "medium",
         "-crf", "18", "-an", str(dst)],
        dst, timeout, image.name,
    )
    info = probe(dst)
    return RenderResult(dst, int(info["width"]), int(info["height"]), float(info["duration"]), vf)


def fit_duration(
    src: Path, dst: Path, *, secs: float, fps: int = DEFAULT_FPS,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> RenderResult:
    """Force a clip to be exactly ``secs`` long — trimming it, or looping it to fill.

    Generated footage arrives at whatever length the model chose, which is never the
    length the edit needs. Dropping it in as-is is the same desync that a mis-timed
    capture causes: the picture and the voice diverge, and it only shows up on playback.

    Longer is trimmed. Shorter is looped rather than frozen — a held frame under
    continuing narration reads as a stall, where a loop of ambient footage does not.
    """
    if secs <= 0:
        raise ValueError(f"secs must be positive, got {secs}")
    exe = _require("ffmpeg")
    have = float(probe(src)["duration"])
    cmd = [exe, "-nostdin", "-y"]
    if have + 0.05 < secs:
        cmd += ["-stream_loop", "-1"]
    cmd += [
        "-i", str(src), "-t", f"{secs:g}", "-r", str(fps),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", _PIX_FMT, "-an", str(dst),
    ]
    _exec(cmd, dst, timeout, src.name)
    info = probe(dst)
    return RenderResult(
        dst, int(info["width"]), int(info["height"]), float(info["duration"]),
        "trim" if have >= secs else "loop",
    )


def _concat_list_file(parts: list[Path], work_dir: Path) -> Path:
    """The concat demuxer's manifest — same escaping rules as the audio one."""
    work_dir.mkdir(parents=True, exist_ok=True)
    listing = work_dir / "concat.txt"
    listing.write_text(
        "\n".join(
            "file '{}'".format(str(p.resolve()).replace("\\", "/").replace("'", r"'\''"))
            for p in parts
        )
        + "\n",
        encoding="utf-8",
    )
    return listing


def join(
    parts: list[Path], dst: Path, *, transition: str = "cut", transition_s: float = 0.4,
    effect: str = "fade", fps: int = DEFAULT_FPS, timeout: int = DEFAULT_TIMEOUT_S,
) -> RenderResult:
    """Join clips end to end. ``transition="xfade"`` crossfades instead of cutting.

    ``effect`` names any of ffmpeg's xfade transitions — ``fade`` (the default),
    ``fadeblack``, ``pixelize``, ``radial``, ``wipeleft``, ``squeezev`` and the rest. It
    was hardcoded to ``fade`` before, which is the one transition that reads as a mistake
    on a hard-cut format: a slow dissolve between two shots of a music video looks like a
    slideshow, where a black flash or a pixelate reads as an edit.

    Like the audio :func:`~navig.media.audio_edit.concat`, the stream-copy path is
    verified by duration rather than trusted — joining clips that differ in codec or
    frame rate is a case where ffmpeg exits 0 and writes something the wrong length.
    """
    if not parts:
        raise VideoEditError("nothing to join — the part list is empty")
    missing = [p for p in parts if not p.exists()]
    if missing:
        raise VideoEditError(f"missing input(s): {', '.join(p.name for p in missing)}")
    if transition_s < 0:
        raise ValueError(f"transition_s must be zero or positive, got {transition_s}")

    if len(parts) == 1:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(parts[0], dst)
        info = probe(dst)
        return RenderResult(dst, int(info["width"]), int(info["height"]), float(info["duration"]), "copy")

    exe = _require("ffmpeg")
    durations = [float(probe(p)["duration"]) for p in parts]

    if transition == "xfade" and transition_s > 0:
        # Each crossfade eats `transition_s` of runtime, and every offset is cumulative:
        # a fade's offset is measured on the chain built so far, not on the input.
        graph, prev, elapsed = [], "0:v", durations[0]
        for i in range(1, len(parts)):
            offset = max(0.0, elapsed - transition_s)
            label = f"v{i}"
            graph.append(
                f"[{prev}][{i}:v]xfade=transition={effect}:duration={transition_s:g}"
                f":offset={offset:g}[{label}]"
            )
            prev = label
            elapsed = offset + transition_s + durations[i] - transition_s
        cmd = [exe, "-nostdin", "-y"]
        for part in parts:
            cmd += ["-i", str(part)]
        cmd += [
            "-filter_complex", ";".join(graph), "-map", f"[{prev}]",
            "-r", str(fps), "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", _PIX_FMT, "-an", str(dst),
        ]
        _exec(cmd, dst, timeout, dst.name)
        info = probe(dst)
        return RenderResult(
            dst, int(info["width"]), int(info["height"]), float(info["duration"]), "xfade"
        )

    expected = sum(durations)
    with tempfile.TemporaryDirectory(prefix="navig-vjoin-") as tmp:
        listing = _concat_list_file(parts, Path(tmp))
        base = [exe, "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", str(listing)]
        try:
            _exec([*base, "-c", "copy", str(dst)], dst, timeout, dst.name)
            if abs(float(probe(dst)["duration"]) - expected) <= max(0.2, expected * 0.02):
                info = probe(dst)
                return RenderResult(
                    dst, int(info["width"]), int(info["height"]),
                    float(info["duration"]), "concat:copy",
                )
        except VideoEditError:
            pass  # inputs differ; re-encoding below is the fallback, not a failure
        _exec(
            [*base, "-r", str(fps), "-c:v", "libx264", "-preset", "medium", "-crf", "18",
             "-pix_fmt", _PIX_FMT, "-an", str(dst)],
            dst, timeout, dst.name,
        )
    info = probe(dst)
    return RenderResult(
        dst, int(info["width"]), int(info["height"]), float(info["duration"]), "concat:encode"
    )


def from_frames(
    frames: list[tuple[Path, float]], dst: Path, *, fps: int = DEFAULT_FPS,
    width: int | None = None, height: int | None = None,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> RenderResult:
    """Assemble ``(image, seconds_on_screen)`` pairs into a video.

    This exists for **screencast capture**, where the frames do NOT arrive at a fixed
    rate: the browser emits one when the page paints, so a fast animation yields frames
    milliseconds apart and a static page yields almost none. Encoding that pile at a
    constant ``-r 30`` stretches the idle stretches and compresses the busy ones — the
    video plays at subtly the wrong speed and drifts against a voiceover, which is
    exactly the failure that is easy to ship and hard to diagnose.

    Timing is encoded by **repeating each frame** for as many 1/fps ticks as it was
    actually on screen, rather than with the concat demuxer's ``duration`` directive.
    That directive's semantics around the final entry are genuinely surprising — measured
    on ffmpeg 8, the same ten frames come out 1.23s without a trailing repeat and 2.77s
    with one, against a true 2.00s — so it is not a safe foundation. Repetition has no
    such ambiguity: the output is exactly ``frames / fps`` seconds long.

    Frame counts are derived from **cumulative** elapsed time, not per-frame rounding.
    Rounding each frame independently biases every short frame upward (a 0.05s frame at
    30fps rounds 1.5 up to 2) and the error compounds across a long capture.
    """
    if not frames:
        raise VideoEditError("no frames captured — nothing to assemble")
    missing = [p for p, _ in frames if not p.exists()]
    if missing:
        raise VideoEditError(f"missing frame(s): {', '.join(p.name for p in missing[:3])}")
    if any(d < 0 for _, d in frames):
        raise ValueError("frame durations must be zero or positive")

    exe = _require("ffmpeg")
    with tempfile.TemporaryDirectory(prefix="navig-frames-") as tmp:
        listing = Path(tmp) / "frames.txt"
        lines: list[str] = []
        elapsed = 0.0
        emitted = 0
        last_literal = ""
        for path, seconds in frames:
            elapsed += seconds
            literal = str(path.resolve()).replace("\\", "/").replace("'", r"'\''")
            last_literal = literal
            # Every frame is placed against the running total, so a rounding decision
            # here cannot leak into the next one.
            #
            # `ticks` may legitimately be ZERO, and must be allowed to be: a browser
            # screencast paints at ~60fps, so at fps=30 half the frames belong to an
            # output tick that is already taken and have to be dropped. Flooring this at
            # 1 (as the first version did) gives every input frame a tick of its own and
            # the video comes out at exactly the ratio of the two rates — a 3s capture
            # played back over 6s, in perfect sync with nothing.
            ticks = int(round(elapsed * fps)) - emitted
            if ticks <= 0:
                continue
            lines.extend([f"file '{literal}'"] * ticks)
            emitted += ticks
        if not lines:
            # A capture shorter than a single output tick still has to produce a file.
            lines.append(f"file '{last_literal}'")
        listing.write_text("\n".join(lines) + "\n", encoding="utf-8")

        vf = f"{vertical_filter(width, height)},format={_PIX_FMT}" if width and height else f"format={_PIX_FMT}"
        _exec(
            [exe, "-nostdin", "-y", "-f", "concat", "-safe", "0",
             "-r", str(fps), "-i", str(listing),  # -r BEFORE -i: one tick per entry
             "-vf", vf, "-r", str(fps),
             "-c:v", "libx264", "-preset", "medium", "-crf", "18", str(dst)],
            dst, timeout, dst.name,
        )
    info = probe(dst)
    return RenderResult(
        dst, int(info["width"]), int(info["height"]), float(info["duration"]), "frames"
    )


# Short-form apps put their own chrome over the bottom of the frame — caption, handle,
# music pill and the action rail. Measured against TikTok's 1080x1920 layout, the bottom
# ~340px is covered; captions sitting in the default ~40px margin are simply not read.
SAFE_BOTTOM_PX = 380

# ⚠ `force_style` values are in the SUBTITLE SCRIPT's coordinate space, not the video's.
# A plain SRT carries no PlayRes, so libass assumes a 288-tall script and scales up —
# measured on this build, one MarginV unit moves the text 6.68 video pixels. Passing a
# pixel value straight through (MarginV=380) therefore means ~2,536px and puts the
# caption off the top of the frame, where it renders as nothing at all rather than as an
# error. Fontsize has the same trap: 52 there is ~350px here.
_ASS_PLAYRES_Y = 288
_MARGIN_V = round(SAFE_BOTTOM_PX * _ASS_PLAYRES_Y / VERTICAL_H)

# Font size is deliberately NOT overridden — libass's default already lands close to
# right once scaled, and a number that looks reasonable here is enormous on screen.
CAPTION_STYLE = f"Bold=1,BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV={_MARGIN_V}"


def apply_filter(
    src: Path, dst: Path, vf: str, *, fps: int = DEFAULT_FPS,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> RenderResult:
    """Run an arbitrary filter chain over a clip — the seam a *look* plugs into.

    An empty chain copies rather than re-encodes: a look that disables every effect
    should cost nothing and lose no quality, not silently put the video through another
    generation of x264.
    """
    if not vf.strip():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        info = probe(dst)
        return RenderResult(
            dst, int(info["width"]), int(info["height"]), float(info["duration"]), "copy"
        )
    exe = _require("ffmpeg")
    _exec(
        [exe, "-nostdin", "-y", "-i", str(src), "-vf", f"{vf},format={_PIX_FMT}",
         "-r", str(fps), "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-c:a", "copy", str(dst)],
        dst, timeout, src.name,
    )
    info = probe(dst)
    return RenderResult(
        dst, int(info["width"]), int(info["height"]), float(info["duration"]), vf
    )


def burn_captions(
    src: Path, subtitles: Path, dst: Path, *, style: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> RenderResult:
    """Burn subtitles into the picture, clear of the platform's own UI.

    Burned in, not a sidecar: most short-form is watched muted, and a caption track the
    platform may or may not render is a caption most viewers never see.

    The default style lifts the text above :data:`SAFE_BOTTOM_PX` — captions in libass's
    default margin land underneath TikTok's caption and action rail, which is a subtitle
    that technically rendered and nobody can read.
    """
    if not subtitles.exists():
        raise VideoEditError(f"subtitle file not found: {subtitles}")
    exe = _require("ffmpeg")
    force = (style or CAPTION_STYLE).replace("'", "")
    vf = f"subtitles='{escape_filter_path(subtitles)}':force_style='{force}'"
    _exec(
        [exe, "-nostdin", "-y", "-i", str(src), "-vf", vf,
         "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-pix_fmt", _PIX_FMT, "-c:a", "copy", str(dst)],
        dst, timeout, src.name,
    )
    info = probe(dst)
    return RenderResult(dst, int(info["width"]), int(info["height"]), float(info["duration"]), vf)


def mix(
    video: Path, dst: Path, *, voice: Path | None = None, bed: Path | None = None,
    duck_db: float = -12.0, fade_s: float = 0.6, timeout: int = DEFAULT_TIMEOUT_S,
) -> RenderResult:
    """Lay a voiceover and/or a music bed under ``video``, ending with the picture.

    The bed is attenuated by a fixed ``duck_db`` rather than side-chained. On a clip this
    short a compressor's attack and release are audible as pumping, and a predictable
    level is worth more than a dynamic one nobody asked for.
    """
    if voice is None and bed is None:
        raise VideoEditError("mix() needs a voice, a bed, or both")
    exe = _require("ffmpeg")
    picture = float(probe(video)["duration"])

    cmd = [exe, "-nostdin", "-y", "-i", str(video)]
    graph, labels = [], []
    if voice is not None:
        cmd += ["-i", str(voice)]
        labels.append("[a_v]")
        graph.append(f"[{len(labels)}:a]aformat=sample_fmts=fltp:sample_rates=44100[a_v]")
    if bed is not None:
        # -stream_loop makes a short bed cover a longer picture instead of falling silent.
        cmd += ["-stream_loop", "-1", "-i", str(bed)]
        idx = len(labels) + 1
        labels.append("[a_b]")
        graph.append(
            f"[{idx}:a]aformat=sample_fmts=fltp:sample_rates=44100,"
            f"volume={duck_db:g}dB,atrim=0:{picture:g},"
            f"afade=t=out:st={max(0.0, picture - fade_s):g}:d={fade_s:g}[a_b]"
        )
    if len(labels) == 2:
        graph.append("[a_v][a_b]amix=inputs=2:duration=first:dropout_transition=0[aout]")
        out_label = "[aout]"
    else:
        out_label = labels[0]

    cmd += [
        "-filter_complex", ";".join(graph),
        "-map", "0:v", "-map", out_label,
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(dst),
    ]
    _exec(cmd, dst, timeout, video.name)
    info = probe(dst)
    return RenderResult(
        dst, int(info["width"]), int(info["height"]), float(info["duration"]), "mix"
    )
