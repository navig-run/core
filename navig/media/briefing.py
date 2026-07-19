"""navig.media.briefing — turn a video / audio / doc into a briefing.md.

The analysis half of `navig media` (the inverse of the generation half). REUSES, never rebuilds:
  * transcript / doc text  → :func:`navig.inbox.extract.extract` (ffmpeg audio → STT; pdf/docx…)
  * video frames           → :mod:`navig.media.frames` (the one new capability)
  * frame text             → :func:`navig.core.ocr.extract_ocr_text_from_image_bytes`
  * synthesis (optional)   → :func:`navig.llm.generate.llm_generate` (degrades to a template)

Output profiles (`--as`): **howto** (transcript + every link + numbered steps), **creative**
(frame index + reference frames + prompt scaffold), **generic** (transcript + frames + key points).
Never raises on missing optional deps — it just produces a thinner briefing and says so.
"""
from __future__ import annotations

import re
from pathlib import Path

from navig.media.frames import extract_frames, ffmpeg_available, probe

_LINK_RE = re.compile(r"https?://[^\s)>\]}\"']+")
_VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
_AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}


def _kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _VIDEO_EXT:
        return "video"
    if ext in _AUDIO_EXT:
        return "audio"
    return "doc"


def _transcript(path: Path) -> str:
    """Transcript (video/audio) or text (doc) via the extract.py spine. '' on failure."""
    try:
        from navig.inbox.extract import extract  # noqa: PLC0415
        res = extract(path)
        return (getattr(res, "text", "") or "").strip()
    except Exception:  # noqa: BLE001 — extract never raises, but guard imports too
        return ""


def _ocr_frames(frames: list[Path]) -> list[tuple[str, str]]:
    try:
        from navig.core.ocr import extract_ocr_text_from_image_bytes  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return []
    out: list[tuple[str, str]] = []
    for f in frames:
        try:
            txt = extract_ocr_text_from_image_bytes(Path(f).read_bytes())
            if txt and txt.strip():
                out.append((f.name, txt.strip()))
        except Exception:  # noqa: BLE001
            continue
    return out


def _maybe_llm(profile: str, transcript: str, ocr: list[tuple[str, str]], links: list[str]) -> str:
    """One llm_generate pass to synthesise. Returns '' if no LLM (caller keeps the template)."""
    if not transcript and not ocr:
        return ""
    ocr_txt = "\n".join(f"[{n}] {t}" for n, t in ocr[:40])
    goal = {
        "howto": "Write a step-by-step how-to briefing: 1-line TL;DR, the numbered steps, any "
                 "commands/tools mentioned, gotchas, and a 'Links' list. Be faithful to the source.",
        "creative": "Write a creative-reference briefing: the visual style, key on-screen elements, "
                    "a short reference-frame list, and 2-3 reusable image-gen prompt scaffolds.",
        "generic": "Write a concise briefing: 1-line TL;DR, key points as bullets, and a Links list.",
    }.get(profile, "Write a concise briefing with a TL;DR, key points, and links.")
    try:
        from navig.llm.generate import llm_generate  # noqa: PLC0415
        return llm_generate(
            [{"role": "system", "content": "You distil media into a tight markdown briefing."},
             {"role": "user", "content":
              f"{goal}\n\n## Transcript\n{transcript[:6000]}\n\n## On-screen text (OCR)\n{ocr_txt[:2000]}"
              + (f"\n\n## Links found\n" + "\n".join(links) if links else "")}],
            mode="summarize",
        ).strip()
    except Exception:  # noqa: BLE001
        return ""


def build_briefing(path: str | Path, out_dir: str | Path, *, profile: str = "generic",
                   max_frames: int = 40) -> dict:
    """Produce ``out_dir/briefing.md`` (+ ``frames/`` for video). Returns a summary dict."""
    path = Path(path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    kind = _kind(path)

    meta: dict = {}
    frames: list[Path] = []
    notes: list[str] = []
    if kind == "video":
        meta = probe(path)
        if ffmpeg_available():
            frames = extract_frames(path, out_dir / "frames", max_frames=max_frames)
        else:
            notes.append("ffmpeg not installed → no frames (install ffmpeg for the visual pass)")

    transcript = _transcript(path)
    if not transcript:
        notes.append("no transcript (whisper/ffmpeg unavailable, or a non-speech source)")
    ocr = _ocr_frames(frames)
    links = sorted(set(_LINK_RE.findall(transcript)))

    synth = _maybe_llm(profile, transcript, ocr, links)

    # ── assemble the briefing.md (template body always written; LLM synthesis prepended if any) ──
    lines = [f"# Briefing — {path.name}", ""]
    if meta:
        lines += [f"> {kind} · {meta.get('duration', 0):.0f}s · "
                  f"{meta.get('width')}×{meta.get('height')} · {meta.get('fps')}fps · "
                  f"{len(frames)} frames · profile: `{profile}`", ""]
    else:
        lines += [f"> {kind} · profile: `{profile}`", ""]
    if synth:
        lines += ["## Summary", "", synth, ""]
    if links:
        lines += ["## Links", ""] + [f"- {u}" for u in links] + [""]
    if frames:
        lines += ["## Frames", "", f"{len(frames)} keyframes in `frames/`:", ""]
        lines += [f"- `frames/{f.name}`" + (f" — {dict(ocr).get(f.name, '')[:80]}" if f.name in dict(ocr) else "")
                  for f in frames[:40]] + [""]
    if transcript:
        lines += ["## Transcript", "", transcript[:8000], ""]
    if notes:
        lines += ["## Notes", ""] + [f"- {n}" for n in notes] + [""]

    dest = out_dir / "briefing.md"
    dest.write_text("\n".join(lines), encoding="utf-8")
    return {"briefing": dest, "kind": kind, "frames": len(frames), "links": len(links),
            "has_transcript": bool(transcript), "llm": bool(synth), "notes": notes}
