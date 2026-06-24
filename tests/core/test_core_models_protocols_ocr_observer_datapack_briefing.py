"""
Batch 45 — hermetic unit tests for:
  navig/core/models.py          — Pydantic domain models
  navig/core/protocols.py       — ConfigProvider / HostConfigProvider / AppConfigProvider
  navig/core/ocr.py             — extract_ocr_text_from_image_bytes
  navig/agent/pattern_observer.py — PatternRecord / PatternObserver
  navig/tools/domains/data_pack.py — _json_parse
  navig/spaces/briefing.py      — build_spaces_briefing_lines
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# navig/core/models.py
# ---------------------------------------------------------------------------

from navig.core.models import (
    CommandParameter,
    NavigCommand,
    NavigPack,
    PackStep,
    SkillExample,
    SkillManifest,
)


class TestCommandParameter:
    def test_required_fields(self):
        p = CommandParameter(type="string", description="A param")
        assert p.type == "string"
        assert p.description == "A param"

    def test_defaults(self):
        p = CommandParameter(type="boolean", description="flag")
        assert p.required is False
        assert p.default is None
        assert p.options is None

    def test_options_list(self):
        p = CommandParameter(type="string", description="x", options=["a", "b", "c"])
        assert p.options == ["a", "b", "c"]

    def test_required_true(self):
        p = CommandParameter(type="string", description="x", required=True)
        assert p.required is True

    def test_default_value(self):
        p = CommandParameter(type="integer", description="n", default=42)
        assert p.default == 42


class TestNavigCommand:
    def test_minimal(self):
        cmd = NavigCommand(name="ls", syntax="ls [dir]", description="List files")
        assert cmd.name == "ls"
        assert cmd.syntax == "ls [dir]"
        assert cmd.description == "List files"

    def test_defaults(self):
        cmd = NavigCommand(name="ls", syntax="ls", description="List")
        assert cmd.risk == "safe"
        assert cmd.confirmation_required is False
        assert cmd.confirmation_msg is None
        assert cmd.parameters is None
        assert cmd.source_skill is None

    def test_custom_risk(self):
        cmd = NavigCommand(name="rm", syntax="rm <path>", description="Remove", risk="destructive")
        assert cmd.risk == "destructive"

    def test_confirmation_required(self):
        cmd = NavigCommand(
            name="drop",
            syntax="drop db",
            description="Drop DB",
            confirmation_required=True,
            confirmation_msg="Are you sure?",
        )
        assert cmd.confirmation_required is True
        assert cmd.confirmation_msg == "Are you sure?"

    def test_with_parameters(self):
        params = {"path": CommandParameter(type="string", description="Target path")}
        cmd = NavigCommand(name="cd", syntax="cd <path>", description="Change dir", parameters=params)
        assert "path" in cmd.parameters


class TestSkillExample:
    def test_fields(self):
        ex = SkillExample(user="list files", thought="ls command", command="ls -la")
        assert ex.user == "list files"
        assert ex.thought == "ls command"
        assert ex.command == "ls -la"


class TestSkillManifest:
    def test_minimal(self):
        sm = SkillManifest(name="fs-skill", description="Filesystem skill", version="1.0.0")
        assert sm.name == "fs-skill"
        assert sm.version == "1.0.0"

    def test_defaults(self):
        sm = SkillManifest(name="x", description="y")
        assert sm.version == "0.0.1"
        assert sm.author is None
        assert sm.category == "uncategorized"
        assert sm.risk_level == "safe"
        assert sm.user_invocable is True
        assert sm.requires == []
        assert sm.tags == []
        assert sm.navig_commands == []
        assert sm.examples == []

    def test_alias_risk_level(self):
        sm = SkillManifest(**{"name": "x", "description": "y", "risk-level": "moderate"})
        assert sm.risk_level == "moderate"

    def test_alias_user_invocable(self):
        sm = SkillManifest(**{"name": "x", "description": "y", "user-invocable": False})
        assert sm.user_invocable is False

    def test_alias_navig_commands(self):
        cmd_data = {"name": "c", "syntax": "c", "description": "d"}
        sm = SkillManifest(**{"name": "x", "description": "y", "navig-commands": [cmd_data]})
        assert len(sm.navig_commands) == 1
        assert sm.navig_commands[0].name == "c"

    def test_examples_list(self):
        ex = SkillExample(user="q", thought="t", command="c")
        sm = SkillManifest(name="s", description="d", examples=[ex])
        assert len(sm.examples) == 1

    def test_populate_by_name(self):
        sm = SkillManifest(name="x", description="y", risk_level="high")
        assert sm.risk_level == "high"


class TestPackStep:
    def test_defaults(self):
        s = PackStep(command="ls")
        assert s.name == "unnamed-step"
        assert s.description is None
        assert s.continue_on_error is False

    def test_custom(self):
        s = PackStep(name="deploy", description="Run deploy", command="./deploy.sh", continue_on_error=True)
        assert s.name == "deploy"
        assert s.continue_on_error is True


class TestNavigPack:
    def test_minimal(self):
        p = NavigPack(name="db-pack", description="Database runbook")
        assert p.name == "db-pack"
        assert p.description == "Database runbook"

    def test_defaults(self):
        p = NavigPack(name="x", description="y")
        assert p.version == "1.0.0"
        assert p.author == "unknown"
        assert p.type == "runbook"
        assert p.tags == []
        assert p.steps == []

    def test_with_steps(self):
        steps = [PackStep(command="ls"), PackStep(name="check", command="df -h")]
        p = NavigPack(name="x", description="y", steps=steps)
        assert len(p.steps) == 2
        assert p.steps[1].name == "check"

    def test_type_variants(self):
        for t in ("runbook", "checklist", "workflow"):
            p = NavigPack(name="x", description="y", type=t)
            assert p.type == t


# ---------------------------------------------------------------------------
# navig/core/protocols.py
# ---------------------------------------------------------------------------

from navig.core.protocols import AppConfigProvider, ConfigProvider, HostConfigProvider


class TestProtocols:
    """Structural tests — protocols are runtime-checkable via isinstance for
    concrete implementations and expose the expected abstract members."""

    def test_config_provider_is_protocol(self):
        from typing import Protocol
        assert issubclass(ConfigProvider, Protocol)

    def test_host_config_provider_inherits_config_provider(self):
        # HostConfigProvider extends ConfigProvider — check via __bases__
        assert ConfigProvider in HostConfigProvider.__bases__

    def test_app_config_provider_inherits_config_provider(self):
        assert ConfigProvider in AppConfigProvider.__bases__

    def test_config_provider_has_expected_members(self):
        annotations = ConfigProvider.__protocol_attrs__  # Python 3.12+
        # Fall back for older runtimes
        try:
            attrs = ConfigProvider.__protocol_attrs__
        except AttributeError:
            attrs = set(ConfigProvider.__abstractmethods__) | set(
                k for k, v in vars(ConfigProvider).items()
                if not k.startswith("_") or k in ("app_config_dir", "global_config_dir", "base_dir", "verbose", "get_config_directories")
            )
        expected = {"app_config_dir", "global_config_dir", "base_dir", "verbose", "get_config_directories"}
        # All expected attrs must be present in the protocol
        assert expected.issubset(attrs)

    def test_host_config_provider_adds_is_directory_accessible(self):
        try:
            attrs = HostConfigProvider.__protocol_attrs__
        except AttributeError:
            attrs = set(vars(HostConfigProvider).keys())
        assert "_is_directory_accessible" in attrs

    def test_app_config_provider_adds_host_methods(self):
        try:
            attrs = AppConfigProvider.__protocol_attrs__
        except AttributeError:
            attrs = set(vars(AppConfigProvider).keys())
        for method in ("load_host_config", "save_host_config", "list_hosts"):
            assert method in attrs

    def test_concrete_class_satisfies_config_provider(self):
        """A concrete class with all required members passes runtime check."""

        class Concrete:
            @property
            def app_config_dir(self) -> Path | None:
                return None

            @property
            def global_config_dir(self) -> Path:
                return Path("/tmp")

            @property
            def base_dir(self) -> Path:
                return Path("/tmp")

            @property
            def verbose(self) -> bool:
                return False

            def get_config_directories(self) -> list[Path]:
                return []

        # Protocols aren't checked via isinstance by default unless
        # @runtime_checkable; just verify the class can be assigned without errors
        obj = Concrete()
        assert obj.verbose is False
        assert obj.get_config_directories() == []


# ---------------------------------------------------------------------------
# navig/core/ocr.py
# ---------------------------------------------------------------------------

from navig.core.ocr import extract_ocr_text_from_image_bytes


class TestExtractOcrText:
    def _fake_image_bytes(self) -> bytes:
        return b"\x89PNG\r\n\x1a\nfakeimagedata"

    def test_returns_none_when_pytesseract_unavailable(self):
        """When pytesseract is not installed, import fails and None is returned."""
        with patch("builtins.__import__", side_effect=ImportError("no pytesseract")):
            result = extract_ocr_text_from_image_bytes(b"image")
        assert result is None

    def test_returns_text_when_ocr_succeeds(self):
        mock_img = MagicMock()
        mock_pytesseract = MagicMock()
        mock_pytesseract.image_to_string.return_value = "Hello World"
        mock_pil_image = MagicMock()
        mock_pil_image.Image.open.return_value = mock_img

        import sys

        fake_modules = {
            "pytesseract": mock_pytesseract,
            "PIL": mock_pil_image,
            "PIL.Image": mock_pil_image.Image,
        }

        with patch.dict(sys.modules, fake_modules):
            result = extract_ocr_text_from_image_bytes(self._fake_image_bytes())

        assert result == "Hello World"

    def test_returns_none_when_text_too_short(self):
        mock_img = MagicMock()
        mock_pytesseract = MagicMock()
        mock_pytesseract.image_to_string.return_value = "ab"  # < 3 chars
        mock_pil_image = MagicMock()
        mock_pil_image.Image.open.return_value = mock_img

        import sys

        fake_modules = {
            "pytesseract": mock_pytesseract,
            "PIL": mock_pil_image,
            "PIL.Image": mock_pil_image.Image,
        }

        with patch.dict(sys.modules, fake_modules):
            result = extract_ocr_text_from_image_bytes(self._fake_image_bytes())

        assert result is None

    def test_returns_none_on_ocr_exception(self):
        mock_pytesseract = MagicMock()
        mock_pytesseract.image_to_string.side_effect = RuntimeError("OCR failed")
        mock_pil_image = MagicMock()
        mock_pil_image.Image.open.return_value = MagicMock()

        import sys

        fake_modules = {
            "pytesseract": mock_pytesseract,
            "PIL": mock_pil_image,
            "PIL.Image": mock_pil_image.Image,
        }

        with patch.dict(sys.modules, fake_modules):
            result = extract_ocr_text_from_image_bytes(self._fake_image_bytes())

        assert result is None

    def test_returns_none_when_empty_bytes(self):
        mock_pytesseract = MagicMock()
        mock_pytesseract.image_to_string.side_effect = Exception("invalid image")
        mock_pil_image = MagicMock()
        mock_pil_image.Image.open.side_effect = Exception("bad bytes")

        import sys

        fake_modules = {
            "pytesseract": mock_pytesseract,
            "PIL": mock_pil_image,
            "PIL.Image": mock_pil_image.Image,
        }

        with patch.dict(sys.modules, fake_modules):
            result = extract_ocr_text_from_image_bytes(b"")

        assert result is None

    def test_returns_text_exactly_three_chars(self):
        mock_img = MagicMock()
        mock_pytesseract = MagicMock()
        mock_pytesseract.image_to_string.return_value = "abc"  # exactly 3
        mock_pil_image = MagicMock()
        mock_pil_image.Image.open.return_value = mock_img

        import sys

        fake_modules = {
            "pytesseract": mock_pytesseract,
            "PIL": mock_pil_image,
            "PIL.Image": mock_pil_image.Image,
        }

        with patch.dict(sys.modules, fake_modules):
            result = extract_ocr_text_from_image_bytes(self._fake_image_bytes())

        assert result == "abc"

    def test_strips_whitespace_from_result(self):
        mock_img = MagicMock()
        mock_pytesseract = MagicMock()
        mock_pytesseract.image_to_string.return_value = "  hello there  \n"
        mock_pil_image = MagicMock()
        mock_pil_image.Image.open.return_value = mock_img

        import sys

        fake_modules = {
            "pytesseract": mock_pytesseract,
            "PIL": mock_pil_image,
            "PIL.Image": mock_pil_image.Image,
        }

        with patch.dict(sys.modules, fake_modules):
            result = extract_ocr_text_from_image_bytes(self._fake_image_bytes())

        assert result == "hello there"


# ---------------------------------------------------------------------------
# navig/agent/pattern_observer.py
# ---------------------------------------------------------------------------

from navig.agent.pattern_observer import PatternObserver, PatternRecord


class TestPatternRecord:
    def test_fields(self):
        r = PatternRecord(command="ls -la")
        assert r.command == "ls -la"

    def test_is_dataclass(self):
        from dataclasses import fields
        fnames = [f.name for f in fields(PatternRecord)]
        assert "command" in fnames


class TestPatternObserver:
    def test_default_db_path_is_set(self):
        obs = PatternObserver()
        assert obs.db_path is not None
        assert "pattern_log.sqlite" in str(obs.db_path)

    def test_custom_db_path(self, tmp_path):
        db = tmp_path / "custom.sqlite"
        obs = PatternObserver(db_path=db)
        assert obs.db_path == db

    def test_returns_empty_when_db_not_exists(self, tmp_path):
        obs = PatternObserver(db_path=tmp_path / "nonexistent.sqlite")
        result = obs.get_recent()
        assert result == []

    def test_returns_records_from_db(self, tmp_path):
        db_path = tmp_path / "patterns.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE patterns (command TEXT, ts INTEGER)")
        conn.execute("INSERT INTO patterns VALUES ('ls -la', 1)")
        conn.execute("INSERT INTO patterns VALUES ('cd /tmp', 2)")
        conn.commit()
        conn.close()

        obs = PatternObserver(db_path=db_path)
        results = obs.get_recent(limit=10)
        assert len(results) == 2
        commands = {r.command for r in results}
        assert "ls -la" in commands
        assert "cd /tmp" in commands

    def test_respects_limit(self, tmp_path):
        db_path = tmp_path / "patterns.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE patterns (command TEXT, ts INTEGER)")
        for i in range(10):
            conn.execute(f"INSERT INTO patterns VALUES ('cmd-{i}', {i})")
        conn.commit()
        conn.close()

        obs = PatternObserver(db_path=db_path)
        results = obs.get_recent(limit=3)
        assert len(results) == 3

    def test_filters_null_commands(self, tmp_path):
        db_path = tmp_path / "patterns.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE patterns (command TEXT, ts INTEGER)")
        conn.execute("INSERT INTO patterns VALUES ('valid-cmd', 1)")
        conn.execute("INSERT INTO patterns VALUES (NULL, 2)")
        conn.commit()
        conn.close()

        obs = PatternObserver(db_path=db_path)
        results = obs.get_recent()
        assert len(results) == 1
        assert results[0].command == "valid-cmd"

    def test_returns_empty_on_sqlite_error(self, tmp_path):
        """If the database is corrupted, returns [] gracefully."""
        db_path = tmp_path / "bad.sqlite"
        db_path.write_bytes(b"not a sqlite file at all")

        obs = PatternObserver(db_path=db_path)
        result = obs.get_recent()
        assert result == []

    def test_returns_pattern_record_instances(self, tmp_path):
        db_path = tmp_path / "patterns.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE patterns (command TEXT, ts INTEGER)")
        conn.execute("INSERT INTO patterns VALUES ('git status', 1)")
        conn.commit()
        conn.close()

        obs = PatternObserver(db_path=db_path)
        results = obs.get_recent()
        assert all(isinstance(r, PatternRecord) for r in results)

    def test_default_limit_is_500(self, tmp_path):
        """get_recent with no args uses limit=500 (test via inspect)."""
        import inspect
        sig = inspect.signature(PatternObserver.get_recent)
        assert sig.parameters["limit"].default == 500


# ---------------------------------------------------------------------------
# navig/tools/domains/data_pack.py
# ---------------------------------------------------------------------------

from navig.tools.domains.data_pack import _json_parse


class TestJsonParse:
    def test_valid_object(self):
        result = _json_parse('{"key": "value"}')
        assert result == {"parsed": {"key": "value"}}

    def test_valid_array(self):
        result = _json_parse("[1, 2, 3]")
        assert result == {"parsed": [1, 2, 3]}

    def test_valid_string(self):
        result = _json_parse('"hello"')
        assert result == {"parsed": "hello"}

    def test_valid_number(self):
        result = _json_parse("42")
        assert result == {"parsed": 42}

    def test_valid_bool_true(self):
        result = _json_parse("true")
        assert result == {"parsed": True}

    def test_valid_bool_false(self):
        result = _json_parse("false")
        assert result == {"parsed": False}

    def test_valid_null(self):
        result = _json_parse("null")
        assert result == {"parsed": None}

    def test_empty_object(self):
        result = _json_parse("{}")
        assert result == {"parsed": {}}

    def test_nested_json(self):
        result = _json_parse('{"a": {"b": [1, 2]}}')
        assert result["parsed"]["a"]["b"] == [1, 2]

    def test_invalid_json_returns_error(self):
        result = _json_parse("{not valid json")
        assert "error" in result
        assert "Invalid JSON" in result["error"]

    def test_empty_string_returns_error(self):
        result = _json_parse("")
        assert "error" in result

    def test_error_message_contains_details(self):
        result = _json_parse("bad {json}")
        assert "Invalid JSON" in result["error"]

    def test_extra_kwargs_ignored(self):
        result = _json_parse('{"x": 1}', ignored_kwarg="whatever")
        assert result == {"parsed": {"x": 1}}


# ---------------------------------------------------------------------------
# navig/spaces/briefing.py
# ---------------------------------------------------------------------------

from navig.spaces.briefing import build_spaces_briefing_lines


class _FakeSpaceRow:
    def __init__(self, name, scope, completion_pct, goal):
        self.name = name
        self.scope = scope
        self.completion_pct = completion_pct
        self.goal = goal


class _FakeAction:
    def __init__(self, space, scope, next_task=None):
        self.space = space
        self.scope = scope
        self.next_task = next_task


class TestBuildSpacesBriefingLines:
    def _spaces(self):
        return [
            _FakeSpaceRow("devops", "global", 75.0, "Deploy infra"),
            _FakeSpaceRow("sysops", "project", 30.5, "Fix monitoring"),
        ]

    def test_no_spaces_returns_default_message(self):
        with (
            patch("navig.spaces.briefing.collect_spaces_progress", return_value=[]),
            patch("navig.spaces.briefing.select_best_next_action", return_value=None),
        ):
            lines = build_spaces_briefing_lines()
        assert lines == ["_No spaces available for briefing._"]

    def test_with_spaces_returns_header(self):
        with (
            patch("navig.spaces.briefing.collect_spaces_progress", return_value=self._spaces()),
            patch("navig.spaces.briefing.select_best_next_action", return_value=None),
        ):
            lines = build_spaces_briefing_lines()
        assert lines[0] == "*Spaces Progress:*"

    def test_space_names_in_output(self):
        with (
            patch("navig.spaces.briefing.collect_spaces_progress", return_value=self._spaces()),
            patch("navig.spaces.briefing.select_best_next_action", return_value=None),
        ):
            lines = build_spaces_briefing_lines()
        combined = "\n".join(lines)
        assert "devops" in combined
        assert "sysops" in combined

    def test_completion_pct_in_output(self):
        with (
            patch("navig.spaces.briefing.collect_spaces_progress", return_value=self._spaces()),
            patch("navig.spaces.briefing.select_best_next_action", return_value=None),
        ):
            lines = build_spaces_briefing_lines()
        combined = "\n".join(lines)
        assert "75.0" in combined

    def test_goal_in_output(self):
        with (
            patch("navig.spaces.briefing.collect_spaces_progress", return_value=self._spaces()),
            patch("navig.spaces.briefing.select_best_next_action", return_value=None),
        ):
            lines = build_spaces_briefing_lines()
        combined = "\n".join(lines)
        assert "Deploy infra" in combined

    def test_max_items_respected(self):
        spaces = [_FakeSpaceRow(f"sp{i}", "global", float(i * 10), f"Goal {i}") for i in range(10)]
        with (
            patch("navig.spaces.briefing.collect_spaces_progress", return_value=spaces),
            patch("navig.spaces.briefing.select_best_next_action", return_value=None),
        ):
            lines = build_spaces_briefing_lines(max_items=3)
        # header + 3 space lines = 4
        assert len(lines) == 4

    def test_action_focus_when_action_present(self):
        action = _FakeAction("devops", "global", "Deploy app")
        with (
            patch("navig.spaces.briefing.collect_spaces_progress", return_value=self._spaces()),
            patch("navig.spaces.briefing.select_best_next_action", return_value=action),
        ):
            lines = build_spaces_briefing_lines()
        combined = "\n".join(lines)
        assert "*Action Focus:*" in combined
        assert "Deploy app" in combined

    def test_action_focus_default_task_when_next_task_none(self):
        action = _FakeAction("devops", "global", next_task=None)
        with (
            patch("navig.spaces.briefing.collect_spaces_progress", return_value=self._spaces()),
            patch("navig.spaces.briefing.select_best_next_action", return_value=action),
        ):
            lines = build_spaces_briefing_lines()
        combined = "\n".join(lines)
        assert "Define next concrete task" in combined

    def test_no_action_focus_when_action_none(self):
        with (
            patch("navig.spaces.briefing.collect_spaces_progress", return_value=self._spaces()),
            patch("navig.spaces.briefing.select_best_next_action", return_value=None),
        ):
            lines = build_spaces_briefing_lines()
        combined = "\n".join(lines)
        assert "*Action Focus:*" not in combined

    def test_single_space(self):
        single = [_FakeSpaceRow("solo", "project", 50.0, "Solo goal")]
        with (
            patch("navig.spaces.briefing.collect_spaces_progress", return_value=single),
            patch("navig.spaces.briefing.select_best_next_action", return_value=None),
        ):
            lines = build_spaces_briefing_lines()
        assert len(lines) == 2  # header + 1 item
        assert "solo" in lines[1]

    def test_scope_in_output(self):
        with (
            patch("navig.spaces.briefing.collect_spaces_progress", return_value=self._spaces()),
            patch("navig.spaces.briefing.select_best_next_action", return_value=None),
        ):
            lines = build_spaces_briefing_lines()
        combined = "\n".join(lines)
        assert "global" in combined
        assert "project" in combined
