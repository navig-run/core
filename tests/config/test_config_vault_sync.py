"""
Tests for issue #62: vault state sync.

3a — `navig config set <sensitive-key>` writes through to the vault.
3b — `navig vault list` shows a footer for AI provider keys found outside the vault.
"""

from unittest.mock import MagicMock, patch
import pytest

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_vault_mock(existing=None):
    """Return a mock CredentialsVault."""
    vault = MagicMock()
    vault.get.return_value = existing
    return vault


# ---------------------------------------------------------------------------
# 3a: set_config vault write-through
# ---------------------------------------------------------------------------


class TestSetConfigVaultWriteThrough:
    """set_config() should forward known sensitive keys to the vault."""

    def _invoke_set_config(self, key, value, vault_mock):
        """Call set_config() with a mocked vault and ConfigManager."""

        fake_cfg_manager = MagicMock()
        fake_cfg_manager.global_config = {}
        fake_cfg_manager.update_global_config = MagicMock()

        with (
            patch("navig.commands.config.get_config_manager", return_value=fake_cfg_manager),
            patch("navig.vault.get_vault", return_value=vault_mock),
            patch("navig.commands.config.ch"),  # silence console output
        ):
            # Import after patching to pick up fresh module state
            from navig.commands.config import set_config

            set_config(key, value)

        try:
            import navig.vault.core as _vault_core

            active_vault = getattr(_vault_core, "_vault", None)
            if active_vault is not None and hasattr(active_vault, "_store"):
                store = active_vault._store
                if store is not None and hasattr(store, "close"):
                    store.close()
            _vault_core._vault = None
        except Exception:  # noqa: BLE001
            pass

    def test_sensitive_key_calls_vault_add_when_no_existing(self):
        """Config set with a new sensitive key should call vault.add()."""
        vault = _make_vault_mock(existing=None)

        self._invoke_set_config("openai_api_key", "sk-test-123", vault)

        vault.get.assert_called_once_with("openai", caller="config.set")
        vault.add.assert_called_once()
        call_kwargs = vault.add.call_args.kwargs
        assert call_kwargs["provider"] == "openai"
        assert call_kwargs["data"] == {"api_key": "sk-test-123"}

    def test_sensitive_key_calls_vault_update_when_entry_exists(self):
        """Config set with an existing vault entry should call vault.update()."""
        existing = MagicMock()
        existing.id = "abc-123"
        vault = _make_vault_mock(existing=existing)

        self._invoke_set_config("openai_api_key", "sk-new-key", vault)

        vault.get.assert_called_once_with("openai", caller="config.set")
        vault.update.assert_called_once_with("abc-123", data={"api_key": "sk-new-key"})
        vault.add.assert_not_called()

    def test_non_sensitive_key_does_not_touch_vault(self):
        """Config set with a non-sensitive key must never touch the vault."""
        vault = _make_vault_mock()

        self._invoke_set_config("log_level", "DEBUG", vault)

        vault.get.assert_not_called()
        vault.add.assert_not_called()
        vault.update.assert_not_called()

    def test_vault_failure_is_silently_swallowed(self):
        """If vault.add() raises, set_config() must not raise."""
        vault = _make_vault_mock(existing=None)
        vault.add.side_effect = RuntimeError("vault unavailable")

        # Should not raise
        self._invoke_set_config("openai_api_key", "sk-bad", vault)

    def test_empty_value_skips_vault_write(self):
        """An empty string value must not trigger a vault write."""
        vault = _make_vault_mock(existing=None)

        self._invoke_set_config("openai_api_key", "", vault)

        vault.get.assert_not_called()
        vault.add.assert_not_called()

    def test_telegram_bot_token_maps_to_telegram_provider(self):
        """telegram_bot_token should write to provider='telegram', type='token'."""
        vault = _make_vault_mock(existing=None)

        self._invoke_set_config("telegram_bot_token", "1234567:AATest", vault)

        vault.get.assert_called_once_with("telegram", caller="config.set")
        call_kwargs = vault.add.call_args.kwargs
        assert call_kwargs["provider"] == "telegram"
        assert call_kwargs["data"] == {"token": "1234567:AATest"}

    def test_gemini_api_key_maps_to_google_provider(self):
        """gemini_api_key is an alias for the google provider."""
        vault = _make_vault_mock(existing=None)

        self._invoke_set_config("gemini_api_key", "AIza-test", vault)

        vault.get.assert_called_once_with("google", caller="config.set")
        call_kwargs = vault.add.call_args.kwargs
        assert call_kwargs["provider"] == "google"


