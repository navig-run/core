"""The motion layer.

Most of this is pure — a filtergraph is a string, so it is asserted as one and needs no
ffmpeg. The handful of tests that DO run ffmpeg are the ones where a string assertion
would prove nothing: an expression ffmpeg accepts but that moves nothing looks identical
on the command line to one that works. Those are checked on pixels.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from navig.media import fx
from navig.media.fx import (
    MAX_PUNCH,
    MAX_SHAKE_PX,
    MIN_PUNCH,
    Word,
    broadcast_grade,
    caption_style,
    chain,
    drawtext,
    glitch,
    grain,
    kinetic_ass,
    letterbox,
    punch_in,
    shake,
    speed_ramp,
    vhs,
    vignette,
    words_from_alignment,
)
from navig.media.video_edit import SAFE_BOTTOM_PX, VERTICAL_H, ffmpeg_available

requires_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")


# ── composition ───────────────────────────────────────────────────────────────


def test_chain_drops_empty_fragments() -> None:
    # A look disables an effect by passing 0, so empties are routine, not exceptional.
    assert chain("a", "", "b", "") == "a,b"


def test_chain_of_nothing_is_empty_not_a_stray_comma() -> None:
    assert chain("", "") == ""


def test_a_disabled_effect_returns_empty() -> None:
    assert grain(0) == ""
    assert vignette(0) == ""
    assert shake(0) == ""
    assert glitch([]) == ""
    assert speed_ramp([]) == ""
    assert letterbox(1.0) == ""


# ── texture ───────────────────────────────────────────────────────────────────


def test_grain_touches_luma_not_chroma() -> None:
    # allf on chroma crawls and reads as compression artefacts, not film.
    assert "alls=" in grain(8)


def test_vhs_bleeds_chroma_horizontally_only() -> None:
    # A vertical shift reads as 3D glasses; analogue smears sideways.
    frag = vhs(bleed=3, scanlines=False)
    assert "cbh=3" in frag and "crh=-3" in frag
    assert "cbv" not in frag and "crv" not in frag


def test_scanlines_are_optional() -> None:
    assert "geq" in vhs(scanlines=True)
    assert "geq" not in vhs(scanlines=False)


def test_grade_softens_after_contrast_not_before() -> None:
    # Blurring first would simply be undone by the contrast lift.
    frag = broadcast_grade(softness=0.6)
    assert frag.index("eq=") < frag.index("gblur")


def test_letterbox_scales_down_and_pads_rather_than_cropping() -> None:
    # The point is to SHOW the frame edges; cropping would hide them.
    frag = letterbox(0.8)
    assert "scale=" in frag and "pad=" in frag and "crop" not in frag


@pytest.mark.parametrize("bad", [0, 1.5, -0.2])
def test_an_impossible_letterbox_ratio_is_refused(bad: float) -> None:
    if bad >= 1.0:
        assert letterbox(bad) == ""   # >=1 means "no frame", which is legitimate
    else:
        with pytest.raises(ValueError, match="ratio"):
            letterbox(bad)


# ── motion ────────────────────────────────────────────────────────────────────


def test_a_punch_references_every_beat() -> None:
    frag = punch_in([1.0, 2.5, 4.0])
    for beat in ("1", "2.5", "4"):
        assert beat in frag


def test_punch_amount_is_clamped_to_something_watchable() -> None:
    # Below MIN it is invisible; above MAX it lurches.
    assert f"{MAX_PUNCH - 1:.4f}" in punch_in([1.0], amount=99)
    assert f"{MIN_PUNCH - 1:.4f}" in punch_in([1.0], amount=1.0)


def test_a_punch_with_no_hold_is_refused() -> None:
    with pytest.raises(ValueError, match="hold"):
        punch_in([1.0], hold=0)


def test_shake_uses_two_frequencies_or_it_is_a_diagonal_line() -> None:
    frag = shake(4, hz=7)
    assert "sin(t*7)" in frag
    assert "sin(t*9.59)" in frag  # 7 * 1.37 — deliberately incommensurate


def test_shake_is_clamped_before_it_reads_as_a_broken_export() -> None:
    assert f"{MAX_SHAKE_PX:g}*sin" in shake(999)


def test_shake_pads_before_cropping_so_edges_never_go_black() -> None:
    frag = shake(4)
    assert frag.index("pad=") < frag.index("crop=")


def test_speed_ramp_only_retimes_the_picture() -> None:
    # Re-timing audio would break the one guarantee the pipeline makes.
    frag = speed_ramp([(0.0, 1.0), (2.0, 0.5)])
    assert frag.startswith("setpts=")
    assert "atempo" not in frag


def test_a_zero_speed_is_refused() -> None:
    with pytest.raises(ValueError, match="speed"):
        speed_ramp([(0.0, 0)])


def test_glitch_is_gated_to_windows_not_continuous() -> None:
    # A permanent RGB split looks like a broken colour pipeline.
    frag = glitch([1.0, 3.0])
    assert frag.count("between(t") == 2


def test_glitch_gates_via_enable_because_the_shift_takes_no_expression() -> None:
    """rgbashift's rh/bh are ints; an expression there fails with a bare
    'Invalid argument' that names the option but never the reason."""
    frag = glitch([1.0])
    assert "enable=" in frag
    assert "rh=6" in frag and "bh=-6" in frag


def test_a_punch_emits_a_constant_output_size() -> None:
    """The failure this guards against is late and loud in the wrong place: a
    time-varying `scale` builds fine and then kills libx264 with -22."""
    frag = punch_in([0.5], width=1080, height=1920)
    assert frag.startswith("zoompan=")
    assert "s=1080x1920" in frag
    assert "scale=" not in frag


def test_a_punch_counts_in_frames_not_seconds() -> None:
    # zoompan's clock is `on` (output frame index); passing seconds would put every
    # beat ~30x too late and the punch would simply never be seen.
    assert "on/30" in punch_in([0.5], fps=30)
    assert "on/24" in punch_in([0.5], fps=24)


# ── captions: the timing that makes them land ────────────────────────────────


def test_words_carry_their_own_measured_timing() -> None:
    chars = list("hi there")
    starts = [i * 0.1 for i in range(len(chars))]
    ends = [s + 0.1 for s in starts]
    words = words_from_alignment(chars, starts, ends)
    assert [w.text for w in words] == ["hi", "there"]
    assert words[0].start == pytest.approx(0.0)
    assert words[1].start == pytest.approx(0.3)


def test_a_drawn_out_word_stays_on_screen_longer() -> None:
    # The whole reason for using measured timings instead of dividing by length.
    chars = list("a b")
    starts, ends = [0.0, 0.2, 5.0], [4.0, 0.3, 6.0]
    words = words_from_alignment(chars, starts, ends)
    assert words[0].end - words[0].start > 3.0


def test_ragged_alignment_arrays_do_not_crash() -> None:
    # The provider is not obliged to return three arrays of equal length.
    assert words_from_alignment(list("abc"), [0.0], [1.0]) == [Word("a", 0.0, 1.0)]


def test_no_words_yields_no_cues() -> None:
    assert words_from_alignment([], [], []) == []


def test_caption_margin_is_converted_out_of_video_pixels() -> None:
    """The trap: force_style is in the SUBTITLE's coordinate space, not the video's."""
    style = caption_style(safe_bottom_px=SAFE_BOTTOM_PX)
    margin = int(style.split("MarginV=")[1].split(",")[0])
    assert margin != SAFE_BOTTOM_PX, "a raw pixel value would land ~7x too high"
    assert 40 < margin < 80
    # And it must describe the same physical position.
    assert margin / 288 == pytest.approx(SAFE_BOTTOM_PX / VERTICAL_H, rel=0.02)


def test_kinetic_ass_is_a_complete_document() -> None:
    words = [Word("one", 0.0, 0.4), Word("two", 0.4, 0.8), Word("three", 0.8, 1.2)]
    doc = kinetic_ass(words)
    assert "[Script Info]" in doc and "[V4+ Styles]" in doc and "[Events]" in doc
    assert "PlayResY: 288" in doc


def test_every_word_reaches_the_document() -> None:
    words = [Word(f"w{i}", i * 0.3, i * 0.3 + 0.3) for i in range(7)]
    doc = kinetic_ass(words, group=3)
    for i in range(7):
        assert f"w{i}" in doc


def test_words_arrive_individually_not_as_a_block() -> None:
    # Each word carries its own \t transform offset — that IS the kinetic effect.
    words = [Word("a", 0.0, 0.3), Word("b", 0.5, 0.8), Word("c", 1.0, 1.3)]
    doc = kinetic_ass(words, group=3)
    assert doc.count("\\t(") == 3


def test_cues_are_ordered_and_do_not_run_backwards() -> None:
    words = [Word(f"w{i}", i * 0.4, i * 0.4 + 0.4) for i in range(9)]
    doc = kinetic_ass(words, group=3)
    times = [
        line.split(",")[1] for line in doc.splitlines() if line.startswith("Dialogue:")
    ]
    assert times == sorted(times)


def test_a_group_size_of_zero_is_refused() -> None:
    with pytest.raises(ValueError, match="group"):
        kinetic_ass([Word("a", 0, 1)], group=0)


def test_the_lead_never_produces_a_negative_timestamp() -> None:
    doc = kinetic_ass([Word("a", 0.01, 0.4)], lead=0.5)
    assert "-" not in doc.split("Dialogue: 0,")[1].split(",")[0]


# ── overlays ──────────────────────────────────────────────────────────────────


def test_drawtext_escapes_the_characters_that_end_the_option_early() -> None:
    frag = drawtext("09:30:40", font=r"C:\Windows\Fonts\OCRAEXT.TTF", x="10", y="10")
    assert r"\:" in frag
    assert "C\\:" in frag or "C:/" not in frag.split("text=")[0].replace(r"\:", "")


def test_drawtext_keeps_a_windows_font_path_usable() -> None:
    frag = drawtext("X", font=r"C:\Windows\Fonts\OCRAEXT.TTF", x="0", y="0")
    assert "\\W" not in frag  # backslash-separated path would break the parser


# ── the ones a string assertion cannot prove ─────────────────────────────────


def _clip(path: Path, seconds: float = 2.0) -> Path:
    subprocess.run(
        [shutil.which("ffmpeg"), "-nostdin", "-y", "-f", "lavfi",
         "-i", f"testsrc=size=540x960:rate=30:duration={seconds:g}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, check=True,
    )
    return path


def _render(src: Path, dst: Path, vf: str) -> bool:
    proc = subprocess.run(
        [shutil.which("ffmpeg"), "-nostdin", "-y", "-v", "error", "-i", str(src),
         "-vf", vf, "-frames:v", "45", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dst)],
        capture_output=True,
    )
    return proc.returncode == 0 and dst.exists() and dst.stat().st_size > 0


@requires_ffmpeg
@pytest.mark.parametrize("name,vf", [
    ("grain", grain(8)),
    ("vhs", vhs()),
    ("grade", broadcast_grade()),
    ("vignette", vignette(0.4)),
    ("letterbox", letterbox(0.82)),
    ("punch", punch_in([0.5, 1.0])),
    ("shake", shake(4)),
    ("glitch", glitch([0.5])),
])
def test_every_fragment_is_a_filtergraph_ffmpeg_accepts(name: str, vf: str,
                                                        tmp_path: Path) -> None:
    """The point: a fragment that ffmpeg REJECTS fails the whole render at run time."""
    src = _clip(tmp_path / "src.mp4")
    assert _render(src, tmp_path / f"{name}.mp4", vf), f"{name} produced an invalid graph"


@requires_ffmpeg
def test_a_punch_actually_changes_the_frame(tmp_path: Path) -> None:
    """An expression ffmpeg accepts but that moves nothing looks identical in the CLI."""
    src = _clip(tmp_path / "src.mp4")
    plain, punched = tmp_path / "plain.mp4", tmp_path / "punched.mp4"
    assert _render(src, plain, "null")
    assert _render(src, punched, punch_in([0.5], amount=1.25, hold=0.4))

    def frame(video: Path, n: int) -> bytes:
        out = tmp_path / f"{video.stem}{n}.pgm"
        subprocess.run(
            [shutil.which("ffmpeg"), "-nostdin", "-y", "-v", "error", "-i", str(video),
             "-vf", f"select='eq(n\\,{n})',format=gray", "-frames:v", "1", str(out)],
            capture_output=True, check=True,
        )
        return out.read_bytes()

    # Frame 18 is mid-punch (0.6s at 30fps); the picture must differ from the un-punched.
    assert frame(punched, 18) != frame(plain, 18), "the punch moved nothing"


@requires_ffmpeg
def test_the_vhs_pass_actually_alters_chroma(tmp_path: Path) -> None:
    src = _clip(tmp_path / "src.mp4")
    plain, taped = tmp_path / "plain.mp4", tmp_path / "taped.mp4"
    assert _render(src, plain, "null")
    assert _render(src, taped, vhs(bleed=6, scanlines=False))
    assert plain.read_bytes() != taped.read_bytes()


def test_holding_an_accent_is_not_flat_desaturation() -> None:
    """Flat desaturation reads as period-correct and erases the brand's only accent.

    Measured on a real frame: at saturation 0.15 the coral hoodie simply vanished.
    `colorhold` keeps the named colour and greys the rest, which is the art direction's
    own rule ("ONE lead accent, let the dark carry the rest") applied to the grade.
    """
    from navig.media.fx import hold_accent

    frag = hold_accent("0xD63A32")
    assert frag.startswith("colorhold=")
    assert "0xD63A32" in frag
    assert "saturation" not in frag


@pytest.mark.parametrize("bad", [0, -0.1, 1.5])
def test_an_impossible_similarity_is_refused(bad: float) -> None:
    from navig.media.fx import hold_accent

    with pytest.raises(ValueError, match="similarity"):
        hold_accent("0xD63A32", similarity=bad)


@pytest.mark.parametrize("ratio", [0.62, 0.7, 0.82, 0.86, 0.93])
def test_letterbox_always_produces_even_dimensions(ratio: float) -> None:
    """Odd dimensions kill libx264 under yuv420p — and only at encode time, long after
    the filtergraph has been accepted. 1080x0.86 padded by 1/0.86 gives 1079x1919."""
    frag = letterbox(ratio, width=1080, height=1920)
    scale = frag.split("scale=")[1].split(",")[0]
    w, h = (int(v) for v in scale.split(":"))
    assert w % 2 == 0 and h % 2 == 0, f"{ratio} produced {w}x{h}"


def test_letterbox_pads_back_to_the_exact_frame_size() -> None:
    # Not "approximately the input size" — the output must match the format exactly.
    assert "pad=1080:1920:" in letterbox(0.86, width=1080, height=1920)


# ── the beat hit ──────────────────────────────────────────────────────────────


def test_a_flash_lifts_midtones_and_leaves_black_alone() -> None:
    """Additive brightness was the obvious implementation and it destroys dark footage.

    ``eq=brightness`` raises the FLOOR, so a frame that was black becomes flat grey for
    the duration of the hit — measured on a real render, where every downbeat washed the
    picture out to olive. Gamma leaves true black at zero.
    """
    chain = fx.flash([1.0, 2.0], amount=0.35)
    assert "gamma=0.65" in chain
    assert "brightness" not in chain
    assert "between(t,1,1.05)" in chain


def test_a_flash_with_no_beats_is_nothing_at_all() -> None:
    assert fx.flash([], amount=0.5) == ""
    assert fx.flash([1.0], amount=0) == ""


@pytest.mark.parametrize("amount", [0, 1, 1.5, -0.2])
def test_an_out_of_range_flash_is_refused(amount) -> None:
    if amount <= 0:
        assert fx.flash([1.0], amount=amount) == ""
    else:
        with pytest.raises(ValueError, match="between 0 and 1"):
            fx.flash([1.0], amount=amount)


# ── overlays ──────────────────────────────────────────────────────────────────


def test_an_overlay_is_placed_by_name_not_by_pixels() -> None:
    # text_w is only known to ffmpeg at render time, so a caller computing pixels could
    # never centre anything.
    centred = fx.overlay("HELLO", font="/f.ttf", pos="bottom-center")
    assert "x=(w-text_w)/2" in centred
    # ...and the bottom margin clears the platform's own chrome, not just a few pixels.
    assert f"h-text_h-{fx.SAFE_BOTTOM_PX}" in centred


def test_an_unknown_overlay_position_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown overlay position"):
        fx.overlay("x", font="/f.ttf", pos="middle-ish")


def test_a_timecode_keeps_its_own_colons() -> None:
    """drawtext() escapes ':' — right for arbitrary text, fatal for a timecode."""
    chain = fx.timecode(font="/f.ttf", fps=30)
    assert r"timecode='00\:00\:00\:00'" in chain
    assert "timecode_rate=30" in chain
