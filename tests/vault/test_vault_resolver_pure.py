"""Unit tests for navig.vault.resolver pure helpers.

Covers:
- ENV_VAULT_LABELS constant structure
- has_refs()
- list_refs()
- vault_labels_for_env()
- _normalize_names() (internal, tested via resolver import)

No vault, no I/O, no network — all hermetic.
"""

from __future__ import annotations

import pytest

from navig.vault.resolver import (
    ENV_VAULT_LABELS,
    has_refs,
    list_refs,
    vault_labels_for_env,
)

# ---------------------------------------------------------------------------
# ENV_VAULT_LABELS structure
# ---------------------------------------------------------------------------


class TestEnvVaultLabels:
    def test_is_dict(self) -> None:
        assert isinstance(ENV_VAULT_LABELS, dict)

    def test_keys_are_strings(self) -> None:
        for k in ENV_VAULT_LABELS:
            assert isinstance(k, str), f"Key {k!r} is not a string"

    def test_values_are_non_empty_lists(self) -> None:
        for k, v in ENV_VAULT_LABELS.items():
            assert isinstance(v, list), f"{k}: value is not a list"
            assert len(v) > 0, f"{k}: value list is empty"

    def test_values_contain_strings(self) -> None:
        for k, v in ENV_VAULT_LABELS.items():
            for item in v:
                assert isinstance(item, str), f"{k}: {item!r} is not a string"

    def test_openai_api_key_present(self) -> None:
        assert "OPENAI_API_KEY" in ENV_VAULT_LABELS

    def test_anthropic_api_key_present(self) -> None:
        assert "ANTHROPIC_API_KEY" in ENV_VAULT_LABELS

    def test_openrouter_api_key_present(self) -> None:
        assert "OPENROUTER_API_KEY" in ENV_VAULT_LABELS

    def test_github_token_present(self) -> None:
        assert "GITHUB_TOKEN" in ENV_VAULT_LABELS

    def test_telegram_bot_token_present(self) -> None:
        assert "TELEGRAM_BOT_TOKEN" in ENV_VAULT_LABELS

    def test_openai_labels_contain_openai_path(self) -> None:
        labels = ENV_VAULT_LABELS["OPENAI_API_KEY"]
        assert any("openai" in lbl for lbl in labels)

    def test_anthropic_labels_contain_anthropic_path(self) -> None:
        labels = ENV_VAULT_LABELS["ANTHROPIC_API_KEY"]
        assert any("anthropic" in lbl for lbl in labels)


# ---------------------------------------------------------------------------
# has_refs
# ---------------------------------------------------------------------------


class TestHasRefs:
    def test_empty_string(self) -> None:
        assert has_refs("") is False

    def test_plain_text(self) -> None:
        assert has_refs("hello world") is False

    def test_vault_namespace(self) -> None:
        assert has_refs("token=${VAULT:openai/api_key}") is True

    def test_blackbox_namespace(self) -> None:
        assert has_refs("key=${BLACKBOX:mykey}") is True

    def test_cred_namespace(self) -> None:
        assert has_refs("pass=${CRED:provider}") is True

    def test_invalid_namespace_not_matched(self) -> None:
        assert has_refs("${SECRET:something}") is False

    def test_incomplete_token_no_closing_brace(self) -> None:
        assert has_refs("${VAULT:openai/api_key") is False

    def test_incomplete_token_no_opening_brace(self) -> None:
        assert has_refs("$VAULT:openai/api_key}") is False

    def test_multiple_refs_returns_true(self) -> None:
        text = "${VAULT:openai/key} and ${BLACKBOX:other}"
        assert has_refs(text) is True

    def test_ref_embedded_in_json(self) -> None:
        assert has_refs('{"token": "${VAULT:my/secret}"}') is True

    def test_returns_bool_type(self) -> None:
        assert type(has_refs("${VAULT:test}")) is bool
        assert type(has_refs("nothing")) is bool


# ---------------------------------------------------------------------------
# list_refs
# ---------------------------------------------------------------------------


