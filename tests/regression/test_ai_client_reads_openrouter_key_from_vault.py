"""AIClient read the OpenRouter key from the vault with a call that could never succeed.

`vault.get_secret("openrouter", "api_key", caller=…)` — `get_secret(label)` takes one
argument, so every call raised TypeError, the broad `except` swallowed it, and a key stored
in the vault was silently ignored (the client then fell back to config/env, or had no key).
"""

from __future__ import annotations

import types

import pytest


def test_openrouter_key_comes_from_the_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    import navig.vault as vault_pkg
    from navig.agent.ai_client import AIClient

    calls = []

    class _Vault:
        def get_api_key(self, provider, profile_id=None, caller="unknown"):
            calls.append((provider, caller))
            return "  sk-or-from-vault  " if provider == "openrouter" else None

        def get_secret(self, label):  # the real one-argument signature
            raise AssertionError("get_secret is not the provider-key reader")

    monkeypatch.setattr(vault_pkg, "get_vault", lambda *a, **k: _Vault())
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    client = AIClient.__new__(AIClient)
    client._load_navig_config()

    assert ("openrouter", "ai_client") in calls
    assert client._navig_api_key == "sk-or-from-vault"


def test_no_vault_key_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import navig.vault as vault_pkg
    from navig.agent.ai_client import AIClient

    monkeypatch.setattr(
        vault_pkg, "get_vault", lambda *a, **k: types.SimpleNamespace(get_api_key=lambda *a, **k: None)
    )
    client = AIClient.__new__(AIClient)
    client._load_navig_config()  # must not raise
