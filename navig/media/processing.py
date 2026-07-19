"""Media processing — turn AI-generated / reference art into game-ready assets.

This is the pixel-art / game-asset side of NAVIG Media. The generators in
:mod:`navig.tools` (and the versioned :mod:`navig.media.refs_library`) produce raw
art; this module *finishes* it into something a game can actually load:

  * **crop** — trim the dead border a model loves to add around a sprite.
  * **downscale** — snap to a target tile size (64×64 by default) with a crisp
    NEAREST resample so pixel art stays sharp.
  * **quantize** — reduce to a tight N-color palette, or remap onto an *exact*
    hand-authored palette (alpha preserved).
  * **chroma_key** — the classic magenta (``#FF00FF``) → transparency key.
  * **remove_solid_bg** — flood-fill a near-uniform background to alpha from the
    four corners (keeps interior pixels that happen to match the bg color).
  * **normalize** — trim to the content bounding box, pad, and center on a square
    transparent canvas so every sprite shares a consistent frame.
  * **pipeline** — chain the above from a JSON-friendly list of op dicts.

Plus two review aids: **contact_sheet** (montage a folder of variants for a quick
eyeball pass) and **extract_palette** (pull the dominant colors as ``#hex``).

Everything is **filesystem path → filesystem path** so it slots straight into the
refs-library model (stage a variant, process it, promote/reject the result).

Pillow (PIL) is a **soft dependency**: if it is not installed,
:func:`pillow_available` returns ``False`` and every processing function raises
``ImportError("Pillow required: pip install Pillow")``. Nothing here needs any
third-party package beyond Pillow + the stdlib.
"""

from __future__ import annotations

import logging
import math
import shutil
import tempfile
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)

try:  # soft import — the whole module degrades gracefully without Pillow
    from PIL import Image, ImageChops, ImageDraw, ImageFont

    _PIL_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only in Pillow-less envs
    Image = ImageChops = ImageDraw = ImageFont = None  # type: ignore[assignment]
    _PIL_AVAILABLE = False

__all__ = [
    "pillow_available",
    "crop",
    "downscale",
    "quantize",
    "chroma_key",
    "remove_solid_bg",
    "normalize",
    "pipeline",
    "extract_palette",
    "contact_sheet",
]


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #
def pillow_available() -> bool:
    """Return ``True`` when Pillow (PIL) can be imported in this environment."""
    return _PIL_AVAILABLE


def _require() -> None:
    if not _PIL_AVAILABLE:
        raise ImportError("Pillow required: pip install Pillow")


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    """Parse ``#RGB`` / ``#RRGGBB`` (``#`` optional) into an ``(r, g, b)`` tuple."""
    s = value.strip().lstrip("#")
    if len(s) == 3:  # shorthand #abc -> #aabbcc
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        raise ValueError(f"bad hex color: {value!r}")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def _rgb_to_hex(rgb: tuple[int, ...]) -> str:
    r, g, b = rgb[:3]
    return f"#{int(r):02X}{int(g):02X}{int(b):02X}"


def _has_alpha(img: "Image.Image") -> bool:
    return img.mode in ("RGBA", "LA", "PA") or (
        img.mode == "P" and "transparency" in img.info
    )


def _open(src: str | Path) -> "Image.Image":
    """Open ``src`` and fully detach it from disk (safe to delete temp inputs)."""
    _require()
    with Image.open(src) as im:
        im.load()
        return im.copy()


