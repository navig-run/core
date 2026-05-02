"""
Unit tests for pure-data modules:
  - navig/cli/_quotes.py  (HACKER_QUOTES)
  - navig/cli/help_dictionaries.py  (HELP_REGISTRY)
"""

from __future__ import annotations

from navig.cli._quotes import HACKER_QUOTES
from navig.cli.help_dictionaries import HELP_REGISTRY

# ---------------------------------------------------------------------------
# HACKER_QUOTES
# ---------------------------------------------------------------------------


class TestHackerQuotes:
    def test_is_list(self):
        assert isinstance(HACKER_QUOTES, list)

    def test_non_empty(self):
        assert len(HACKER_QUOTES) > 0

    def test_each_entry_is_tuple(self):
        for entry in HACKER_QUOTES:
            assert isinstance(entry, tuple), f"expected tuple, got {type(entry)}"

    def test_each_tuple_has_two_elements(self):
        for entry in HACKER_QUOTES:
            assert len(entry) == 2, f"expected 2 elements, got {len(entry)}"

    def test_first_element_is_string(self):
        for quote, _ in HACKER_QUOTES:
            assert isinstance(quote, str)

    def test_second_element_is_string(self):
        for _, attribution in HACKER_QUOTES:
            assert isinstance(attribution, str)

    def test_no_empty_quotes(self):
        for quote, _ in HACKER_QUOTES:
            assert quote.strip(), "Empty quote text found"

    def test_no_empty_attributions(self):
        for _, attribution in HACKER_QUOTES:
            assert attribution.strip(), "Empty attribution found"

    def test_at_least_ten_quotes(self):
        assert len(HACKER_QUOTES) >= 10

    def test_known_quote_present(self):
        all_quotes = [q for q, _ in HACKER_QUOTES]
        assert any(
            "code" in q.lower() or "software" in q.lower() or "debug" in q.lower()
            for q in all_quotes
        )

    def test_unique_quotes(self):
        all_quotes = [q for q, _ in HACKER_QUOTES]
        assert len(all_quotes) == len(set(all_quotes)), "Duplicate quotes found"


# ---------------------------------------------------------------------------
# HELP_REGISTRY
# ---------------------------------------------------------------------------


class TestHelpRegistry:
    def test_is_dict(self):
        assert isinstance(HELP_REGISTRY, dict)

    def test_non_empty(self):
        assert len(HELP_REGISTRY) > 0

    def test_keys_are_strings(self):
        for key in HELP_REGISTRY:
            assert isinstance(key, str), f"Non-string key: {key!r}"

    def test_values_are_dicts(self):
        for key, val in HELP_REGISTRY.items():
            assert isinstance(val, dict), f"Value for {key!r} is not dict"

    def test_each_entry_has_desc(self):
        for key, val in HELP_REGISTRY.items():
            assert "desc" in val, f"Missing 'desc' in HELP_REGISTRY[{key!r}]"

    def test_desc_is_non_empty_string(self):
        for key, val in HELP_REGISTRY.items():
            assert isinstance(val["desc"], str), f"desc not str for {key!r}"
            assert val["desc"].strip(), f"Empty desc for {key!r}"

    def test_commands_field_when_present_is_dict(self):
        for key, val in HELP_REGISTRY.items():
            if "commands" in val:
                assert isinstance(val["commands"], dict), f"commands not dict for {key!r}"

    def test_command_values_are_strings(self):
        for key, val in HELP_REGISTRY.items():
            cmds = val.get("commands", {})
            for cmd_name, cmd_desc in cmds.items():
                assert isinstance(cmd_desc, str), (
                    f"Command description not str: HELP_REGISTRY[{key!r}][commands][{cmd_name!r}]"
                )

    def test_host_entry_exists(self):
        assert "host" in HELP_REGISTRY

    def test_host_has_list_command(self):
        cmds = HELP_REGISTRY.get("host", {}).get("commands", {})
        assert "list" in cmds

    def test_at_least_five_top_level_entries(self):
        assert len(HELP_REGISTRY) >= 5

    def test_all_command_names_are_strings(self):
        for key, val in HELP_REGISTRY.items():
            cmds = val.get("commands", {})
            for cmd_name in cmds:
                assert isinstance(cmd_name, str), (
                    f"Command name not str: HELP_REGISTRY[{key!r}][commands][{cmd_name!r}]"
                )
