"""Tests for navig/commands/help_cmd.py — run_help function."""
from __future__ import annotations

import json
from unittest.mock import patch

import typer
import pytest
from typer.testing import CliRunner

from navig.commands.help_cmd import run_help

runner = CliRunner()

_wrapper = typer.Typer()

_FAKE_SCHEMA = {
    "commands": [
        {"path": "navig db list", "summary": "List databases", "status": "stable", "since": ""},
        {"path": "navig db query", "summary": "Run SQL query", "status": "stable", "since": ""},
        {"path": "navig host show", "summary": "Show host info", "status": "stable", "since": ""},
    ]
}


@_wrapper.command("help")
def _help_cmd(
    ctx: typer.Context,
    topic: str = typer.Argument(None),
    plain: bool = typer.Option(False, "--plain"),
    json_output: bool = typer.Option(False, "--json"),
    raw: bool = typer.Option(False, "--raw"),
    schema_out: bool = typer.Option(False, "--schema"),
):
    ctx.ensure_object(dict)
    run_help(ctx, topic, plain=plain, json_output=json_output, raw=raw, schema_out=schema_out)


class TestSchemaFlag:
    def test_schema_outputs_json(self):
        with patch("navig.cli.registry.get_schema", return_value=_FAKE_SCHEMA):
            result = runner.invoke(_wrapper, ["--schema"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "commands" in data

    def test_schema_is_valid_json(self):
        with patch("navig.cli.registry.get_schema", return_value=_FAKE_SCHEMA):
            result = runner.invoke(_wrapper, ["--schema"])
        parsed = json.loads(result.output)
        assert isinstance(parsed, dict)


class TestNoTopic:
    def test_no_topic_exits_0(self):
        with patch("navig.cli.registry.get_schema", return_value=_FAKE_SCHEMA):
            result = runner.invoke(_wrapper, [])
        assert result.exit_code == 0

    def test_no_topic_json_has_topics_key(self):
        with patch("navig.cli.registry.get_schema", return_value=_FAKE_SCHEMA):
            result = runner.invoke(_wrapper, ["--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "topics" in data
        assert isinstance(data["topics"], list)

    def test_no_topic_json_has_sources(self):
        with patch("navig.cli.registry.get_schema", return_value=_FAKE_SCHEMA):
            result = runner.invoke(_wrapper, ["--json"])
        data = json.loads(result.output)
        assert "sources" in data

    def test_no_topic_plain_exits_0(self):
        with patch("navig.cli.registry.get_schema", return_value=_FAKE_SCHEMA):
            result = runner.invoke(_wrapper, ["--plain"])
        assert result.exit_code == 0


class TestTopicFromRegistry:
    def test_known_topic_plain_exits_0(self):
        with patch("navig.cli.registry.get_schema", return_value=_FAKE_SCHEMA):
            result = runner.invoke(_wrapper, ["db", "--plain"])
        assert result.exit_code == 0

    def test_known_topic_plain_shows_commands(self):
        with patch("navig.cli.registry.get_schema", return_value=_FAKE_SCHEMA):
            result = runner.invoke(_wrapper, ["db", "--plain"])
        assert "navig db" in result.output

    def test_known_topic_json_has_topic_key(self):
        with patch("navig.cli.registry.get_schema", return_value=_FAKE_SCHEMA):
            result = runner.invoke(_wrapper, ["db", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["topic"] == "db"

    def test_known_topic_json_source_is_string(self):
        with patch("navig.cli.registry.get_schema", return_value=_FAKE_SCHEMA):
            result = runner.invoke(_wrapper, ["db", "--json"])
        data = json.loads(result.output)
        assert isinstance(data["source"], str)
        assert len(data["source"]) > 0

    def test_known_topic_default_mode_exits_0(self):
        with patch("navig.cli.registry.get_schema", return_value=_FAKE_SCHEMA):
            result = runner.invoke(_wrapper, ["db"])
        assert result.exit_code == 0


class TestUnknownTopic:
    def test_unknown_topic_exits_1(self):
        with patch("navig.cli.registry.get_schema", return_value=_FAKE_SCHEMA):
            result = runner.invoke(_wrapper, ["nonexistenttopic"])
        assert result.exit_code == 1
