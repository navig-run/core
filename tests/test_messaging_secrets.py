import importlib
import logging


def test_resolve_telegram_token_prefers_navig_env(monkeypatch):
    mod = importlib.import_module("navig.messaging.secrets")

    # Vault is probed before env vars; stub both vault functions so the
    # env-var-preference branch is actually reached.
    monkeypatch.setattr(mod, "_resolve_telegram_token_from_vault_v2", lambda: "")
    monkeypatch.setattr(mod, "_resolve_telegram_token_from_vault_v1", lambda: "")

    monkeypatch.setenv("NAVIG_TELEGRAM_BOT_TOKEN", "navig-token")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "legacy-token")

    assert mod.resolve_telegram_bot_token({}) == "navig-token"


def test_resolve_telegram_token_uses_vault_provider_scan(monkeypatch):
    mod = importlib.import_module("navig.messaging.secrets")

    monkeypatch.delenv("NAVIG_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    class _Secret:
        def reveal(self):
            return ""

    class _Info:
        def __init__(self, credential_id, enabled=True):
            self.id = credential_id
            self.enabled = enabled

    class _Cred:
        def __init__(self, data):
            self.data = data

    class _Vault:
        def get_secret(self, provider, key, caller=None):
            return _Secret()

        def list(self, provider=None):
            if provider == "telegram":
                return [_Info("abc123")]
            return []

        def get_by_id(self, credential_id, caller=None):
            if credential_id == "abc123":
                return _Cred({"token": "vault-token"})
            return None

    monkeypatch.setattr("navig.vault.get_vault", lambda: _Vault())

    assert mod.resolve_telegram_bot_token({}) == "vault-token"


def test_resolve_telegram_token_from_config_yaml_emits_deprecation_warning(
    monkeypatch, caplog
):
    """Verify a deprecation warning is logged when bot_token is read from config."""
    mod = importlib.import_module("navig.messaging.secrets")

    monkeypatch.setattr(mod, "_resolve_telegram_token_from_vault_v2", lambda: "")
    monkeypatch.setattr(mod, "_resolve_telegram_token_from_vault_v1", lambda: "")
    monkeypatch.delenv("NAVIG_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    raw_config = {"telegram": {"bot_token": "plaintext-token"}}
    with caplog.at_level(logging.WARNING, logger="navig.messaging.secrets"):
        token = mod.resolve_telegram_bot_token(raw_config)

    assert token == "plaintext-token"
    assert any("deprecated" in rec.message.lower() for rec in caplog.records)
    assert any("vault" in rec.message.lower() for rec in caplog.records)
