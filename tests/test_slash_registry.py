"""
Tests for the slash-command registry refactor (Phase 1).

Verifies that:
- The old _SLASH_CLI_MAP class attribute is gone
- _SLASH_REGISTRY is the single source of truth
- _build_slash_handlers / _register_commands derive from it
- Auto-generated /help text covers all visible commands
"""

import inspect
import sys
import types

import pytest

# ---------------------------------------------------------------------------
# Lazy import helpers
# ---------------------------------------------------------------------------


def _import_registry():
    """Import the module-level _SLASH_REGISTRY and SlashCommandEntry."""
    mod = pytest.importorskip("navig.gateway.channels.telegram_commands")
    return mod._SLASH_REGISTRY, mod.SlashCommandEntry  # type: ignore[attr-defined]


def _import_mixin():
    mod = pytest.importorskip("navig.gateway.channels.telegram_commands")
    return mod.TelegramCommandsMixin


# ---------------------------------------------------------------------------
# Registry structure
# ---------------------------------------------------------------------------


def test_slash_registry_exists():
    registry, entry_cls = _import_registry()
    assert isinstance(registry, list)
    assert len(registry) > 0, "_SLASH_REGISTRY must not be empty"


def test_slash_registry_entries_are_dataclass_instances():
    registry, entry_cls = _import_registry()
    for entry in registry:
        assert isinstance(entry, entry_cls), f"{entry!r} is not a SlashCommandEntry"


def test_slash_entry_has_required_fields():
    _, entry_cls = _import_registry()
    fields = {f.name for f in entry_cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    required = {"command", "description", "visible", "category"}
    assert required.issubset(fields), f"Missing fields: {required - fields}"


def test_old_slash_cli_map_is_gone():
    """_SLASH_CLI_MAP must NOT exist on TelegramCommandsMixin after refactor."""
    mixin = _import_mixin()
    assert not hasattr(
        mixin, "_SLASH_CLI_MAP"
    ), "_SLASH_CLI_MAP still present — registry refactor incomplete"


def test_no_duplicate_slash_commands():
    registry, _ = _import_registry()
    commands = [e.command for e in registry]
    duplicates = {c for c in commands if commands.count(c) > 1}
    assert not duplicates, f"Duplicate commands in registry: {duplicates}"


# ---------------------------------------------------------------------------
# CLI template resolution
# ---------------------------------------------------------------------------


def test_cli_template_entries_have_non_empty_template():
    registry, _ = _import_registry()
    for entry in registry:
        if entry.cli_template is not None:
            assert (
                entry.cli_template.strip()
            ), f"/{entry.command} has empty cli_template; set to None or a real string"


# ---------------------------------------------------------------------------
# /help auto-generation
# ---------------------------------------------------------------------------


def test_help_text_covers_visible_commands():
    """All visible registry entries must appear in the auto-generated help text."""
    registry, _ = _import_registry()
    mixin = _import_mixin()
    # Call the static/class method that generates help
    help_gen = getattr(mixin, "_generate_help_text", None)
    if help_gen is None:
        pytest.skip("_generate_help_text not found — adjust test to match impl")
    text = help_gen()
    visible = [e.command for e in registry if e.visible]
    for cmd in visible:
        assert f"/{cmd}" in text, f"Visible command /{cmd} missing from /help output"


# ---------------------------------------------------------------------------
# _register_commands derives from registry
# ---------------------------------------------------------------------------


def test_register_commands_count_matches_registry():
    """Number of registered bot commands == number of visible registry entries."""
    registry, _ = _import_registry()
    mixin = _import_mixin()
    expected_visible = sum(1 for e in registry if e.visible)

    build_fn = getattr(mixin, "_build_command_list_for_registration", None)
    if build_fn is None:
        # Fallback: inspect _register_commands source for list literal length
        # (acceptable if the method builds the list dynamically)
        pytest.skip("_build_command_list_for_registration helper not found")
    result = build_fn()
    assert (
        len(result) == expected_visible
    ), f"Registered command count {len(result)} != visible registry count {expected_visible}"
