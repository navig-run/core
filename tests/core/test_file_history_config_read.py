"""Regression: file_history.enabled must be READ from config, and INTERPRETED correctly.

Two bugs, in sequence:

1. `_is_enabled()` called `ConfigManager.get` (no such method) inside a bare
   `except Exception: return False`, so it was permanently False regardless of config.
2. The fix read the value but did `bool(...)` on it. `navig config set
   file_history.enabled false` stores the raw STRING "false", and `bool("false")` is
   True — so the documented way to turn file history OFF left it running, still
   snapshotting every changed file. And `Config()` served the snapshot loaded at
   process start, so a toggle needed a daemon restart.

These tests drive a REAL config file rather than stubbing the getter. The original
version monkeypatched `Config.get` to return a real `True`, which proves the value is
read but structurally cannot see it being misinterpreted — the value never travels
through the code that decides what it means.
"""

from __future__ import annotations

import pytest

from navig.file_history import get_file_history_store


@pytest.fixture
def config_value(tmp_path, monkeypatch):
    """Write a real `file_history.enabled` into an isolated config dir.

    The process-wide ConfigManager/ConfigSingleton caches are reset so a manager built
    by an earlier test cannot leak a different config dir in.
    """
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))

    from navig import config as config_mod
    from navig.core import shared_config

    def _write(literal: str) -> None:
        config_mod.reset_config_manager()
        monkeypatch.setattr(shared_config.ConfigSingleton, "_instance", None, raising=False)
        (tmp_path / "config.yaml").write_text(
            f"file_history:\n  enabled: {literal}\n", encoding="utf-8"
        )

    yield _write
    config_mod.reset_config_manager()
    monkeypatch.setattr(shared_config.ConfigSingleton, "_instance", None, raising=False)


def test_file_history_enabled_reflects_config(config_value) -> None:
    """The original regression: a real `true` in config must actually enable it."""
    config_value("true")
    assert get_file_history_store()._is_enabled() is True


@pytest.mark.parametrize("literal", ["'false'", "'off'", "'no'", "'0'", "false"])
def test_disabling_it_actually_disables_it(config_value, literal) -> None:
    """THE REGRESSION: `navig config set … false` stores a STRING; bool('false') is True."""
    config_value(literal)
    assert get_file_history_store()._is_enabled() is False, (
        f"config value {literal} left file history ENABLED — a kill switch that cannot "
        "be switched off"
    )


@pytest.mark.parametrize("literal", ["'true'", "'on'", "'yes'", "'1'", "'ON'", "true"])
def test_enabling_it_works_in_every_documented_form(config_value, literal) -> None:
    config_value(literal)
    assert get_file_history_store()._is_enabled() is True, f"config value {literal} did not enable it"


def test_unset_defaults_to_off(tmp_path, monkeypatch) -> None:
    """Absent config must not enable a feature that writes snapshots to disk."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    from navig import config as config_mod
    from navig.core import shared_config

    config_mod.reset_config_manager()
    monkeypatch.setattr(shared_config.ConfigSingleton, "_instance", None, raising=False)
    (tmp_path / "config.yaml").write_text("{}\n", encoding="utf-8")

    assert get_file_history_store()._is_enabled() is False


def test_a_toggle_is_picked_up_without_restarting_the_process(config_value) -> None:
    """`Config()` served a boot-time snapshot, so a toggle needed a daemon restart."""
    config_value("'false'")
    store = get_file_history_store()
    assert store._is_enabled() is False

    # Operator runs `navig config set file_history.enabled true` — a DIFFERENT process
    # writing the same file while this one keeps running. No cache reset here: that is
    # the whole point.
    from navig.platform.paths import config_dir

    (config_dir() / "config.yaml").write_text(
        "file_history:\n  enabled: 'true'\n", encoding="utf-8"
    )

    assert store._is_enabled() is True, (
        "a config change made while the process runs was not picked up — the read is "
        "serving a boot-time snapshot"
    )