def _save(img: "Image.Image", dst: str | Path) -> str:
    """Write ``img`` to ``dst``, forcing PNG whenever the image carries alpha."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    suffix = dst.suffix.lower()
    fmt: str | None = None
    if not suffix or (_has_alpha(img) and suffix not in (".png", ".webp", ".gif", ".tiff")):
        fmt = "PNG"
    img.save(dst, format=fmt)
    return str(dst)


def _auto_bbox(img: "Image.Image") -> tuple[int, int, int, int] | None:
    """Bounding box of the content: alpha extent, else diff from the corner color.

    Returns ``None`` when the image is fully uniform / empty (nothing to trim).
    """
    if _has_alpha(img):
        bbox = img.convert("RGBA").getchannel("A").getbbox()
        if bbox is not None:
            return bbox
    rgb = img.convert("RGB")
    bg = rgb.getpixel((0, 0))
    diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, bg))
    return diff.getbbox()


def _resample(method: str):
    table = {
        "nearest": Image.Resampling.NEAREST,
        "bilinear": Image.Resampling.BILINEAR,
        "lanczos": Image.Resampling.LANCZOS,
    }
    try:
        return table[method.lower()]
    except KeyError:
        raise ValueError(f"unknown resample method: {method!r} (nearest|bilinear|lanczos)")


def _palette_image(palette: list[str]) -> "Image.Image":
    """Build a ``P``-mode image whose palette is exactly ``palette`` (padded)."""
    if not palette:
        raise ValueError("palette must contain at least one #hex color")
    flat: list[int] = []
    for hexc in palette:
        flat.extend(_hex_to_rgb(hexc))
    # Pad to a full 256-entry palette by cycling the given colors, so unused
    # slots are duplicates of real colors (never a stray black that pixels map to).
    i = 0
    while len(flat) < 256 * 3:
        flat.extend(_hex_to_rgb(palette[i % len(palette)]))
        i += 1
    pal_img = Image.new("P", (1, 1))
    pal_img.putpalette(flat[: 256 * 3])
    return pal_img


def _free_color(rgb: "Image.Image") -> tuple[int, int, int]:
    """Pick an RGB color that appears nowhere in ``rgb`` (a flood-fill sentinel)."""
    # maxcolors covers the entire RGB space, so this never returns None.
    used = {c for _, c in (rgb.getcolors(maxcolors=1 << 24) or [])}
    candidates = [
        (255, 0, 255), (0, 255, 0), (0, 0, 255), (255, 255, 0),
        (0, 255, 255), (255, 0, 0), (1, 2, 3), (2, 3, 4),
    ]
    for cand in candidates:
        if cand not in used:
            return cand
    for r in range(256):  # pragma: no cover - only for pathologically dense images
        cand = (r, 0, 254)
        if cand not in used:
            return cand
    return (13, 17, 19)  # pragma: no cover


# --------------------------------------------------------------------------- #
# single-op processors
# --------------------------------------------------------------------------- #
def crop(src: str | Path, dst: str | Path,
         box: tuple[int, int, int, int] | str = "auto") -> str:
    """Crop to an explicit ``(left, top, right, bottom)`` box, or ``"auto"``.

    ``"auto"`` trims a uniform / transparent border by measuring the content
    bounding box (alpha extent, or a difference image against the corner color).
    """
    img = _open(src)
    if box == "auto" or box is None:
        bb = _auto_bbox(img)
        if bb is not None:
            img = img.crop(bb)
    else:
        if not (isinstance(box, (tuple, list)) and len(box) == 4):
            raise ValueError("box must be (left, top, right, bottom) or 'auto'")
        img = img.crop(tuple(int(v) for v in box))
    return _save(img, dst)


def downscale(src: str | Path, dst: str | Path,
              size: int = 64, method: str = "nearest") -> str:
    """Resize so the LONGEST side equals ``size``, preserving aspect ratio.

    Defaults to NEAREST resampling (crisp pixel art); pass ``"bilinear"`` or
    ``"lanczos"`` for smooth downscales of non-pixel art.
    """
    img = _open(src)
    w, h = img.size
    longest = max(w, h)
    if longest == 0:
        return _save(img, dst)
    scale = size / longest
    new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
    out = img.resize(new_size, _resample(method))
    return _save(out, dst)


def quantize(src: str | Path, dst: str | Path,
             colors: int = 16, palette: list[str] | None = None) -> str:
    """Reduce to ``colors`` colors, or remap onto an exact ``palette`` of #hex.

    Alpha is preserved: the RGB channels are quantized while the original alpha
    channel is carried through untouched.
    """
    img = _open(src)
    has_alpha = _has_alpha(img)
    if has_alpha:
        rgba = img.convert("RGBA")
        alpha = rgba.getchannel("A")
        rgb = rgba.convert("RGB")
    else:
        alpha = None
        rgb = img.convert("RGB")

    if palette:
        q = rgb.quantize(palette=_palette_image(palette), dither=Image.Dither.NONE)
    else:
        q = rgb.quantize(colors=max(1, colors), dither=Image.Dither.NONE)

    out = q.convert("RGB")
    if has_alpha and alpha is not None:
        out = out.convert("RGBA")
        out.putalpha(alpha)
    return _save(out, dst)


def chroma_key(src: str | Path, dst: str | Path,
               color: str = "#FF00FF", tolerance: int = 30) -> str:
    """Key out ``color`` (default magenta) to transparency; write an RGBA PNG.

    A pixel becomes fully transparent when its **maximum per-channel** difference
    from ``color`` is within ``tolerance``. Everything else keeps its alpha.
    """
    img = _open(src).convert("RGBA")
    target = _hex_to_rgb(color)
    rgb = img.convert("RGB")

    diff = ImageChops.difference(rgb, Image.new("RGB", img.size, target))
    dr, dg, db = diff.split()
    max_diff = ImageChops.lighter(ImageChops.lighter(dr, dg), db)  # per-pixel max channel Δ
    keep = max_diff.point(lambda v: 255 if v > tolerance else 0)   # 255 = stays opaque

    new_alpha = ImageChops.multiply(img.getchannel("A"), keep)
    img.putalpha(new_alpha)
    return _save(img, dst)


def remove_solid_bg(src: str | Path, dst: str | Path, tolerance: int = 30) -> str:
    """Flood-fill a near-uniform background to transparency from the four corners.

    Unlike :func:`chroma_key`, this only removes the background *connected* to a
    corner, so interior pixels that happen to match the background color survive.
    Output is an RGBA PNG.
    """
    img = _open(src).convert("RGBA")
    w, h = img.size
    rgb = img.convert("RGB")

    work = rgb.copy()
    sentinel = _free_color(rgb)
    for xy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        ImageDraw.floodfill(work, xy, sentinel, thresh=float(tolerance))

    # Pixels now equal to the sentinel are the flood-filled background.
    diff = ImageChops.difference(work, Image.new("RGB", img.size, sentinel))
    dr, dg, db = diff.split()
    max_diff = ImageChops.lighter(ImageChops.lighter(dr, dg), db)
    keep = max_diff.point(lambda v: 255 if v > 0 else 0)  # 0 exactly at sentinel -> drop

    new_alpha = ImageChops.multiply(img.getchannel("A"), keep)
    img.putalpha(new_alpha)
    return _save(img, dst)


def normalize(src: str | Path, dst: str | Path,
              pad: int = 0, square: bool = True) -> str:
    """Trim to the content bbox, optionally ``pad``, optionally center on a square.

    The output is RGBA on a transparent canvas so sprites share a consistent frame.
    """
    img = _open(src)
    bb = _auto_bbox(img)
    if bb is not None:
        img = img.crop(bb)
    img = img.convert("RGBA")
    w, h = img.size

    if square:
        side = max(w, h) + 2 * pad
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        canvas.paste(img, ((side - w) // 2, (side - h) // 2), img)
        out = canvas
    elif pad:
        canvas = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
        canvas.paste(img, (pad, pad), img)
        out = canvas
    else:
        out = img
    return _save(out, dst)


# --------------------------------------------------------------------------- #
# pipeline
# --------------------------------------------------------------------------- #
_PIPELINE_OPS = {"crop", "downscale", "quantize", "chroma_key", "remove_solid_bg", "normalize"}


def pipeline(src: str | Path, dst: str | Path, ops: list[dict]) -> str:
    """Chain single-ops from a JSON-friendly list, writing the final image to ``dst``.

    Each op is a dict with an ``"op"`` key plus that op's keyword arguments, e.g.::

        [{"op": "chroma_key", "color": "#FF00FF", "tolerance": 30},
         {"op": "quantize", "colors": 16},
         {"op": "downscale", "size": 64},
         {"op": "normalize", "square": True}]

    Ops are applied in order via intermediate temp files. An unknown ``op`` name
    raises :class:`ValueError`.
    """
    _require()
    cur = str(src)
    tmpdir = Path(tempfile.mkdtemp(prefix="navig_generate_pipe_"))
    try:
        for i, op in enumerate(ops):
            name = op.get("op")
            if name not in _PIPELINE_OPS:
                raise ValueError(f"unknown pipeline op: {name!r}")
            step = str(tmpdir / f"step_{i:02d}.png")
            if name == "crop":
                crop(cur, step, box=op.get("box", "auto"))
            elif name == "downscale":
                downscale(cur, step, size=op.get("size", 64), method=op.get("method", "nearest"))
            elif name == "quantize":
                quantize(cur, step, colors=op.get("colors", 16), palette=op.get("palette"))
            elif name == "chroma_key":
                chroma_key(cur, step, color=op.get("color", "#FF00FF"),
                           tolerance=op.get("tolerance", 30))
            elif name == "remove_solid_bg":
                remove_solid_bg(cur, step, tolerance=op.get("tolerance", 30))
            elif name == "normalize":
                normalize(cur, step, pad=op.get("pad", 0), square=op.get("square", True))
            cur = step
        return _save(_open(cur), dst)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# review aids
# --------------------------------------------------------------------------- #
def extract_palette(paths: list[str | Path], colors: int = 16) -> list[str]:
    """Aggregate the dominant colors across ``paths`` as ``#hex`` strings.

    Each image is thumbnailed and quantized; palette usage is tallied by pixel
    count across all inputs, and the top ``colors`` most common are returned
    (fewer if the images simply do not contain that many distinct colors).
    """
    _require()
    counter: Counter[tuple[int, int, int]] = Counter()
    per_image = max(1, colors)
    for p in paths:
        img = _open(p).convert("RGB")
        img.thumbnail((128, 128), Image.Resampling.NEAREST)
        q = img.quantize(colors=per_image, dither=Image.Dither.NONE).convert("RGB")
        for count, rgb in q.getcolors(maxcolors=1 << 16) or []:
            counter[rgb] += count
    return [_rgb_to_hex(rgb) for rgb, _ in counter.most_common(colors)]


def contact_sheet(paths: list[str | Path], dst: str | Path,
                  cols: int = 6, cell: int = 128, label: bool = True) -> str:
    """Montage ``paths`` into a grid PNG for a quick review pass.

    Each image is thumbnailed to ``cell`` × ``cell`` (NEAREST — pixel art stays
    crisp), centered in its cell on a dark background, with the filename drawn
    underneath when ``label`` is set. Returns the ``dst`` path.
    """
    _require()
    paths = list(paths)
    cols = max(1, cols)
    rows = max(1, math.ceil(len(paths) / cols)) if paths else 1

    gap = 8
    label_h = 14 if label else 0
    cell_w = cell + gap
    cell_h = cell + label_h + gap
    width = cols * cell_w + gap
    height = rows * cell_h + gap

    sheet = Image.new("RGB", (width, height), (24, 24, 28))
    draw = ImageDraw.Draw(sheet)
    font = _load_font()

    for i, p in enumerate(paths):
        col, row = i % cols, i // cols
        x0 = gap + col * cell_w
        y0 = gap + row * cell_h
        try:
            thumb = _open(p).convert("RGBA")
        except Exception as exc:  # noqa: BLE001 - one bad file shouldn't kill the sheet
            logger.warning("contact_sheet: skipping %s (%s)", p, exc)
            continue
        thumb.thumbnail((cell, cell), Image.Resampling.NEAREST)
        tw, th = thumb.size
        ox = x0 + (cell - tw) // 2
        oy = y0 + (cell - th) // 2
        sheet.paste(thumb, (ox, oy), thumb)
        if label:
            name = Path(str(p)).name
            if len(name) > 18:
                name = name[:15] + "…"
            draw.text((x0 + 2, y0 + cell + 1), name, fill=(200, 200, 205), font=font)

    return _save(sheet, dst)


def _load_font():
    try:
        return ImageFont.load_default()
    except Exception:  # pragma: no cover - default font is always available in Pillow
        return None
