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

# How much wall-clock to allow per second of finished video, plus a fixed head start.
# A flat 600s is fine for a fifteen-second cut and hopeless for a nine-minute one: a
# 1080x1920 x264 `medium` pass runs near 0.7x realtime, and a full-song render chains
# several of them (grade, then one per overlay, then captions). An 8:54 track blew the
# flat limit on its FIRST pass -- and the render died after the model work was already
# paid for, which is the expensive way to find out.
TIMEOUT_PER_SECOND = 8.0
TIMEOUT_OVERHEAD_S = 120


def timeout_for(seconds: float, floor: int = DEFAULT_TIMEOUT_S) -> int:
    """Wall-clock budget for a pass over ``seconds`` of video.

    Scales with the material instead of trusting one number to fit both a 15-second cut
    and a nine-minute one. Never returns less than ``floor``, so short clips keep the old
    behaviour exactly.
    """
    if seconds <= 0:
        return floor
    return max(floor, int(seconds * TIMEOUT_PER_SECOND) + TIMEOUT_OVERHEAD_S)

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


# Every motion a still can have. They used to be three names for ONE filter: "kenburns",
# "zoom" and "drift" all produced the identical slow push-in, so a shotlist that carefully
# alternated them rendered a reel where every shot moved exactly the same way. The names
# are kept and now mean what they say.
MOTIONS = frozenset({
    "none", "zoom-in", "zoom-out",
    "pan-left", "pan-right", "pan-up", "pan-down",
    "kenburns", "kenburns-out",
})
MOTION_ALIASES = {"zoom": "zoom-in", "drift": "pan-right", "": "none"}
# A pan needs somewhere to go: at zoom 1.0 the visible region IS the frame and the move has
# zero travel. Pans therefore get their own floor regardless of the zoom asked for.
PAN_ZOOM = 1.22


