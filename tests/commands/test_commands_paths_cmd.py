"""Tests for navig/commands/paths_cmd.py."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from navig.commands.paths_cmd import _path_rows, paths_app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Rows come from the canonical resolvers, not hand-built ~/.navig/<x>
# ---------------------------------------------------------------------------

def test_logs_row_is_the_canonical_log_dir():
    """`navig paths` must show the REAL log dir, not ~/.navig/logs.

    log_dir() is OS-idiomatic (%LOCALAPPDATA%\\navig\\logs on Windows, etc.); the old
    hand-built ~/.navig/logs pointed at an empty directory — the #192 wrong-path class.
    """
    from navig.platform import paths as p

    rows = dict(_path_rows())
    assert rows["logs"] == p.log_dir()
    assert rows["logs"] != Path.home() / ".navig" / "logs"


def test_debug_log_row_is_surfaced_and_canonical():
    from navig.platform import paths as p

    rows = dict(_path_rows())
    assert "debug log" in rows
    assert rows["debug log"] == p.debug_log_path()
    assert rows["debug log"] == rows["logs"] / "debug.log"


def test_rows_honour_env_overrides(monkeypatch, tmp_path):
    """The hand-built paths ignored NAVIG_*_DIR; the resolvers must not."""
    monkeypatch.setenv("NAVIG_LOG_DIR", str(tmp_path / "L"))
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "C"))
    rows = dict(_path_rows())
    assert rows["logs"] == tmp_path / "L"
    assert rows["debug log"] == tmp_path / "L" / "debug.log"
    # config-scoped rows follow NAVIG_CONFIG_DIR too
    assert rows["plugins"] == tmp_path / "C" / "plugins"


def test_config_logs_row_surfaces_the_second_log_dir():
    """Half the tree writes to config_dir()/logs (agent.log, tray.log, router traces);
    `navig paths` used to hide it. It must be shown, and distinct from the OS log dir."""
    from navig.platform import paths as p

    rows = dict(_path_rows())
    assert rows["logs (config)"] == p.config_dir() / "logs"
    assert rows["logs (config)"] != rows["logs"]  # genuinely a different directory


def test_config_logs_row_collapses_when_it_equals_log_dir(monkeypatch, tmp_path):
    """If NAVIG_LOG_DIR happens to equal config_dir()/logs, don't show a redundant row."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("NAVIG_LOG_DIR", str(tmp_path / "logs"))
    rows = dict(_path_rows())
    assert "logs (config)" not in rows


def test_packs_row_is_the_canonical_packages_dir():
    """`navig paths` must show packages_dir() (honours NAVIG_PACKAGES_DIR + legacy fallback),
    not a hand-built cfg/"packs" that ignores both — the wrong-path class the docstring flags."""
    from navig.platform import paths as p

    rows = dict(_path_rows())
    assert rows["packs"] == p.packages_dir()


def test_packs_row_honours_packages_env_override(monkeypatch, tmp_path):
    """The hand-built cfg/'packs' ignored NAVIG_PACKAGES_DIR; the resolver must not."""
    monkeypatch.setenv("NAVIG_PACKAGES_DIR", str(tmp_path / "P"))
    rows = dict(_path_rows())
    assert rows["packs"] == tmp_path / "P"


def test_user_content_rows_are_surfaced_and_canonical():
    """scripts/skills/workflows must be discoverable AND match their canonical helpers, so an
    operator can find where an evolved script/skill/workflow actually lands (#276/#281/#285)."""
    from navig.platform import paths as p

    rows = dict(_path_rows())
    assert rows["scripts"] == p.scripts_dir()
    assert rows["skills"] == p.skills_dir()
    assert rows["workflows"] == p.workflows_dir()


