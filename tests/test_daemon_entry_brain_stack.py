"""Unit tests for daemon/entry.py helpers, commands/brain.py, and commands/stack.py."""
from __future__ import annotations

import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# navig.daemon.entry — _as_bool, _as_int, DEFAULT_DAEMON_CONFIG
# ---------------------------------------------------------------------------

from navig.daemon.entry import (
    DEFAULT_DAEMON_CONFIG,
    _as_bool,
    _as_int,
)


class TestAsBool:
    # --- passthrough booleans ---
    def test_true_passthrough(self):
        assert _as_bool(True, False) is True

    def test_false_passthrough(self):
        assert _as_bool(False, True) is False

    # --- string truthy ---
    def test_str_1(self):
        assert _as_bool("1", False) is True

    def test_str_true(self):
        assert _as_bool("true", False) is True

    def test_str_TRUE_upper(self):
        assert _as_bool("TRUE", False) is True

    def test_str_yes(self):
        assert _as_bool("yes", False) is True

    def test_str_on(self):
        assert _as_bool("on", False) is True

    def test_str_truthy_with_whitespace(self):
        assert _as_bool("  true  ", False) is True

    # --- string falsey ---
    def test_str_0(self):
        assert _as_bool("0", True) is False

    def test_str_false(self):
        assert _as_bool("false", True) is False

    def test_str_FALSE_upper(self):
        assert _as_bool("FALSE", True) is False

    def test_str_no(self):
        assert _as_bool("no", True) is False

    def test_str_off(self):
        assert _as_bool("off", True) is False

    def test_str_empty(self):
        assert _as_bool("", True) is False

    def test_str_whitespace_only(self):
        assert _as_bool("   ", True) is False

    # --- unknown string → default ---
    def test_str_unknown_returns_default_true(self):
        assert _as_bool("maybe", True) is True

    def test_str_unknown_returns_default_false(self):
        assert _as_bool("maybe", False) is False

    # --- numeric ---
    def test_int_nonzero(self):
        assert _as_bool(1, False) is True

    def test_int_zero(self):
        assert _as_bool(0, True) is False

    def test_float_nonzero(self):
        assert _as_bool(0.5, False) is True

    def test_float_zero(self):
        assert _as_bool(0.0, True) is False

    # --- unknown type → default ---
    def test_none_returns_default(self):
        assert _as_bool(None, True) is True

    def test_list_returns_default(self):
        assert _as_bool([], False) is False


class TestAsInt:
    # --- bool coercion ---
    def test_bool_true(self):
        assert _as_int(True, 0) == 1

    def test_bool_false(self):
        assert _as_int(False, 99) == 0

    # --- int passthrough ---
    def test_int_passthrough(self):
        assert _as_int(42, 0) == 42

    def test_int_negative(self):
        assert _as_int(-5, 0) == -5

    def test_int_zero(self):
        assert _as_int(0, 99) == 0

    # --- float truncation ---
    def test_float_truncation(self):
        assert _as_int(3.9, 0) == 3

    def test_float_negative(self):
        assert _as_int(-1.7, 0) == -1

    # --- string numeric ---
    def test_str_decimal(self):
        assert _as_int("8080", 0) == 8080

    def test_str_negative(self):
        assert _as_int("-1", 0) == -1

    def test_str_with_whitespace(self):
        assert _as_int("  42  ", 0) == 42

    # --- string falsey / invalid ---
    def test_str_empty_returns_default(self):
        assert _as_int("", 99) == 99

    def test_str_letters_returns_default(self):
        assert _as_int("abc", 7) == 7

    def test_str_float_returns_default(self):
        # int() cannot parse "3.14" directly
        assert _as_int("3.14", 5) == 5

    # --- unknown type → default ---
    def test_none_returns_default(self):
        assert _as_int(None, 10) == 10

    def test_list_returns_default(self):
        assert _as_int([], 10) == 10


class TestDefaultDaemonConfig:
    def test_is_dict(self):
        assert isinstance(DEFAULT_DAEMON_CONFIG, dict)

    def test_has_telegram_bot_key(self):
        assert "telegram_bot" in DEFAULT_DAEMON_CONFIG

    def test_has_gateway_key(self):
        assert "gateway" in DEFAULT_DAEMON_CONFIG

    def test_has_gateway_port(self):
        assert "gateway_port" in DEFAULT_DAEMON_CONFIG

    def test_has_scheduler(self):
        assert "scheduler" in DEFAULT_DAEMON_CONFIG

    def test_has_health_port(self):
        assert "health_port" in DEFAULT_DAEMON_CONFIG

    def test_has_engagement(self):
        assert "engagement" in DEFAULT_DAEMON_CONFIG

    def test_telegram_bot_is_bool(self):
        assert isinstance(DEFAULT_DAEMON_CONFIG["telegram_bot"], bool)

    def test_gateway_default_false(self):
        assert DEFAULT_DAEMON_CONFIG["gateway"] is False

    def test_gateway_port_is_int(self):
        assert isinstance(DEFAULT_DAEMON_CONFIG["gateway_port"], int)

    def test_health_port_default_zero(self):
        assert DEFAULT_DAEMON_CONFIG["health_port"] == 0

    def test_engagement_default_true(self):
        assert DEFAULT_DAEMON_CONFIG["engagement"] is True


# ---------------------------------------------------------------------------
# navig.commands.brain — _prompt_dirs, _resolve
# ---------------------------------------------------------------------------

from navig.commands.brain import _prompt_dirs, _resolve