def _zoompan(motion: str, *, frames: int, zoom: float, width: int, height: int,
             fps: int) -> str:
    """The zoompan fragment for one motion.

    zoompan counts in OUTPUT FRAMES (``on``), so progress is ``on/frames``. Inside its own
    expressions ``zoom`` is the CURRENT zoom, which is what makes the pan extents correct
    while a zoom is also running: the travel is ``iw-iw/zoom``, recomputed per frame.
    """
    progress = f"(on/{max(1, frames)})"
    centre_x, centre_y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    span_x, span_y = "(iw-iw/zoom)", "(ih-ih/zoom)"
    pan = f"{max(zoom, PAN_ZOOM):g}"

    if motion == "zoom-in":
        z, x, y = f"1+{zoom - 1:.6f}*{progress}", centre_x, centre_y
    elif motion == "zoom-out":
        z, x, y = f"{zoom:g}-{zoom - 1:.6f}*{progress}", centre_x, centre_y
    elif motion == "pan-right":
        z, x, y = pan, f"{span_x}*{progress}", centre_y
    elif motion == "pan-left":
        z, x, y = pan, f"{span_x}*(1-{progress})", centre_y
    elif motion == "pan-down":
        z, x, y = pan, centre_x, f"{span_y}*{progress}"
    elif motion == "pan-up":
        z, x, y = pan, centre_x, f"{span_y}*(1-{progress})"
    elif motion == "kenburns":
        z = f"1+{zoom - 1:.6f}*{progress}"
        x, y = f"{span_x}*(0.25+0.5*{progress})", f"{span_y}*(0.75-0.5*{progress})"
    else:  # kenburns-out
        z = f"{zoom:g}-{zoom - 1:.6f}*{progress}"
        x, y = f"{span_x}*(0.75-0.5*{progress})", f"{span_y}*(0.25+0.5*{progress})"

    return (
        f"zoompan=z='{z}':x='{x}':y='{y}'"
        f":d={frames}:s={width}x{height}:fps={fps}"
    )


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
    move = MOTION_ALIASES.get(motion, motion)
    if move not in MOTIONS:
        raise ValueError(
            f"unknown motion {motion!r} — use one of: {', '.join(sorted(MOTIONS))}"
        )
    if move == "none":
        vf = f"{vertical_filter(width, height)},format={_PIX_FMT}"
    else:
        vf = (
            f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
            f"crop={width * 2}:{height * 2},"
            f"{_zoompan(move, frames=frames, zoom=zoom, width=width, height=height, fps=fps)},"
            f"setsar=1,format={_PIX_FMT}"
        )
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
    # Budget scaled to the material: a grade over nine minutes cannot finish inside the
    # same ten minutes that comfortably covers a fifteen-second cut.
    budget = timeout_for(float(probe(src)["duration"]), floor=timeout)
    _exec(
        [exe, "-nostdin", "-y", "-i", str(src), "-vf", f"{vf},format={_PIX_FMT}",
         "-r", str(fps), "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-c:a", "copy", str(dst)],
        dst, budget, src.name,
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
        dst, timeout_for(float(probe(src)["duration"]), floor=timeout), src.name,
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


# ── compositing: real footage laid over the picture ───────────────────────────

# Blend modes that read as *light added to a scene* rather than as a second picture
# stuck on top. `screen` is the one a black-background overlay wants: black contributes
# nothing, so bokeh, dust, smoke and film burn land as light and the black around them
# disappears without anyone cutting a matte.
BLEND_MODES = frozenset({
    "screen", "lighten", "addition", "overlay", "softlight", "hardlight", "multiply",
})

# Above this an overlay stops being atmosphere and becomes the subject. It is a soft
# ceiling: a look may want 0.9 for a full-frame film burn, but the default sits where a
# viewer reads the overlay without losing the picture underneath.
DEFAULT_OVERLAY_OPACITY = 0.55


def tint_filter(colour: str) -> str:
    """Recolour footage to one hue, keeping its brightness — grey it, then paint it.

    Overlay stock comes in whatever colour the person who shot it liked, and blue bokeh
    over a blood-red picture reads as two unrelated videos rather than one. Desaturating
    first and re-colouring by luminance keeps every particle, flare and falloff exactly
    where it was and only changes what colour the light is.

    The mixer works on an already-grey frame, where R=G=B=Y, so ``rr`` alone scales the
    red output to ``r*Y`` — which is why the off-diagonal terms stay at zero rather than
    being set to the colour as well.
    """
    raw = colour.strip().lstrip("#").removeprefix("0x")
    if len(raw) != 6:
        raise VideoEditError(f"tint must be a six-digit hex colour, got {colour!r}")
    try:
        r, g, b = (int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError as exc:
        raise VideoEditError(f"tint is not a hex colour: {colour!r}") from exc
    return f"hue=s=0,colorchannelmixer=rr={r:.4f}:gg={g:.4f}:bb={b:.4f}"


# How long an overlay takes to arrive and leave when it is windowed. Long enough that it
# reads as light entering the shot rather than a layer being switched on.
OVERLAY_FADE_S = 0.7


def composite(
    base: Path, overlay: Path, dst: Path, *,
    mode: str = "screen",
    opacity: float = DEFAULT_OVERLAY_OPACITY,
    start: float = 0.0,
    tint: str | None = None,
    window: tuple[float, float] | None = None,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> RenderResult:
    """Lay ``overlay`` footage over ``base`` — bokeh, dust, smoke, film burn, static.

    This is the difference between a generated picture and a shot one. A still that has
    been zoomed and graded still reads as a still; the same still under real particles
    moving at a rate nothing else in the frame shares reads as *filmed*, because the two
    motions do not agree and the eye stops looking for the seam.

    **The overlay loops and the base decides the length.** Overlay stock is rarely the
    length of the shot that needs it, and an overlay that runs out mid-clip leaves the
    picture visibly bare for the remainder — the one failure a viewer always notices. So
    the overlay is looped indefinitely and ``-shortest`` ends the render with the base.

    ``start`` seeks into the overlay before looping, which is how two clips using the
    same stock avoid opening on the same frame — the tell that gives a library away.

    The overlay is scaled to *cover* the base and centre-cropped: letterboxing an overlay
    would put hard black bars across a picture whose whole point is that its black is
    invisible.
    """
    if mode not in BLEND_MODES:
        raise VideoEditError(
            f"unknown blend mode {mode!r} — available: {', '.join(sorted(BLEND_MODES))}"
        )
    if not 0.0 <= opacity <= 1.0:
        raise ValueError(f"opacity must be between 0 and 1, got {opacity}")
    # Checked here, with the other argument validation, rather than where the filter is
    # built: whether a window makes sense has nothing to do with whether the files exist,
    # and a caller that passed both wrongly should hear about the argument it can fix.
    if window is not None and window[1] <= window[0]:
        raise VideoEditError(
            f"overlay window ends at or before it starts ({window[0]:g}s -> {window[1]:g}s)"
        )
    if not Path(base).exists():
        raise VideoEditError(f"base clip not found: {base}")
    if not Path(overlay).exists():
        raise VideoEditError(f"overlay footage not found: {overlay}")

    info = probe(Path(base))
    width, height = int(info["width"]), int(info["height"])
    fps = float(info.get("fps") or DEFAULT_FPS) or DEFAULT_FPS
    seconds = float(info["duration"])

    exe = _require("ffmpeg")
    # `shortest=1` on the BLEND, not `-shortest` on the muxer. Measured the hard way: with
    # an infinitely looped overlay, `-shortest` alone does not end the render — blend's
    # framesync repeats the base's last frame forever rather than reporting EOF, and the
    # encoder happily wrote over an hour of video from an eight-second clip before it was
    # killed. `shortest=1` ends the graph with the first input that actually runs out.
    recolour = f"{tint_filter(tint)}," if tint else ""
    # An overlay that runs for the whole clip stops being an effect and becomes a filter
    # over the film: white-ish particles screened end to end lift the black floor and wash
    # the colour out of everything underneath. `window` confines it to part of the clip.
    #
    # Gated by fading the OVERLAY to black rather than by switching the blend on and off.
    # Under `screen` black contributes nothing, so faded-to-black is the same as absent —
    # and it arrives and leaves as light instead of appearing between two frames.
    gate = ""
    if window is not None:
        w_from, w_to = window
        fade = min(OVERLAY_FADE_S, (w_to - w_from) / 2)
        gate = (f"setpts=PTS-STARTPTS,"
                f"fade=t=in:st={w_from:.3f}:d={fade:.3f},"
                f"fade=t=out:st={max(0.0, w_to - fade):.3f}:d={fade:.3f},")
    chain = (
        f"[1:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},fps={fps:g},{gate}{recolour}format=gbrp,setsar=1[ov];"
        f"[0:v]format=gbrp,setsar=1[bs];"
        f"[bs][ov]blend=all_mode={mode}:all_opacity={opacity:g}:shortest=1,"
        f"format={_PIX_FMT}[out]"
    )
    cmd = [exe, "-nostdin", "-y", "-i", str(base)]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += [
        "-stream_loop", "-1", "-i", str(overlay),
        "-filter_complex", chain, "-map", "[out]",
        # The base may have no audio yet (picture is assembled before the track is mixed
        # in). `0:a?` maps it when present instead of failing the whole render when not.
        "-map", "0:a?",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "copy",
        # A hard cap on top of the filter guard. The base length is already measured, so
        # a runaway loop cannot cost more than the clip it was asked for even if a future
        # ffmpeg changes framesync semantics again.
        "-t", f"{seconds:.3f}", str(dst),
    ]
    _exec(cmd, Path(dst), timeout_for(seconds, floor=timeout),
          f"{Path(base).name} + {Path(overlay).name}")
    out = probe(Path(dst))
    return RenderResult(
        Path(dst), int(out["width"]), int(out["height"]), float(out["duration"]),
        f"blend={mode}@{opacity:g}" + (f" tint={tint}" if tint else "")
        + (f" window={window[0]:g}-{window[1]:g}" if window else ""),
    )
