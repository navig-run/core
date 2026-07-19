"""
Media-generation provider catalog — integrity + resolver tests.

The catalog (`navig.tools.media_providers`) is the single source of truth the
generators and the Deck picker both read. These tests lock the invariants the
Deck UI depends on so a future provider addition can't silently break the flow.
"""

from __future__ import annotations

import pytest

from navig.tools.media_providers import (
    MEDIA_CATALOG,
    catalog_payload,
    key_status,
    resolve_media_key,
)

REQUIRED_KEYS = {"id", "label", "modality", "models", "vault_provider", "env", "free"}


def _all_entries():
    for modality, entries in MEDIA_CATALOG.items():
        for entry in entries:
            yield modality, entry


def test_every_credential_is_vault_addable():
    """The critical UI invariant: any provider the catalog shows must have a
    credential the vault will actually accept — otherwise the Deck renders a
    'Needs key' card with no way to ever fill it.
    """
    from navig.gateway.deck.routes.vault import _VAULT_ALLOWED_PROVIDERS

    offenders = [
        f"{modality}/{entry['id']} -> vault_provider={entry['vault_provider']!r}"
        for modality, entry in _all_entries()
        if entry["vault_provider"] and entry["vault_provider"] not in _VAULT_ALLOWED_PROVIDERS
    ]
    assert not offenders, (
        "Media providers whose credential is not in the vault allowlist "
        "(add it to _VAULT_ALLOWED_PROVIDERS): " + ", ".join(offenders)
    )


def test_catalog_shape_is_complete():
    """Every entry carries the fields the generators + Deck rely on."""
    for modality, entry in _all_entries():
        missing = REQUIRED_KEYS - set(entry)
        assert not missing, f"{modality}/{entry.get('id')} missing {missing}"
        assert entry["modality"] == modality
        assert entry["models"], f"{entry['id']} has no models"
        assert entry["free"] in {"yes", "trial", "no"}, entry["free"]
        # A keyed provider must name at least one env var; only `local` is keyless.
        if entry["vault_provider"]:
            assert entry["env"], f"{entry['id']} has a credential but no env var names"


def test_provider_ids_unique_within_modality():
    for modality, entries in MEDIA_CATALOG.items():
        ids = [e["id"] for e in entries]
        assert len(ids) == len(set(ids)), f"duplicate ids in {modality}: {ids}"


def test_catalog_payload_never_leaks_secrets_and_marks_configured(monkeypatch):
    """catalog_payload() exposes a bool `configured` flag, never a key value."""
    # Ensure a clean baseline: no media keys in the env for this assertion.
    for _, entry in _all_entries():
        for name in entry.get("env", []):
            monkeypatch.delenv(name, raising=False)

    payload = catalog_payload()
    assert set(payload["modalities"]) == {"image", "video", "audio"}
    for providers in payload["modalities"].values():
        for p in providers:
            assert isinstance(p["configured"], bool)
            # No field should carry a raw secret.
            assert "api_key" not in p
            assert "key" not in p


def test_resolve_media_key_prefers_explicit_env(monkeypatch):
    monkeypatch.setenv("RECRAFT_API_KEY", "test-recraft-key-123456")
    assert resolve_media_key("recraft", "RECRAFT_API_KEY") == "test-recraft-key-123456"


def test_resolve_media_key_missing_returns_none(monkeypatch):
    monkeypatch.delenv("NONEXISTENT_MEDIA_KEY", raising=False)
    assert resolve_media_key("definitely-not-a-provider", "NONEXISTENT_MEDIA_KEY") is None


def test_recraft_and_media_providers_are_vault_addable():
    """Every media credential the catalog can surface is on the vault allowlist."""
    from navig.gateway.deck.routes.vault import _VAULT_ALLOWED_PROVIDERS

    for prov in ("recraft", "google", "openai", "elevenlabs", "replicate",
                 "stability", "runway", "luma"):
        assert prov in _VAULT_ALLOWED_PROVIDERS, f"{prov} missing from vault allowlist"


def test_recraft_vault_roundtrip_resolves(monkeypatch):
    """A Recraft key stored in the NAVIG vault is resolved by the media resolver
    (no env var) — proves CLI/Deck/agent all pick up a vault-stored key.
    """
    monkeypatch.delenv("RECRAFT_API_KEY", raising=False)
    from navig.vault.core import get_vault

    vault = get_vault()
    vault.add(provider="recraft", credential_type="api_key",
              data={"api_key": "rk-test-recraft-key-abcdef123456"},
              profile_id="default", label="Recraft Key")
    assert resolve_media_key("recraft", "RECRAFT_API_KEY") == "rk-test-recraft-key-abcdef123456"


def test_gemini_key_lights_up_all_google_providers(monkeypatch):
    """One Google credential must enable every provider that shares it —
    the 'one key, many models' behavior the Deck advertises.
    """
    for _, entry in _all_entries():
        for name in entry.get("env", []):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test-key-0000000000")

    google_entries = [e for _, e in _all_entries() if e["vault_provider"] == "google"]
    assert len(google_entries) >= 3, "expected Gemini Flash, Gemini Pro and Veo"
    assert all(key_status(e) for e in google_entries)
