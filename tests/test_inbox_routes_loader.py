"""Tests for navig/inbox/routes_loader.py"""
from __future__ import annotations

from pathlib import Path

import pytest

from navig.inbox.routes_loader import (
    ChannelConfig,
    ExcludeRule,
    RoutesConfig,
    RoutesDefaults,
    _coerce_list,
    load,
    scan_sibling_spaces,
)


# ---------------------------------------------------------------------------
# _coerce_list
# ---------------------------------------------------------------------------


class TestCoerceList:
    def test_none_returns_empty(self):
        assert _coerce_list(None) == []

    def test_list_passes_through(self):
        assert _coerce_list(["a", "b"]) == ["a", "b"]

    def test_scalar_wraps_in_list(self):
        assert _coerce_list("single") == ["single"]

    def test_list_coerced_to_strings(self):
        result = _coerce_list([1, 2, 3])
        assert result == ["1", "2", "3"]

    def test_empty_list(self):
        assert _coerce_list([]) == []


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


class TestChannelConfig:
    def test_defaults(self):
        ch = ChannelConfig(id="ch1", name="Channel 1")
        assert ch.priority == "normal"
        assert ch.agents == []
        assert ch.keywords == []
        assert ch.sla is None
        assert ch.description == ""

    def test_custom_values(self):
        ch = ChannelConfig(id="ch2", name="Channel 2", priority="high", sla="24h")
        assert ch.priority == "high"
        assert ch.sla == "24h"


class TestExcludeRule:
    def test_defaults(self):
        rule = ExcludeRule()
        assert rule.min_hits == 1
        assert rule.action == "find_best_space"
        assert rule.dry_run is True
        assert rule.on_conflict == "rename"
        assert rule.on_error == "log_and_skip"

    def test_keywords_default_empty(self):
        rule = ExcludeRule()
        assert rule.keywords == []


class TestRoutesDefaults:
    def test_defaults(self):
        d = RoutesDefaults()
        assert d.sla_hours == 48
        assert d.unrouted_fallback == ""


class TestRoutesConfig:
    def test_defaults(self):
        rc = RoutesConfig()
        assert rc.channels == []
        assert rc.exclude == []
        assert isinstance(rc.defaults, RoutesDefaults)
        assert rc.spaces_root is None


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------


