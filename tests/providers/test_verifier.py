"""
Unit tests for navig/providers/verifier.py

Covers: _is_soft_issue, ProviderVerificationResult, _check_probe, verify_provider
"""

from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

import pytest

from navig.providers.verifier import (
    ProviderVerificationResult,
    _is_soft_issue,
    verify_provider,
)


# ──────────────────────────────────────────────────────────────────────
# _is_soft_issue
# ──────────────────────────────────────────────────────────────────────


class TestIsSoftIssue:
    def test_no_api_key_prefix_is_soft(self):
        assert _is_soft_issue("no api key found — set OPENAI_API_KEY") is True

    def test_local_service_unreachable_is_soft(self):
        assert _is_soft_issue("local service unreachable at 127.0.0.1:11434 — is Ollama running?") is True

    def test_case_insensitive_match(self):
        assert _is_soft_issue("No API Key Found — set FOO") is True

    def test_leading_whitespace_ignored(self):
        assert _is_soft_issue("  no api key found — set BAR") is True

    def test_hard_issue_manifest_missing(self):
        assert _is_soft_issue("manifest missing required fields (id / display_name / tier)") is False

    def test_hard_issue_factory(self):
        assert _is_soft_issue("not in runtime factory (_PROVIDER_MAP)") is False

    def test_hard_issue_unexpected_error(self):
        assert _is_soft_issue("unexpected verification error: boom") is False

    def test_empty_string_is_not_soft(self):
        assert _is_soft_issue("") is False

    @pytest.mark.parametrize("prefix", [
        "no api key found",
        "local service unreachable at",
    ])
    def test_exact_prefix_matches(self, prefix):
        assert _is_soft_issue(prefix) is True


# ──────────────────────────────────────────────────────────────────────
# ProviderVerificationResult
# ──────────────────────────────────────────────────────────────────────


class TestProviderVerificationResult:
    def test_defaults_are_all_ok(self):
        r = ProviderVerificationResult(id="test", display_name="Test")
        assert r.manifest_ok is True
        assert r.factory_ok is True
        assert r.config_ok is True
        assert r.key_detected is True
        assert r.local_probe_ok is None
        assert r.issues == []

    def test_ok_true_when_no_issues(self):
        r = ProviderVerificationResult(id="x", display_name="X")
        assert r.ok is True

    def test_ok_false_when_issues_present(self):
        r = ProviderVerificationResult(id="x", display_name="X", issues=["something wrong"])
        assert r.ok is False

    def test_ok_empty_issues_list(self):
        r = ProviderVerificationResult(id="x", display_name="X", issues=[])
        assert r.ok is True

    def test_multiple_issues(self):
        r = ProviderVerificationResult(id="x", display_name="X", issues=["a", "b"])
        assert r.ok is False
        assert len(r.issues) == 2

    def test_fields_stored(self):
        r = ProviderVerificationResult(
            id="openai",
            display_name="OpenAI",
            manifest_ok=True,
            factory_ok=False,
            config_ok=True,
            key_detected=False,
            local_probe_ok=None,
            issues=["not in factory"],
        )
        assert r.id == "openai"
        assert r.display_name == "OpenAI"
        assert r.factory_ok is False
        assert r.key_detected is False
        assert r.ok is False


# ──────────────────────────────────────────────────────────────────────
# verify_provider — integration with manifest mock
# ──────────────────────────────────────────────────────────────────────


def _make_manifest(
    *,
    id="testprov",
    display_name="TestProv",
    tier="cloud",
    enabled=True,
    requires_key=True,
    env_vars=None,
    vault_keys=None,
    local_probe=None,
):
    """Build a minimal ProviderManifest-like MagicMock."""
    m = MagicMock()
    m.id = id
    m.display_name = display_name
    m.tier = tier
    m.enabled = enabled
    m.requires_key = requires_key
    m.env_vars = env_vars or []
    m.vault_keys = vault_keys or []
    m.local_probe = local_probe
    return m


