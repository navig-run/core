"""
Tests for navig/help_texts.py

Validates CommandHelp / OptionHelp dataclasses, all exported constants, and
the get_group_help() / get_command_help() helper functions.
"""

from __future__ import annotations

import inspect
import re

import pytest

import navig.help_texts as ht
from navig.help_texts import CommandHelp, OptionHelp, get_group_help


# ---------------------------------------------------------------------------
# Dataclass shape
# ---------------------------------------------------------------------------


class TestCommandHelpDataclass:
    def test_is_frozen(self):
        h = CommandHelp(short_help="do something", description="Does something.")
        with pytest.raises((AttributeError, TypeError)):
            h.short_help = "changed"  # type: ignore[misc]

    def test_epilog_defaults_to_none(self):
        h = CommandHelp(short_help="do something", description="Does something.")
        assert h.epilog is None

    def test_epilog_accepts_string(self):
        h = CommandHelp(short_help="do it", description="Does it.", epilog="Example\n  navig x y")
        assert "navig x y" in h.epilog

    def test_short_help_stored(self):
        h = CommandHelp(short_help="list items", description="Lists all items.")
        assert h.short_help == "list items"

    def test_description_stored(self):
        h = CommandHelp(short_help="list items", description="Lists all items.")
        assert h.description == "Lists all items."


class TestOptionHelpDataclass:
    def test_is_frozen(self):
        o = OptionHelp(text="some help")
        with pytest.raises((AttributeError, TypeError)):
            o.text = "changed"  # type: ignore[misc]

    def test_text_stored(self):
        o = OptionHelp(text="override active host for this command")
        assert o.text == "override active host for this command"


# ---------------------------------------------------------------------------
# Enumerate every CommandHelp constant exported by the module
# ---------------------------------------------------------------------------


def _all_command_help_instances() -> list[tuple[str, CommandHelp]]:
    """Return all (name, value) pairs for CommandHelp instances at module level."""
    return [
        (name, obj)
        for name, obj in inspect.getmembers(ht)
        if isinstance(obj, CommandHelp)
    ]


def _all_option_help_instances() -> list[tuple[str, OptionHelp]]:
    return [
        (name, obj)
        for name, obj in inspect.getmembers(ht)
        if isinstance(obj, OptionHelp)
    ]


COMMAND_HELP_IDS = [(name, obj) for name, obj in _all_command_help_instances()]
OPTION_HELP_IDS = [(name, obj) for name, obj in _all_option_help_instances()]


class TestAllCommandHelpInstances:
    """Enforce the module's own standardization rules on every constant."""

    @pytest.mark.parametrize("name,h", COMMAND_HELP_IDS, ids=[n for n, _ in COMMAND_HELP_IDS])
    def test_short_help_is_non_empty_string(self, name: str, h: CommandHelp):
        assert isinstance(h.short_help, str) and len(h.short_help.strip()) > 0, (
            f"{name}: short_help must be non-empty"
        )

    @pytest.mark.parametrize("name,h", COMMAND_HELP_IDS, ids=[n for n, _ in COMMAND_HELP_IDS])
    def test_description_is_non_empty_string(self, name: str, h: CommandHelp):
        assert isinstance(h.description, str) and len(h.description.strip()) > 0, (
            f"{name}: description must be non-empty"
        )

    @pytest.mark.parametrize("name,h", COMMAND_HELP_IDS, ids=[n for n, _ in COMMAND_HELP_IDS])
    def test_description_ends_with_period(self, name: str, h: CommandHelp):
        # Grab just the first sentence/line, strip trailing whitespace
        first_line = h.description.strip().splitlines()[0].rstrip()
        assert first_line.endswith("."), (
            f"{name}: description first line must end with '.' — got {first_line!r}"
        )

    @pytest.mark.parametrize("name,h", COMMAND_HELP_IDS, ids=[n for n, _ in COMMAND_HELP_IDS])
    def test_short_help_does_not_end_with_period(self, name: str, h: CommandHelp):
        assert not h.short_help.rstrip().endswith("."), (
            f"{name}: short_help must not end with a period — got {h.short_help!r}"
        )


