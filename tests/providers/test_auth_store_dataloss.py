"""A transient/unreadable read of the auth-profile store must NOT wipe stored credentials.

`AuthProfileManager._load_store` reads through json_io's `load_json_for_update`, which
RAISES `JsonReadError` on a file that exists-with-content but is transiently unreadable
(a Windows AV/backup lock, a half-written read). On that raise the manager flags the load
as failed and `save()` REFUSES to write — instead of the old `except Exception: return
AuthProfileStore()`, which handed back an EMPTY store that the next `save()` persisted
over every stored API key and OAuth refresh token.

This is the vault-class invariant: never overwrite what you could not read.
"""

from __future__ import annotations

import pytest

import navig.core.json_io as jio
from navig.providers.auth import AuthProfileManager

pytestmark = pytest.mark.integration


def _seed_two_keys(tmp_path):
    m = AuthProfileManager(config_dir=tmp_path)
    m.add_api_key(provider="openai", api_key="sk-openai-REAL")
    m.add_api_key(provider="anthropic", api_key="sk-anthropic-REAL")
    return m


def test_normal_round_trip_persists_all_keys(tmp_path):
    _seed_two_keys(tmp_path)
    fresh = AuthProfileManager(config_dir=tmp_path)  # reload from disk
    assert fresh.get_api_key(provider="openai") == "sk-openai-REAL"
    assert fresh.get_api_key(provider="anthropic") == "sk-anthropic-REAL"


def test_missing_store_is_a_fresh_install_not_a_failed_load(tmp_path):
    m = AuthProfileManager(config_dir=tmp_path)
    # No file yet — a genuine fresh install, saves must work (this is how the first key
    # ever gets stored; it must NOT be mistaken for an unreadable store).
    assert m._load_failed is False
    m.add_api_key(provider="openai", api_key="sk-first")
    assert AuthProfileManager(config_dir=tmp_path).get_api_key(provider="openai") == "sk-first"


def test_add_key_during_transient_lock_does_not_wipe_existing_credentials(monkeypatch, tmp_path):
    _seed_two_keys(tmp_path)
    store_file = tmp_path / "credentials" / "auth-profiles.json"
    before = store_file.read_text(encoding="utf-8")
    assert "sk-openai-REAL" in before and "sk-anthropic-REAL" in before

    def _locked(*_a, **_k):
        raise OSError("file is locked")  # a lock that survived json_io's retries

    monkeypatch.setattr(jio, "read_text_retrying", _locked)

    # A NEW manager (the common fresh-per-op pattern) loads during the lock, then tries to
    # add a third key. The load flags failure; save() must REFUSE rather than wipe.
    locked_mgr = AuthProfileManager(config_dir=tmp_path)
    locked_mgr.add_api_key(provider="google", api_key="sk-google-NEW")
    assert locked_mgr._load_failed is True  # the store was seen as unreadable

    # The file on disk is byte-for-byte intact — no wipe, no partial write.
    assert store_file.read_text(encoding="utf-8") == before

    # Lock lifts → a fresh manager reads the ORIGINAL two keys back (nothing lost).
    monkeypatch.undo()
    healed = AuthProfileManager(config_dir=tmp_path)
    assert healed.get_api_key(provider="openai") == "sk-openai-REAL"
    assert healed.get_api_key(provider="anthropic") == "sk-anthropic-REAL"
    # The third key was never persisted (correctly — better a lost add than a wiped store).
    assert healed.get_api_key(provider="google") is None


def test_manager_self_heals_once_the_lock_lifts(monkeypatch, tmp_path):
    _seed_two_keys(tmp_path)

    calls = {"n": 0}
    real = jio.read_text_retrying

    def _locked_then_ok(path, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("locked")  # first read fails
        return real(path, **kw)  # subsequent reads succeed

    monkeypatch.setattr(jio, "read_text_retrying", _locked_then_ok)

    m = AuthProfileManager(config_dir=tmp_path)
    assert m.store.profiles == {}  # first access: locked → empty + flagged
    assert m._load_failed is True
    # Next access re-attempts the load (self-healing) — the real keys reappear.
    assert set(m.store.profiles.keys()) == {"openai", "anthropic"}
    assert m._load_failed is False
