"""processing — chroma-key / quantize / downscale / crop / normalize / pipeline / review aids.

Skips cleanly when Pillow is not installed. All fixtures are tiny synthetic
images built in ``tmp_path`` so nothing touches the real refs library.
"""

from __future__ import annotations

import os

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402

from navig.media import processing as proc  # noqa: E402

MAGENTA = (255, 0, 255)
SQUARE = (0, 128, 255)
SQ_BOX = (30, 30, 70, 70)  # content 40x40 in a 100x100 canvas


@pytest.fixture()
def src(tmp_path):
    """A 100x100 magenta image with a solid blue 40x40 square in the middle."""
    im = Image.new("RGB", (100, 100), MAGENTA)
    ImageDraw.Draw(im).rectangle((SQ_BOX[0], SQ_BOX[1], SQ_BOX[2] - 1, SQ_BOX[3] - 1), fill=SQUARE)
    p = tmp_path / "src.png"
    im.save(p)
    return str(p)


def test_pillow_available():
    assert proc.pillow_available() is True


def test_chroma_key_makes_magenta_transparent(tmp_path, src):
    out = proc.chroma_key(src, tmp_path / "ck.png", color="#FF00FF", tolerance=30)
    im = Image.open(out)
    assert im.mode == "RGBA"
    assert im.getpixel((0, 0))[3] == 0        # magenta corner -> transparent
    assert im.getpixel((50, 50))[3] == 255    # square center -> opaque


def test_downscale_longest_side_and_mode(tmp_path, src):
    original_mode = Image.open(src).mode
    out = proc.downscale(src, tmp_path / "ds.png", size=64)
    im = Image.open(out)
    assert max(im.size) == 64
    assert im.mode == original_mode  # RGB in -> RGB out


def test_downscale_non_square_preserves_aspect(tmp_path):
    p = tmp_path / "wide.png"
    Image.new("RGB", (100, 50), SQUARE).save(p)
    out = proc.downscale(p, tmp_path / "wide_ds.png", size=64)
    im = Image.open(out)
    assert im.size == (64, 32)


def test_quantize_limits_color_count(tmp_path, src):
    out = proc.quantize(src, tmp_path / "q8.png", colors=8)
    im = Image.open(out).convert("RGB")
    distinct = im.getcolors(maxcolors=1 << 16)
    assert distinct is not None and len(distinct) <= 8


def test_quantize_explicit_palette_maps_onto_it(tmp_path, src):
    palette = ["#000000", "#FFFFFF"]
    out = proc.quantize(src, tmp_path / "q2.png", colors=2, palette=palette)
    im = Image.open(out).convert("RGB")
    used = {rgb for _, rgb in im.getcolors(maxcolors=1 << 16)}
    assert used.issubset({(0, 0, 0), (255, 255, 255)})


def test_crop_auto_shrinks_to_content(tmp_path, src):
    out = proc.crop(src, tmp_path / "crop.png", box="auto")
    im = Image.open(out)
    assert im.size == (40, 40)  # trimmed the magenta border down to the square


def test_crop_explicit_box(tmp_path, src):
    out = proc.crop(src, tmp_path / "cropbox.png", box=(10, 10, 40, 60))
    im = Image.open(out)
    assert im.size == (30, 50)


def test_normalize_shrinks_and_squares(tmp_path, src):
    out = proc.normalize(src, tmp_path / "norm.png", pad=0, square=True)
    im = Image.open(out)
    assert im.mode == "RGBA"
    assert im.size == (40, 40)          # content bbox, centered square
    assert im.size[0] < 100             # actually shrank


def test_normalize_pad_grows_canvas(tmp_path, src):
    out = proc.normalize(src, tmp_path / "normpad.png", pad=4, square=True)
    im = Image.open(out)
    assert im.size == (48, 48)          # 40 content + 2*4 padding


def test_remove_solid_bg_keys_the_border(tmp_path, src):
    out = proc.remove_solid_bg(src, tmp_path / "rmbg.png", tolerance=30)
    im = Image.open(out)
    assert im.mode == "RGBA"
    assert im.getpixel((0, 0))[3] == 0        # flood-filled corner -> transparent
    assert im.getpixel((50, 50))[3] == 255    # interior square untouched


def test_pipeline_chains_to_64_rgba(tmp_path, src):
    ops = [
        {"op": "chroma_key", "color": "#FF00FF", "tolerance": 30},
        {"op": "quantize", "colors": 16},
        {"op": "downscale", "size": 64},
    ]
    out = proc.pipeline(src, tmp_path / "pipe.png", ops)
    im = Image.open(out)
    assert im.mode == "RGBA"
    assert max(im.size) == 64


def test_pipeline_unknown_op_raises(tmp_path, src):
    with pytest.raises(ValueError):
        proc.pipeline(src, tmp_path / "bad.png", [{"op": "does_not_exist"}])


def test_extract_palette_returns_hex_strings(tmp_path, src):
    palette = proc.extract_palette([src], colors=4)
    assert isinstance(palette, list)
    assert 1 <= len(palette) <= 4
    assert all(isinstance(c, str) and c.startswith("#") and len(c) == 7 for c in palette)


def test_contact_sheet_writes_nonempty_png(tmp_path, src):
    out = proc.contact_sheet([src, src, src], tmp_path / "sheet.png", cols=2, cell=64)
    assert os.path.exists(out)
    assert os.path.getsize(out) > 0
    assert Image.open(out).format == "PNG"