# ---------------------------------------------------------------------------
# 3b: vault list external credential detection
# ---------------------------------------------------------------------------


def _make_dummy_cred(provider: str = "github_models") -> MagicMock:
    """Return a minimal mock credential that passes vault_list's .enabled filter."""
    cred = MagicMock()
    cred.enabled = True
    cred.id = f"dummy-{provider}"
    cred.provider = provider
    cred.profile_id = "default"
    cred.credential_type = MagicMock()
    cred.credential_type.value = "token"
    cred.kind = MagicMock()
    cred.kind.value = "provider"
    cred.label = provider.title()
    cred.last_used_at = None
    cred.created_at = None
    return cred


class TestVaultListExternalCredentials:
    """vault_list() should report AI credentials found outside the vault."""

    def _invoke_vault_list(
        self,
        vault_providers: list[str] | None = None,
        env_vars: dict | None = None,
        global_cfg: dict | None = None,
    ) -> str:
        """
        Call vault_list() (no-filter path) with injected mocks.

        vault_providers — provider names already in the vault (each gets a dummy cred).
        Always includes a 'github_models' cred to prevent the early-return on empty vault.

        Returns all text passed to get_console().print() joined by newlines.
        """
        env_vars = env_vars or {}
        global_cfg = global_cfg or {}

        # Always include at least one credential so the function doesn't bail out early
        # at the "Vault is empty" guard.  github_models is not in _AI_SIGNALS so it
        # doesn't interfere with the external-detection assertions.
        providers_in_vault = list({"github_models"} | set(vault_providers or []))
        creds = [_make_dummy_cred(p) for p in providers_in_vault]

        fake_vault = MagicMock()
        fake_vault.list.return_value = creds

        fake_cfg_manager = MagicMock()
        fake_cfg_manager.global_config = global_cfg

        output_lines: list[str] = []

        def capture_print(text="", **_kw):
            output_lines.append(str(text))

        fake_console = MagicMock()
        fake_console.print.side_effect = capture_print

        import navig.config as _cfg_mod

        with (
            patch("navig.commands.vault._vault_mod") as vault_mod_patch,
            patch("navig.commands.vault.get_console", return_value=fake_console),
            patch("navig.commands.vault._ch"),  # silence warning/info calls
            patch.dict("os.environ", env_vars, clear=False),
            patch.object(_cfg_mod, "get_config_manager", return_value=fake_cfg_manager),
        ):
            vault_mod_patch.get_vault.return_value = fake_vault

            from navig.commands.vault import vault_list

            vault_list(provider=None, profile=None, json_output=False)

        return "\n".join(output_lines)

    def test_env_var_outside_vault_shows_footer(self):
        """An OPENAI_API_KEY env var not in vault should appear in the footer."""
        output = self._invoke_vault_list(env_vars={"OPENAI_API_KEY": "sk-env-key"})
        assert "openai" in output
        assert "env:OPENAI_API_KEY" in output
        assert "not synced to vault" in output

    def test_config_key_outside_vault_shows_footer(self):
        """A config-stored key not in vault should appear in the footer."""
        output = self._invoke_vault_list(global_cfg={"groq_api_key": "gsk-test"})
        assert "groq" in output
        assert "config:groq_api_key" in output

    def test_provider_already_in_vault_suppressed_from_footer(self):
        """A provider already in vault must NOT appear in the external footer."""
        output = self._invoke_vault_list(
            vault_providers=["openai"],
            env_vars={"OPENAI_API_KEY": "sk-in-vault-too"},
        )
        # openai is in the vault — it should not appear in the "outside vault" footer
        lines_with_openai = [l for l in output.splitlines() if "openai" in l and "not synced" in l]
        assert not lines_with_openai

    def test_no_footer_when_no_external_creds(self):
        """With no env vars and no config keys, no footer should be printed."""
        output = self._invoke_vault_list(env_vars={}, global_cfg={})
        assert "not synced to vault" not in output
        assert "Credentials detected outside vault" not in output

    def test_footer_includes_sync_hint(self):
        """Footer must include a hint to sync via navig vault set."""
        output = self._invoke_vault_list(env_vars={"MISTRAL_API_KEY": "msk-test"})
        assert "navig vault set" in output
