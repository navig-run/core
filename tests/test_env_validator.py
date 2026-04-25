"""Tests for navig.env_validator"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from navig.env_validator import REQUIRED_ENV_VARS, validate_environment


class TestRequiredEnvVars:
    def test_llm_keys_group_present(self):
        assert "LLM_KEYS" in REQUIRED_ENV_VARS

    def test_llm_keys_has_required_fields(self):
        group = REQUIRED_ENV_VARS["LLM_KEYS"]
        assert "vars" in group
        assert "desc" in group
        assert "type" in group

    def test_llm_keys_type_is_any(self):
        assert REQUIRED_ENV_VARS["LLM_KEYS"]["type"] == "any"

    def test_llm_keys_vars_are_strings(self):
        for var in REQUIRED_ENV_VARS["LLM_KEYS"]["vars"]:
            assert isinstance(var, str)

    def test_known_provider_keys_listed(self):
        vars_list = REQUIRED_ENV_VARS["LLM_KEYS"]["vars"]
        assert "OPENROUTER_API_KEY" in vars_list
        assert "OPENAI_API_KEY" in vars_list
        assert "ANTHROPIC_API_KEY" in vars_list


class TestValidateEnvironment:
    def _cleared_env(self):
        """Return a dict with none of the LLM keys set."""
        keys_to_clear = REQUIRED_ENV_VARS["LLM_KEYS"]["vars"]
        return dict.fromkeys(keys_to_clear)

    def test_passes_with_openai_key(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=False):
            validate_environment()  # must not raise

    def test_passes_with_openrouter_key(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-test"}, clear=False):
            validate_environment()  # must not raise

    def test_passes_with_anthropic_key(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}, clear=False):
            validate_environment()  # must not raise

    def test_raises_when_no_llm_key_present(self):
        stripped = dict.fromkeys(REQUIRED_ENV_VARS["LLM_KEYS"]["vars"], "")
        with patch.dict(os.environ, stripped, clear=False):
            with pytest.raises(RuntimeError, match="REQUIRED environment variables"):
                validate_environment()

    def test_error_printed_to_stderr(self, capsys):
        stripped = dict.fromkeys(REQUIRED_ENV_VARS["LLM_KEYS"]["vars"], "")
        with patch.dict(os.environ, stripped, clear=False):
            with pytest.raises(RuntimeError):
                validate_environment()
        captured = capsys.readouterr()
        assert "Environment Verification Failed" in captured.err

    def test_error_message_mentions_group_name(self, capsys):
        stripped = dict.fromkeys(REQUIRED_ENV_VARS["LLM_KEYS"]["vars"], "")
        with patch.dict(os.environ, stripped, clear=False):
            with pytest.raises(RuntimeError):
                validate_environment()
        captured = capsys.readouterr()
        assert "LLM_KEYS" in captured.err
