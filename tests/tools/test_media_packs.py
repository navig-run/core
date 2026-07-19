"""Domain tool packs (image/video/audio) — the async→sync bridge.

Regression: when the handler runs inside a thread that already has a running
event loop, `generate()` is bridged through a worker thread — but the generator
must ALSO be closed on that worker loop. Closing via `asyncio.run()` in the
caller's thread raises `RuntimeError: asyncio.run() cannot be called from a
running event loop` and torpedoed every successful in-loop invocation.
"""

from __future__ import annotations

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


async def test_image_pack_bridge_survives_running_loop(monkeypatch):
    monkeypatch.setattr(ig, "ImageGenerator", _stub_gen([]))
    assert image_pack._sync_generate(prompt="x") == []


async def test_video_pack_bridge_survives_running_loop(monkeypatch):
    monkeypatch.setattr(vg, "VideoGenerator", _stub_gen(_Result()))
    out = video_pack._sync_generate(prompt="x")
    assert out["provider"] == "stub"


async def test_audio_pack_bridge_survives_running_loop(monkeypatch):
    monkeypatch.setattr(ag, "AudioGenerator", _stub_gen(_Result()))
    out = audio_pack._sync_generate(prompt="x")
    assert out["kind"] == "stub"


def test_image_pack_bridge_without_running_loop(monkeypatch):
    """The no-loop path (plain sync caller) keeps working."""
    monkeypatch.setattr(ig, "ImageGenerator", _stub_gen([]))
    assert image_pack._sync_generate(prompt="x") == []
