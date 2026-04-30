"""Tests for vault/validators.py and cli/help_dictionaries.py — batch 112."""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import field
import pytest


# ---------------------------------------------------------------------------
# Helpers: build minimal Credential / TestResult without importing full vault
# ---------------------------------------------------------------------------

def _make_credential(provider: str = "test", data: dict | None = None):
    from navig.vault.types import Credential, CredentialType

    return Credential(
        id="abc12345",
        provider=provider,
        profile_id="default",
        credential_type=CredentialType.API_KEY,
        label=f"{provider} key",
        data=data if data is not None else {},
    )


# ---------------------------------------------------------------------------
# navig/vault/validators.py — registry & pure validators
# ---------------------------------------------------------------------------

class TestValidatorsRegistry:
    def test_validators_is_dict(self):
        from navig.vault.validators import VALIDATORS
        assert isinstance(VALIDATORS, dict)

    def test_known_providers_present(self):
        from navig.vault.validators import VALIDATORS
        for p in ("openai", "anthropic", "github", "telegram"):
            assert p in VALIDATORS

    def test_all_values_are_classes(self):
        from navig.vault.validators import VALIDATORS, CredentialValidator
        for cls in VALIDATORS.values():
            assert issubclass(cls, CredentialValidator)

    def test_email_aliases_all_same(self):
        from navig.vault.validators import VALIDATORS
        assert VALIDATORS["gmail"] is VALIDATORS["email"]
        assert VALIDATORS["outlook"] is VALIDATORS["email"]


class TestGetValidator:
    def test_openai_returns_correct_type(self):
        from navig.vault.validators import get_validator, OpenAIValidator
        v = get_validator("openai")
        assert isinstance(v, OpenAIValidator)

    def test_case_insensitive(self):
        from navig.vault.validators import get_validator, OpenAIValidator
        v = get_validator("OpenAI")
        assert isinstance(v, OpenAIValidator)

    def test_unknown_provider_returns_generic(self):
        from navig.vault.validators import get_validator, GenericValidator
        v = get_validator("some_unknown_provider_xyz")
        assert isinstance(v, GenericValidator)

    def test_returns_instance_not_class(self):
        from navig.vault.validators import get_validator
        v = get_validator("github")
        assert not isinstance(v, type)

    def test_telegram_validator(self):
        from navig.vault.validators import get_validator, TelegramValidator
        v = get_validator("telegram")
        assert isinstance(v, TelegramValidator)

    def test_anthropic_validator(self):
        from navig.vault.validators import get_validator, AnthropicValidator
        v = get_validator("anthropic")
        assert isinstance(v, AnthropicValidator)


class TestListSupportedValidators:
    def test_returns_list(self):
        from navig.vault.validators import list_supported_validators
        result = list_supported_validators()
        assert isinstance(result, list)

    def test_is_sorted(self):
        from navig.vault.validators import list_supported_validators
        result = list_supported_validators()
        assert result == sorted(result)

    def test_contains_known_providers(self):
        from navig.vault.validators import list_supported_validators
        result = list_supported_validators()
        assert "openai" in result
        assert "github" in result

    def test_all_strings(self):
        from navig.vault.validators import list_supported_validators
        for item in list_supported_validators():
            assert isinstance(item, str)

    def test_non_empty(self):
        from navig.vault.validators import list_supported_validators
        assert len(list_supported_validators()) > 0


class TestGenericValidator:
    """GenericValidator makes no HTTP calls — fully testable."""

    def test_empty_data_fails(self):
        from navig.vault.validators import GenericValidator
        v = GenericValidator()
        cred = _make_credential(data={})
        result = v.validate(cred)
        assert result.success is False
        assert "empty" in result.message.lower()

    def test_nonempty_data_passes(self):
        from navig.vault.validators import GenericValidator
        v = GenericValidator()
        cred = _make_credential(data={"api_key": "sk-abc123"})
        result = v.validate(cred)
        assert result.success is True
        assert result.details.get("validation_mode") == "presence_only"

    def test_whitespace_only_value_fails(self):
        from navig.vault.validators import GenericValidator
        v = GenericValidator()
        cred = _make_credential(data={"api_key": "   "})
        result = v.validate(cred)
        assert result.success is False

    def test_result_has_provider(self):
        from navig.vault.validators import GenericValidator
        v = GenericValidator()
        cred = _make_credential(provider="myprovider", data={"token": "abc"})
        result = v.validate(cred)
        assert result.details.get("provider") == "myprovider"

    def test_result_is_test_result_type(self):
        from navig.vault.validators import GenericValidator
        from navig.vault.types import TestResult
        v = GenericValidator()
        cred = _make_credential(data={"k": "v"})
        result = v.validate(cred)
        assert isinstance(result, TestResult)


