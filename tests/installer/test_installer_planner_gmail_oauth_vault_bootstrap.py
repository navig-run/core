"""
Batch 62: hermetic unit tests for
  - navig/installer/planner.py              (plan, _load_module)
  - navig/connectors/gmail/oauth_config.py  (GMAIL_SCOPES, build_gmail_oauth_config)
  - navig/installer/modules/vault_bootstrap.py (plan, apply)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig/installer/planner.py
# ---------------------------------------------------------------------------

class TestInstallerPlan:
    def _make_ctx(self, profile: str = "minimal") -> "InstallerContext":
        from navig.installer.contracts import InstallerContext
        return InstallerContext(profile=profile)

    def test_raises_for_unknown_profile(self) -> None:
        from navig.installer.planner import plan
        ctx = self._make_ctx(profile="unknown_profile_xyz")
        with pytest.raises(ValueError, match="Unknown installer profile"):
            plan(ctx)

    def test_returns_list(self) -> None:
        from navig.installer.planner import plan
        from navig.installer.profiles import VALID_PROFILES
        ctx = self._make_ctx(profile=VALID_PROFILES[0])
        result = plan(ctx)
        assert isinstance(result, list)

    def test_all_items_are_actions(self) -> None:
        from navig.installer.contracts import Action
        from navig.installer.planner import plan
        from navig.installer.profiles import VALID_PROFILES
        ctx = self._make_ctx(profile=VALID_PROFILES[0])
        result = plan(ctx)
        for item in result:
            assert isinstance(item, Action)

    def test_missing_module_yields_placeholder(self) -> None:
        from navig.installer.contracts import InstallerContext
        from navig.installer.planner import plan
        from navig.installer.profiles import PROFILE_MODULES, VALID_PROFILES

        profile = VALID_PROFILES[0]
        modules = list(PROFILE_MODULES[profile])
        if not modules:
            pytest.skip("Profile has no modules")

        ctx = InstallerContext(profile=profile)
        # Make the first module's import fail
        with patch("navig.installer.planner._load_module", side_effect=[ModuleNotFoundError("not found")] + [MagicMock(plan=lambda c: []) for _ in modules[1:]]):
            result = plan(ctx)
        # First item should be a placeholder action
        assert result[0].id.endswith(".placeholder")

    def test_valid_profiles_list(self) -> None:
        from navig.installer.profiles import VALID_PROFILES
        assert isinstance(VALID_PROFILES, list)
        assert len(VALID_PROFILES) > 0

    def test_load_module_imports_module(self) -> None:
        from navig.installer.planner import _load_module
        # Use a standard library module path as a sanity check via mock
        with patch("importlib.import_module") as mock_import:
            mock_import.return_value = MagicMock()
            _load_module("vault_bootstrap")
        mock_import.assert_called_once_with("navig.installer.modules.vault_bootstrap")

    def test_plan_handles_multiple_modules(self) -> None:
        from navig.installer.contracts import Action, InstallerContext
        from navig.installer.planner import plan
        from navig.installer.profiles import VALID_PROFILES

        profile = VALID_PROFILES[0]
        ctx = InstallerContext(profile=profile)
        mock_mod = MagicMock()
        mock_mod.plan.return_value = [
            Action(id="test.a", description="A", module="test", reversible=False)
        ]
        with patch("navig.installer.planner._load_module", return_value=mock_mod):
            result = plan(ctx)
        assert len(result) >= 1


# ---------------------------------------------------------------------------
# navig/connectors/gmail/oauth_config.py
# ---------------------------------------------------------------------------

class TestGmailScopes:
    def test_is_list(self) -> None:
        from navig.connectors.gmail.oauth_config import GMAIL_SCOPES
        assert isinstance(GMAIL_SCOPES, list)

    def test_non_empty(self) -> None:
        from navig.connectors.gmail.oauth_config import GMAIL_SCOPES
        assert len(GMAIL_SCOPES) > 0

    def test_contains_gmail_scope(self) -> None:
        from navig.connectors.gmail.oauth_config import GMAIL_SCOPES
        assert any("gmail" in s for s in GMAIL_SCOPES)

    def test_contains_email_profile(self) -> None:
        from navig.connectors.gmail.oauth_config import GMAIL_SCOPES
        assert "email" in GMAIL_SCOPES
        assert "profile" in GMAIL_SCOPES

    def test_contains_openid(self) -> None:
        from navig.connectors.gmail.oauth_config import GMAIL_SCOPES
        assert "openid" in GMAIL_SCOPES

    def test_all_strings(self) -> None:
        from navig.connectors.gmail.oauth_config import GMAIL_SCOPES
        assert all(isinstance(s, str) for s in GMAIL_SCOPES)

    def test_has_readonly_scope(self) -> None:
        from navig.connectors.gmail.oauth_config import GMAIL_SCOPES
        assert any("readonly" in s for s in GMAIL_SCOPES)

    def test_has_send_scope(self) -> None:
        from navig.connectors.gmail.oauth_config import GMAIL_SCOPES
        assert any("send" in s for s in GMAIL_SCOPES)


class TestBuildGmailOauthConfig:
    def test_returns_oauth_provider_config(self) -> None:
        from navig.connectors.gmail.oauth_config import build_gmail_oauth_config
        from navig.providers.oauth import OAuthProviderConfig
        result = build_gmail_oauth_config("client-id-123")
        assert isinstance(result, OAuthProviderConfig)

    def test_client_id_set(self) -> None:
        from navig.connectors.gmail.oauth_config import build_gmail_oauth_config
        result = build_gmail_oauth_config("my-gmail-client")
        assert result.client_id == "my-gmail-client"

    def test_name_is_gmail(self) -> None:
        from navig.connectors.gmail.oauth_config import build_gmail_oauth_config
        result = build_gmail_oauth_config("cid")
        assert result.name == "Gmail"

    def test_scopes_match_gmail_scopes(self) -> None:
        from navig.connectors.gmail.oauth_config import GMAIL_SCOPES, build_gmail_oauth_config
        result = build_gmail_oauth_config("cid")
        assert result.scopes == GMAIL_SCOPES

    def test_client_secret_none_by_default(self) -> None:
        from navig.connectors.gmail.oauth_config import build_gmail_oauth_config
        result = build_gmail_oauth_config("cid")
        assert result.client_secret is None

    def test_client_secret_set(self) -> None:
        from navig.connectors.gmail.oauth_config import build_gmail_oauth_config
        result = build_gmail_oauth_config("cid", client_secret="sec")
        assert result.client_secret == "sec"

    def test_authorize_url_is_google(self) -> None:
        from navig.connectors.gmail.oauth_config import build_gmail_oauth_config
        result = build_gmail_oauth_config("cid")
        assert "google" in result.authorize_url.lower() or "accounts" in result.authorize_url.lower()

    def test_token_url_set(self) -> None:
        from navig.connectors.gmail.oauth_config import build_gmail_oauth_config
        result = build_gmail_oauth_config("cid")
        assert result.token_url  # non-empty string


# ---------------------------------------------------------------------------
# navig/installer/modules/vault_bootstrap.py
# ---------------------------------------------------------------------------

class TestVaultBootstrapPlan:
    def test_returns_list(self) -> None:
        from navig.installer.contracts import InstallerContext
        from navig.installer.modules.vault_bootstrap import plan
        ctx = InstallerContext(profile="minimal")
        result = plan(ctx)
        assert isinstance(result, list)

    def test_returns_one_action(self) -> None:
        from navig.installer.contracts import InstallerContext
        from navig.installer.modules.vault_bootstrap import plan
        ctx = InstallerContext(profile="minimal")
        assert len(plan(ctx)) == 1

    def test_action_id(self) -> None:
        from navig.installer.contracts import InstallerContext
        from navig.installer.modules.vault_bootstrap import plan
        ctx = InstallerContext(profile="minimal")
        action = plan(ctx)[0]
        assert action.id == "vault_bootstrap.init"

    def test_action_not_reversible(self) -> None:
        from navig.installer.contracts import InstallerContext
        from navig.installer.modules.vault_bootstrap import plan
        ctx = InstallerContext(profile="minimal")
        action = plan(ctx)[0]
        assert action.reversible is False


class TestVaultBootstrapApply:
    def _action(self) -> "Action":
        from navig.installer.contracts import Action
        return Action(id="vault_bootstrap.init", description="test", module="vault_bootstrap", reversible=False)

    def _ctx(self) -> "InstallerContext":
        from navig.installer.contracts import InstallerContext
        return InstallerContext(profile="minimal")

    def test_apply_when_vault_available(self) -> None:
        from navig.installer.contracts import ModuleState
        from navig.installer.modules.vault_bootstrap import apply

        mock_vault = MagicMock()
        with patch("navig.vault.core.get_vault", return_value=mock_vault):
            result = apply(self._action(), self._ctx())
        assert result.state == ModuleState.APPLIED

    def test_apply_when_vault_import_error(self) -> None:
        from navig.installer.contracts import ModuleState
        from navig.installer.modules.vault_bootstrap import apply

        with patch.dict("sys.modules", {"navig.vault.core": None}):
            result = apply(self._action(), self._ctx())
        # ImportError → SKIPPED
        assert result.state == ModuleState.SKIPPED

    def test_apply_when_vault_raises_exception(self) -> None:
        from navig.installer.contracts import ModuleState
        from navig.installer.modules.vault_bootstrap import apply

        with patch("navig.vault.core.get_vault", side_effect=RuntimeError("vault error")):
            result = apply(self._action(), self._ctx())
        assert result.state == ModuleState.SKIPPED
        assert "vault init skipped" in result.message.lower()

    def test_apply_returns_result(self) -> None:
        from navig.installer.contracts import Result
        from navig.installer.modules.vault_bootstrap import apply

        mock_vault = MagicMock()
        with patch("navig.vault.core.get_vault", return_value=mock_vault):
            result = apply(self._action(), self._ctx())
        assert isinstance(result, Result)

    def test_apply_message_applied(self) -> None:
        from navig.installer.modules.vault_bootstrap import apply
        mock_vault = MagicMock()
        with patch("navig.vault.core.get_vault", return_value=mock_vault):
            result = apply(self._action(), self._ctx())
        assert "vault" in result.message.lower()

    def test_module_has_name(self) -> None:
        import navig.installer.modules.vault_bootstrap as vb
        assert vb.name == "vault_bootstrap"

    def test_module_has_description(self) -> None:
        import navig.installer.modules.vault_bootstrap as vb
        assert isinstance(vb.description, str) and vb.description