class TestPromptDirs:
    def test_returns_list(self):
        result = _prompt_dirs()
        assert isinstance(result, list)

    def test_always_has_global_dir(self, tmp_path, monkeypatch):
        """Global config_dir/brain/prompts must always be in the result."""
        from navig.platform import paths

        monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        result = _prompt_dirs()
        expected_global = tmp_path / "brain" / "prompts"
        assert expected_global in result

    def test_project_local_before_global(self, tmp_path, monkeypatch):
        """Project-local dir should appear before global dir."""
        from navig.platform import paths

        # Set up a project-local .navig/brain/prompts/
        local_dir = tmp_path / ".navig" / "brain" / "prompts"
        local_dir.mkdir(parents=True)

        global_base = tmp_path / "global_navig"
        monkeypatch.setattr(paths, "config_dir", lambda: global_base)
        monkeypatch.chdir(tmp_path)

        result = _prompt_dirs()
        assert result[0] == local_dir

    def test_no_duplicate_when_global_is_local(self, tmp_path, monkeypatch):
        """If project dir happens to equal global dir, no duplicate."""
        from navig.platform import paths

        global_base = tmp_path / "navig_home"
        prompt_dir = global_base / "brain" / "prompts"
        prompt_dir.mkdir(parents=True)

        monkeypatch.setattr(paths, "config_dir", lambda: global_base)
        # Ensure cwd is one level above so walk-up finds prompt_dir equivalent
        monkeypatch.chdir(tmp_path)

        result = _prompt_dirs()
        # Count occurrences of global_base / "brain" / "prompts"
        count = sum(1 for d in result if d == global_base / "brain" / "prompts")
        assert count == 1

    def test_all_items_are_paths(self):
        result = _prompt_dirs()
        for item in result:
            assert isinstance(item, Path)


class TestResolve:
    def test_returns_none_when_no_prompts_exist(self, tmp_path, monkeypatch):
        from navig.platform import paths

        monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        assert _resolve("nonexistent-slug") is None

    def test_returns_path_when_found_in_global(self, tmp_path, monkeypatch):
        from navig.platform import paths

        global_prompts = tmp_path / "brain" / "prompts"
        global_prompts.mkdir(parents=True)
        (global_prompts / "myslug.md").write_text("# My Prompt")

        monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)

        result = _resolve("myslug")
        assert result is not None
        assert result.name == "myslug.md"

    def test_project_local_shadows_global(self, tmp_path, monkeypatch):
        from navig.platform import paths

        # Create global prompt
        global_base = tmp_path / "global_home"
        global_prompts = global_base / "brain" / "prompts"
        global_prompts.mkdir(parents=True)
        (global_prompts / "shared.md").write_text("# Global")

        # Create project-local prompt with same slug
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        local_dir = project_dir / ".navig" / "brain" / "prompts"
        local_dir.mkdir(parents=True)
        (local_dir / "shared.md").write_text("# Local")

        monkeypatch.setattr(paths, "config_dir", lambda: global_base)
        monkeypatch.chdir(project_dir)

        result = _resolve("shared")
        assert result is not None
        assert result.parent == local_dir

    def test_md_extension_auto_appended(self, tmp_path, monkeypatch):
        from navig.platform import paths

        global_prompts = tmp_path / "brain" / "prompts"
        global_prompts.mkdir(parents=True)
        (global_prompts / "test.md").write_text("# T")

        monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)

        # Pass slug without .md extension
        result = _resolve("test")
        assert result is not None

    def test_slug_with_md_extension_works(self, tmp_path, monkeypatch):
        from navig.platform import paths

        global_prompts = tmp_path / "brain" / "prompts"
        global_prompts.mkdir(parents=True)
        (global_prompts / "test.md").write_text("# T")

        monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)

        # Pass slug with .md extension explicitly
        result = _resolve("test.md")
        assert result is not None


# ---------------------------------------------------------------------------
# navig.commands.stack — _compose_cmd
# ---------------------------------------------------------------------------

from navig.commands.stack import _compose_cmd


class TestComposeCmd:
    def test_basic_command_without_env(self, tmp_path):
        """No .env file → standard 4-element command."""
        cmd = _compose_cmd(tmp_path)
        assert cmd == ["docker", "compose", "-f", str(tmp_path / "docker-compose.yml")]

    def test_includes_env_file_when_present(self, tmp_path):
        """When .env exists, --env-file is appended."""
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value\n")
        cmd = _compose_cmd(tmp_path)
        assert "--env-file" in cmd
        assert str(env_file) in cmd

    def test_env_file_flags_at_end(self, tmp_path):
        """--env-file must appear after the -f compose file flag."""
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value\n")
        cmd = _compose_cmd(tmp_path)
        f_idx = cmd.index("-f")
        env_idx = cmd.index("--env-file")
        assert env_idx > f_idx

    def test_compose_file_path_in_cmd(self, tmp_path):
        cmd = _compose_cmd(tmp_path)
        assert str(tmp_path / "docker-compose.yml") in cmd

    def test_no_env_file_absent(self, tmp_path):
        """Ensure --env-file not in command when .env absent."""
        cmd = _compose_cmd(tmp_path)
        assert "--env-file" not in cmd

    def test_returns_list_of_strings(self, tmp_path):
        cmd = _compose_cmd(tmp_path)
        assert all(isinstance(s, str) for s in cmd)

    def test_starts_with_docker_compose(self, tmp_path):
        cmd = _compose_cmd(tmp_path)
        assert cmd[0] == "docker"
        assert cmd[1] == "compose"
