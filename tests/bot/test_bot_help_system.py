"""Pure-logic unit tests for navig.bot.help_system — no I/O, no network."""

from __future__ import annotations

import pytest

from navig.bot.help_system import (
    CATEGORIES,
    CommandInfo,
    format_category_help,
    format_command_help,
    format_main_help,
    get_all_commands,
    get_command,
    get_commands_by_category,
    search_commands,
)

# ---------------------------------------------------------------------------
# CATEGORIES constant
# ---------------------------------------------------------------------------


class TestCategories:
    def test_is_dict(self):
        assert isinstance(CATEGORIES, dict)

    def test_has_seven_entries(self):
        assert len(CATEGORIES) == 7

    def test_expected_keys_present(self):
        expected = {"core", "hosts", "monitoring", "docker", "database", "tools", "utilities"}
        assert set(CATEGORIES.keys()) == expected

    def test_each_value_is_dict(self):
        for v in CATEGORIES.values():
            assert isinstance(v, dict)

    def test_each_has_name(self):
        for key, v in CATEGORIES.items():
            assert "name" in v, f"category '{key}' missing 'name'"
            assert isinstance(v["name"], str)
            assert v["name"]

    def test_each_has_emoji(self):
        for key, v in CATEGORIES.items():
            assert "emoji" in v, f"category '{key}' missing 'emoji'"
            assert isinstance(v["emoji"], str)

    def test_each_has_description(self):
        for key, v in CATEGORIES.items():
            assert "description" in v, f"category '{key}' missing 'description'"
            assert isinstance(v["description"], str)
            assert v["description"]

    def test_core_category_exists(self):
        assert "core" in CATEGORIES

    def test_docker_category_exists(self):
        assert "docker" in CATEGORIES

    def test_database_category_exists(self):
        assert "database" in CATEGORIES


# ---------------------------------------------------------------------------
# CommandInfo dataclass
# ---------------------------------------------------------------------------

_MINIMAL_CMD = dict(
    name="start",
    short_desc="Start the bot",
    description="Sends a welcome message.",
    syntax="/start",
    examples=[],
    category="core",
)


class TestCommandInfoDataclass:
    def test_instantiation(self):
        cmd = CommandInfo(**_MINIMAL_CMD)
        assert cmd.name == "start"

    def test_permissions_default(self):
        cmd = CommandInfo(**_MINIMAL_CMD)
        assert cmd.permissions == "Everyone"

    def test_aliases_default_empty(self):
        cmd = CommandInfo(**_MINIMAL_CMD)
        assert cmd.aliases == []

    def test_related_default_empty(self):
        cmd = CommandInfo(**_MINIMAL_CMD)
        assert cmd.related == []

    def test_examples_stored(self):
        cmd = CommandInfo(**{**_MINIMAL_CMD, "examples": ["/start", "/start@bot"]})
        assert len(cmd.examples) == 2

    def test_custom_permissions(self):
        cmd = CommandInfo(**{**_MINIMAL_CMD, "permissions": "Admin"})
        assert cmd.permissions == "Admin"

    def test_category_stored(self):
        cmd = CommandInfo(**_MINIMAL_CMD)
        assert cmd.category == "core"


# ---------------------------------------------------------------------------
# get_all_commands()
# ---------------------------------------------------------------------------


class TestGetAllCommands:
    def test_returns_dict(self):
        result = get_all_commands()
        assert isinstance(result, dict)

    def test_non_empty(self):
        result = get_all_commands()
        assert len(result) > 0

    def test_values_are_command_info(self):
        for v in get_all_commands().values():
            assert isinstance(v, CommandInfo)

    def test_start_command_present(self):
        commands = get_all_commands()
        assert "start" in commands

    def test_keys_are_strings(self):
        for k in get_all_commands().keys():
            assert isinstance(k, str)

    def test_each_command_has_name(self):
        for cmd in get_all_commands().values():
            assert isinstance(cmd.name, str)
            assert cmd.name

    def test_each_command_has_category(self):
        for cmd in get_all_commands().values():
            assert isinstance(cmd.category, str)
            assert cmd.category

    def test_each_command_has_syntax(self):
        for cmd in get_all_commands().values():
            assert isinstance(cmd.syntax, str)


# ---------------------------------------------------------------------------
# get_commands_by_category()
# ---------------------------------------------------------------------------


class TestGetCommandsByCategory:
    def test_returns_dict(self):
        result = get_commands_by_category()
        assert isinstance(result, dict)

    def test_non_empty(self):
        result = get_commands_by_category()
        assert len(result) > 0

    def test_core_group_present(self):
        grouped = get_commands_by_category()
        assert "core" in grouped

    def test_each_group_has_info_key(self):
        for cat_id, cat in get_commands_by_category().items():
            assert "info" in cat, f"category '{cat_id}' missing 'info'"

    def test_each_group_has_commands_key(self):
        for cat_id, cat in get_commands_by_category().items():
            assert "commands" in cat, f"category '{cat_id}' missing 'commands'"

    def test_commands_key_is_dict(self):
        for cat in get_commands_by_category().values():
            assert isinstance(cat["commands"], dict)

    def test_info_key_is_dict(self):
        for cat in get_commands_by_category().values():
            assert isinstance(cat["info"], dict)

    def test_core_group_has_commands(self):
        grouped = get_commands_by_category()
        assert len(grouped["core"]["commands"]) > 0


