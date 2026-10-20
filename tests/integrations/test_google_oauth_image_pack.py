"""Tests for google_oauth_constants and tools/image_pack — batch 46.

(The commands/diagnose tests that lived here went with the module: it was never
registered as a command and printed canned, fabricated output.)
"""
from __future__ import annotations

from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# google_oauth_constants
# ---------------------------------------------------------------------------

def test_google_auth_url():
    from navig.connectors.google_oauth_constants import GOOGLE_AUTH_URL

    assert GOOGLE_AUTH_URL == "https://accounts.google.com/o/oauth2/v2/auth"


def test_google_token_url():
    from navig.connectors.google_oauth_constants import GOOGLE_TOKEN_URL

    assert GOOGLE_TOKEN_URL == "https://oauth2.googleapis.com/token"


def test_google_userinfo_url():
    from navig.connectors.google_oauth_constants import GOOGLE_USERINFO_URL

    assert GOOGLE_USERINFO_URL == "https://www.googleapis.com/oauth2/v3/userinfo"


def test_google_auth_url_is_https():
    from navig.connectors.google_oauth_constants import GOOGLE_AUTH_URL

    assert GOOGLE_AUTH_URL.startswith("https://")


def test_google_token_url_is_https():
    from navig.connectors.google_oauth_constants import GOOGLE_TOKEN_URL

    assert GOOGLE_TOKEN_URL.startswith("https://")


def test_google_userinfo_url_is_https():
    from navig.connectors.google_oauth_constants import GOOGLE_USERINFO_URL

    assert GOOGLE_USERINFO_URL.startswith("https://")


def test_google_auth_url_contains_accounts():
    from navig.connectors.google_oauth_constants import GOOGLE_AUTH_URL

    assert "accounts.google.com" in GOOGLE_AUTH_URL


def test_google_token_url_contains_googleapis():
    from navig.connectors.google_oauth_constants import GOOGLE_TOKEN_URL

    assert "googleapis.com" in GOOGLE_TOKEN_URL


def test_all_google_constants_are_strings():
    from navig.connectors.google_oauth_constants import (
        GOOGLE_AUTH_URL,
        GOOGLE_TOKEN_URL,
        GOOGLE_USERINFO_URL,
    )

    for url in (GOOGLE_AUTH_URL, GOOGLE_TOKEN_URL, GOOGLE_USERINFO_URL):
        assert isinstance(url, str), f"Not a string: {url}"


def test_google_urls_are_distinct():
    from navig.connectors.google_oauth_constants import (
        GOOGLE_AUTH_URL,
        GOOGLE_TOKEN_URL,
        GOOGLE_USERINFO_URL,
    )

    urls = {GOOGLE_AUTH_URL, GOOGLE_TOKEN_URL, GOOGLE_USERINFO_URL}
    assert len(urls) == 3


# ---------------------------------------------------------------------------
# image_pack.register_tools
# ---------------------------------------------------------------------------

def _make_registry():
    registry = MagicMock()
    registry.register = MagicMock()
    return registry


def _make_tool_meta_mock():
    """Return ToolDomain, SafetyLevel, ToolMeta mocks for patch."""
    from navig.tools.router import SafetyLevel, ToolDomain, ToolMeta

    return SafetyLevel, ToolDomain, ToolMeta


def test_image_pack_register_tools_calls_register():
    from navig.tools.domains.image_pack import register_tools

    registry = _make_registry()
    register_tools(registry)
    assert registry.register.called


def test_image_pack_registers_one_tool():
    from navig.tools.domains.image_pack import register_tools

    registry = _make_registry()
    register_tools(registry)
    assert registry.register.call_count == 1


def test_image_pack_tool_name():
    from navig.tools.domains.image_pack import register_tools
    from navig.tools.router import ToolMeta

    registry = _make_registry()
    register_tools(registry)
    call_args = registry.register.call_args
    meta = call_args[0][0] if call_args[0] else call_args[1].get("meta") or call_args[0][0]
    assert isinstance(meta, ToolMeta)
    assert meta.name == "image_generate"


def test_image_pack_handler_is_callable():
    from navig.tools.domains.image_pack import register_tools

    registry = _make_registry()
    register_tools(registry)
    call_kwargs = registry.register.call_args
    handler = call_kwargs[1].get("handler") or call_kwargs[0][1]
    assert callable(handler)


def test_image_pack_declares_no_single_required_key():
    """`image_generate` is multi-provider, so NO single key is required.

    This used to assert ``"OPENAI_API_KEY" in meta.required_config`` — a leftover from
    the single-provider era. The tool now supports recraft / openai_gpt_image / openai /
    gemini_flash / gemini_pro / stability / local, and any ONE provider key enables it
    (see the comment above ``required_config`` in image_pack.py). Declaring
    OPENAI_API_KEY as *required* would tell the user the tool is unusable without OpenAI,
    which is false — it works fine on Recraft, Gemini, Stability or a local model.
    """
    from navig.tools.domains.image_pack import register_tools
    from navig.tools.router import ToolMeta

    registry = _make_registry()
    register_tools(registry)
    meta = registry.register.call_args[0][0]
    assert isinstance(meta, ToolMeta)
    assert meta.required_config == []
    # ...and the multi-provider contract is actually advertised to the caller.
    assert "recraft" in meta.description.lower()
    assert "provider" in meta.parameters_schema


def test_image_pack_has_prompt_parameter():
    from navig.tools.domains.image_pack import register_tools

    registry = _make_registry()
    register_tools(registry)
    meta = registry.register.call_args[0][0]
    assert "prompt" in meta.parameters_schema


def test_image_pack_has_size_parameter():
    from navig.tools.domains.image_pack import register_tools

    registry = _make_registry()
    register_tools(registry)
    meta = registry.register.call_args[0][0]
    assert "size" in meta.parameters_schema


def test_image_pack_default_size():
    from navig.tools.domains.image_pack import register_tools

    registry = _make_registry()
    register_tools(registry)
    meta = registry.register.call_args[0][0]
    assert meta.parameters_schema["size"]["default"] == "1024x1024"


def test_image_pack_tags_include_image():
    from navig.tools.domains.image_pack import register_tools

    registry = _make_registry()
    register_tools(registry)
    meta = registry.register.call_args[0][0]
    assert "image" in meta.tags
