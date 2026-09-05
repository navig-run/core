"""Motion and texture — the layer that separates a slideshow from a cut.

:mod:`navig.media.video_edit` can assemble a correct vertical video: right size, right
length, captions clear of the app's chrome. Correct is not the same as watchable. What
short-form actually rewards is *motion* — a push on the beat, a caption that arrives word
by word, grain that makes a clean render look shot rather than exported.

Everything here is a **pure function returning an ffmpeg filter fragment**. Nothing runs
a subprocess, nothing touches a file. That is deliberate and it is what makes the module
testable: a filtergraph is a string, so the tests assert the string, and only the handful
of functions in ``video_edit`` that actually execute need ffmpeg on the machine.

Fragments compose left to right through :func:`chain`, which is just a comma-join that
drops empties — so a look can switch an effect off by passing ``0`` rather than by
branching at every call site.

⚠ Two hard-won constraints inherited from ``video_edit``, repeated because breaking them
is silent rather than loud:

* **Subtitle coordinates are not video coordinates.** A plain SRT carries no PlayRes, so
  libass assumes a 288-tall script; one ``MarginV`` unit is ~6.68 video pixels at 1920.
  :func:`caption_style` converts for you — never pass raw pixels to force_style.
* **Effects belong on beats and cuts, not on everything.** Grain plus shake plus glitch
  running continuously reads as amateur, not cinematic. The defaults here are
  deliberately restrained; a look that wants more should say so explicitly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from navig.media.video_edit import (
    _ASS_PLAYRES_Y,
    SAFE_BOTTOM_PX,
    VERTICAL_H,
    VERTICAL_W,
)

# A shake wider than this stops reading as energy and starts reading as a broken export.
MAX_SHAKE_PX = 24.0
# Below this a punch is invisible; above ~1.35 it lurches.
MIN_PUNCH = 1.01
MAX_PUNCH = 1.35


def chain(*fragments: str) -> str:
    """Join filter fragments, dropping the empty ones.

    Empties are expected, not exceptional: a look turns an effect off by passing a zero
    amount, and the builder returns "" rather than making every caller write an ``if``.
    """
    return ",".join(f for f in fragments if f)


# ── texture ───────────────────────────────────────────────────────────────────


def grain(amount: float = 8.0) -> str:
    """Film grain. ``amount`` is ffmpeg's noise strength (0 disables).

    Applied to luma only (``allf=t`` would crawl the chroma too and read as compression
    artefacts rather than as film).
    """
    if amount <= 0:
        return ""
    return f"noise=alls={int(round(amount))}:allf=t+u"


def vhs(bleed: float = 2.0, scanlines: bool = True) -> str:
    """Tape look: chroma bleeding sideways, plus optional scanlines.

    Chroma bleed is the signature of the format — analogue video carried colour at lower
    bandwidth than luma, so the colour smears horizontally while the edges stay put.
    Shifting only the horizontal axis is what makes it read as tape rather than as a
    3D-glasses effect.
    """
    parts = []
    if bleed > 0:
        shift = int(round(bleed))
        parts.append(f"chromashift=cbh={shift}:crh={-shift}")
    if scanlines:
        # Darken every other line slightly. `geq` is per-pixel and slow, but this runs on
        # a 1080x1920 still-derived clip, not on live footage.
        parts.append("geq=lum='lum(X,Y)*(0.92+0.08*mod(Y\\,2))':cb='cb(X,Y)':cr='cr(X,Y)'")
    return chain(*parts)


def broadcast_grade(saturation: float = 0.15, contrast: float = 1.1,
                    softness: float = 0.6) -> str:
    """Desaturate, lift contrast, and soften — the 1966-tape grade.

    Softness last: blurring before the contrast lift would just be undone by it.
    """
    parts = [f"eq=saturation={saturation:g}:contrast={contrast:g}"]
    if softness > 0:
        parts.append(f"gblur=sigma={softness:g}")
    return chain(*parts)


def hold_accent(colour: str = "0xD63A32", *, similarity: float = 0.30,
                blend: float = 0.15) -> str:
    """Grey everything EXCEPT one colour — selective colour, done properly.

    This is the difference between a broadcast look that serves the brand and one that
    erases it. Flat desaturation (``eq=saturation=0.15``) reads as period-correct and
    kills the single lead accent that the identity is built on — measured on a real
    frame, the coral simply vanished.

    ``colorhold`` keeps the named colour at full strength and greys the rest, which is
    the same rule the art direction already states ("ONE lead accent per image, let the
    dark carry the rest") applied to the grade rather than to the prompt.

    ``similarity`` too low drops shadowed parts of the accent; too high starts holding
    skin tones, which are neighbours of red.
    """
    if not 0 < similarity <= 1:
        raise ValueError(f"similarity must be between 0 and 1, got {similarity}")
    return f"colorhold=color={colour}:similarity={similarity:g}:blend={blend:g}"


def vignette(amount: float = 0.3) -> str:
    """Corner falloff. ``amount`` 0..1 maps onto the lens angle."""
    if amount <= 0:
        return ""
    # PI/5 is barely visible, PI/2.2 is heavy; interpolate between them.
    angle = math.pi / 5 - (math.pi / 5 - math.pi / 2.2) * max(0.0, min(1.0, amount))
    return f"vignette=angle={angle:.4f}"


def letterbox(ratio: float = 0.82, *, width: int = VERTICAL_W,
              height: int = VERTICAL_H) -> str:
    """Shrink the picture inside a black frame — the "archival footage" device.

    ``ratio`` is the fraction of the frame the image fills. The image is scaled DOWN and
    padded rather than cropped, because the point is to show the frame edges.

    ⚠ Dimensions are computed here and forced EVEN rather than left as
    ``scale=iw*r,pad=iw/r``. That expression form lands on odd pixels — 1080×0.86 padded
    back by 1/0.86 gives 1079×1919 — and libx264 refuses odd dimensions under yuv420p
    with "width not divisible by 2", *after* the whole filtergraph has run. Padding to
    the true frame size also guarantees the output matches the format exactly rather
    than approximately.
    """
    if ratio >= 1.0:
        return ""
    if not 0 < ratio < 1:
        raise ValueError(f"letterbox ratio must be between 0 and 1, got {ratio}")
    inner_w = max(2, int(width * ratio) // 2 * 2)
    inner_h = max(2, int(height * ratio) // 2 * 2)
    return (
        f"scale={inner_w}:{inner_h},"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
    )


# ── motion ────────────────────────────────────────────────────────────────────


def punch_in(at: list[float], *, amount: float = 1.06, hold: float = 0.18,
             fps: int = 30, width: int = VERTICAL_W, height: int = VERTICAL_H) -> str:
    """Scale bumps on beats — the core short-form move.

    A punch is a step, not a ramp: the frame snaps bigger on the beat and eases back over
    ``hold`` seconds. Ramping both ways reads as a slow zoom and loses the hit entirely.

    Implemented with ``zoompan`` because it is the only filter that varies zoom per frame
    while emitting a CONSTANT output size. Both obvious alternatives are dead ends and
    fail late rather than loudly: a time-varying ``scale`` emits a different frame size
    every frame, which no encoder accepts (libx264 dies with `-22` after ffmpeg has
    happily built the graph), and ``crop`` fixes its width and height at configuration
    time, so only its x/y can move.

    ⚠ zoompan counts in OUTPUT FRAMES (``on``), not seconds — beats are converted here.
    """
    if not at:
        return ""
    amount = max(MIN_PUNCH, min(MAX_PUNCH, amount))
    if hold <= 0:
        raise ValueError(f"hold must be positive, got {hold}")
    # Each beat contributes a decaying pulse; take the strongest so overlapping beats
    # never stack into a zoom beyond the clamp.
    pulses = "+".join(
        f"max(0\\,1-((on/{fps})-{beat:g})/{hold:g})*gte(on/{fps}\\,{beat:g})"
        for beat in sorted(at)
    )
    zoom = f"1+{amount - 1:.4f}*min(1\\,{pulses})"
    return (
        f"zoompan=z='{zoom}':d=1:s={width}x{height}:fps={fps}"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    )


def shake(intensity: float = 4.0, hz: float = 7.0) -> str:
    """Handheld jitter via animated crop offsets.

    Two different frequencies on the two axes, otherwise the motion is a diagonal line
    rather than a wobble — the single most common tell of a fake camera shake.
    """
    if intensity <= 0:
        return ""
    amp = min(MAX_SHAKE_PX, intensity)
    pad = int(math.ceil(amp)) + 1
    return (
        f"pad=iw+{2 * pad}:ih+{2 * pad}:{pad}:{pad},"
        f"crop=w=iw-{2 * pad}:h=ih-{2 * pad}"
        f":x={pad}+{amp:g}*sin(t*{hz:g})"
        f":y={pad}+{amp:g}*sin(t*{hz * 1.37:g})"
    )


def speed_ramp(segments: list[tuple[float, float]]) -> str:
    """Piecewise playback speed. ``segments`` is [(from_seconds, multiplier), …].

    Only the video stream — audio is narration and must never be re-timed, or the picture
    stops matching the voice, which is the one thing the whole pipeline guarantees.
    """
    if not segments:
        return ""
    for _, mult in segments:
        if mult <= 0:
            raise ValueError(f"speed multiplier must be positive, got {mult}")
    ordered = sorted(segments)
    expr = f"PTS/{ordered[0][1]:g}"
    for start, mult in ordered[1:]:
        expr = f"if(gte(T\\,{start:g}),PTS/{mult:g},{expr})"
    return f"setpts={expr}"


def glitch(at: list[float], *, width: float = 0.08, strength: int = 6) -> str:
    """RGB split on cut points — a digital tear, not a continuous effect.

    Gated to a short window around each timestamp so the frame is clean the rest of the
    time; a permanent split just looks like a broken colour pipeline.
    """
    if not at or strength <= 0:
        return ""
    # `rh`/`bh` are INTEGERS — rgbashift takes no expressions, and passing one fails with
    # a bare "Invalid argument" that names the option but not the reason. The filter does
    # support ffmpeg's timeline, so the gating belongs in `enable`, not in the shift.
    gate = "+".join(f"between(t,{t:g},{t + width:g})" for t in sorted(at))
    return (
        f"rgbashift=rh={strength}:bh={-strength}:enable='{gate}'"
    )


# ── captions ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Word:
    """One spoken word with the timing the synthesis actually produced."""

    text: str
    start: float
    end: float


def words_from_alignment(characters: list[str], starts: list[float],
                         ends: list[float]) -> list[Word]:
    """Group per-character timings into words.

    ElevenLabs returns a start and end for every CHARACTER. That is the difference
    between captions that land on the word and captions that merely look like they do —
    most short-form fakes this by dividing a sentence by its length. Here the timing is
    measured, so a word that was drawn out stays on screen longer.
    """
    usable = min(len(characters), len(starts), len(ends))
    words: list[Word] = []
    buf, first = "", None
    for i in range(usable):
        char = characters[i]
        if char.isspace():
            if buf:
                words.append(Word(buf, first, ends[i - 1]))
                buf, first = "", None
            continue
        if not buf:
            first = starts[i]
        buf += char
    if buf and first is not None:
        words.append(Word(buf, first, ends[usable - 1]))
    return words


def caption_style(*, safe_bottom_px: int = SAFE_BOTTOM_PX, bold: bool = True,
                  outline: int = 2) -> str:
    """An ASS ``force_style`` string with the margin converted to script units.

    The conversion is the whole point. ``force_style`` is interpreted in the subtitle
    script's coordinate space — 288 tall for a plain SRT — so a pixel value passed
    straight through lands roughly seven times too high and the caption renders off the
    frame as *nothing at all*, which looks like a missing caption rather than an error.
    """
    margin = round(safe_bottom_px * _ASS_PLAYRES_Y / VERTICAL_H)
    return (
        f"Bold={1 if bold else 0},BorderStyle=1,Outline={outline},Shadow=1,"
        f"Alignment=2,MarginV={margin}"
    )


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{int(hours)}:{int(minutes):02d}:{secs:05.2f}"


def kinetic_ass(words: list[Word], *, group: int = 3, lead: float = 0.06,
                safe_bottom_px: int = SAFE_BOTTOM_PX) -> str:
    """A full ASS document where each word appears as it is spoken.

    Words are shown in small groups so the eye has something to read, and the word being
    spoken is highlighted rather than being the only thing on screen — a single word at a
    time is legible but gives the reader no context and tests badly.

    ``lead`` brings each cue slightly forward: a caption that appears exactly on the
    phoneme reads as late, because the eye needs longer than the ear.
    """
    if group < 1:
        raise ValueError(f"group must be at least 1, got {group}")
    margin = round(safe_bottom_px * _ASS_PLAYRES_Y / VERTICAL_H)
    header = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 2\n"
        f"PlayResX: 384\nPlayResY: {_ASS_PLAYRES_Y}\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,"
        "BackColour,Bold,Italic,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,"
        "MarginV,Encoding\n"
        "Style: Kinetic,Arial,18,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
        f"-1,0,1,2,1,2,20,20,{margin},1\n\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )
    lines = []
    for i in range(0, len(words), group):
        chunk = words[i:i + group]
        start = max(0.0, chunk[0].start - lead)
        end = chunk[-1].end
        # Each word turns from dim to full as its own start passes, using \t transforms
        # keyed to the group's start — this is what makes it arrive word by word rather
        # than as a block.
        parts = []
        for word in chunk:
            offset_ms = int(max(0.0, word.start - start) * 1000)
            parts.append(
                f"{{\\alpha&H60&\\t({offset_ms},{offset_ms + 90},\\alpha&H00&)}}{word.text}"
            )
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Kinetic,,0,0,0,,"
            + " ".join(parts)
        )
    return header + "\n".join(lines) + "\n"


# ── overlays ──────────────────────────────────────────────────────────────────


def drawtext(text: str, *, font: str, x: str, y: str, size: int = 28,
             colour: str = "white", alpha: float = 1.0) -> str:
    """A drawtext fragment with the escaping ffmpeg's parser actually needs.

    Colons and backslashes inside the text end the option early; a literal newline is
    written as ``\\n`` and must survive as one.
    """
    safe = (
        text.replace("\\", "\\\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace("%", r"\%")
    )
    fontfile = font.replace("\\", "/").replace(":", r"\:")
    opts = [
        f"fontfile='{fontfile}'",
        f"text='{safe}'",
        f"x={x}", f"y={y}",
        f"fontsize={size}",
        f"fontcolor={colour}@{alpha:g}",
    ]
    return "drawtext=" + ":".join(opts)