class TestLoad:
    def test_returns_none_when_no_routes_file(self, tmp_path):
        result = load(tmp_path)
        assert result is None

    def test_loads_empty_routes_yaml(self, tmp_path):
        routes_dir = tmp_path / ".navig" / "inbox"
        routes_dir.mkdir(parents=True)
        (routes_dir / "routes.yaml").write_text("{}", encoding="utf-8")
        result = load(tmp_path)
        assert result is not None
        assert isinstance(result, RoutesConfig)

    def test_returns_none_for_malformed_yaml(self, tmp_path):
        routes_dir = tmp_path / ".navig" / "inbox"
        routes_dir.mkdir(parents=True)
        (routes_dir / "routes.yaml").write_text(":: invalid ::\n", encoding="utf-8")
        result = load(tmp_path)
        assert result is None

    def test_returns_none_when_yaml_not_mapping(self, tmp_path):
        routes_dir = tmp_path / ".navig" / "inbox"
        routes_dir.mkdir(parents=True)
        (routes_dir / "routes.yaml").write_text("- item1\n- item2\n", encoding="utf-8")
        result = load(tmp_path)
        assert result is None

    def test_loads_channels(self, tmp_path):
        routes_dir = tmp_path / ".navig" / "inbox"
        routes_dir.mkdir(parents=True)
        (routes_dir / "routes.yaml").write_text(
            "channels:\n  - id: ch1\n    name: Channel 1\n    priority: high\n",
            encoding="utf-8",
        )
        result = load(tmp_path)
        assert result is not None
        assert len(result.channels) == 1
        assert result.channels[0].id == "ch1"
        assert result.channels[0].priority == "high"

    def test_skips_channel_without_id(self, tmp_path):
        routes_dir = tmp_path / ".navig" / "inbox"
        routes_dir.mkdir(parents=True)
        (routes_dir / "routes.yaml").write_text(
            "channels:\n  - name: No ID Channel\n",
            encoding="utf-8",
        )
        result = load(tmp_path)
        assert result is not None
        assert len(result.channels) == 0

    def test_loads_exclude_rules(self, tmp_path):
        routes_dir = tmp_path / ".navig" / "inbox"
        routes_dir.mkdir(parents=True)
        (routes_dir / "routes.yaml").write_text(
            "exclude:\n  - keywords: [spam, promo]\n    min_hits: 2\n    dry_run: false\n",
            encoding="utf-8",
        )
        result = load(tmp_path)
        assert result is not None
        assert len(result.exclude) == 1
        assert result.exclude[0].keywords == ["spam", "promo"]
        assert result.exclude[0].min_hits == 2
        assert result.exclude[0].dry_run is False

    def test_loads_defaults(self, tmp_path):
        routes_dir = tmp_path / ".navig" / "inbox"
        routes_dir.mkdir(parents=True)
        (routes_dir / "routes.yaml").write_text(
            "defaults:\n  sla_hours: 72\n  unrouted_fallback: fallback_agent\n",
            encoding="utf-8",
        )
        result = load(tmp_path)
        assert result is not None
        assert result.defaults.sla_hours == 72
        assert result.defaults.unrouted_fallback == "fallback_agent"

    def test_spaces_root_defaults_to_parent_dir(self, tmp_path):
        space = tmp_path / "myspace"
        routes_dir = space / ".navig" / "inbox"
        routes_dir.mkdir(parents=True)
        (routes_dir / "routes.yaml").write_text("{}", encoding="utf-8")
        result = load(space)
        assert result is not None
        assert result.spaces_root == tmp_path  # parent of space

    def test_explicit_spaces_root_set(self, tmp_path):
        routes_dir = tmp_path / ".navig" / "inbox"
        routes_dir.mkdir(parents=True)
        (routes_dir / "routes.yaml").write_text(
            "spaces_root: /custom/spaces\n", encoding="utf-8"
        )
        result = load(tmp_path)
        assert result is not None
        assert result.spaces_root == Path("/custom/spaces")

    def test_source_path_set(self, tmp_path):
        routes_dir = tmp_path / ".navig" / "inbox"
        routes_dir.mkdir(parents=True)
        routes_file = routes_dir / "routes.yaml"
        routes_file.write_text("{}", encoding="utf-8")
        result = load(tmp_path)
        assert result is not None
        assert result.source_path == routes_file

    def test_channels_with_agents_as_list(self, tmp_path):
        routes_dir = tmp_path / ".navig" / "inbox"
        routes_dir.mkdir(parents=True)
        (routes_dir / "routes.yaml").write_text(
            "channels:\n  - id: ch1\n    name: C1\n    agents: [agent1, agent2]\n",
            encoding="utf-8",
        )
        result = load(tmp_path)
        assert result.channels[0].agents == ["agent1", "agent2"]

    def test_skips_non_dict_channel_entries(self, tmp_path):
        routes_dir = tmp_path / ".navig" / "inbox"
        routes_dir.mkdir(parents=True)
        (routes_dir / "routes.yaml").write_text(
            "channels:\n  - id: ch1\n    name: C1\n  - just_a_string\n",
            encoding="utf-8",
        )
        result = load(tmp_path)
        # Only the dict entry should be processed; string entry skipped
        assert len(result.channels) == 1


# ---------------------------------------------------------------------------
# scan_sibling_spaces
# ---------------------------------------------------------------------------


class TestScanSiblingSpaces:
    def test_returns_empty_when_dir_does_not_exist(self, tmp_path):
        result = scan_sibling_spaces(tmp_path / "nonexistent")
        assert result == []

    def test_returns_spaces_with_navig_inbox(self, tmp_path):
        space1 = tmp_path / "space1"
        (space1 / ".navig" / "inbox").mkdir(parents=True)
        space2 = tmp_path / "space2"
        (space2 / ".navig" / "inbox").mkdir(parents=True)
        # space3 has no .navig/inbox
        (tmp_path / "space3").mkdir()
        result = scan_sibling_spaces(tmp_path)
        result_names = {p.name for p in result}
        assert "space1" in result_names
        assert "space2" in result_names
        assert "space3" not in result_names

    def test_ignores_files_not_dirs(self, tmp_path):
        (tmp_path / "readme.txt").write_text("text")
        result = scan_sibling_spaces(tmp_path)
        assert result == []

    def test_returns_empty_list_for_empty_dir(self, tmp_path):
        result = scan_sibling_spaces(tmp_path)
        assert result == []