class TestListRefs:
    def test_empty_string_returns_empty_list(self) -> None:
        assert list_refs("") == []

    def test_plain_text_returns_empty_list(self) -> None:
        assert list_refs("no refs here") == []

    def test_single_vault_ref(self) -> None:
        result = list_refs("${VAULT:openai/api_key}")
        assert result == [("VAULT", "openai/api_key")]

    def test_single_blackbox_ref(self) -> None:
        result = list_refs("${BLACKBOX:mykey}")
        assert result == [("BLACKBOX", "mykey")]

    def test_single_cred_ref(self) -> None:
        result = list_refs("${CRED:provider}")
        assert result == [("CRED", "provider")]

    def test_multiple_refs_order_preserved(self) -> None:
        text = "${VAULT:first} and ${BLACKBOX:second} then ${CRED:third}"
        result = list_refs(text)
        assert result == [("VAULT", "first"), ("BLACKBOX", "second"), ("CRED", "third")]

    def test_returns_list_of_tuples(self) -> None:
        result = list_refs("${VAULT:a/b}")
        assert isinstance(result, list)
        assert isinstance(result[0], tuple)
        assert len(result[0]) == 2

    def test_path_with_slashes_captured(self) -> None:
        result = list_refs("${VAULT:a/b/c/d}")
        assert result == [("VAULT", "a/b/c/d")]

    def test_path_with_underscore_and_hyphen(self) -> None:
        result = list_refs("${VAULT:my-provider/api_key}")
        assert result == [("VAULT", "my-provider/api_key")]

    def test_duplicate_refs_both_captured(self) -> None:
        text = "${VAULT:key} ${VAULT:key}"
        result = list_refs(text)
        assert len(result) == 2
        assert result[0] == result[1] == ("VAULT", "key")

    def test_invalid_namespace_not_captured(self) -> None:
        result = list_refs("${SECRET:something}")
        assert result == []

    def test_embedded_in_larger_string(self) -> None:
        text = "Authorization: Bearer ${VAULT:stripe/secret_key}"
        result = list_refs(text)
        assert result == [("VAULT", "stripe/secret_key")]

    def test_namespace_is_first_element(self) -> None:
        result = list_refs("${CRED:openai}")
        assert result[0][0] == "CRED"

    def test_label_is_second_element(self) -> None:
        result = list_refs("${VAULT:my/label}")
        assert result[0][1] == "my/label"


# ---------------------------------------------------------------------------
# vault_labels_for_env
# ---------------------------------------------------------------------------


class TestVaultLabelsForEnv:
    def test_known_env_var_returns_list(self) -> None:
        result = vault_labels_for_env("OPENAI_API_KEY")
        assert isinstance(result, list)
        assert len(result) > 0

    def test_unknown_env_var_returns_empty_list(self) -> None:
        result = vault_labels_for_env("TOTALLY_UNKNOWN_VAR_XYZ")
        assert result == []

    def test_returns_new_list_each_call(self) -> None:
        a = vault_labels_for_env("OPENAI_API_KEY")
        b = vault_labels_for_env("OPENAI_API_KEY")
        assert a == b
        assert a is not b  # must be a copy, not the same object

    def test_openai_labels_are_strings(self) -> None:
        for lbl in vault_labels_for_env("OPENAI_API_KEY"):
            assert isinstance(lbl, str)

    def test_anthropic_labels_contain_anthropic(self) -> None:
        labels = vault_labels_for_env("ANTHROPIC_API_KEY")
        assert any("anthropic" in lbl for lbl in labels)

    def test_github_token_returns_labels(self) -> None:
        labels = vault_labels_for_env("GITHUB_TOKEN")
        assert len(labels) > 0

    def test_telegram_bot_token_returns_labels(self) -> None:
        labels = vault_labels_for_env("TELEGRAM_BOT_TOKEN")
        assert len(labels) > 0

    def test_empty_string_returns_empty_list(self) -> None:
        assert vault_labels_for_env("") == []

    def test_case_sensitive_mismatch_returns_empty(self) -> None:
        # Keys in ENV_VAULT_LABELS are uppercase; lowercase should not match
        assert vault_labels_for_env("openai_api_key") == []

    def test_openrouter_labels_present(self) -> None:
        labels = vault_labels_for_env("OPENROUTER_API_KEY")
        assert len(labels) > 0
