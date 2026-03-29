import importlib


def test_resolve_telegram_token_prefers_navig_env(monkeypatch):
    mod = importlib.import_module("navig.messaging.secrets")

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
