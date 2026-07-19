"""Dev license override — the local tier switcher's invariants.

``~/.navig/dev/license-override.json`` lets a developer impersonate any Harbor
tier (and simulate owned ``item:<id>`` grants) so the Bay/paywall UI can be
tested, WITHOUT the founder signing key and WITHOUT touching the real
``license.key``. These tests lock:

  1. no override file  → the normal (token) path is used;
  2. an override tier lands its capabilities in ``current_status()``;
  3. Harbor display aliases resolve (``pass`` → ``plus``);
  4. a simulated owned item rides ``perpetual_modules`` even on Free (the lapse
     "yours forever" state);
  5. deleting the file restores the normal path (nothing is corrupted).
"""

from __future__ import annotations

import json

import pytest

from navig.license.quota import TIER_CAPABILITIES


@pytest.fixture()
def isolated_config(tmp_path, monkeypatch):
    """Point config_dir() at a temp dir and reset the status cache so each
    assertion re-derives from disk."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    import navig.license as lic

    lic._invalidate_cache()
    yield lic
    lic._invalidate_cache()


def _write_override(lic, data: dict) -> None:
    p = lic.dev_override_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")
    lic._invalidate_cache()


def test_no_override_uses_normal_path(isolated_config):
    lic = isolated_config
    assert not lic.dev_override_path().is_file()
    st = lic.current_status()
    # Empty temp config → no license.key → Free, and NOT flagged as a dev id.
    assert st.effective_tier == "free"
    assert st.license_id != "dev-override"


def test_enterprise_override_unlocks_all(isolated_config):
    lic = isolated_config
    _write_override(lic, {"tier": "enterprise"})
    st = lic.current_status()
    assert st.effective_tier == "enterprise"
    assert st.license_id == "dev-override"
    assert set(TIER_CAPABILITIES["enterprise"]) <= set(st.capabilities)
    assert st.subscription_active is True


def test_pass_alias_resolves_to_plus(isolated_config):
    lic = isolated_config
    _write_override(lic, {"tier": "pass"})
    st = lic.current_status()
    assert st.effective_tier == "plus"


def test_free_with_owned_item_is_the_lapse_state(isolated_config):
    lic = isolated_config
    _write_override(lic, {"tier": "free", "perpetual_modules": ["item:security-audit"]})
    st = lic.current_status()
    assert st.effective_tier == "free"
    assert st.subscription_active is False  # lapsed / never subscribed…
    assert "item:security-audit" in st.capabilities  # …but the bought item survives
    assert "item:security-audit" in st.perpetual_modules


def test_malformed_override_falls_through_to_real_license(isolated_config):
    """A corrupt override must NOT synthesize a (Free) status that shadows the
    real license — it falls through to the signed license path. Here no
    license.key exists, so status is the genuine unlicensed result, and crucially
    NOT the 'dev-override' synthesized status."""
    lic = isolated_config
    p = lic.dev_override_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ this is not json", encoding="utf-8")
    lic._invalidate_cache()
    st = lic.current_status()  # must not raise
    assert st.license_id != "dev-override"  # did not fabricate an override status
    assert st.effective_tier == "free"      # genuine unlicensed default


def test_override_ignored_on_shipped_install(isolated_config, monkeypatch):
    """SECURITY: on a shipped wheel (no writer tool, no NAVIG_DEV_LICENSE), a
    hand-written override must NOT bypass the signed license — else any user
    could self-grant Enterprise with one JSON file."""
    lic = isolated_config
    monkeypatch.delenv("NAVIG_DEV_LICENSE", raising=False)
    # Simulate the wheel: _dev_overrides_allowed() sees no dev signal.
    monkeypatch.setattr(lic, "_dev_overrides_allowed", lambda: False)
    _write_override(lic, {"tier": "enterprise"})
    lic._invalidate_cache()
    st = lic.current_status()
    assert st.effective_tier != "enterprise"       # override did NOT take effect
    assert st.license_id != "dev-override"          # fell through to the real path


def test_env_opt_in_allows_override(isolated_config, monkeypatch):
    """An explicit NAVIG_DEV_LICENSE opt-in honors the override even without the
    source tool (for testing a shipped build)."""
    lic = isolated_config
    monkeypatch.setattr(lic, "_dev_overrides_allowed",
                        lambda: lic._coerce_bool(__import__("os").environ.get("NAVIG_DEV_LICENSE"), False))
    monkeypatch.setenv("NAVIG_DEV_LICENSE", "1")
    _write_override(lic, {"tier": "max"})
    lic._invalidate_cache()
    assert lic.current_status().effective_tier == "max"


def test_delete_override_restores_normal_path(isolated_config):
    lic = isolated_config
    _write_override(lic, {"tier": "max"})
    assert lic.current_status().effective_tier == "max"
    lic.dev_override_path().unlink()
    lic._invalidate_cache()
    st = lic.current_status()
    assert st.effective_tier == "free"
    assert st.license_id != "dev-override"
