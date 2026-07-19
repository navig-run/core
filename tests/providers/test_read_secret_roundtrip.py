"""
Regression: ConnectionStore.read_secret must round-trip the EXACT stored payload
against the REAL vault — a raw API key AND a JSON OAuth token bundle.

Uses the real navig.vault.Vault (isolated via NAVIG_DATA_DIR), NOT a FakeVault —
the bug this guards is invisible to FakeVault. store_secret writes the raw payload
under a SECRET-kind item; the old read_secret went through vault.get_secret, which
json.loads() SECRET items and returns a single field:
  * a raw key → JSONDecodeError (crashed openai-compat / chatgpt-OAuth connect),
  * a claude-max bundle → only access_token survived (refresh_token dropped → no
    OAuth auto-refresh).
read_secret now uses vault.get_bytes to round-trip both intact.
"""

from __future__ import annotations

import json

import pytest

from navig.providers.connections import ConnectionStore


@pytest.fixture
def real_store(tmp_path, monkeypatch):
    # Isolate the real vault + db under a temp NAVIG_DATA_DIR.
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    return ConnectionStore(tmp_path / "connections.db")


def test_raw_api_key_round_trips(real_store):
    ref = real_store.store_secret("openai-compat", "sk-raw-key-abcdef123456")
    assert real_store.read_secret(ref) == "sk-raw-key-abcdef123456"  # no JSONDecodeError


def test_oauth_bundle_round_trips_with_refresh_token(real_store):
    bundle = json.dumps({
        "access_token": "AT-xyz", "refresh_token": "RT-abc",
        "expires_at": 1234567890, "scopes": ["user:inference"],
    })
    ref = real_store.store_secret("claude-max", bundle)
    got = real_store.read_secret(ref)
    assert got == bundle                       # full payload, not just one field
    parsed = json.loads(got)
    assert parsed["refresh_token"] == "RT-abc"  # refresh token survives → auto-refresh works


def test_missing_ref_returns_none(real_store):
    assert real_store.read_secret(None) is None
    assert real_store.read_secret("connection/openai-compat/missing") is None
