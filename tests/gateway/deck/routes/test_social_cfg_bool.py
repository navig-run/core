"""The Social deck routes read adapter/matrix `enabled` flags through `_cfg_bool`, which
coerces the raw config value. `navig config set adapters.<net>.enabled false` (or
`comms.matrix.enabled false`) stores the STRING "false" — truthy — so the previous
`bool(_cfg_get(...))` reported a disabled adapter/matrix as ENABLED. coerce_bool fixes it
while still accepting the real bools the deck writes via _cfg_set_path.
"""

from __future__ import annotations

from types import SimpleNamespace

from navig.gateway.deck.routes.social import _cfg_bool


def _cfg(global_config: dict):
    return SimpleNamespace(global_config=global_config)


def test_config_set_string_false_is_false():
    cfg = _cfg({"adapters": {"discord": {"enabled": "false"}}})
    assert _cfg_bool(cfg, "adapters.discord.enabled") is False  # not the truthy string


def test_string_true_and_real_bools():
    assert _cfg_bool(_cfg({"matrix": {"enabled": "true"}}), "matrix.enabled") is True
    assert _cfg_bool(_cfg({"matrix": {"enabled": True}}), "matrix.enabled") is True
    assert _cfg_bool(_cfg({"matrix": {"enabled": False}}), "matrix.enabled") is False


def test_missing_key_uses_default():
    cfg = _cfg({})
    assert _cfg_bool(cfg, "adapters.reddit.enabled") is False          # default False
    assert _cfg_bool(cfg, "adapters.reddit.enabled", default=True) is True


def test_none_cfg_uses_default():
    assert _cfg_bool(None, "matrix.enabled") is False
    assert _cfg_bool(None, "matrix.enabled", default=True) is True
