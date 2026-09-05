"""Regression: providers.discovery detects a vault-stored provider key.

``_check_vault_keys`` used ``vault.get(<label-path>)``, but ``Vault.get`` treats
its first arg as a PROVIDER ID (it builds ``_cred_label(provider, "default")``),
so a key stored via ``navig vault add openai`` (or under a manifest label path)
was never detected — the provider showed as disconnected and vision-model
resolution skipped it. Now it mirrors ``verifier._check_key``'s vault detection.
"""

from __future__ import annotations

from navig.providers.discovery import _check_vault_keys
from navig.providers.registry import get_provider


class _FakeVault:
    """Records what the code asks for; deliberately returns None from ``get`` so
    the OLD (buggy) call path can never be what makes detection pass."""

    def __init__(self, *, api_keys=None, secrets=None, items=None):
        self._api_keys = api_keys or {}
        self._secrets = secrets or {}
        self._items = items or []

    def get(self, provider, *args, **kwargs):  # the old, wrong call site
        return None

    def get_api_key(self, provider, *args, **kwargs):
        return self._api_keys.get(provider)

    def get_secret(self, label, *args, **kwargs):
        return self._secrets.get(label)

    def list(self, *args, **kwargs):
        return self._items


class _Item:
    def __init__(self, label="", provider=""):
        self.label = label
        self.provider = provider


def _use_vault(monkeypatch, vault):
    import navig.vault as vault_mod

    monkeypatch.setattr(vault_mod, "get_vault", lambda: vault)


def test_vault_key_by_provider_id_is_detected(monkeypatch):
    """Key stored under the provider id (the `navig vault add openai` shape)."""
    m = get_provider("openai")
    assert m is not None
    _use_vault(monkeypatch, _FakeVault(api_keys={"openai": "sk-live"}))
    assert _check_vault_keys(m) is True  # old code: False (only tried vault.get(label))


def test_vault_key_by_label_path_is_detected(monkeypatch):
    """Key stored under a manifest label path (e.g. 'openai/api-key')."""
    m = get_provider("openai")
    _use_vault(monkeypatch, _FakeVault(secrets={m.vault_keys[0]: "sk-live"}))
    assert _check_vault_keys(m) is True


def test_presence_only_via_list_when_decryption_unavailable(monkeypatch):
    """Daemon without a loaded master key: get_api_key/get_secret can't decrypt,
    but list() still shows the credential exists."""
    m = get_provider("openai")
    _use_vault(monkeypatch, _FakeVault(items=[_Item(provider="openai")]))
    assert _check_vault_keys(m) is True


def test_no_stored_key_returns_false(monkeypatch):
    """Nothing in the vault → not detected (regression guard, passes pre/post fix)."""
    m = get_provider("openai")
    _use_vault(monkeypatch, _FakeVault())
    assert _check_vault_keys(m) is False