# ---------------------------------------------------------------------------
# get_command(command_id)
# ---------------------------------------------------------------------------


class TestGetCommand:
    def test_known_command_returns_command_info(self):
        result = get_command("start")
        assert isinstance(result, CommandInfo)

    def test_unknown_command_returns_none(self):
        result = get_command("totally_nonexistent_42xyz")
        assert result is None

    def test_return_type_for_known(self):
        result = get_command("start")
        assert result is not None

    def test_name_field_matches(self):
        result = get_command("start")
        assert result is not None
        # name field holds the stored name (may include slash)
        assert "start" in result.name.lower()

    def test_none_for_empty_string(self):
        result = get_command("")
        assert result is None


# ---------------------------------------------------------------------------
# search_commands(query)
# ---------------------------------------------------------------------------


class TestSearchCommands:
    def test_returns_list(self):
        result = search_commands("start")
        assert isinstance(result, list)

    def test_known_keyword_returns_results(self):
        results = search_commands("start")
        assert len(results) > 0

    def test_results_are_command_info(self):
        for cmd in search_commands("start"):
            assert isinstance(cmd, CommandInfo)

    def test_unknown_keyword_returns_empty_or_list(self):
        result = search_commands("zzznomatchxyz99999")
        assert isinstance(result, list)
        assert len(result) == 0

    def test_case_insensitive_match(self):
        lower = search_commands("docker")
        upper = search_commands("DOCKER")
        assert len(lower) == len(upper)

    def test_partial_match(self):
        results = search_commands("host")
        assert len(results) > 0

    def test_empty_query_returns_list(self):
        result = search_commands("")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# format_command_help(cmd, detailed)
# ---------------------------------------------------------------------------


class TestFormatCommandHelp:
    def _get_start(self) -> CommandInfo:
        cmd = get_command("start")
        assert cmd is not None
        return cmd

    def test_returns_str(self):
        cmd = self._get_start()
        result = format_command_help(cmd)
        assert isinstance(result, str)

    def test_contains_command_name(self):
        cmd = self._get_start()
        result = format_command_help(cmd)
        assert "start" in result.lower()

    def test_contains_short_desc(self):
        cmd = self._get_start()
        result = format_command_help(cmd)
        assert cmd.short_desc in result

    def test_brief_mode_is_default(self):
        cmd = self._get_start()
        brief = format_command_help(cmd)
        detailed = format_command_help(cmd, detailed=True)
        # Detailed should be at least as long as brief
        assert len(detailed) >= len(brief)

    def test_detailed_includes_syntax(self):
        cmd = self._get_start()
        result = format_command_help(cmd, detailed=True)
        assert cmd.syntax in result

    def test_detailed_includes_description(self):
        cmd = self._get_start()
        result = format_command_help(cmd, detailed=True)
        assert cmd.description in result

    def test_cmd_with_examples_detailed(self):
        # Build a synthetic command with examples
        cmd = CommandInfo(
            name="test",
            short_desc="Test cmd",
            description="Test description.",
            syntax="/test [arg]",
            examples=["/test foo", "/test bar"],
            category="core",
        )
        result = format_command_help(cmd, detailed=True)
        assert "/test foo" in result

    def test_cmd_with_related_detailed(self):
        cmd = CommandInfo(
            name="test2",
            short_desc="Test cmd 2",
            description="Another test.",
            syntax="/test2",
            examples=[],
            category="core",
            related=["help", "start"],
        )
        result = format_command_help(cmd, detailed=True)
        assert "help" in result


# ---------------------------------------------------------------------------
# format_category_help(category_id)
# ---------------------------------------------------------------------------


class TestFormatCategoryHelp:
    def test_known_category_returns_str(self):
        result = format_category_help("core")
        assert isinstance(result, str)

    def test_unknown_category_returns_error_message(self):
        result = format_category_help("nonexistent_category")
        assert "Unknown category" in result
        assert "nonexistent_category" in result

    def test_known_category_contains_name(self):
        result = format_category_help("core")
        info = CATEGORIES["core"]
        assert info["name"] in result

    def test_known_category_contains_commands_section(self):
        result = format_category_help("core")
        assert "Commands" in result

    def test_docker_category(self):
        result = format_category_help("docker")
        assert isinstance(result, str)
        assert "Unknown category" not in result


# ---------------------------------------------------------------------------
# format_main_help()
# ---------------------------------------------------------------------------


class TestFormatMainHelp:
    def test_returns_str(self):
        result = format_main_help()
        assert isinstance(result, str)

    def test_contains_navig(self):
        result = format_main_help()
        assert "NAVIG" in result

    def test_contains_categories_header(self):
        result = format_main_help()
        assert "Categories" in result or "categories" in result.lower()

    def test_mentions_core_category(self):
        result = format_main_help()
        assert "core" in result.lower() or CATEGORIES["core"]["name"] in result

    def test_length_reasonable(self):
        result = format_main_help()
        # Should be more than a trivial string
        assert len(result) > 100
