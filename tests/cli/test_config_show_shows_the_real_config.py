"""`navig config show global` must print the config the operator actually has.

Measured against one real install, 2026-09-04:

    keys in config.yaml                 163
    keys printed by `config show`       126
    keys DROPPED                        114

Whole subtrees were missing -- `adapters` (which holds Twilio account_sid and
auth_token), `cloud`, `user`, `plugins`, `missions`, `llm_router`, `apps` --
while schema defaults the operator never set were displayed as though configured.

Cause: the command read `ConfigManager._load_global_config()`, which returns the
PYDANTIC-VALIDATED view (`validate_global_config(...).model_dump()`) and therefore
keeps only fields the schema declares.

Why this matters more than a cosmetic diff: `config show` is what someone runs to
work out why a setting is not taking effect. Showing a filtered copy sends them
looking in the wrong place -- and it is the same reader that left the CLI unable
to find `gateway.auth.token`, so all seven `navig cron` commands answered 401.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from navig.commands import config as config_cmd

# The two views of ONE file, in the shape observed on the real install.
_REAL_FILE = {
    "adapters": {"sms": {"twilio": {"account_sid": "AC-real"}}},
    "cloud": {"mode": "lighthouse"},
    "user": {"name": "operator"},
    "gateway": {"auth": {"token": "tok"}, "mesh_token": "mesh"},
}
_SCHEMA_VIEW = {
    # `adapters` / `cloud` / `user` are simply absent: the schema does not declare
    # them. `gateway` survives, but only its declared fields -- auth is gone.
    "gateway": {"enabled": True, "host": "127.0.0.1", "port": 8789, "require_auth": True},
}


@pytest.fixture
def _printed(monkeypatch: pytest.MonkeyPatch) -> list:
    captured: list = []
    monkeypatch.setattr(
        config_cmd, "get_config_manager",
        lambda: SimpleNamespace(
            get_global_config=lambda: _REAL_FILE,
            _load_global_config=lambda *a, **k: _SCHEMA_VIEW,
        ),
    )
    monkeypatch.setattr(config_cmd.ch, "print_json", captured.append)
    return captured


def test_show_prints_the_operators_own_keys(_printed) -> None:
    config_cmd.config_show_cmd("global")

    assert _printed, "config show printed nothing"
    shown = _printed[0]
    for subtree in ("adapters", "cloud", "user"):
        assert subtree in shown, (
            f"`config show` hid the whole '{subtree}' subtree, which IS in config.yaml -- "
            "the operator is being shown a config they do not have"
        )
    assert shown["adapters"]["sms"]["twilio"]["account_sid"] == "AC-real"
    assert shown["gateway"].get("auth", {}).get("token") == "tok", (
        "the schema view drops gateway.auth entirely; that is what broke CLI auth"
    )


def test_the_schema_view_really_is_lossy() -> None:
    """Discriminator: pins WHY the old reader was wrong, so the fix can't be undone.

    Without this, the fixture above looks like an arbitrary stub rather than a
    reproduction of a measured defect.
    """
    for subtree in ("adapters", "cloud", "user"):
        assert subtree in _REAL_FILE
        assert subtree not in _SCHEMA_VIEW, "fixture no longer reproduces the loss"
    assert "auth" in _REAL_FILE["gateway"]
    assert "auth" not in _SCHEMA_VIEW["gateway"]


def test_an_empty_config_still_prints_an_object(monkeypatch: pytest.MonkeyPatch) -> None:
    """A brand-new install must render `{}`, never crash on None."""
    captured: list = []
    monkeypatch.setattr(
        config_cmd, "get_config_manager",
        lambda: SimpleNamespace(get_global_config=lambda: None),
    )
    monkeypatch.setattr(config_cmd.ch, "print_json", captured.append)
    config_cmd.config_show_cmd("global")
    assert captured == [{}]
