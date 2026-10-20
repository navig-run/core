"""Domain tool packs (image/video/audio) — async handlers, no sync bridge.

The packs used to bridge async→sync: inside a running loop they ran `generate()` on a
worker thread and blocked the caller's event loop on ``.result()`` for the whole render.
They are now ``async def`` handlers: the ToolRouter awaits them (``async_execute``) or
drives them to completion where no loop runs (``execute``). Generate and close still
happen on the SAME loop — the client is bound to it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import navig.tools.audio_generation as ag
import navig.tools.image_generation as ig
import navig.tools.video_generation as vg
from navig.tools.domains import audio_pack, image_pack, video_pack


class _Result:
    url = None
    local_path = None
    model = "stub"

    class _Enum:
        value = "stub"

    provider = _Enum()
    kind = _Enum()


def _stub_gen(result):
    class _StubGen:
        def __init__(self, config=None):
            pass

        async def generate(self, **kwargs):
            return result

        async def close(self):
            pass

    return _StubGen


def test_every_pack_handler_is_async_so_it_cannot_block_a_loop():
    import inspect

    for pack in (image_pack, audio_pack, video_pack):
        assert inspect.iscoroutinefunction(pack._generate), pack.__name__
        assert not hasattr(pack, "_sync_generate"), f"{pack.__name__} still has the sync bridge"


async def test_image_pack_awaits_inside_a_running_loop(monkeypatch):
    monkeypatch.setattr(ig, "ImageGenerator", _stub_gen([]))
    assert await image_pack._generate(prompt="x") == []


async def test_video_pack_awaits_inside_a_running_loop(monkeypatch):
    monkeypatch.setattr(vg, "VideoGenerator", _stub_gen(_Result()))
    out = await video_pack._generate(prompt="x")
    assert out["provider"] == "stub"


async def test_audio_pack_awaits_inside_a_running_loop(monkeypatch):
    monkeypatch.setattr(ag, "AudioGenerator", _stub_gen(_Result()))
    out = await audio_pack._generate(prompt="x")
    assert out["kind"] == "stub"


def test_image_pack_runs_where_no_loop_is_running(monkeypatch):
    """The no-loop path (a plain sync caller, as router.execute drives it) keeps working."""
    import asyncio

    monkeypatch.setattr(ig, "ImageGenerator", _stub_gen([]))
    assert asyncio.run(image_pack._generate(prompt="x")) == []


async def test_the_generator_is_closed_even_when_generate_fails(monkeypatch):
    closed: list[bool] = []

    class _Failing:
        def __init__(self, config=None):
            pass

        async def generate(self, **kwargs):
            raise RuntimeError("provider down")

        async def close(self):
            closed.append(True)

    monkeypatch.setattr(ag, "AudioGenerator", _Failing)
    with pytest.raises(RuntimeError, match="provider down"):
        await audio_pack._generate(prompt="x")
    assert closed == [True]


# -- seed-image guard (a model-supplied local path is read, never exfiltrated) ---


def test_seed_image_http_url_passes_untouched():
    url = "https://cdn.example/frame.png"
    assert video_pack._safe_seed_image(url) == url


def test_seed_image_inside_the_media_home_is_allowed(tmp_path, monkeypatch):
    """Flows inlines a local seed — allowed only under a NAVIG-owned root."""
    import navig_sdk.host as host

    home = tmp_path / "data"
    monkeypatch.setattr(host, "media_dir", lambda kind="": home / kind)
    seed = home / "videos" / "frame.png"
    seed.parent.mkdir(parents=True)
    seed.write_bytes(b"png")

    got = video_pack._safe_seed_image(str(seed))
    assert Path(got).resolve() == seed.resolve()


def test_seed_image_outside_every_root_is_refused(tmp_path, monkeypatch):
    """The exfil shape: a model points image_url at a secret outside every root."""
    import navig_sdk.host as host

    monkeypatch.setattr(host, "media_dir", lambda kind="": tmp_path / "data" / kind)
    secret = tmp_path / "outside.env"
    secret.write_text("SECRET", encoding="utf-8")

    with pytest.raises(ValueError, match="http"):
        video_pack._safe_seed_image(str(secret))
