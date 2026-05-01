"""
Batch 81: hermetic unit tests for
  - navig/boot_messages.py  (NAVIG_BOOT_MESSAGES, get_boot_message)
  - navig/help_texts.py     (GroupHelp dataclass, OptionHelp, get_group_help,
                              OPT_* constants)
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# navig/boot_messages.py
# ---------------------------------------------------------------------------

class TestNavgBootMessages:
    def test_list_not_empty(self) -> None:
        from navig.boot_messages import NAVIG_BOOT_MESSAGES
        assert len(NAVIG_BOOT_MESSAGES) > 0

    def test_all_messages_are_strings(self) -> None:
        from navig.boot_messages import NAVIG_BOOT_MESSAGES
        for msg in NAVIG_BOOT_MESSAGES:
            assert isinstance(msg, str) and len(msg) > 0


class TestGetBootMessage:
    def test_returns_string(self) -> None:
        from navig.boot_messages import get_boot_message
        assert isinstance(get_boot_message(), str)

    def test_no_args_is_nonempty(self) -> None:
        from navig.boot_messages import get_boot_message
        assert len(get_boot_message()) > 0

    def test_message_is_one_of_variants(self) -> None:
        from navig.boot_messages import get_boot_message, NAVIG_BOOT_MESSAGES
        msg = get_boot_message()
        # The message starts with one of the variants
        assert any(msg.startswith(variant) for variant in NAVIG_BOOT_MESSAGES)

    def test_with_location_appends_position(self) -> None:
        from navig.boot_messages import get_boot_message
        msg = get_boot_message(location="48.8566N")
        assert "48.8566N" in msg

    def test_with_uptime_appends_session(self) -> None:
        from navig.boot_messages import get_boot_message
        msg = get_boot_message(uptime=3600)
        assert "3600" in msg

    def test_with_signal_strength_appends_signal(self) -> None:
        from navig.boot_messages import get_boot_message
        msg = get_boot_message(signal_strength=85)
        assert "85%" in msg

    def test_all_extras_combined(self) -> None:
        from navig.boot_messages import get_boot_message
        msg = get_boot_message(location="NYC", uptime=120, signal_strength=99)
        assert "NYC" in msg
        assert "120" in msg
        assert "99%" in msg

    def test_no_extras_no_separator(self) -> None:
        from navig.boot_messages import get_boot_message
        msg = get_boot_message()
        # No extras so separator should not appear
        assert " · " not in msg

    def test_with_extras_has_separator(self) -> None:
        from navig.boot_messages import get_boot_message
        msg = get_boot_message(uptime=0)
        assert " · " in msg

    def test_uptime_zero_is_included(self) -> None:
        from navig.boot_messages import get_boot_message
        msg = get_boot_message(uptime=0)
        assert "0s" in msg


# ---------------------------------------------------------------------------
# navig/help_texts.py
# ---------------------------------------------------------------------------

class TestOptionHelp:
    def test_opt_host_is_string(self) -> None:
        from navig.help_texts import OPT_HOST
        assert hasattr(OPT_HOST, "text")
        assert isinstance(OPT_HOST.text, str)

    def test_opt_yes_text_is_nonempty(self) -> None:
        from navig.help_texts import OPT_YES
        assert len(OPT_YES.text) > 0

    def test_opt_dry_run_mentions_changes(self) -> None:
        from navig.help_texts import OPT_DRY_RUN
        assert "changes" in OPT_DRY_RUN.text.lower() or "done" in OPT_DRY_RUN.text.lower()

    def test_standard_opts_exist(self) -> None:
        from navig.help_texts import (
            OPT_HOST, OPT_APP, OPT_VERBOSE, OPT_QUIET, OPT_YES,
            OPT_CONFIRM, OPT_DRY_RUN, OPT_JSON, OPT_PLAIN, OPT_FORCE,
        )
        opts = [OPT_HOST, OPT_APP, OPT_VERBOSE, OPT_QUIET, OPT_YES,
                OPT_CONFIRM, OPT_DRY_RUN, OPT_JSON, OPT_PLAIN, OPT_FORCE]
        for opt in opts:
            assert hasattr(opt, "text") and len(opt.text) > 0


class TestGetGroupHelp:
    def test_known_group_returns_dict(self) -> None:
        from navig.help_texts import get_group_help
        result = get_group_help("host")
        assert isinstance(result, dict)
        assert "desc" in result or "short_help" in result

    def test_unknown_group_returns_none(self) -> None:
        from navig.help_texts import get_group_help
        result = get_group_help("nonexistent_group_xyz")
        assert result is None

    def test_db_group_help(self) -> None:
        from navig.help_texts import get_group_help
        result = get_group_help("db")
        assert result is not None

    def test_docker_group_help(self) -> None:
        from navig.help_texts import get_group_help
        result = get_group_help("docker")
        assert result is not None

    def test_all_known_groups_return_dicts(self) -> None:
        from navig.help_texts import get_group_help
        groups = ["host", "tunnel", "app", "docker", "web", "db", "file",
                  "log", "backup", "flow", "ai", "config", "agent", "memory"]
        for group in groups:
            result = get_group_help(group)
            if result is not None:
                assert isinstance(result, dict)
