"""Tests for navig/providers/source_scan.py"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import navig.providers.source_scan as ss_mod
from navig.providers.source_scan import (
    PROVIDER_ENV_KEYS,
    _FALLBACK_PROVIDER_IDS,
    check_api_key_in_env,
    detect_provider_sources,
    provider_env_key,
    provider_env_vars,
    provider_has_config_key,
    provider_has_vault_key,
    scan_enabled_provider_sources,
)


# ---------------------------------------------------------------------------
# PROVIDER_ENV_KEYS
# ---------------------------------------------------------------------------

class TestProviderEnvKeys:
    def test_core_providers_present(self):
        for pid in ("openai", "anthropic", "groq", "google", "openrouter", "mistral"):
            assert pid in PROVIDER_ENV_KEYS

    def test_local_providers_have_empty_tuple(self):
        assert PROVIDER_ENV_KEYS["ollama"] == ()
        assert PROVIDER_ENV_KEYS["llamacpp"] == ()

    def test_values_are_tuples_of_strings(self):
        for key, val in PROVIDER_ENV_KEYS.items():
            assert isinstance(val, tuple), f"{key} should be a tuple"
            for v in val:
                assert isinstance(v, str)


# ---------------------------------------------------------------------------
# provider_env_vars
# ---------------------------------------------------------------------------

class TestProviderEnvVars:
    def test_returns_tuple(self):
        result = provider_env_vars("openai")
        assert isinstance(result, tuple)

    def test_fallback_keys_included(self):
        # registry call will fail in tests — should fall back to PROVIDER_ENV_KEYS
        with patch("navig.providers.registry.get_provider", side_effect=ImportError):
            result = provider_env_vars("openai")
        assert "OPENAI_API_KEY" in result

    def test_registry_env_vars_merged(self):
        mock_manifest = MagicMock()
        mock_manifest.env_vars = ["CUSTOM_KEY"]
        with patch("navig.providers.registry.get_provider", return_value=mock_manifest):
            result = provider_env_vars("openai")
        assert "CUSTOM_KEY" in result
        assert "OPENAI_API_KEY" in result  # fallback still included

    def test_no_duplicates_in_result(self):
        # if registry returns same key as fallback, it should appear only once
        mock_manifest = MagicMock()
        mock_manifest.env_vars = ["OPENAI_API_KEY"]
        with patch("navig.providers.registry.get_provider", return_value=mock_manifest):
            result = provider_env_vars("openai")
        assert result.count("OPENAI_API_KEY") == 1

    def test_unknown_provider_returns_empty_tuple(self):
        with patch("navig.providers.registry.get_provider", return_value=None):
            result = provider_env_vars("nonexistent_xyz")
        assert result == ()

    def test_registry_exception_falls_back(self):
        with patch("navig.providers.registry.get_provider", side_effect=RuntimeError("boom")):
            result = provider_env_vars("openai")
        assert "OPENAI_API_KEY" in result


# ---------------------------------------------------------------------------
# provider_env_key
# ---------------------------------------------------------------------------

class TestProviderEnvKey:
    def test_returns_empty_string_when_not_set(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with patch("navig.providers.registry.get_provider", return_value=None):
            result = provider_env_key("openai")
        assert result == ""

    def test_returns_value_from_env(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-12345")
        result = provider_env_key("openai")
        assert result == "sk-test-12345"

    def test_returns_first_non_empty_env_var(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("CLAUDE_API_KEY", "claude-token")
        result = provider_env_key("anthropic")
        assert result == "claude-token"


# ---------------------------------------------------------------------------
# provider_has_config_key
# ---------------------------------------------------------------------------

class TestProviderHasConfigKey:
    def test_false_for_empty_cfg(self, tmp_path):
        assert provider_has_config_key("openai", navig_dir=tmp_path, cfg={}) is False

    def test_false_for_none_value(self, tmp_path):
        assert provider_has_config_key("openai", navig_dir=tmp_path, cfg={"openai_api_key": None}) is False

    def test_false_for_whitespace_value(self, tmp_path):
        assert provider_has_config_key("openai", navig_dir=tmp_path, cfg={"openai_api_key": "   "}) is False

    def test_true_when_key_present(self, tmp_path):
        result = provider_has_config_key("openai", navig_dir=tmp_path, cfg={"openai_api_key": "sk-abc"})
        assert result is True

    def test_true_for_second_alias(self, tmp_path):
        # google has ("google_api_key", "gemini_api_key")
        result = provider_has_config_key("google", navig_dir=tmp_path, cfg={"gemini_api_key": "gk-123"})
        assert result is True

    def test_reads_yaml_when_cfg_is_none(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("openai_api_key: sk-from-yaml\n", encoding="utf-8")
        # patch safe_load_yaml to return the content
        with patch("navig.providers.source_scan.safe_load_yaml", return_value={"openai_api_key": "sk-from-yaml"}):
            result = provider_has_config_key("openai", navig_dir=tmp_path, cfg=None)
        assert result is True

    def test_false_for_unknown_provider(self, tmp_path):
        result = provider_has_config_key("unknown_provider_xyz", navig_dir=tmp_path, cfg={"foo": "bar"})
        assert result is False


# ---------------------------------------------------------------------------
# provider_has_vault_key
# ---------------------------------------------------------------------------

class TestProviderHasVaultKey:
    def test_returns_false_when_vault_import_fails(self):
        with patch.dict("sys.modules", {"navig.vault.core": None}):
            result = provider_has_vault_key("openai")
        assert result is False

    def test_returns_false_when_get_vault_returns_none(self):
        # get_vault imported lazily from navig.vault.core
        with patch("navig.vault.core.get_vault", return_value=None):
            result = provider_has_vault_key("openai")
        assert result is False

    def test_returns_true_when_secret_found(self):
        mock_vault = MagicMock()
        mock_vault.get_secret.return_value = "secret-value"
        with patch("navig.vault.core.get_vault", return_value=mock_vault):
            result = provider_has_vault_key("openai")
        assert result is True

    def test_returns_false_when_all_secrets_empty(self):
        mock_vault = MagicMock()
        mock_vault.get_secret.return_value = ""
        with patch("navig.vault.core.get_vault", return_value=mock_vault):
            result = provider_has_vault_key("openai")
        assert result is False


# ---------------------------------------------------------------------------
# detect_provider_sources
# ---------------------------------------------------------------------------

class TestDetectProviderSources:
    def test_empty_when_nothing_configured(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with patch("navig.providers.source_scan.provider_env_key", return_value=""):
            with patch("navig.providers.source_scan.provider_has_vault_key", return_value=False):
                with patch("navig.providers.source_scan.provider_has_config_key", return_value=False):
                    sources = detect_provider_sources("openai", navig_dir=tmp_path)
        assert sources == []

    def test_env_source_added(self, tmp_path):
        with patch("navig.providers.source_scan.provider_env_key", return_value="sk-abc"):
            with patch("navig.providers.source_scan.provider_has_vault_key", return_value=False):
                with patch("navig.providers.source_scan.provider_has_config_key", return_value=False):
                    sources = detect_provider_sources("openai", navig_dir=tmp_path)
        assert "env" in sources

    def test_vault_source_added(self, tmp_path):
        with patch("navig.providers.source_scan.provider_env_key", return_value=""):
            with patch("navig.providers.source_scan.provider_has_vault_key", return_value=True):
                with patch("navig.providers.source_scan.provider_has_config_key", return_value=False):
                    sources = detect_provider_sources("openai", navig_dir=tmp_path)
        assert "vault" in sources

    def test_config_source_added(self, tmp_path):
        with patch("navig.providers.source_scan.provider_env_key", return_value=""):
            with patch("navig.providers.source_scan.provider_has_vault_key", return_value=False):
                with patch("navig.providers.source_scan.provider_has_config_key", return_value=True):
                    sources = detect_provider_sources("openai", navig_dir=tmp_path)
        assert "config" in sources

    def test_all_sources_returned(self, tmp_path):
        with patch("navig.providers.source_scan.provider_env_key", return_value="x"):
            with patch("navig.providers.source_scan.provider_has_vault_key", return_value=True):
                with patch("navig.providers.source_scan.provider_has_config_key", return_value=True):
                    sources = detect_provider_sources("openai", navig_dir=tmp_path)
        assert set(sources) == {"env", "vault", "config"}


# ---------------------------------------------------------------------------
# check_api_key_in_env
# ---------------------------------------------------------------------------

class TestCheckApiKeyInEnv:
    def test_returns_false_when_not_set(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with patch("navig.providers.source_scan.provider_env_vars", return_value=("OPENAI_API_KEY",)):
            result = check_api_key_in_env("openai")
        assert result is False

    def test_returns_true_when_env_set(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-present")
        with patch("navig.providers.source_scan.provider_env_vars", return_value=("OPENAI_API_KEY",)):
            result = check_api_key_in_env("openai")
        assert result is True


# ---------------------------------------------------------------------------
# scan_enabled_provider_sources
# ---------------------------------------------------------------------------

class TestScanEnabledProviderSources:
    def test_uses_fallback_ids_when_registry_fails(self, tmp_path):
        # list_enabled_providers imported lazily from navig.providers.registry
        with patch("navig.providers.registry.list_enabled_providers", side_effect=ImportError):
            with patch("navig.providers.source_scan.detect_provider_sources", return_value=[]):
                with patch("navig.providers.source_scan.safe_load_yaml", return_value={}):
                    result = scan_enabled_provider_sources(navig_dir=tmp_path)
        assert isinstance(result, dict)

    def test_returns_only_providers_with_sources(self, tmp_path):
        mock_provider = MagicMock()
        mock_provider.id = "openai"
        with patch("navig.providers.registry.list_enabled_providers", return_value=[mock_provider]):
            with patch("navig.providers.source_scan.detect_provider_sources", return_value=["env"]):
                with patch("navig.providers.source_scan.safe_load_yaml", return_value={}):
                    result = scan_enabled_provider_sources(navig_dir=tmp_path)
        assert "openai" in result
        assert result["openai"] == ["env"]

    def test_excludes_providers_with_no_sources(self, tmp_path):
        mock_provider = MagicMock()
        mock_provider.id = "openai"
        with patch("navig.providers.registry.list_enabled_providers", return_value=[mock_provider]):
            with patch("navig.providers.source_scan.detect_provider_sources", return_value=[]):
                with patch("navig.providers.source_scan.safe_load_yaml", return_value={}):
                    result = scan_enabled_provider_sources(navig_dir=tmp_path)
        assert "openai" not in result

    def test_deduplicates_provider_ids(self, tmp_path):
        mock_p1 = MagicMock(); mock_p1.id = "openai"
        mock_p2 = MagicMock(); mock_p2.id = "openai"  # duplicate
        seen = []
        def fake_detect(pid, **kwargs):
            seen.append(pid)
            return ["env"]
        with patch("navig.providers.registry.list_enabled_providers", return_value=[mock_p1, mock_p2]):
            with patch("navig.providers.source_scan.detect_provider_sources", side_effect=fake_detect):
                with patch("navig.providers.source_scan.safe_load_yaml", return_value={}):
                    scan_enabled_provider_sources(navig_dir=tmp_path)
        assert seen.count("openai") == 1