class TestAllOptionHelpInstances:
    @pytest.mark.parametrize("name,o", OPTION_HELP_IDS, ids=[n for n, _ in OPTION_HELP_IDS])
    def test_text_is_non_empty(self, name: str, o: OptionHelp):
        assert isinstance(o.text, str) and len(o.text.strip()) > 0, (
            f"{name}: OptionHelp text must not be empty"
        )

    @pytest.mark.parametrize("name,o", OPTION_HELP_IDS, ids=[n for n, _ in OPTION_HELP_IDS])
    def test_text_lowercase_start(self, name: str, o: OptionHelp):
        # Rule: option help text should start lowercase (after stripping)
        first_char = o.text.strip()[0]
        assert first_char.islower(), (
            f"{name}: OptionHelp text should start lowercase — got {o.text!r}"
        )


# ---------------------------------------------------------------------------
# Spot-check a few known constants
# ---------------------------------------------------------------------------


class TestKnownConstants:
    def test_host_short_help(self):
        assert "host" in ht.HOST.short_help.lower()

    def test_host_add_short_help(self):
        assert ht.HOST_ADD.short_help == "add new host interactively"

    def test_host_list_description_complete_sentence(self):
        assert ht.HOST_LIST.description.endswith(".")

    def test_run_has_epilog(self):
        assert ht.RUN.epilog is not None and "--b64" in ht.RUN.epilog

    def test_db_short_help_starts_uppercase(self):
        # Group descriptions start with Verb (uppercase)
        assert ht.DB.short_help[0].isupper()

    def test_opt_yes_text(self):
        assert "confirmation" in ht.OPT_YES.text

    def test_opt_dry_run_text(self):
        assert "what would be done" in ht.OPT_DRY_RUN.text

    def test_opt_json_text(self):
        assert "JSON" in ht.OPT_JSON.text or "json" in ht.OPT_JSON.text.lower()

    def test_docker_ps_description_ends_period(self):
        assert ht.DOCKER_PS.description.endswith(".")

    def test_file_add_description_mentions_upload_or_create(self):
        combined = (ht.FILE_ADD.description + ht.FILE_ADD.short_help).lower()
        assert "upload" in combined or "create" in combined


# ---------------------------------------------------------------------------
# get_group_help()
# ---------------------------------------------------------------------------


class TestGetGroupHelp:
    def test_returns_dict(self):
        result = get_group_help("host")
        assert isinstance(result, dict)

    def test_host_group_has_short_help(self):
        result = get_group_help("host")
        assert "short_help" in result
        assert isinstance(result["short_help"], str)

    def test_host_group_has_description(self):
        result = get_group_help("host")
        assert "desc" in result

    def test_known_group_names(self):
        known = ["host", "tunnel", "app", "docker", "db", "web", "file", "backup"]
        for name in known:
            result = get_group_help(name)
            assert result.get("short_help"), f"group '{name}' should have short_help"

    def test_unknown_group_returns_none(self):
        result = get_group_help("__nonexistent__")
        # Returns None for unknown groups — must not raise
        assert result is None


# ---------------------------------------------------------------------------
# get_command_help() — optional; present in some versions of the module
# ---------------------------------------------------------------------------


class TestGetCommandHelp:
    def test_function_present_or_skip(self):
        if not hasattr(ht, "get_command_help"):
            pytest.skip("get_command_help not in this module version")

    def test_returns_dict_for_known(self):
        if not hasattr(ht, "get_command_help"):
            pytest.skip("get_command_help not available")
        result = ht.get_command_help("host", "list")
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Volume sanity checks
# ---------------------------------------------------------------------------


class TestModuleVolume:
    def test_at_least_30_command_help_constants(self):
        count = len(_all_command_help_instances())
        assert count >= 30, f"Expected at least 30 CommandHelp constants, found {count}"

    def test_at_least_5_option_help_constants(self):
        count = len(_all_option_help_instances())
        assert count >= 5, f"Expected at least 5 OptionHelp constants, found {count}"
