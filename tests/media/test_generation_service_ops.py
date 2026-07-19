"""generation_service derivative ops — edit / rembg / redesign / process / ingest / license.

Image-provider ops are mocked (no network); local ops (process/palette/contact-sheet)
run for real against Pillow on genuine PNGs.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

import navig.tools.image_generation as ig  # noqa: E402
from navig.media import generation_service as svc  # noqa: E402
from navig.store.generated_media import GeneratedMediaStore  # noqa: E402


def _write_png(path: Path, color=(40, 140, 180)) -> None:
    Image.new("RGB", (32, 32), color).save(path, "PNG")


class _FakeGen:
    """Stand-in for ImageGenerator: returns a GeneratedImage with tiny b64 bytes."""

    def __init__(self, config=None):
        pass

    async def close(self):
        pass

    def _img(self, prompt, provider=ig.ImageProvider.RECRAFT):
        return ig.GeneratedImage(
            prompt=prompt, revised_prompt=None, provider=provider,
            size="1024x1024", b64_data=base64.b64encode(b"EDITEDBYTES").decode(),
            model="fake-op",
        )

    async def edit(self, path, instruction, mask_path=None):
        return self._img(instruction, ig.ImageProvider.OPENAI_GPT_IMAGE)

    async def remove_background(self, path):
        return self._img("[remove background]")

    async def img2img(self, path, prompt, strength=0.4):
        return self._img(prompt)


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Temp store + a real-PNG provider + a mocked ImageGenerator for ops."""
    store = GeneratedMediaStore(tmp_path / "gm.db")
    monkeypatch.setattr(svc, "get_generated_media", lambda: store)

    async def fake_run(modality, enriched_prompt, *, provider, size, kind,
                       duration_s, n, seed, out_dir):
        objs = []
        for i in range(max(1, n) if modality == "image" else 1):
            p = Path(out_dir) / f"fake_{i}.png"
            _write_png(p)

            class _O:
                local_path = str(p)
                seed = None
                model = "fake"
            objs.append(_O())
        return objs

    monkeypatch.setattr(svc, "_run_provider", fake_run)
    monkeypatch.setattr(ig, "ImageGenerator", _FakeGen)
    return tmp_path, store


async def _source(space) -> dict:
    res = await svc.generate(modality="image", prompt="octopus", n=1, space_dir=space)
    return res["variants"][0]


async def test_edit_creates_new_variant_in_same_group(wired):
    space, store = wired
    src = await _source(space)
    edited = await svc.edit(src["id"], "make it more normal, no cape")
    assert edited["id"] != src["id"]
    assert edited["group_id"] == src["group_id"]  # lineage preserved
    assert edited["context"]["op"] == "edit"
    assert edited["context"]["derived_from"] == src["id"]
    assert Path(edited["path"]).exists()


async def test_remove_background_and_redesign(wired):
    space, _ = wired
    src = await _source(space)
    cut = await svc.remove_background(src["id"])
    assert cut["context"]["op"] == "remove_background"
    redo = await svc.redesign(src["id"], "civic worker, grounded", strength=0.5)
    assert redo["context"]["op"] == "redesign"


async def test_process_pixel_pipeline_real(wired):
    space, _ = wired
    src = await _source(space)  # a real 32x32 PNG
    out = svc.process(src["id"], [{"op": "downscale", "size": 16}, {"op": "quantize", "colors": 4}])
    assert out["context"]["op"] == "process"
    assert Path(out["path"]).exists()
    from PIL import Image as _I
    assert max(_I.open(out["path"]).size) == 16  # downscaled


async def test_derivative_ops_follow_kept_and_rejected_files(wired):
    """keep/reject move the file AND update the row path, so edit/process on a
    decided variant still find the bytes at their new location."""
    space, _ = wired

    kept_src = await _source(space)
    svc.keep(kept_src["id"])
    out = svc.process(kept_src["id"], [{"op": "downscale", "size": 8}])
    assert Path(out["path"]).exists()

    rej_src = await _source(space)
    svc.reject(rej_src["id"])
    edited = await svc.edit(rej_src["id"], "make it more normal")
    assert Path(edited["path"]).exists()
    redone = svc.process(rej_src["id"], [{"op": "quantize", "colors": 2}])
    assert Path(redone["path"]).exists()


async def test_process_rejects_non_image_rows(wired, tmp_path):
    """The LOCAL pixel pipeline only applies to images — video/audio must be refused."""
    space, store = wired
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"NOTANIMAGE")
    store.create(id="vid1", group_id="g-vid", modality="video",
                 path=str(clip), space=str(space))
    with pytest.raises(ValueError, match="images"):
        svc.process("vid1", [{"op": "downscale", "size": 8}])


async def test_ingest_external_files(wired, tmp_path):
    space, store = wired
    drop = tmp_path / "drop"
    drop.mkdir()
    for i in range(3):
        _write_png(drop / f"mj_{i}.png")
    # one companion sidecar with provenance
    (drop / "mj_0.json").write_text('{"provider":"midjourney","license":"custom"}', encoding="utf-8")
    result = svc.ingest(str(drop), provider="external", space_dir=space)
    assert result["count"] == 3
    ids = [v["id"] for v in result["variants"]]
    first = store.get(ids[0])
    assert first["provider"] == "midjourney" and first["license"] == "custom"


async def test_set_license_and_palette(wired):
    space, _ = wired
    src = await _source(space)
    row = svc.set_license(src["id"], "CC0-1.0")
    assert row["license"] == "CC0-1.0"
    # palette extraction over the kept-refs dir (use the staging dir of real PNGs)
    staging = Path(space) / ".navig" / "refs" / ".staging"
    cols = svc.extract_palette(str(staging), colors=4)
    assert cols and all(c.startswith("#") for c in cols)
