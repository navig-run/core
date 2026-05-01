"""Unit tests for navig.gateway.deck.routes.admin.

Tests the _load_* helper functions that power the /api/deck/admin/* endpoints.
Tests cover:
  - Return type and shape of each payload
  - Icon and key mapping completeness
  - Graceful degradation on import failure
  - Response shape matches the TypeScript types in navig-deck
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from navig.gateway.deck.routes.admin import (
    _load_admin_settings,
    _load_image_providers,
    _load_llm_providers,
    _load_mcp_servers,
    _load_search_providers,
    _load_voice_providers,
)

# ─── _load_llm_providers ──────────────────────────────────────────────────────


class TestLoadLlmProviders:
    def test_returns_list(self):
        result = _load_llm_providers()
        assert isinstance(result, list)

    def test_non_empty(self):
        assert len(_load_llm_providers()) > 0

    def test_each_item_has_required_keys(self):
        required = {"key", "icon", "label", "subtitle", "type", "action"}
        for item in _load_llm_providers():
            assert required <= item.keys(), f"Missing keys in {item}"

    def test_type_values_valid(self):
        valid_types = {"cloud", "local", "proxy"}
        for item in _load_llm_providers():
            assert item["type"] in valid_types, f"Invalid type: {item['type']}"

    def test_openai_present(self):
        keys = {p["key"] for p in _load_llm_providers()}
        assert "openai" in keys

    def test_custom_provider_action_is_set_up(self):
        for p in _load_llm_providers():
            if p["key"] == "custom":
                assert p["action"] == "Set Up"

    def test_non_custom_provider_action_is_connect(self):
        for p in _load_llm_providers():
            if p["key"] != "custom":
                assert p["action"] == "Connect"

    def test_all_items_have_non_empty_icon(self):
        for item in _load_llm_providers():
            assert item["icon"], f"Empty icon for {item['key']}"

    def test_returns_empty_list_on_import_failure(self):
        with patch.dict(sys.modules, {"navig.routing.router": None}):
            result = _load_llm_providers()
        assert result == []


# ─── _load_search_providers ───────────────────────────────────────────────────


class TestLoadSearchProviders:
    def test_returns_dict_with_search_and_crawlers(self):
        result = _load_search_providers()
        assert "search" in result
        assert "crawlers" in result

    def test_search_is_list(self):
        assert isinstance(_load_search_providers()["search"], list)

    def test_crawlers_is_list(self):
        assert isinstance(_load_search_providers()["crawlers"], list)

    def test_search_items_have_required_keys(self):
        required = {"key", "icon", "label", "subtitle", "requires_api_key", "noToggle"}
        for item in _load_search_providers()["search"]:
            assert required <= item.keys()

    def test_crawler_items_have_required_keys(self):
        required = {"key", "icon", "label", "subtitle", "requires_api_key", "built_in", "noToggle"}
        for item in _load_search_providers()["crawlers"]:
            assert required <= item.keys()

    def test_navig_crawler_is_built_in_and_no_toggle(self):
        for c in _load_search_providers()["crawlers"]:
            if c["key"] == "navig_crawler":
                assert c["built_in"] is True
                assert c["noToggle"] is True

    def test_requires_api_key_is_bool(self):
        for item in _load_search_providers()["search"]:
            assert isinstance(item["requires_api_key"], bool)

    def test_returns_empty_on_import_failure(self):
        with patch.dict(sys.modules, {"navig.routing.router": None}):
            result = _load_search_providers()
        assert result == {"search": [], "crawlers": []}


# ─── _load_image_providers ────────────────────────────────────────────────────


class TestLoadImageProviders:
    def test_returns_list(self):
        assert isinstance(_load_image_providers(), list)

    def test_non_empty(self):
        assert len(_load_image_providers()) > 0

    def test_groups_have_required_keys(self):
        required = {"vendor_key", "vendor_name", "icon", "models"}
        for group in _load_image_providers():
            assert required <= group.keys()

    def test_models_is_list_of_dicts(self):
        for group in _load_image_providers():
            assert isinstance(group["models"], list)
            for m in group["models"]:
                assert {"key", "label", "description"} <= m.keys()

    def test_openai_group_present(self):
        keys = {g["vendor_key"] for g in _load_image_providers()}
        assert "openai" in keys

    def test_returns_empty_on_import_failure(self):
        with patch.dict(sys.modules, {"navig.routing.router": None}):
            result = _load_image_providers()
        assert result == []


# ─── _load_voice_providers ────────────────────────────────────────────────────


class TestLoadVoiceProviders:
    def test_returns_dict_with_stt_and_tts(self):
        result = _load_voice_providers()
        assert "stt" in result
        assert "tts" in result

    def test_stt_is_list(self):
        assert isinstance(_load_voice_providers()["stt"], list)

    def test_tts_is_list(self):
        assert isinstance(_load_voice_providers()["tts"], list)

    def test_stt_items_have_required_keys(self):
        required = {"key", "icon", "label", "subtitle", "provider"}
        for item in _load_voice_providers()["stt"]:
            assert required <= item.keys()

    def test_tts_items_have_required_keys(self):
        required = {"key", "icon", "label", "subtitle", "provider"}
        for item in _load_voice_providers()["tts"]:
            assert required <= item.keys()

    def test_whisper_present_in_stt(self):
        keys = {m["key"] for m in _load_voice_providers()["stt"]}
        assert "whisper" in keys

    def test_returns_empty_on_import_failure(self):
        with patch.dict(sys.modules, {"navig.routing.router": None}):
            result = _load_voice_providers()
        assert result == {"stt": [], "tts": []}


# ─── _load_mcp_servers ────────────────────────────────────────────────────────


class TestLoadMcpServers:
    def test_returns_list(self):
        result = _load_mcp_servers()
        assert isinstance(result, list)

    def test_items_have_required_keys(self):
        required = {"key", "label", "subtitle", "enabled", "running", "command"}
        for item in _load_mcp_servers():
            assert required <= item.keys()

    def test_enabled_is_bool(self):
        for item in _load_mcp_servers():
            assert isinstance(item["enabled"], bool)

    def test_running_is_bool(self):
        for item in _load_mcp_servers():
            assert isinstance(item["running"], bool)

    def test_returns_empty_list_on_mcp_manager_failure(self):
        fake_mod = MagicMock()
        fake_mod.MCPManager.side_effect = Exception("unavailable")
        with patch.dict(sys.modules, {"navig.mcp_manager": fake_mod}):
            result = _load_mcp_servers()
        assert result == []

    def test_empty_when_no_servers_registered(self):
        """MCPManager.list() returning [] should yield an empty result."""
        fake_mgr = MagicMock()
        fake_mgr.list.return_value = []
        fake_mod = MagicMock()
        fake_mod.MCPManager.return_value = fake_mgr
        with patch.dict(sys.modules, {"navig.mcp_manager": fake_mod}):
            result = _load_mcp_servers()
        assert result == []


# ─── _load_admin_settings ─────────────────────────────────────────────────────


class TestLoadAdminSettings:
    def test_returns_dict_with_three_sections(self):
        result = _load_admin_settings()
        assert {"code_interpreter", "chat_preferences", "index"} <= result.keys()

    def test_code_interpreter_keys(self):
        ci = _load_admin_settings()["code_interpreter"]
        assert {
            "python_enabled",
            "node_enabled",
            "network_enabled",
            "timeout_seconds",
            "memory_mb",
        } <= ci.keys()

    def test_chat_preferences_keys(self):
        cp = _load_admin_settings()["chat_preferences"]
        assert {"verbosity", "markdown", "streaming", "citations", "context_messages"} <= cp.keys()

    def test_index_keys(self):
        idx = _load_admin_settings()["index"]
        assert {
            "embedding_model",
            "chunk_size",
            "chunk_overlap",
            "hybrid_search",
            "reranking",
        } <= idx.keys()

    def test_boolean_fields_are_bool(self):
        s = _load_admin_settings()
        assert isinstance(s["code_interpreter"]["python_enabled"], bool)
        assert isinstance(s["chat_preferences"]["markdown"], bool)
        assert isinstance(s["index"]["hybrid_search"], bool)

    def test_integer_fields_are_int(self):
        s = _load_admin_settings()
        assert isinstance(s["code_interpreter"]["timeout_seconds"], int)
        assert isinstance(s["index"]["chunk_size"], int)
        assert isinstance(s["chat_preferences"]["context_messages"], int)

    def test_returns_defaults_on_config_failure(self):
        with patch("navig.gateway.deck.routes.admin._load_admin_settings") as mock_load:
            # Simulate config manager raising; the real function should still return defaults
            pass
        # Call real function with broken config manager
        fake_mod = MagicMock()
        fake_mod.get_config_manager.side_effect = Exception("no config")
        with patch.dict(sys.modules, {"navig.config": fake_mod}):
            result = _load_admin_settings()
        assert "code_interpreter" in result
        assert result["code_interpreter"]["python_enabled"] is True
