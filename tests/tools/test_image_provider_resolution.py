"""
Image-provider default resolution (bug fix): configuring a Recraft key used to
have NO effect — `navig generate` hardcoded the default to OpenAI and then
errored "OpenAI API key not configured". Now the default honors an explicit
env/config choice and, failing that, the provider whose key you configured.
"""

from __future__ import annotations

import pytest

from navig.tools import image_generation as ig
from navig.tools.image_generation import ImageProvider, _resolve_default_provider


@pytest.fixture(autouse=True)
def _no_env_no_config(monkeypatch):
    monkeypatch.delenv("IMAGE_PROVIDER", raising=False)
    # Default: no persistent config setting. Individual tests override.
    monkeypatch.setattr(ig, "_config_image_provider", lambda: None)


def test_explicit_env_wins(monkeypatch):
    monkeypatch.setenv("IMAGE_PROVIDER", "recraft")
    # even with an OpenAI key present, the explicit env choice is honored
    assert _resolve_default_provider({"openai": "sk-x"}) is ImageProvider.RECRAFT


def test_persistent_config_used_when_no_env(monkeypatch):
    monkeypatch.setattr(ig, "_config_image_provider", lambda: "recraft")
    assert _resolve_default_provider({"openai": "sk-x"}) is ImageProvider.RECRAFT


def test_invalid_explicit_falls_through_to_smart_default(monkeypatch):
    monkeypatch.setenv("IMAGE_PROVIDER", "nonsense")
    # invalid → ignored → smart default picks the one configured key (recraft)
    assert _resolve_default_provider({"recraft": "r-key"}) is ImageProvider.RECRAFT


def test_smart_default_picks_configured_recraft_when_no_openai_key():
    # THE bug scenario: only Recraft configured, no OpenAI key, nothing explicit.
    assert _resolve_default_provider({"recraft": "r-key"}) is ImageProvider.RECRAFT


def test_smart_default_maps_google_key_to_gemini():
    assert _resolve_default_provider({"google": "AIza..."}) is ImageProvider.GEMINI_FLASH


def test_openai_key_keeps_openai_default():
    assert _resolve_default_provider({"openai": "sk-x", "recraft": "r"}) is ImageProvider.OPENAI


def test_ambiguous_multiple_keys_keeps_openai_default():
    # Two non-openai keys and no openai/explicit → don't guess; historical default.
    assert _resolve_default_provider({"recraft": "r", "stability": "s"}) is ImageProvider.OPENAI


def test_nothing_configured_keeps_openai_default():
    assert _resolve_default_provider({}) is ImageProvider.OPENAI
