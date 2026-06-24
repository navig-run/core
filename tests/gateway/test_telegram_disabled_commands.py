"""Unit tests for the Telegram disabled-commands feature.

Covers:
- `get_disabled_commands()` reads config, lowercases, strips slashes,
  and defensively filters locked commands.
- `_build_command_list_for_registration()` excludes disabled commands so
  they vanish from Telegram's autocomplete on next setMyCommands call.
- `LOCKED_COMMANDS` matches the set declared in the Deck social route
  (so the two stay in sync).
"""

from __future__ import annotations

import pytest

from navig.gateway.channels.telegram_commands import (
    LOCKED_COMMANDS,
    TelegramCommandsMixin,
    _iter_unique_registry,
    get_disabled_commands,
)

# `_build_command_list_for_registration` was refactored from a module function
# into a @staticmethod on TelegramCommandsMixin (R9 test-debt fix); bind it back
# so the existing call sites below keep working.
_build_command_list_for_registration = TelegramCommandsMixin._build_command_list_for_registration


class _FakeCfg:
    def __init__(self, telegram: dict) -> None:
        self._tg = telegram

    def get(self, key: str):
        if key == "telegram":
            return self._tg
        return None


@pytest.fixture
def patch_cfg(monkeypatch):
    """Inject a fake config manager into navig.config.get_config_manager."""

    def _apply(telegram: dict):
        import navig.config as nc

        monkeypatch.setattr(nc, "get_config_manager", lambda: _FakeCfg(telegram))

    return _apply


def test_get_disabled_commands_empty_when_unset(patch_cfg):
    patch_cfg({})
    assert get_disabled_commands() == set()


def test_get_disabled_commands_normalizes_input(patch_cfg):
    patch_cfg({"disabled_commands": ["/weather", "CRYPTO_LIST", " ip "]})
    assert get_disabled_commands() == {"weather", "crypto_list", "ip"}


def test_get_disabled_commands_filters_locked_defensively(patch_cfg):
    # Even if config was hand-edited to disable a locked command, the
    # runtime must refuse to honor it — otherwise users could lose UI access.
    patch_cfg({"disabled_commands": ["start", "help", "settings", "status", "weather"]})
    assert get_disabled_commands() == {"weather"}


def test_get_disabled_commands_handles_garbage(patch_cfg):
    patch_cfg({"disabled_commands": ["valid", 123, None, "", "  "]})
    assert get_disabled_commands() == {"valid"}


def test_get_disabled_commands_handles_non_list(patch_cfg):
    patch_cfg({"disabled_commands": "not a list"})
    assert get_disabled_commands() == set()


def test_get_disabled_commands_swallows_import_errors(monkeypatch):
    # If the config module is unavailable for any reason, return empty set —
    # never raise during a dispatch.
    import navig.config as nc

    def _boom():
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(nc, "get_config_manager", _boom)
    assert get_disabled_commands() == set()


def test_locked_commands_present():
    assert {"start", "help", "settings", "status"} == set(LOCKED_COMMANDS)


def test_locked_set_matches_social_route():
    """The Deck route must use the same locked set as the dispatch layer."""
    from navig.gateway.deck.routes.social import _TELEGRAM_LOCKED_COMMANDS

    assert set(_TELEGRAM_LOCKED_COMMANDS) == set(LOCKED_COMMANDS)


def test_build_command_list_excludes_disabled(patch_cfg):
    patch_cfg({"disabled_commands": ["weather", "crypto_list"]})
    payload = _build_command_list_for_registration()
    names = {c["command"] for c in payload}
    assert "weather" not in names
    assert "crypto_list" not in names


def test_build_command_list_includes_locked_even_if_disabled(patch_cfg):
    # If a user smuggled a locked command into disabled_commands, the
    # registry must still include it (locked commands are unstoppable).
    patch_cfg({"disabled_commands": ["start", "help"]})
    payload = _build_command_list_for_registration()
    names = {c["command"] for c in payload}
    assert "start" in names
    assert "help" in names


def test_build_command_list_returns_visible_commands(patch_cfg):
    """With no disabled commands, the registration payload equals the visible registry."""
    patch_cfg({})
    payload = _build_command_list_for_registration()
    expected = {e.command for e in _iter_unique_registry(visible_only=True)}
    assert {c["command"] for c in payload} == expected
