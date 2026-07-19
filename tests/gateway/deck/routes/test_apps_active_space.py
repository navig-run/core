"""Regression: the deck's _get_active_space_name() must return the space NAME (a string),
never the `{"active": name}` parent dict.

`navig space switch` (_set_active_space) writes `space.active` (canonical) + `active_space`
(string mirror) and REMOVES the legacy `spaces.active`. The old reader did
`cfg.get("spaces.active") or cfg.get("space")` — the first is the removed legacy key (always
None) and the second returns the whole `{"active": name}` dict. So once a space was active the
function returned a dict: the space badge never matched, and the roadmap fallback
`_get_spaces_dir() / <dict>` raised TypeError (silently swallowed → empty dashboard).
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def isolated_cfg(tmp_path, monkeypatch):
    """Isolated global config; the cached ConfigManager singleton is reset to pick it up."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("NAVIG_HOME", raising=False)  # legacy env would shadow NAVIG_CONFIG_DIR
    from navig.config import reset_config_manager

    reset_config_manager()
    yield tmp_path
    reset_config_manager()


def test_active_space_name_is_the_string_not_the_parent_dict(isolated_cfg):
    from navig.config import get_config_manager
    from navig.gateway.deck.routes.apps import _get_active_space_name

    # Exactly what `navig space switch` persists to config.yaml.
    get_config_manager().set_global("space.active", "myspace")

    result = _get_active_space_name()
    assert result == "myspace"
    assert isinstance(result, str)  # the bug returned {"active": "myspace"} here


def test_active_space_name_falls_back_to_active_space_string_key(isolated_cfg):
    from navig.config import get_config_manager
    from navig.gateway.deck.routes.apps import _get_active_space_name

    # Only the string mirror present (no `space` node) — must still resolve.
    get_config_manager().set_global("active_space", "solo")

    assert _get_active_space_name() == "solo"


def test_active_space_name_is_none_when_unset(isolated_cfg):
    from navig.gateway.deck.routes.apps import _get_active_space_name

    assert _get_active_space_name() is None


def test_active_space_name_reads_cache_file_when_config_empty(isolated_cfg, monkeypatch):
    """The deck now honours the cache file (source of truth) even when the config mirror is
    empty — the gap the old config-only reader missed. `navig space switch` writes the cache
    file FIRST and config best-effort, so a config-less-but-cached state must still resolve."""
    monkeypatch.delenv("NAVIG_SPACE", raising=False)
    from navig.gateway.deck.routes.apps import _get_active_space_name
    from navig.platform.paths import config_dir

    cache = config_dir() / "cache" / "active_space.txt"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("cached-space", encoding="utf-8")

    assert _get_active_space_name() == "cached-space"
