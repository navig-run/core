"""Tests for template_manager.TemplateSchema, boot_messages, and console_helper.Colors."""
from __future__ import annotations

import pytest


# ──────────────────────────────────────────────────────────────────────────────
# template_manager.TemplateSchema.validate
# ──────────────────────────────────────────────────────────────────────────────
from navig.template_manager import TemplateSchema


class TestTemplateSchemaValidate:
    def _valid(self) -> dict:
        return {
            "name": "my_template",
            "version": "1.0.0",
            "description": "A test template",
            "author": "Tester",
        }

    def test_valid_minimal(self):
        ok, err = TemplateSchema.validate(self._valid())
        assert ok is True
        assert err is None

    def test_missing_name_fails(self):
        d = self._valid()
        del d["name"]
        ok, err = TemplateSchema.validate(d)
        assert ok is False
        assert "name" in err.lower()

    def test_missing_version_fails(self):
        d = self._valid()
        del d["version"]
        ok, err = TemplateSchema.validate(d)
        assert ok is False

    def test_missing_description_fails(self):
        d = self._valid()
        del d["description"]
        ok, err = TemplateSchema.validate(d)
        assert ok is False

    def test_missing_author_fails(self):
        d = self._valid()
        del d["author"]
        ok, err = TemplateSchema.validate(d)
        assert ok is False

    def test_empty_version_fails(self):
        d = self._valid()
        d["version"] = ""
        ok, err = TemplateSchema.validate(d)
        assert ok is False

    def test_non_string_version_fails(self):
        d = self._valid()
        d["version"] = 123
        ok, err = TemplateSchema.validate(d)
        assert ok is False

    def test_dependencies_as_list_ok(self):
        d = self._valid()
        d["dependencies"] = ["dep1", "dep2"]
        ok, err = TemplateSchema.validate(d)
        assert ok is True

    def test_dependencies_as_dict_fails(self):
        d = self._valid()
        d["dependencies"] = {"dep1": "1.0"}
        ok, err = TemplateSchema.validate(d)
        assert ok is False
        assert err is not None

    def test_enabled_as_bool_ok(self):
        d = self._valid()
        d["enabled"] = True
        ok, err = TemplateSchema.validate(d)
        assert ok is True

    def test_enabled_as_string_fails(self):
        d = self._valid()
        d["enabled"] = "yes"
        ok, err = TemplateSchema.validate(d)
        assert ok is False

    def test_required_fields_list(self):
        assert "name" in TemplateSchema.REQUIRED_FIELDS
        assert "version" in TemplateSchema.REQUIRED_FIELDS
        assert "author" in TemplateSchema.REQUIRED_FIELDS


# ──────────────────────────────────────────────────────────────────────────────
# boot_messages.get_boot_message
# ──────────────────────────────────────────────────────────────────────────────
from navig.boot_messages import NAVIG_BOOT_MESSAGES, get_boot_message


class TestNavigBootMessages:
    def test_list_non_empty(self):
        assert len(NAVIG_BOOT_MESSAGES) > 0

    def test_all_strings(self):
        for msg in NAVIG_BOOT_MESSAGES:
            assert isinstance(msg, str) and msg


class TestGetBootMessage:
    def test_returns_string(self):
        assert isinstance(get_boot_message(), str)

    def test_non_empty(self):
        assert get_boot_message() != ""

    def test_no_args_base_message(self):
        # Result should be one of the base messages (possibly with suffix)
        msg = get_boot_message()
        assert any(msg.startswith(base) for base in NAVIG_BOOT_MESSAGES)

    def test_with_location_suffix(self):
        msg = get_boot_message(location="48.8566° N")
        assert "48.8566° N" in msg

    def test_with_uptime_suffix(self):
        msg = get_boot_message(uptime=3600)
        assert "3600s" in msg

    def test_with_signal_strength(self):
        msg = get_boot_message(signal_strength=85)
        assert "85%" in msg

    def test_with_all_extras(self):
        msg = get_boot_message(location="Paris", uptime=100, signal_strength=99)
        assert "Paris" in msg
        assert "100s" in msg
        assert "99%" in msg

    def test_separator_when_extras(self):
        msg = get_boot_message(location="NYC")
        assert " · " in msg

    def test_no_separator_without_extras(self):
        msg = get_boot_message()
        assert " · " not in msg

    def test_deterministic_with_seeded_random(self):
        import random

        random.seed(42)
        msg1 = get_boot_message()
        random.seed(42)
        msg2 = get_boot_message()
        assert msg1 == msg2


# ──────────────────────────────────────────────────────────────────────────────
# console_helper.Colors
# ──────────────────────────────────────────────────────────────────────────────
from navig.console_helper import Colors


class TestColors:
    def test_success_is_green(self):
        assert Colors.SUCCESS == "green"

    def test_error_is_red(self):
        assert Colors.ERROR == "red"

    def test_warning_is_yellow(self):
        assert Colors.WARNING == "yellow"

    def test_info_is_blue(self):
        assert Colors.INFO == "blue"

    def test_prompt_is_cyan(self):
        assert Colors.PROMPT == "cyan"

    def test_all_attributes_are_strings(self):
        attrs = {k: v for k, v in vars(Colors).items() if not k.startswith("_")}
        for name, val in attrs.items():
            assert isinstance(val, str), f"Colors.{name} is not a string"
