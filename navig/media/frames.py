"""navig.media.frames — video frame / scene extraction (ffmpeg).

The one genuinely-new media capability: the existing video paths (`inbox.extract._extract_video`,
`telegram_catalog_analyzer._analyze_video`) only pull the audio track or a *single* thumbnail.
This does scene-change detection + interval sampling into a `frames/` dir, plus `ffprobe`
metadata — the frames the `navig media` briefing pipeline reads with OCR / vision.

Pure subprocess over ffmpeg; **degrades gracefully** — returns ``[]`` / ``{}`` when ffmpeg is
absent or on error, never raises. Mirrors the ffmpeg recipe already documented in the distillery
skill's ``references/preprocess.md`` (promoted here into reusable code).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

_FRAME_GLOB = "f_*.jpg"


def ffmpeg_available() -> bool:
    """True when the ffmpeg binary is on PATH."""
    return shutil.which("ffmpeg") is not None


def probe(video: str | Path) -> dict:
    """ffprobe → ``{duration, width, height, fps}`` (floats/ints). ``{}`` if unavailable/on error."""
    if shutil.which("ffprobe") is None:
        return {}
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error",
             "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate",
             "-show_entries", "format=duration",
             "-of", "json", str(video)],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
        data = json.loads(out)
        stream = (data.get("streams") or [{}])[0]
        fmt = data.get("format") or {}
        fr = str(stream.get("r_frame_rate", "0/1"))
        try:
            num, den = fr.split("/")
            fps = round(float(num) / float(den), 2) if float(den) else 0.0
        except (ValueError, ZeroDivisionError):
            fps = 0.0
        return {
            "duration": float(fmt.get("duration", 0) or 0),
            "width": stream.get("width"),
            "height": stream.get("height"),
            "fps": fps,
        }
    except (subprocess.SubprocessError, OSError, ValueError, json.JSONDecodeError):
        return {}


def extract_frames(
    video: str | Path,
    out_dir: str | Path,
    *,
    mode: str = "scene",
    scene_threshold: float = 0.4,
    interval: float = 2.0,
    max_frames: int = 60,
    width: int = 960,
    timeout: int = 600,
) -> list[Path]:
    """Extract keyframes from *video* into ``out_dir/f_###.jpg``. Returns sorted frame paths.

    ``mode="scene"``  → ffmpeg scene-change detection (``select='gt(scene,threshold)'``): fewer,
    meaningful frames. Falls back to interval sampling if a low-motion clip yields none.
    ``mode="interval"`` → one frame every ``interval`` seconds (``fps=1/interval``).

    Never raises: returns ``[]`` if ffmpeg is missing, ``[]``/partial on error.
    """
    if not ffmpeg_available():
        return []
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    if mode == "scene":
        vf = f"select='gt(scene,{scene_threshold})',scale={width}:-1"
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
               "-vf", vf, "-vsync", "vfr", "-frames:v", str(max_frames),
               str(out / "f_%03d.jpg")]
    else:
        vf = f"fps=1/{interval},scale={width}:-1"
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
               "-vf", vf, "-frames:v", str(max_frames), str(out / "f_%03d.jpg")]

    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True)
    except (subprocess.SubprocessError, OSError):
        pass  # non-zero exit (cut-free clip, or an encoder quirk) → handled by the fallback below

    frames = sorted(out.glob(_FRAME_GLOB))
    # Scene mode can legitimately yield nothing (a static clip) OR fail to encode on some sources →
    # fall back to interval sampling once, adapting the step to the clip length so short clips
    # still yield a few frames. (Interval mode itself just returns whatever it produced.)
    if not frames and mode == "scene":
        dur = probe(video).get("duration") or 0
        step = min(interval, max(0.5, dur / 8)) if dur else interval
        return extract_frames(video, out, mode="interval", interval=step,
                              max_frames=max_frames, width=width, timeout=timeout)
    return frames