def test_user_content_rows_honour_config_dir(monkeypatch, tmp_path):
    """The content rows follow NAVIG_CONFIG_DIR (call-time resolution)."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    rows = dict(_path_rows())
    assert rows["scripts"] == tmp_path / "scripts"
    assert rows["skills"] == tmp_path / "skills"
    assert rows["workflows"] == tmp_path / "workflows"


def test_spaces_row_is_plural_and_canonical():
    """REGRESSION: the row showed a hand-built cfg/'space' (SINGULAR) — a directory nothing
    creates — while installed spaces live in config_dir()/spaces (PLURAL). `navig paths` sent
    the operator hunting their spaces to a non-existent path."""
    from navig.platform import paths as p

    rows = dict(_path_rows())
    assert "space" not in rows  # the buggy singular key is gone
    assert rows["spaces"] == p.spaces_dir()
    assert rows["spaces"].name == "spaces"


def test_plugins_and_wiki_rows_are_canonical():
    from navig.platform import paths as p

    rows = dict(_path_rows())
    assert rows["plugins"] == p.plugins_dir()
    assert rows["wiki"] == p.wiki_dir()


# ---------------------------------------------------------------------------
# Basic invocation
# ---------------------------------------------------------------------------

def test_default_invocation_exits_0():
    result = runner.invoke(paths_app, [])
    assert result.exit_code == 0


def test_help_exits_0():
    result = runner.invoke(paths_app, ["--help"])
    assert result.exit_code == 0


def test_default_produces_output():
    result = runner.invoke(paths_app, [])
    assert len(result.output) > 0


# ---------------------------------------------------------------------------
# Key names present in output
# ---------------------------------------------------------------------------

def test_output_contains_config_key():
    result = runner.invoke(paths_app, [])
    assert "config" in result.output


def test_output_contains_data_key():
    result = runner.invoke(paths_app, [])
    assert "data" in result.output


def test_output_contains_logs_key():
    result = runner.invoke(paths_app, [])
    assert "logs" in result.output


def test_output_contains_plugins_key():
    result = runner.invoke(paths_app, [])
    assert "plugins" in result.output


def test_output_contains_store_key():
    result = runner.invoke(paths_app, [])
    assert "store" in result.output


def test_output_contains_wiki_key():
    result = runner.invoke(paths_app, [])
    assert "wiki" in result.output


def test_output_contains_space_key():
    result = runner.invoke(paths_app, [])
    assert "space" in result.output


def test_output_contains_packs_key():
    result = runner.invoke(paths_app, [])
    assert "packs" in result.output


# ---------------------------------------------------------------------------
# Path content
# ---------------------------------------------------------------------------

def test_output_contains_navig_dir():
    # "navig" (the product name) always appears — e.g. in the OS log dir
    # (…/navig/logs). Not ".navig" specifically: the paths now honour NAVIG_CONFIG_DIR,
    # so under an isolated config dir (as the test suite uses) they aren't under ~/.navig.
    result = runner.invoke(paths_app, [])
    assert "navig" in result.output


def test_output_contains_home_segment():
    result = runner.invoke(paths_app, [])
    home = str(Path.home())
    # At least partial home path segment should appear
    home_parts = Path.home().parts
    # Check that some meaningful segment appears
    assert any(p in result.output for p in home_parts if len(p) > 1)


def test_output_contains_existence_marker():
    result = runner.invoke(paths_app, [])
    # Table has ✓ or – for each row
    assert "✓" in result.output or "–" in result.output or "-" in result.output


# ---------------------------------------------------------------------------
# Table structure
# ---------------------------------------------------------------------------

def test_output_is_table_like():
    """Output should contain multiple lines resembling a table."""
    result = runner.invoke(paths_app, [])
    lines = [l for l in result.output.splitlines() if l.strip()]
    assert len(lines) >= 8  # at least one line per path entry


def test_all_eight_keys_present():
    result = runner.invoke(paths_app, [])
    expected_keys = ["config", "data", "logs", "plugins", "store", "wiki", "space", "packs"]
    for key in expected_keys:
        assert key in result.output, f"Expected key '{key}' in output"


# ---------------------------------------------------------------------------
# No subcommand needed — callback is the default action
# ---------------------------------------------------------------------------

def test_no_args_does_not_require_subcommand():
    result = runner.invoke(paths_app, [])
    # Should not say "Missing command" or similar error
    assert "Error" not in result.output or result.exit_code == 0


def test_unrecognized_subcommand_exits_nonzero():
    result = runner.invoke(paths_app, ["nonexistent-sub"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Idempotency / multiple calls
# ---------------------------------------------------------------------------

def test_multiple_invocations_consistent():
    result1 = runner.invoke(paths_app, [])
    result2 = runner.invoke(paths_app, [])
    assert result1.exit_code == result2.exit_code == 0
    assert "config" in result1.output
    assert "config" in result2.output
