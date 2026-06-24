"""Batch 62 — permissions/rules, registry/meta, importers/safari."""
from __future__ import annotations

import plistlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig.permissions.rules — PermissionRule.matches, PermissionDecision
# ---------------------------------------------------------------------------

class TestPermissionRule:
    def _make(self, action="allow", tool="bash", pattern="*", **kw):
        from navig.permissions.rules import PermissionRule, RuleAction
        return PermissionRule(
            action=RuleAction(action), tool=tool, pattern=pattern, **kw
        )

    def test_wildcard_tool_matches_anything(self):
        rule = self._make(tool="*", pattern="*")
        assert rule.matches("bash", "ls -la")
        assert rule.matches("python", "import sys")

    def test_exact_tool_match_case_insensitive(self):
        rule = self._make(tool="bash", pattern="*")
        assert rule.matches("bash", "cmd")
        assert rule.matches("BASH", "cmd")
        assert rule.matches("Bash", "cmd")

    def test_tool_prefix_match(self):
        # "Bash" matches "BashTool"
        rule = self._make(tool="Bash", pattern="*")
        assert rule.matches("BashTool", "cmd")

    def test_tool_reverse_prefix_match(self):
        # "BashTool" matches "Bash" via reverse prefix
        rule = self._make(tool="BashTool", pattern="*")
        assert rule.matches("Bash", "cmd")

    def test_tool_no_match(self):
        rule = self._make(tool="python", pattern="*")
        assert not rule.matches("bash", "any")

    def test_wildcard_pattern_matches_any_input(self):
        rule = self._make(tool="bash", pattern="*")
        assert rule.matches("bash", "rm -rf /")
        assert rule.matches("bash", "")

    def test_empty_pattern_matches_any_input(self):
        rule = self._make(tool="bash", pattern="")
        assert rule.matches("bash", "anything")

    def test_glob_pattern(self):
        rule = self._make(tool="bash", pattern="rm -rf /tmp/*")
        assert rule.matches("bash", "rm -rf /tmp/something")
        assert not rule.matches("bash", "ls /home")

    def test_substring_fallback(self):
        rule = self._make(tool="bash", pattern="dangerous")
        assert rule.matches("bash", "run dangerous command")

    def test_input_not_matching_pattern(self):
        rule = self._make(tool="bash", pattern="secret_pattern")
        assert not rule.matches("bash", "harmless command")

    def test_frozen_dataclass(self):
        rule = self._make()
        with pytest.raises((AttributeError, TypeError)):
            rule.tool = "other"  # type: ignore[misc]


class TestPermissionDecision:
    def test_defaults(self):
        from navig.permissions.rules import PermissionDecision
        d = PermissionDecision()
        assert d.denied is False
        assert d.reason == ""
        assert d.matching_rule is None

    def test_denied_with_reason(self):
        from navig.permissions.rules import PermissionDecision, PermissionRule, RuleAction
        rule = PermissionRule(action=RuleAction.DENY, tool="bash", pattern="rm *")
        d = PermissionDecision(denied=True, reason="blocked", matching_rule=rule)
        assert d.denied is True
        assert d.reason == "blocked"
        assert d.matching_rule is rule


# ---------------------------------------------------------------------------
# navig.registry.meta — command_meta decorator, get_meta_for_callback
# ---------------------------------------------------------------------------

class TestCommandMeta:
    def test_decorator_attaches_meta(self):
        from navig.registry.meta import command_meta, _META_ATTR
        @command_meta(summary="test cmd", status="stable", since="1.0")
        def my_fn():
            pass
        assert hasattr(my_fn, _META_ATTR)

    def test_meta_summary_correct(self):
        from navig.registry.meta import command_meta, _META_ATTR
        @command_meta(summary="do something", status="beta", since="0.5")
        def my_fn2():
            pass
        meta = getattr(my_fn2, _META_ATTR)
        assert meta.summary == "do something"

    def test_meta_status_correct(self):
        from navig.registry.meta import command_meta, _META_ATTR
        @command_meta(summary="x", status="experimental", since="0.1")
        def my_fn3():
            pass
        meta = getattr(my_fn3, _META_ATTR)
        assert meta.status == "experimental"

    def test_decorator_returns_function(self):
        from navig.registry.meta import command_meta
        @command_meta(summary="fn", status="stable", since="1.0")
        def my_fn4():
            return 42
        assert my_fn4() == 42

    def test_with_deprecated_info(self):
        from navig.registry.meta import command_meta, _META_ATTR
        @command_meta(
            summary="old cmd", status="deprecated", since="0.1",
            deprecated={"since": "0.9", "remove_after": "2.0", "replaced_by": "new-cmd"},
        )
        def my_fn5():
            pass
        meta = getattr(my_fn5, _META_ATTR)
        assert meta.deprecated is not None
        assert meta.deprecated.replaced_by == "new-cmd"

    def test_no_deprecated_is_none(self):
        from navig.registry.meta import command_meta, _META_ATTR
        @command_meta(summary="cmd", status="stable", since="1.0")
        def my_fn6():
            pass
        meta = getattr(my_fn6, _META_ATTR)
        assert meta.deprecated is None


