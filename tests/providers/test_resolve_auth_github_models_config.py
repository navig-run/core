"""``resolve_auth`` reads the same ``github_models.token`` every dispatcher does.

Five runtime readers honour ``github_models: {token: …}`` in config.yaml — the
path the provider's own error message tells users to set — but the key store
behind ``navig ai providers`` / ``--test`` / the fallback client factory did not.
A token that worked for every chat showed as ``✗ not set`` and failed ``--test``.
"""

from __future__ import annotations

import pytest

from navig.providers import auth as auth_mod
from navig.providers.auth import AuthProfileManager


@pytest.fixture
def bare_manager(tmp_path, monkeypatch):
    for var in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_MODELS_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    class _NoVault:
        def get_api_key(self, provider):
            return None

    import navig.vault as vault_pkg

    monkeypatch.setattr(vault_pkg, "get_vault", lambda: _NoVault())
    return AuthProfileManager(config_dir=tmp_path)


def _config(monkeypatch, data: dict) -> None:
    import navig.config as cfg_mod

    class _CM:
        global_config = data

    monkeypatch.setattr(cfg_mod, "get_config_manager", lambda: _CM())


def test_config_token_is_found_and_named(bare_manager, monkeypatch):
    _config(monkeypatch, {"github_models": {"token": "  ghp_from_config  "}})
    key, source = bare_manager.resolve_auth("github_models")
    assert key == "ghp_from_config"
    assert source == "config:github_models.token"


def test_env_and_vault_still_win_over_config(bare_manager, monkeypatch):
    _config(monkeypatch, {"github_models": {"token": "ghp_from_config"}})
    monkeypatch.setenv("GH_TOKEN", "ghp_from_env")
    key, source = bare_manager.resolve_auth("github_models")
    assert (key, source) == ("ghp_from_env", "env:GH_TOKEN")


def test_other_providers_do_not_grow_a_config_convention(bare_manager, monkeypatch):
    """Only github_models documents a config.yaml token; nothing else should
    start reading ``<provider>.token`` because this seam exists."""
    _config(monkeypatch, {"openai": {"token": "sk-should-be-ignored"},
                          "github_models": {"token": "ghp_x"}})
    assert bare_manager.resolve_auth("openai") == (None, "not_found")


def test_empty_or_broken_config_is_not_found(bare_manager, monkeypatch):
    _config(monkeypatch, {"github_models": {"token": "   "}})
    assert bare_manager.resolve_auth("github_models") == (None, "not_found")

    def _boom():
        raise RuntimeError("config unreadable")

    import navig.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "get_config_manager", _boom)
    assert bare_manager.resolve_auth("github_models") == (None, "not_found")


def test_helper_is_the_one_reader(monkeypatch):
    """Teeth: the branch in resolve_auth goes through the helper, so a later
    edit that drops the helper cannot keep the branch alive by accident."""
    monkeypatch.setattr(auth_mod, "_github_models_config_token", lambda: "ghp_helper")
    for var in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_MODELS_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    import navig.vault as vault_pkg

    class _NoVault:
        def get_api_key(self, provider):
            return None

    monkeypatch.setattr(vault_pkg, "get_vault", lambda: _NoVault())
    import tempfile
    from pathlib import Path

    m = AuthProfileManager(config_dir=Path(tempfile.mkdtemp()))
    assert m.resolve_auth("github_models") == ("ghp_helper", "config:github_models.token")