class TestVerifyProvider:
    def test_missing_manifest_fields_adds_issue(self):
        manifest = _make_manifest(id="", display_name="", tier="")
        with (
            patch("navig.providers.verifier._check_factory", return_value=True),
            patch("navig.providers.verifier._check_config", return_value=True),
            patch("navig.providers.verifier._check_key", return_value=True),
        ):
            result = verify_provider(manifest)
        assert result.manifest_ok is False
        assert any("manifest missing" in i for i in result.issues)

    def test_factory_missing_enabled_adds_issue(self):
        manifest = _make_manifest(requires_key=False)
        with (
            patch("navig.providers.verifier._check_factory", return_value=False),
            patch("navig.providers.verifier._check_config", return_value=True),
            patch("navig.providers.verifier._check_key", return_value=True),
        ):
            result = verify_provider(manifest)
        assert result.factory_ok is False
        assert any("runtime factory" in i for i in result.issues)

    def test_factory_missing_disabled_no_issue(self):
        manifest = _make_manifest(enabled=False, requires_key=False)
        with (
            patch("navig.providers.verifier._check_factory", return_value=False),
            patch("navig.providers.verifier._check_config", return_value=True),
            patch("navig.providers.verifier._check_key", return_value=True),
        ):
            result = verify_provider(manifest)
        assert result.factory_ok is False
        # Disabled provider missing factory → no issue
        assert not any("runtime factory" in i for i in result.issues)

    def test_no_key_enabled_adds_issue(self):
        manifest = _make_manifest(env_vars=["MY_KEY"], vault_keys=["my-key"])
        with (
            patch("navig.providers.verifier._check_factory", return_value=True),
            patch("navig.providers.verifier._check_config", return_value=True),
            patch("navig.providers.verifier._check_key", return_value=False),
        ):
            result = verify_provider(manifest)
        assert result.key_detected is False
        assert any("no API key found" in i for i in result.issues)

    def test_local_probe_unreachable_adds_issue(self):
        manifest = _make_manifest(tier="local", requires_key=False, local_probe="127.0.0.1:11434")
        with (
            patch("navig.providers.verifier._check_factory", return_value=True),
            patch("navig.providers.verifier._check_config", return_value=True),
            patch("navig.providers.verifier._check_probe", return_value=False),
        ):
            result = verify_provider(manifest)
        assert result.local_probe_ok is False
        assert any("local service unreachable" in i for i in result.issues)

    def test_local_probe_reachable_no_issue(self):
        manifest = _make_manifest(tier="local", requires_key=False, local_probe="127.0.0.1:11434")
        with (
            patch("navig.providers.verifier._check_factory", return_value=True),
            patch("navig.providers.verifier._check_config", return_value=True),
            patch("navig.providers.verifier._check_probe", return_value=True),
        ):
            result = verify_provider(manifest)
        assert result.local_probe_ok is True
        assert result.ok is True

    def test_all_ok_no_issues(self):
        manifest = _make_manifest()
        with (
            patch("navig.providers.verifier._check_factory", return_value=True),
            patch("navig.providers.verifier._check_config", return_value=True),
            patch("navig.providers.verifier._check_key", return_value=True),
        ):
            result = verify_provider(manifest)
        assert result.ok is True
        assert result.issues == []

    def test_no_probe_when_no_local_probe(self):
        manifest = _make_manifest(local_probe=None, requires_key=False)
        with (
            patch("navig.providers.verifier._check_factory", return_value=True),
            patch("navig.providers.verifier._check_config", return_value=True),
        ):
            result = verify_provider(manifest)
        assert result.local_probe_ok is None

    def test_non_cloud_skips_config_check(self):
        manifest = _make_manifest(tier="local", requires_key=False)
        with (
            patch("navig.providers.verifier._check_factory", return_value=True),
            patch("navig.providers.verifier._check_config", return_value=False) as mock_cfg,
        ):
            result = verify_provider(manifest)
        # _check_config should not have been called for non-cloud tier
        mock_cfg.assert_not_called()
        assert result.config_ok is True
