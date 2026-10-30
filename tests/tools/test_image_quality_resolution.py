"""Image quality is configurable (``generate.default_quality``).

``ImageGenerator.generate`` has always accepted ``quality``; nothing on the
``navig generate`` path ever set it, so every image ran at ``standard`` (=
OpenAI ``medium``) and ``navig config set generate.default_quality high`` was a
dead key. These tests pin the three ways quality now arrives at a request:

- ``generate.default_quality`` from persistent config (``from_env``),
- an explicit ``--quality`` / ``quality=`` argument, which always wins,
- a preset's ``params.quality`` (previously translated, then dropped).
"""

from __future__ import annotations

import pytest

from navig.tools import image_generation as ig
from navig.tools.image_generation import ImageQuality, ImageSize


@pytest.fixture(autouse=True)
def _no_persistent_config(monkeypatch):
    """No env pin, no config: the built-in defaults decide."""
    monkeypatch.delenv("IMAGE_PROVIDER", raising=False)
    monkeypatch.setattr(ig, "_config_get", lambda *keys: None)


def _config(monkeypatch, **values):
    """Pretend ``navig config set generate.<key>`` put these values in place."""
    monkeypatch.setattr(ig, "_config_get", lambda *keys: values.get(keys[0]))


def test_quality_defaults_to_standard_without_config():
    assert ig._config_image_quality(ImageQuality.STANDARD) is ImageQuality.STANDARD


def test_config_quality_reaches_from_env(monkeypatch):
    _config(monkeypatch, **{"generate.default_quality": "high"})
    cfg = ig.ImageGenerationConfig.from_env()
    assert cfg.default_quality is ImageQuality.HIGH


def test_config_size_reaches_from_env(monkeypatch):
    _config(monkeypatch, **{"generate.default_size": "1536x1024"})
    cfg = ig.ImageGenerationConfig.from_env()
    assert cfg.default_size is ImageSize.LANDSCAPE_GPT


def test_unknown_config_quality_falls_back_instead_of_failing(monkeypatch):
    # A typo in a config key must not fail a render that would otherwise work.
    _config(monkeypatch, **{"generate.default_quality": "ultra"})
    cfg = ig.ImageGenerationConfig.from_env()
    assert cfg.default_quality is ImageQuality.STANDARD


def test_unknown_config_size_falls_back(monkeypatch):
    _config(monkeypatch, **{"generate.default_size": "1024x1820"})
    cfg = ig.ImageGenerationConfig.from_env()
    assert cfg.default_size is ImageSize.SQUARE_LARGE


@pytest.mark.asyncio
async def test_run_provider_passes_an_explicit_quality(monkeypatch, tmp_path):
    """The CLI/service seam: an explicit quality is converted and forwarded."""
    from navig_generate.media import generation_service as gs
    from navig_generate.tools import image_generation as plugin_ig

    seen: dict = {}

    class FakeGen:
        def __init__(self, cfg):
            self.config = cfg

        async def generate(self, prompt, **kwargs):
            seen.update(kwargs)
            return []

        async def close(self):
            pass

    monkeypatch.setattr(plugin_ig, "ImageGenerator", FakeGen)
    await gs._run_provider(
        "image", "a pigeon", provider="openai_gpt_image", size="1024x1024", kind="music",
        duration_s=None, n=1, seed=None, out_dir=tmp_path, quality="high",
    )
    assert seen["quality"] is plugin_ig.ImageQuality.HIGH


@pytest.mark.asyncio
async def test_run_provider_leaves_quality_unset_when_not_asked(monkeypatch, tmp_path):
    """No ``--quality`` → ``None``, so the config default applies inside the generator."""
    from navig_generate.media import generation_service as gs
    from navig_generate.tools import image_generation as plugin_ig

    seen: dict = {}

    class FakeGen:
        def __init__(self, cfg):
            self.config = cfg

        async def generate(self, prompt, **kwargs):
            seen.update(kwargs)
            return []

        async def close(self):
            pass

    monkeypatch.setattr(plugin_ig, "ImageGenerator", FakeGen)
    await gs._run_provider(
        "image", "a pigeon", provider=None, size=None, kind="music",
        duration_s=None, n=1, seed=None, out_dir=tmp_path,
    )
    assert seen["quality"] is None


@pytest.mark.asyncio
async def test_run_provider_refuses_an_unknown_quality(monkeypatch, tmp_path):
    """Same rule as ``--size``: an unknown value is refused, never silently downgraded."""
    from navig_generate.media import generation_service as gs

    with pytest.raises(ValueError):
        await gs._run_provider(
            "image", "a pigeon", provider=None, size=None, kind="music",
            duration_s=None, n=1, seed=None, out_dir=tmp_path, quality="ultra",
        )


def _response(status: int, payload: dict):
    """A stand-in for an httpx response, body included."""
    httpx = pytest.importorskip("httpx")
    return httpx.Response(status, json=payload, request=httpx.Request("POST", "https://api.openai.com/v1/images/generations"))


def test_a_retired_model_names_the_config_key_that_fixes_it():
    """A 400 says "the model does not exist". That is a config fix, not a code bug.

    gpt-image-1 kept appearing in /v1/models after it stopped being callable for
    this account, so the bare httpx message ("Client error '400 Bad Request'")
    sent the operator looking in the wrong place.
    """
    gen = ig.ImageGenerator(ig.ImageGenerationConfig(openai_image_model="gpt-image-1"))
    payload = {"error": {"message": "The model 'gpt-image-1' does not exist.", "param": "model", "code": "invalid_value"}}

    with pytest.raises(ValueError) as caught:
        gen._raise_for_openai_status(_response(400, payload), what="image generation")

    message = str(caught.value)
    assert "generate.openai_image_model" in message
    assert "gpt-image-1" in message
    assert "does not exist" in message


def test_other_failures_still_raise_the_http_error_with_its_reason():
    httpx = pytest.importorskip("httpx")
    gen = ig.ImageGenerator(ig.ImageGenerationConfig())
    payload = {"error": {"message": "Rate limit reached for images per min", "param": None}}

    with pytest.raises(httpx.HTTPStatusError) as caught:
        gen._raise_for_openai_status(_response(429, payload), what="image generation")

    assert "Rate limit reached" in str(caught.value)


def test_a_successful_response_is_not_a_failure():
    gen = ig.ImageGenerator(ig.ImageGenerationConfig())
    assert gen._raise_for_openai_status(_response(200, {"data": []}), what="image generation") is None
