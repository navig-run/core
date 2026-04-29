"""
Tests for navig/commands/help_cmd.py

Strategy: create a minimal Typer app that delegates to run_help,
invoke via CliRunner, and assert output properties.
"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import typer
import pytest
from typer.testing import CliRunner

from navig.commands.help_cmd import run_help

runner = CliRunner()

# ---------------------------------------------------------------------------
# Minimal wrapper app so we can invoke run_help with real exits
# ---------------------------------------------------------------------------

_wrapper = typer.Typer()


@_wrapper.command("help")
def _help_cmd(
    topic: str = typer.Argument(None),
    plain: bool = typer.Option(False, "--plain"),
    json_output: bool = typer.Option(False, "--json"),
    raw: bool = typer.Option(False, "--raw"),
    schema_out: bool = typer.Option(False, "--schema"),
):
    ctx = typer.get_current_context()
    ctx.ensure_object(dict)
    run_help(ctx, topic, plain=plain, json_output=json_output, raw=raw, schema_out=schema_out)


# Fake schema with minimal structure
_FAKE_SCHEMA = {
    "commands": [
        {"path": "navig db list", "summary": "List databases", "status": "stable", "since": ""},
        {"path": "navig db query", "summary": "Run SQL query", "status": "stable", "since": ""},
        {"path": "navig host show", "summary": "Show host info", "status": "stable", "since": ""},
    ]
}


# ---------------------------------------------------------------------------
# --schema flag
# ---------------------------------------------------------------------------


class TestSchemaFlag:
    def test_schema_flag_outputs_json(self):
        with patch("navig.commands.help_cmd.run_help.__code__") as _:
            pass  # just to exercise import
        with patch("navig.cli.registry.get_schema", return_value=_FAKE_SCHEMA):
            result = runner.invoke(_wrapper, ["help", "--schema"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "commands" in data

    def test_schema_output_is_valid_json(self):
        with patch("navig.cli.registry.get_schema", return_value=_FAKE_SCHEMA):
            result = runner.invoke(_wrapper, ["help", "--schema"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# No topic — listing mode
# ---------------------------------------------------------------------------


class TestNoTopic:
    def test_no_topic_exits_0(self, tmp_path):
        fake_help = tmp_path / "help"
        fake_help.mkdir()
        (fake_help / "db.md").write_text("# db help", encoding="utf-8")
        with patch("navig.commands.help_cmd.Path") as mock_path_cls:
            # Let the original Path work for most calls, only intercept help_dir
            mock_path_cls.side_effect = lambda *a, **kw: Path(*a, **kw)
            with patch("navig.cli.registry.get_schema", return_value=_FAKE_SCHEMA):
                result = runner.invoke(_wrapper, ["help"])
        assert result.exit_code == 0

    def test_no_topic_json_outputs_topics_key(self):
        with patch("navig.cli.registry.get_schema", return_value=_FAKE_SCHEMA):
            result = runner.invoke(_wrapper, ["help", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "topics" in data
        assert isinstance(data["topics"], list)

    def test_no_topic_json_contains_sources(self):
        with patch("navig.cli.registry.get_schema", return_value=_FAKE_SCHEMA):
            result = runner.invoke(_wrapper, ["help", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "sources" in data
        assert "registry" in data["sources"]
        assert "markdown" in data["sources"]

    def test_no_topic_plain_exits_0(self):
        with patch("navig.cli.registry.get_schema", return_value=_FAKE_SCHEMA):
            result = runner.invoke(_wrapper, ["help", "--plain"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Topic from schema registry
# ---------------------------------------------------------------------------


class TestTopicFromRegistry:
    def test_known_topic_plain_exits_0(self):
        with patch("navig.cli.registry.get_schema", return_value=_FAKE_SCHEMA):
            result = runner.invoke(_wrapper, ["help", "db", "--plain"])
        assert result.exit_code == 0

    def test_known_topic_plain_shows_commands(self):
        with patch("navig.cli.registry.get_schema", return_value=_FAKE_SCHEMA):
            result = runner.invoke(_wrapper, ["help", "db", "--plain"])
        assert result.exit_code == 0
        assert "navig db" in result.output

    def test_known_topic_json_outputs_topic_key(self):
        with patch("navig.cli.registry.get_schema", return_value=_FAKE_SCHEMA):
            result = runner.invoke(_wrapper, ["help", "db", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["topic"] == "db"
        assert "commands" in data

    def test_known_topic_json_source_is_registry(self):
        with patch("navig.cli.registry.get_schema", return_value=_FAKE_SCHEMA):
            result = runner.invoke(_wrapper, ["help", "db", "--json"])
        data = json.loads(result.output)
        assert data["source"] == "registry"

    def test_known_topic_default_render_exits_0(self):
        with patch("navig.cli.registry.get_schema", return_value=_FAKE_SCHEMA):
            result = runner.invoke(_wrapper, ["help", "db"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Unknown topic — falls through to subcommand help
# ---------------------------------------------------------------------------


class TestUnknownTopic:
    def test_unknown_topic_exits_0(self):
        with patch("navig.cli.registry.get_schema", return_value=_FAKE_SCHEMA):
            with patch("navig.cli._callbacks.show_subcommand_help"):
                result = runner.invoke(_wrapper, ["help", "nonexistenttopic"])
        assert result.exit_code == 0