class TestGetMetaForCallback:
    def test_returns_meta_for_decorated_fn(self):
        from navig.registry.meta import command_meta, get_meta_for_callback
        @command_meta(summary="lookup test", status="stable", since="1.0")
        def my_lookup():
            pass
        meta = get_meta_for_callback(my_lookup)
        assert meta is not None
        assert meta.summary == "lookup test"

    def test_returns_none_for_undecorated(self):
        from navig.registry.meta import get_meta_for_callback
        def plain_fn():
            pass
        assert get_meta_for_callback(plain_fn) is None

    def test_returns_none_for_none_input(self):
        from navig.registry.meta import get_meta_for_callback
        assert get_meta_for_callback(None) is None


# ---------------------------------------------------------------------------
# navig.importers.sources.safari — SafariImporter._walk, parse, detect
# ---------------------------------------------------------------------------

class TestSafariImporterWalk:
    def _make_importer(self):
        from navig.importers.sources.safari import SafariImporter
        return SafariImporter()

    def test_walk_empty_list(self):
        imp = self._make_importer()
        items = []
        imp._walk(children=[], folder_path=[], items=items)
        assert items == []

    def test_walk_single_bookmark(self):
        imp = self._make_importer()
        items = []
        imp._walk(
            children=[{"Title": "Google", "URLString": "https://google.com"}],
            folder_path=[],
            items=items,
        )
        assert len(items) == 1
        assert items[0].value == "https://google.com"
        assert items[0].label == "Google"

    def test_walk_url_without_title_uses_url_as_label(self):
        imp = self._make_importer()
        items = []
        imp._walk(
            children=[{"URLString": "https://example.com"}],
            folder_path=[],
            items=items,
        )
        assert items[0].label == "https://example.com"

    def test_walk_nested_children(self):
        imp = self._make_importer()
        items = []
        imp._walk(
            children=[{
                "Title": "Folder",
                "Children": [{"Title": "Sub", "URLString": "https://sub.com"}],
            }],
            folder_path=[],
            items=items,
        )
        assert len(items) == 1
        assert items[0].meta["folder"] == "Folder"

    def test_walk_folder_path_propagated(self):
        imp = self._make_importer()
        items = []
        imp._walk(
            children=[{"Title": "Page", "URLString": "https://x.com"}],
            folder_path=["Root", "Sub"],
            items=items,
        )
        assert items[0].meta["folder"] == "Root/Sub"

    def test_walk_node_without_url_not_added(self):
        imp = self._make_importer()
        items = []
        imp._walk(
            children=[{"Title": "FolderOnly"}],
            folder_path=[],
            items=items,
        )
        assert items == []


class TestSafariImporterParse:
    def _make_importer(self):
        from navig.importers.sources.safari import SafariImporter
        return SafariImporter()

    def test_parse_missing_file_returns_empty(self, tmp_path):
        imp = self._make_importer()
        result = imp.parse(str(tmp_path / "nonexistent.plist"))
        assert result == []

    def test_parse_invalid_plist_returns_empty(self, tmp_path):
        bad = tmp_path / "bad.plist"
        bad.write_bytes(b"not a plist")
        imp = self._make_importer()
        result = imp.parse(str(bad))
        assert result == []

    def test_parse_valid_plist_returns_items(self, tmp_path):
        payload = {
            "Children": [
                {"Title": "NAVIG", "URLString": "https://navig.dev"}
            ]
        }
        plist_file = tmp_path / "Bookmarks.plist"
        with plist_file.open("wb") as f:
            plistlib.dump(payload, f)

        imp = self._make_importer()
        result = imp.parse(str(plist_file))
        assert len(result) == 1
        assert result[0].value == "https://navig.dev"

    def test_detect_false_when_no_default_path(self):
        imp = self._make_importer()
        with patch.object(imp, "default_path", return_value=None):
            assert imp.detect() is False

    def test_detect_false_when_path_not_exists(self, tmp_path):
        imp = self._make_importer()
        missing = str(tmp_path / "missing.plist")
        with patch.object(imp, "default_path", return_value=missing):
            assert imp.detect() is False