class TestOpenAIValidatorEmptyKey:
    """Tests that don't make HTTP calls (empty key early-return)."""

    def test_empty_key_fails_without_http(self):
        from navig.vault.validators import OpenAIValidator
        v = OpenAIValidator()
        cred = _make_credential(provider="openai", data={"api_key": ""})
        result = v.validate(cred)
        assert result.success is False
        assert "empty" in result.message.lower()

    def test_missing_key_fails_without_http(self):
        from navig.vault.validators import OpenAIValidator
        v = OpenAIValidator()
        cred = _make_credential(provider="openai", data={})
        result = v.validate(cred)
        assert result.success is False


class TestAnthropicValidatorEmptyKey:
    def test_empty_key_fails(self):
        from navig.vault.validators import AnthropicValidator
        v = AnthropicValidator()
        cred = _make_credential(provider="anthropic", data={"api_key": ""})
        result = v.validate(cred)
        assert result.success is False


class TestTelegramValidatorEmptyToken:
    def test_empty_token_fails(self):
        from navig.vault.validators import TelegramValidator
        v = TelegramValidator()
        cred = _make_credential(provider="telegram", data={})
        result = v.validate(cred)
        assert result.success is False
        assert "empty" in result.message.lower()


class TestCredentialGenerateId:
    def test_generate_id_returns_8_chars(self):
        from navig.vault.types import Credential
        id_ = Credential.generate_id()
        assert len(id_) == 8

    def test_generate_id_unique(self):
        from navig.vault.types import Credential
        ids = {Credential.generate_id() for _ in range(20)}
        assert len(ids) == 20

    def test_get_secret(self):
        cred = _make_credential(data={"api_key": "sk-hello"})
        assert cred.get_secret("api_key") == "sk-hello"

    def test_get_secret_missing_returns_none(self):
        cred = _make_credential(data={})
        assert cred.get_secret("api_key") is None


# ---------------------------------------------------------------------------
# navig/cli/help_dictionaries.py — HELP_REGISTRY structure
# ---------------------------------------------------------------------------

class TestHelpRegistry:
    def test_is_dict(self):
        from navig.cli.help_dictionaries import HELP_REGISTRY
        assert isinstance(HELP_REGISTRY, dict)

    def test_non_empty(self):
        from navig.cli.help_dictionaries import HELP_REGISTRY
        assert len(HELP_REGISTRY) > 0

    def test_host_entry_present(self):
        from navig.cli.help_dictionaries import HELP_REGISTRY
        assert "host" in HELP_REGISTRY

    def test_each_entry_has_desc(self):
        from navig.cli.help_dictionaries import HELP_REGISTRY
        for key, val in HELP_REGISTRY.items():
            assert "desc" in val, f"Entry '{key}' missing 'desc'"

    def test_each_entry_has_commands(self):
        from navig.cli.help_dictionaries import HELP_REGISTRY
        for key, val in HELP_REGISTRY.items():
            assert "commands" in val, f"Entry '{key}' missing 'commands'"

    def test_commands_are_dicts(self):
        from navig.cli.help_dictionaries import HELP_REGISTRY
        for key, val in HELP_REGISTRY.items():
            assert isinstance(val["commands"], dict), f"Entry '{key}' commands not a dict"

    def test_desc_are_strings(self):
        from navig.cli.help_dictionaries import HELP_REGISTRY
        for key, val in HELP_REGISTRY.items():
            assert isinstance(val["desc"], str), f"Entry '{key}' desc not a string"

    def test_all_command_values_are_strings(self):
        from navig.cli.help_dictionaries import HELP_REGISTRY
        for group_key, val in HELP_REGISTRY.items():
            for cmd_key, cmd_desc in val["commands"].items():
                assert isinstance(cmd_desc, str), \
                    f"Entry '{group_key}.{cmd_key}' description not a string"

    def test_context_entry(self):
        from navig.cli.help_dictionaries import HELP_REGISTRY
        assert "context" in HELP_REGISTRY
        assert "show" in HELP_REGISTRY["context"]["commands"]

    def test_keys_are_strings(self):
        from navig.cli.help_dictionaries import HELP_REGISTRY
        for k in HELP_REGISTRY:
            assert isinstance(k, str)
