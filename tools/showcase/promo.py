#!/usr/bin/env python3
"""Render the NAVIG promo videos (YouTube 16:9 + TikTok 9:16) from real showcase recordings.

Inputs (all outside the repo — masters are never committed):
  <studio>/promo.json          the edit decision list: segments, voice-over lines, captions, copy
  <studio>/audio/*.mp3         music beds + SFX (navig audio gen) ; audio/vo/<video>-<nn>.mp3 lines
  --masters DIR [DIR …]        showcase MP4s (record.sh): hero.mp4, plugin-devhost.mp4, …

Outputs:
  <studio>/out/youtube/<id>.mp4 + <id>-thumb.png
  <studio>/out/tiktok/<id>.mp4  + <id>-cover.png
  <studio>/out/POSTING.md       every title, description, chapter list, tag and hashtag, ready to paste

Honesty contract: every frame of footage is a real recording (see record.sh); cards and captions are
the only added graphics. Nothing is posted anywhere — this only writes files.

    py -3.13 core/tools/showcase/promo.py --studio C:/studio/navig/videos/promo-2026-09 \
        --masters C:/studio/navig/videos/showcase-2026-09-27 C:/studio/navig/videos/plugins-2026-09-27
    … --only yt-navig-in-90-seconds tt-https-localhost
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path

FPS = 30
FONT_DIRS = [
    Path("C:/Windows/Fonts"),
    Path.home() / "AppData/Local/Microsoft/Windows/Fonts",
    Path("/usr/share/fonts"),
    Path.home() / ".fonts",
]
BG = "0x0b0b10"
BRAND = "0x2271D0"
XFADE = 0.35


@dataclass
class Format:
    name: str
    w: int
    h: int


YOUTUBE = Format("youtube", 1920, 1080)
TIKTOK = Format("tiktok", 1080, 1920)


# ── helpers ─────────────────────────────────────────────────────────────────────


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.stderr.write(" ".join(cmd[:6]) + " …\n" + r.stderr[-2500:])
        raise SystemExit(f"ffmpeg failed ({r.returncode})")


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
    ).stdout.strip()
    return float(out or 0)


def font(name: str) -> str:
    for d in FONT_DIRS:
        p = d / name
        if p.exists():
            return ff_path(p)
    raise SystemExit(f"font {name} not found in {FONT_DIRS}")


def ff_path(p: Path) -> str:
    """A path usable inside an ffmpeg filter argument (drive colon escaped, forward slashes)."""
    return str(p).replace("\\", "/").replace(":", "\\:")


class TextFiles:
    """drawtext reads text from files — no quoting/escaping of colons, quotes or percent signs."""

    def __init__(self, root: Path):
        self.root = root
        self.n = 0

    def __call__(self, text: str) -> str:
        self.n += 1
        p = self.root / f"t{self.n:04d}.txt"
        # LF only: on Windows text mode would write CRLF, and drawtext renders the CR as a box.
        p.write_bytes(text.encode("utf-8"))
        return ff_path(p)


def drawtext(
    tf: TextFiles,
    text: str,
    *,
    fontfile: str,
    size: int,
    y: str,
    color: str = "white",
    box: bool = False,
    enable: str | None = None,
    alpha: str | None = None,
) -> str:
    if "\n" in text:
        # One drawtext per line: this ffmpeg draws the newline character itself as a
        # box glyph at the end of every wrapped line.
        step = round(size * 1.32)
        return ",".join(
            drawtext(
                tf,
                line,
                fontfile=fontfile,
                size=size,
                y=f"({y})+{i * step}",
                color=color,
                box=box,
                enable=enable,
                alpha=alpha,
            )
            for i, line in enumerate(text.split("\n"))
        )
    parts = [
        f"fontfile='{fontfile}'",
        f"textfile='{tf(text)}'",
        f"fontsize={size}",
        f"fontcolor={color}",
        "x=(w-text_w)/2",
        f"y={y}",
        "text_align=C",
        "line_spacing=14",
    ]
    if box:
        parts += ["box=1", "boxcolor=black@0.62", "boxborderw=26"]
    if enable:
        parts.append(f"enable='{enable}'")
    if alpha:
        parts.append(f"alpha='{alpha}'")
    return "drawtext=" + ":".join(parts)


# ── segments ────────────────────────────────────────────────────────────────────


def seg_length(seg: dict, vo: Path | None, fmt: "Format | None" = None) -> float:
    base = duration(vo) + 1.2 if vo and vo.exists() else 3.0
    if "card" in seg:
        return max(base, 3.2)
    # On YouTube the terminal output is the point: give each clip time to be READ, not just
    # heard (the first cut ran 39 s under a "90 seconds" title with every clip flashing by).
    floor = float(seg.get("min", 10.0 if fmt is YOUTUBE else 5.0))
    return max(base, floor)


def render_card(seg: dict, fmt: Format, length: float, out: Path, tf: TextFiles) -> None:
    bold, med = font("Inter-Bold.otf"), font("Inter-Medium.otf")
    title_size = 132 if fmt is YOUTUBE else 118
    fade = "if(lt(t,0.5),t/0.5,1)"
    vf = ",".join(
        [
            f"drawbox=x=0:y=ih*0.62:w=iw:h=3:color={BRAND}@0.9:t=fill",
            drawtext(
                tf, seg["card"], fontfile=bold, size=title_size, y="h*0.42-text_h", alpha=fade
            ),
            drawtext(
                tf,
                seg.get("sub", ""),
                fontfile=med,
                size=46,
                y="h*0.62+40",
                color="0xb8c4d6",
                alpha=fade,
            ),
            "vignette=PI/5",
        ]
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={BG}:s={fmt.w}x{fmt.h}:r={FPS}:d={length:.3f}",
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "18",
            "-an",
            str(out),
        ]
    )


def render_clip(
    seg: dict, fmt: Format, length: float, src: Path, out: Path, tf: TextFiles, hook: str | None
) -> None:
    start = float(seg.get("start", 0.4))
    avail = max(0.0, duration(src) - start)
    speed = 1.0
    if avail < length and avail > 0:  # never freeze on a dead frame for long: pad at most 1.5 s
        pad = length - avail
        if pad > 1.5:
            speed = avail / (length - 1.5)
    bold = font("Inter-Bold.otf")
    if fmt is YOUTUBE:
        # Fit inside the frame whatever the recording's aspect (most are 12:7, a full-screen
        # app is 4:3), outline it, then centre it.
        frame = (
            "scale=w=1720:h=1000:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"drawbox=x=0:y=0:w=iw:h=ih:color={BRAND}@0.35:t=3,"
            f"pad={fmt.w}:{fmt.h}:(ow-iw)/2:(oh-ih)/2:color={BG}"
        )
        overlay = ""
    else:
        # The recordings are left-aligned terminals with an empty right third: crop to the
        # content so the text is legible on a phone, then fill the width.
        frame = (
            f"crop=iw*{seg.get('crop', 0.68)}:ih:0:0,scale=1040:-2,"
            f"pad={fmt.w}:{fmt.h}:(ow-iw)/2:(oh-ih)/2+80:color={BG}"
        )
        caps = seg.get("captions") or []
        lines = []
        if hook:
            lines.append(
                drawtext(tf, textwrap.fill(hook, 18), fontfile=bold, size=78, y="h*0.09", box=True)
            )
        for i, cap in enumerate(caps):
            a, b = length * i / len(caps), length * (i + 1) / len(caps)
            lines.append(
                drawtext(
                    tf,
                    textwrap.fill(cap, 22),
                    fontfile=bold,
                    size=64,
                    y="h*0.79",
                    box=True,
                    enable=f"between(t,{a:.2f},{b:.2f})",
                    color="0xffe066",
                )
            )
        overlay = ("," + ",".join(lines)) if lines else ""
    setpts = f"setpts={1 / speed:.4f}*(PTS-STARTPTS)," if speed != 1.0 else "setpts=PTS-STARTPTS,"
    vf = f"{setpts}fps={FPS},{frame}{overlay},tpad=stop_mode=clone:stop_duration=2,trim=duration={length:.3f}"
    run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start:.2f}",
            "-i",
            str(src),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "18",
            "-an",
            str(out),
        ]
    )


# ── assembly ────────────────────────────────────────────────────────────────────


def build_video(
    video: dict, fmt: Format, studio: Path, masters: dict[str, Path], out_dir: Path
) -> dict:
    audio = studio / "audio"
    with tempfile.TemporaryDirectory(prefix="navig-promo-") as td:
        tmp = Path(td)
        tf = TextFiles(tmp)
        parts, lengths, vos = [], [], []
        for i, seg in enumerate(video["segments"]):
            vo = audio / "vo" / f"{video['id']}-{i:02d}.mp3"
            length = seg_length(seg, vo, fmt)
            if fmt is TIKTOK and len(video["segments"]) == 1:
                length = max(length, duration(vo) + 1.5)
            part = tmp / f"seg{i:02d}.mp4"
            if "card" in seg:
                render_card(seg, fmt, length, part, tf)
            else:
                src = masters.get(seg["clip"])
                if not src:
                    raise SystemExit(f"{video['id']}: no master for clip '{seg['clip']}'")
                hook = video.get("hook") if (fmt is TIKTOK and i == 0) else None
                render_clip(seg, fmt, length, src, part, tf, hook)
            parts.append(part)
            lengths.append(length)
            vos.append(vo if vo.exists() else None)

        # video: crossfade chain
        starts = [0.0]
        for L in lengths[:-1]:
            starts.append(starts[-1] + L - XFADE)
        total = starts[-1] + lengths[-1]
        inputs = sum((["-i", str(p)] for p in parts), [])
        chain, last = [], "[0:v]"
        for k in range(1, len(parts)):
            lbl = f"[v{k}]"
            chain.append(
                f"{last}[{k}:v]xfade=transition=fade:duration={XFADE}:offset={starts[k]:.3f}{lbl}"
            )
            last = lbl
        if not chain:
            chain.append("[0:v]null[v1]")
            last = "[v1]"
        silent = tmp / "video.mp4"
        run(
            [
                "ffmpeg",
                "-y",
                *inputs,
                "-filter_complex",
                ";".join(chain),
                "-map",
                last,
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-crf",
                "18",
                str(silent),
            ]
        )

        # audio: voice-over lines at segment starts, SFX at boundaries, music ducked under the voice
        a_inputs, a_filters, mix = [], [], []
        idx = 0
        for k, vo in enumerate(vos):
            if vo:
                a_inputs += ["-i", str(vo)]
                delay = int((starts[k] + 0.45) * 1000)
                a_filters.append(f"[{idx}:a]adelay={delay}|{delay},volume=1.0[vo{k}]")
                mix.append(f"[vo{k}]")
                idx += 1
        vo_count = len(mix)
        for k, seg in enumerate(video["segments"]):
            sfx = seg.get("sfx")
            if sfx and (audio / f"sfx-{sfx}.mp3").exists():
                a_inputs += ["-i", str(audio / f"sfx-{sfx}.mp3")]
                delay = int(max(0.0, starts[k] - 0.15) * 1000)
                a_filters.append(f"[{idx}:a]adelay={delay}|{delay},volume=0.55[sfx{k}]")
                mix.append(f"[sfx{k}]")
                idx += 1
        music = audio / video["music"]
        a_inputs += ["-stream_loop", "-1", "-i", str(music)]
        m = idx
        voice_bus = "".join(mix[:vo_count])
        a_filters.append(f"{voice_bus}amix=inputs={vo_count}:normalize=0,asplit=2[voice][key]")
        a_filters.append(
            f"[{m}:a]atrim=0:{total:.3f},afade=t=in:d=1.2,afade=t=out:st={max(0.0, total - 2.5):.3f}:d=2.5,"
            f"volume=0.42[bed]"
        )
        a_filters.append(
            "[bed][key]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[ducked]"
        )
        others = "".join(mix[vo_count:])
        n = 2 + len(mix[vo_count:])
        a_filters.append(
            f"[voice][ducked]{others}amix=inputs={n}:normalize=0,loudnorm=I=-14:TP=-1.0:LRA=11[aout]"
        )
        wav = tmp / "mix.m4a"
        run(
            [
                "ffmpeg",
                "-y",
                *a_inputs,
                "-filter_complex",
                ";".join(a_filters),
                "-map",
                "[aout]",
                "-t",
                f"{total:.3f}",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                str(wav),
            ]
        )

        out_dir.mkdir(parents=True, exist_ok=True)
        final = out_dir / f"{video['id']}.mp4"
        run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(silent),
                "-i",
                str(wav),
                "-map",
                "0:v",
                "-map",
                "1:a",
                "-c:v",
                "copy",
                "-c:a",
                "copy",
                "-shortest",
                "-movflags",
                "+faststart",
                str(final),
            ]
        )

        # thumbnail / cover: composed from the CLEAN master frame (the finished video carries
        # burned-in hooks and captions that collide with a headline), dimmed, headline on top.
        cover = out_dir / (
            f"{video['id']}-thumb.png" if fmt is YOUTUBE else f"{video['id']}-cover.png"
        )
        headline = (video.get("cover") or video["title"].split("—")[0]).strip()
        clip_seg = next((s for s in video["segments"] if "clip" in s), None)
        src = masters[clip_seg["clip"]]
        at = float(clip_seg.get("start", 0.4)) + min(6.0, duration(src) * 0.6)
        bold = font("Inter-Bold.otf")
        if fmt is YOUTUBE:
            base = (
                "scale=w=1920:h=1080:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color={BG}"
            )
            text = [
                drawtext(
                    tf, textwrap.fill(headline, 22), fontfile=bold, size=118, y="h*0.22", box=True
                ),
                drawtext(tf, "NAVIG", fontfile=bold, size=64, y="h*0.82", color=BRAND),
            ]
        else:
            base = (
                f"crop=iw*{clip_seg.get('crop', 0.68)}:ih:0:0,scale=1080:-2,"
                f"pad=1080:1920:0:(oh-ih)/2+220:color={BG}"
            )
            text = [
                drawtext(
                    tf, textwrap.fill(headline, 11), fontfile=bold, size=150, y="h*0.10", box=True
                ),
                drawtext(tf, "NAVIG", fontfile=bold, size=72, y="h*0.86", color=BRAND),
            ]
        vf = ",".join([base, "boxblur=2:1", "eq=brightness=-0.22:saturation=1.15", *text])
        run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{at:.2f}",
                "-i",
                str(src),
                "-frames:v",
                "1",
                "-vf",
                vf,
                str(cover),
            ]
        )

        chapters = []
        for k, seg in enumerate(video["segments"]):
            t = int(starts[k])
            label = (
                seg.get("label")
                or seg.get("card")
                or seg.get("clip", "").replace("plugin-", "").replace("-", " ").title()
            )
            chapters.append((t, label))
        # YouTube only shows chapters when the first starts at 0:00 and EVERY chapter lasts at
        # least 10 s — a 2 s intro card or a 4 s outro would switch chapters off entirely. Fold a
        # short chapter into the one after it (keeping the earlier start), and drop a short tail.
        merged: list[tuple[int, str]] = []
        pending: tuple[int, str] | None = None
        for i, (t, label) in enumerate(chapters):
            start, name = (pending[0], f"{pending[1]} · {label}") if pending else (t, label)
            nxt = chapters[i + 1][0] if i + 1 < len(chapters) else int(total)
            if nxt - start < 10:
                if i + 1 < len(chapters):
                    # too short on its own: carry it into the next chapter (a title card
                    # carries no label worth keeping)
                    pending = (
                        (start, label if "card" not in video["segments"][i] else "")
                        if not pending
                        else (start, name)
                    )
                    continue
                if merged:  # a short tail joins the chapter before it
                    merged[-1] = (merged[-1][0], f"{merged[-1][1]} · {label}")
                    continue
            merged.append((start, name.lstrip(" ·")))
            pending = None
        chapters = [f"{t // 60}:{t % 60:02d} {label}" for t, label in merged]
        return {"file": final, "cover": cover, "duration": total, "chapters": chapters}


def masters_index(dirs: list[Path]) -> dict[str, Path]:
    idx: dict[str, Path] = {}
    for d in dirs:
        for p in sorted(d.glob("*.mp4")):
            idx.setdefault(p.stem, p)  # the first dir wins: pass the newest recordings first
    return idx


def posting(spec: dict, results: dict[str, dict]) -> str:
    lines = [
        "# NAVIG promo — ready to post",
        "",
        "Every clip is a real navig recording (core/tools/showcase). Music, sound effects and voice-over "
        "were generated with ElevenLabs through `navig audio` on the owner's account. Nothing has been posted.",
        "",
    ]
    lines += ["## YouTube", ""]
    for v in spec["youtube"]:
        r = results.get(v["id"])
        if not r:
            continue
        desc = v["description"].replace("{chapters}", "\n".join(r["chapters"]))
        lines += [
            f"### {v['title']}",
            "",
            f"- File: `youtube/{r['file'].name}` ({r['duration']:.0f} s, 1920×1080)",
            f"- Thumbnail: `youtube/{r['cover'].name}`",
            "",
            "**Title**",
            "",
            v["title"],
            "",
            "**Description**",
            "",
            "```",
            desc,
            "```",
            "",
            "**Tags**",
            "",
            ", ".join(v["tags"]),
            "",
        ]
    lines += ["## TikTok", ""]
    for v in spec["tiktok"]:
        r = results.get(v["id"])
        if not r:
            continue
        lines += [
            f"### {v['hook']}",
            "",
            f"- File: `tiktok/{r['file'].name}` ({r['duration']:.0f} s, 1080×1920)",
            f"- Cover: `tiktok/{r['cover'].name}`",
            "",
            "**Caption**",
            "",
            "```",
            v["caption"],
            "```",
            "",
        ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--studio", type=Path, required=True)
    ap.add_argument("--masters", type=Path, nargs="+", required=True)
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is not on PATH")
    spec = json.loads((args.studio / "promo.json").read_text(encoding="utf-8"))
    masters = masters_index(args.masters)
    out = args.studio / "out"
    results: dict[str, dict] = {}
    prev = out / "results.json"
    if prev.exists():
        for k, v in json.loads(prev.read_text(encoding="utf-8")).items():
            results[k] = {**v, "file": Path(v["file"]), "cover": Path(v["cover"])}
    for fmt, key in ((YOUTUBE, "youtube"), (TIKTOK, "tiktok")):
        for video in spec[key]:
            if args.only and video["id"] not in args.only:
                continue
            print(f"▸ {video['id']}", flush=True)
            results[video["id"]] = build_video(video, fmt, args.studio, masters, out / key)
            r = results[video["id"]]
            print(f"  ✓ {r['file'].name} · {r['duration']:.1f}s", flush=True)
    prev.parent.mkdir(parents=True, exist_ok=True)
    prev.write_text(
        json.dumps(
            {
                k: {**v, "file": str(v["file"]), "cover": str(v["cover"])}
                for k, v in results.items()
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (out / "POSTING.md").write_text(posting(spec, results), encoding="utf-8")
    print(f"✓ {len(results)} video(s) · {out / 'POSTING.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
