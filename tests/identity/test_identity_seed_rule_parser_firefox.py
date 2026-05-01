"""Batch 64 — identity/seed, permissions/rule_parser, importers/firefox."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# navig.identity.seed
# ---------------------------------------------------------------------------

class TestGenerateSeed:
    def test_returns_string(self):
        from navig.identity.seed import generate_seed
        assert isinstance(generate_seed(), str)

    def test_returns_64_hex_chars(self):
        from navig.identity.seed import generate_seed
        seed = generate_seed()
        assert len(seed) == 64
        assert all(c in "0123456789abcdef" for c in seed)

    def test_deterministic_same_call(self):
        from navig.identity.seed import generate_seed
        s1 = generate_seed()
        s2 = generate_seed()
        assert s1 == s2

    def test_fallback_to_uuid_when_no_attributes(self):
        """When all attribute collection fails, still returns a 32-char hex string."""
        from navig.identity.seed import generate_seed
        import uuid
        with (
            patch("navig.identity.seed.uuid.getnode", side_effect=Exception),
            patch("navig.identity.seed.platform.node", side_effect=Exception),
            patch("navig.identity.seed._get_username", side_effect=Exception),
            patch("navig.identity.seed.platform.system", side_effect=Exception),
            patch("navig.identity.seed.uuid.uuid4", return_value=uuid.UUID("12345678123412341234123456789abc")),
        ):
            seed = generate_seed()
        assert isinstance(seed, str)
        assert len(seed) == 32  # uuid4().hex

    def test_sha256_used_for_normal_path(self):
        from navig.identity.seed import generate_seed
        import hashlib
        with (
            patch("navig.identity.seed.uuid.getnode", return_value=123456),
            patch("navig.identity.seed.platform.node", return_value="myhost"),
            patch("navig.identity.seed._get_username", return_value="alice"),
            patch("navig.identity.seed.platform.system", return_value="Linux"),
        ):
            seed = generate_seed()
        expected = hashlib.sha256("123456myhostaliceLinux".encode()).hexdigest()
        assert seed == expected


class TestGetUsername:
    def test_returns_string(self):
        from navig.identity.seed import _get_username
        assert isinstance(_get_username(), str)

    def test_uses_env_when_getlogin_fails(self):
        from navig.identity.seed import _get_username
        with (
            patch("navig.identity.seed.os.getlogin", side_effect=OSError),
            patch.dict(os.environ, {"USERNAME": "testuser"}),
        ):
            result = _get_username()
        assert result == "testuser"

    def test_fallback_to_operator(self):
        from navig.identity.seed import _get_username
        clean_env = {k: v for k, v in os.environ.items() if k not in ("USERNAME", "USER", "LOGNAME")}
        with (
            patch("navig.identity.seed.os.getlogin", side_effect=OSError),
            patch.dict(os.environ, clean_env, clear=True),
        ):
            result = _get_username()
        assert result == "operator"


# ---------------------------------------------------------------------------
# navig.permissions.rule_parser
# ---------------------------------------------------------------------------

class TestParseRuleSpec:
    def _parse(self, action, spec, source=""):
        from navig.permissions.rule_parser import parse_rule_spec
        return parse_rule_spec(action, spec, source)

    def test_wildcard_spec(self):
        rule = self._parse("allow", "*")
        assert rule is not None
        assert rule.tool == "*"
        assert rule.pattern == "*"

    def test_allow_action(self):
        from navig.permissions.rules import RuleAction
        rule = self._parse("allow", "Bash(git commit:*)")
        assert rule.action == RuleAction.ALLOW

    def test_deny_action(self):
        from navig.permissions.rules import RuleAction
        rule = self._parse("deny", "Bash(rm *)")
        assert rule.action == RuleAction.DENY

    def test_case_insensitive_action(self):
        rule = self._parse("ALLOW", "Bash(ls)")
        assert rule is not None

    def test_unknown_action_returns_none(self):
        assert self._parse("block", "Bash(*)") is None

    def test_empty_spec_returns_none(self):
        assert self._parse("allow", "") is None

    def test_bash_tool_normalised(self):
        rule = self._parse("allow", "BashTool(ls -la)")
        assert rule.tool == "bash"

    def test_bash_normalised(self):
        rule = self._parse("allow", "Bash(ls -la)")
        assert rule.tool == "bash"

    def test_pattern_extracted(self):
        rule = self._parse("deny", "Bash(rm -rf /tmp/*)")
        assert rule.pattern == "rm -rf /tmp/*"

    def test_source_propagated(self):
        rule = self._parse("allow", "Bash(*)", source="project")
        assert rule.source == "project"

    def test_fallback_to_wildcard_tool_for_plain_glob(self):
        rule = self._parse("deny", "some.pattern")
        assert rule.tool == "*"
        assert rule.pattern == "some.pattern"


class TestNormaliseTool:
    def test_bash_tool(self):
        from navig.permissions.rule_parser import _normalise_tool
        assert _normalise_tool("BashTool") == "bash"

    def test_bash_unchanged(self):
        from navig.permissions.rule_parser import _normalise_tool
        assert _normalise_tool("Bash") == "bash"

    def test_wildcard(self):
        from navig.permissions.rule_parser import _normalise_tool
        assert _normalise_tool("*") == "*"

    def test_custom_tool(self):
        from navig.permissions.rule_parser import _normalise_tool
        assert _normalise_tool("Python") == "python"


# ---------------------------------------------------------------------------
# navig.importers.sources.firefox — _resolve_folder_chain, parse
# ---------------------------------------------------------------------------

class TestResolveFolderChain:
    def _make_importer(self):
        from navig.importers.sources.firefox import FirefoxImporter
        return FirefoxImporter()

    def test_simple_chain(self):
        imp = self._make_importer()
        folders = {
            1: {"title": "Root", "parent": 0},
            2: {"title": "Sub", "parent": 1},
        }
        result = imp._resolve_folder_chain(2, folders)
        assert result == "Root/Sub"

    def test_single_level(self):
        imp = self._make_importer()
        folders = {1: {"title": "Bookmarks", "parent": 0}}
        result = imp._resolve_folder_chain(1, folders)
        assert result == "Bookmarks"

    def test_unknown_parent(self):
        imp = self._make_importer()
        result = imp._resolve_folder_chain(99, {})
        assert result == ""

    def test_no_infinite_loop_on_cycle(self):
        imp = self._make_importer()
        folders = {
            1: {"title": "A", "parent": 2},
            2: {"title": "B", "parent": 1},
        }
        # Should terminate without hanging
        result = imp._resolve_folder_chain(1, folders)
        assert isinstance(result, str)

    def test_empty_title_skipped(self):
        imp = self._make_importer()
        folders = {
            1: {"title": "", "parent": 0},
            2: {"title": "Visible", "parent": 1},
        }
        result = imp._resolve_folder_chain(2, folders)
        assert result == "Visible"


class TestFirefoxImporterParse:
    def _make_importer(self):
        from navig.importers.sources.firefox import FirefoxImporter
        return FirefoxImporter()

    def test_missing_file_returns_empty(self, tmp_path):
        imp = self._make_importer()
        result = imp.parse(str(tmp_path / "places.sqlite"))
        assert result == []

    def test_valid_db_returns_items(self, tmp_path):
        db = tmp_path / "places.sqlite"
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT, title TEXT)")
        con.execute("CREATE TABLE moz_bookmarks (id INTEGER PRIMARY KEY, fk INTEGER, parent INTEGER, title TEXT, type INTEGER)")
        con.execute("INSERT INTO moz_places VALUES (1, 'https://navig.dev', 'NAVIG')")
        con.execute("INSERT INTO moz_bookmarks VALUES (1, 1, 0, 'NAVIG', 1)")
        con.commit()
        con.close()

        imp = self._make_importer()
        result = imp.parse(str(db))
        assert len(result) == 1
        assert result[0].value == "https://navig.dev"

    def test_detect_false_when_no_path(self):
        imp = self._make_importer()
        with patch.object(imp, "default_path", return_value=None):
            assert imp.detect() is False

    def test_detect_false_when_file_missing(self, tmp_path):
        imp = self._make_importer()
        with patch.object(imp, "default_path", return_value=str(tmp_path / "missing.sqlite")):
            assert imp.detect() is False
