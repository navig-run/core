"""The two shapes a music request can take, and the free plan preview.

ElevenLabs refuses a body that mixes ``prompt`` with ``composition_plan``, and only
honours ``force_instrumental`` / ``music_length_ms`` in prompt mode and ``seed`` in plan
mode. The client has to build exactly one shape per call; a wrong mix is a 422 that
arrives after the user has already been told a render started.
"""

from __future__ import annotations

import pytest

from navig.tools import audio_generation as ag


class _Resp:
    def __init__(self, payload=None, content=b"\xff\xfbaudio", status=200):
        self._payload = payload if payload is not None else {}
        self.content = content
        self.status_code = status
        self.headers = {}
        self.text = ""

    def json(self):
        return self._payload


class _Client:
    def __init__(self):
        self.calls = []
        self.is_closed = False

    async def post(self, url, headers=None, json=None, **kw):
        self.calls.append((url, json))
        if url.endswith("/music/plan"):
            return _Resp({"positive_global_styles": ["x"], "sections": []})
        return _Resp()

    async def aclose(self):
        self.is_closed = True


@pytest.fixture
def gen(monkeypatch):
    monkeypatch.setattr(ag.AudioGenerator, "_api_key", lambda self: "k")
    g = ag.AudioGenerator(ag.AudioGenerationConfig(save_locally=False))
    client = _Client()
    g._client = client
    return g, client


async def test_prompt_mode_carries_length_and_instrumental_only(gen):
    g, client = gen
    res = await g.generate("dark boom bap", kind="music", duration_s=30, force_instrumental=True, save=False)
    url, body = client.calls[-1]
    assert url.endswith("/music")
    assert body == {"model_id": "music_v1", "prompt": "dark boom bap", "music_length_ms": 30000,
                    "force_instrumental": True}
    assert res.plan is None and res.audio == b"\xff\xfbaudio"


async def test_plan_mode_never_sends_a_prompt_or_length(gen):
    g, client = gen
    plan = {"positive_global_styles": ["a"], "negative_global_styles": [], "sections": []}
    res = await g.generate("label only", kind="music", duration_s=30, composition_plan=plan, seed=5,
                           model_id="music_v2_5", save=False)
    _, body = client.calls[-1]
    assert body == {"model_id": "music_v2_5", "composition_plan": plan,
                    "respect_sections_durations": True, "seed": 5}
    assert res.model == "music_v2_5" and res.plan is plan and res.seed == 5
    assert res.to_dict()["seed"] == 5


async def test_old_callers_get_the_old_body(gen):
    g, client = gen
    await g.generate("lofi", kind="music", duration_s=10, save=False)
    _, body = client.calls[-1]
    assert body == {"model_id": "music_v1", "prompt": "lofi", "music_length_ms": 10000}


async def test_music_plan_hits_the_free_endpoint(gen):
    g, client = gen
    plan = await g.music_plan("dark boom bap", 120, source_plan={"sections": []})
    url, body = client.calls[-1]
    assert url.endswith("/music/plan")
    assert body == {"prompt": "dark boom bap", "model_id": "music_v1", "music_length_ms": 120000,
                    "source_composition_plan": {"sections": []}}
    assert plan["positive_global_styles"] == ["x"]
