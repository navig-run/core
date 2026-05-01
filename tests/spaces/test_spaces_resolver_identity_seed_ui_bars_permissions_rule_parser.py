"""
Batch 64: hermetic unit tests for
  - navig/spaces/resolver.py        (_find_project_navig_root, resolve_space,
                                     discover_space_paths, get_default_space)
  - navig/identity/seed.py          (generate_seed, _get_username)
  - navig/ui/bars.py                (_make_bar, render_metric_bars, render_sparklines)
  - navig/permissions/rule_parser.py (parse_rule_spec, _normalise_tool)
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig/spaces/resolver.py
# ---------------------------------------------------------------------------

class TestFindProjectNavigRoot:
    def test_returns_none_when_no_navig_dir(self, tmp_path: Path) -> None:
        from navig.spaces.resolver import _find_project_navig_root
        # Skip when a parent already has .navig (e.g. AppData/Local/Temp on dev machines)
        for parent in [tmp_path, *tmp_path.parents]:
            if (parent / ".navig").is_dir():
                pytest.skip("parent directory already contains .navig")
        result = _find_project_navig_root(tmp_path)
        assert result is None

    def test_finds_navig_in_current_dir(self, tmp_path: Path) -> None:
        from navig.spaces.resolver import _find_project_navig_root
        navig_dir = tmp_path / ".navig"
        navig_dir.mkdir()
        result = _find_project_navig_root(tmp_path)
        assert result == navig_dir

    def test_finds_navig_in_parent(self, tmp_path: Path) -> None:
        from navig.spaces.resolver import _find_project_navig_root
        (tmp_path / ".navig").mkdir()
        child = tmp_path / "sub" / "dir"
        child.mkdir(parents=True)
        result = _find_project_navig_root(child)
        assert result == tmp_path / ".navig"


class TestResolveSpace:
    def test_returns_global_when_no_project(self, tmp_path: Path) -> None:
        from navig.spaces.resolver import resolve_space
        fake_config = tmp_path / "config"
        fake_config.mkdir()
        fake_cwd = tmp_path / "workdir"
        fake_cwd.mkdir()
        with patch("navig.spaces.resolver.paths.config_dir", return_value=fake_config):
            result = resolve_space("default", cwd=fake_cwd)
        assert result.scope == "global"

    def test_returns_project_when_project_space_exists(self, tmp_path: Path) -> None:
        from navig.spaces.resolver import resolve_space
        navig_dir = tmp_path / ".navig"
        project_space = navig_dir / "spaces" / "default"
        project_space.mkdir(parents=True)
        fake_config = tmp_path / "config"
        fake_config.mkdir()
        with patch("navig.spaces.resolver.paths.config_dir", return_value=fake_config):
            result = resolve_space("default", cwd=tmp_path)
        assert result.scope == "project"
        assert result.path == project_space

    def test_canonical_name_normalized(self, tmp_path: Path) -> None:
        from navig.spaces.resolver import resolve_space
        fake_config = tmp_path / "config"
        fake_config.mkdir()
        fake_cwd = tmp_path / "workdir"
        fake_cwd.mkdir()
        with patch("navig.spaces.resolver.paths.config_dir", return_value=fake_config):
            result = resolve_space("DevOps", cwd=fake_cwd)
        assert result.canonical_name == result.canonical_name.lower()

    def test_requested_name_preserved(self, tmp_path: Path) -> None:
        from navig.spaces.resolver import resolve_space
        fake_config = tmp_path / "config"
        fake_config.mkdir()
        fake_cwd = tmp_path / "workdir"
        fake_cwd.mkdir()
        with patch("navig.spaces.resolver.paths.config_dir", return_value=fake_config):
            result = resolve_space("DevOps", cwd=fake_cwd)
        assert result.requested_name == "DevOps"


class TestDiscoverSpacePaths:
    def test_empty_when_no_dirs(self, tmp_path: Path) -> None:
        from navig.spaces.resolver import discover_space_paths
        fake_config = tmp_path / "config"
        fake_config.mkdir()
        fake_cwd = tmp_path / "workdir"
        fake_cwd.mkdir()
        with patch("navig.spaces.resolver.paths.config_dir", return_value=fake_config):
            result = discover_space_paths(cwd=fake_cwd)
        assert result == {}

    def test_discovers_global_spaces(self, tmp_path: Path) -> None:
        from navig.spaces.resolver import discover_space_paths
        global_spaces = tmp_path / "config" / "spaces"
        (global_spaces / "default").mkdir(parents=True)
        (global_spaces / "career").mkdir(parents=True)
        fake_cwd = tmp_path / "workdir"
        fake_cwd.mkdir()
        with patch("navig.spaces.resolver.paths.config_dir", return_value=tmp_path / "config"):
            result = discover_space_paths(cwd=fake_cwd)
        assert "default" in result
        assert "career" in result

    def test_project_overrides_global(self, tmp_path: Path) -> None:
        from navig.spaces.resolver import discover_space_paths
        # Global has "default" marked global
        global_spaces = tmp_path / "config" / "spaces"
        (global_spaces / "default").mkdir(parents=True)
        # Project also has "default" — should override
        navig_dir = tmp_path / "project" / ".navig"
        project_space = navig_dir / "spaces" / "default"
        project_space.mkdir(parents=True)
        cwd = tmp_path / "project" / "sub"
        cwd.mkdir(parents=True)
        with patch("navig.spaces.resolver.paths.config_dir", return_value=tmp_path / "config"):
            result = discover_space_paths(cwd=cwd)
        assert result["default"].scope == "project"


class TestGetDefaultSpace:
    def test_returns_default_without_env(self) -> None:
        from navig.spaces.resolver import get_default_space
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NAVIG_SPACE", None)
            result = get_default_space()
        assert result == "default"

    def test_respects_navig_space_env(self) -> None:
        from navig.spaces.resolver import get_default_space
        with patch.dict(os.environ, {"NAVIG_SPACE": "career"}):
            result = get_default_space()
        assert result == "career"

    def test_normalizes_env_value(self) -> None:
        from navig.spaces.resolver import get_default_space
        with patch.dict(os.environ, {"NAVIG_SPACE": "DevOps"}):
            result = get_default_space()
        assert result == result.lower()


# ---------------------------------------------------------------------------
# navig/identity/seed.py
# ---------------------------------------------------------------------------

class TestGenerateSeed:
    def test_returns_string(self) -> None:
        from navig.identity.seed import generate_seed
        result = generate_seed()
        assert isinstance(result, str)

    def test_returns_hex_string(self) -> None:
        from navig.identity.seed import generate_seed
        result = generate_seed()
        assert all(c in "0123456789abcdef" for c in result)

    def test_length_64(self) -> None:
        from navig.identity.seed import generate_seed
        result = generate_seed()
        # sha256 -> 64 hex chars; uuid4.hex -> 32; either is acceptable
        assert len(result) in (64, 32)

    def test_deterministic_on_same_inputs(self) -> None:
        from navig.identity.seed import generate_seed
        import hashlib
        raw = "12345678901234kernel:5.0johndoeLinux"
        expected = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        with (
            patch("uuid.getnode", return_value=12345678901234),
            patch("platform.node", return_value="kernel:5.0"),
            patch("navig.identity.seed._get_username", return_value="johndoe"),
            patch("platform.system", return_value="Linux"),
        ):
            result = generate_seed()
        assert result == expected

    def test_falls_back_to_random_on_all_failures(self) -> None:
        from navig.identity.seed import generate_seed
        def _raise(*a, **kw):
            raise OSError("no hardware")
        with (
            patch("uuid.getnode", side_effect=_raise),
            patch("platform.node", side_effect=_raise),
            patch("navig.identity.seed._get_username", side_effect=_raise),
            patch("platform.system", side_effect=_raise),
        ):
            result = generate_seed()
        assert isinstance(result, str) and len(result) > 0


class TestGetUsername:
    def test_returns_string(self) -> None:
        from navig.identity.seed import _get_username
        result = _get_username()
        assert isinstance(result, str) and result

    def test_returns_env_username_on_os_login_fail(self) -> None:
        from navig.identity.seed import _get_username
        with (
            patch("os.getlogin", side_effect=OSError),
            patch.dict(os.environ, {"USERNAME": "testuser"}),
        ):
            result = _get_username()
        assert result == "testuser"

    def test_falls_back_to_operator(self) -> None:
        from navig.identity.seed import _get_username
        env_without_user = {k: v for k, v in os.environ.items()
                            if k not in ("USERNAME", "USER", "LOGNAME")}
        with (
            patch("os.getlogin", side_effect=OSError),
            patch.dict(os.environ, env_without_user, clear=True),
        ):
            result = _get_username()
        assert result == "operator"


# ---------------------------------------------------------------------------
# navig/ui/bars.py  (_make_bar — pure function, no console I/O)
# ---------------------------------------------------------------------------

class TestMakeBar:
    def test_importable(self) -> None:
        from navig.ui.bars import _make_bar
        assert callable(_make_bar)

    def test_returns_two_strings(self) -> None:
        from navig.ui.bars import _make_bar
        filled, empty = _make_bar(0.5)
        assert isinstance(filled, str)
        assert isinstance(empty, str)

    def test_total_width_correct(self) -> None:
        from navig.ui.bars import _make_bar, _BAR_WIDTH
        for fill in (0.0, 0.25, 0.5, 0.75, 1.0):
            filled, empty = _make_bar(fill)
            # Safe mode: '#' and '.'; Rich mode: block chars — each is one char
            assert len(filled) + len(empty) == _BAR_WIDTH

    def test_full_fill(self) -> None:
        from navig.ui.bars import _make_bar, _BAR_WIDTH
        filled, empty = _make_bar(1.0)
        assert len(filled) == _BAR_WIDTH
        assert empty == ""

    def test_empty_fill(self) -> None:
        from navig.ui.bars import _make_bar, _BAR_WIDTH
        filled, empty = _make_bar(0.0)
        assert filled == ""
        assert len(empty) == _BAR_WIDTH

    def test_clamps_below_zero(self) -> None:
        from navig.ui.bars import _make_bar, _BAR_WIDTH
        filled, empty = _make_bar(-1.0)
        assert filled == ""
        assert len(empty) == _BAR_WIDTH

    def test_clamps_above_one(self) -> None:
        from navig.ui.bars import _make_bar, _BAR_WIDTH
        filled, empty = _make_bar(99.0)
        assert len(filled) == _BAR_WIDTH
        assert empty == ""

    def test_custom_width(self) -> None:
        from navig.ui.bars import _make_bar
        filled, empty = _make_bar(0.5, width=10)
        assert len(filled) + len(empty) == 10


class TestRenderMetricBars:
    def _metric(self, label="CPU", value="50%", fill=0.5, sparkline=None, color="cyan"):
        from navig.ui.models import Metric
        return Metric(label=label, value=value, bar_fill=fill,
                      sparkline=sparkline, color=color)

    def test_does_not_raise_on_empty_list(self) -> None:
        from navig.ui.bars import render_metric_bars
        with patch("navig.ui.bars.console") as mock_console:
            render_metric_bars([])
        mock_console.print.assert_not_called()

    def test_calls_console_print(self) -> None:
        from navig.ui.bars import render_metric_bars
        metrics = [self._metric()]
        with patch("navig.ui.bars.console") as mock_console:
            render_metric_bars(metrics)
        assert mock_console.print.called

    def test_prints_title(self) -> None:
        from navig.ui.bars import render_metric_bars
        metrics = [self._metric()]
        printed = []
        with patch("navig.ui.bars.console") as mock_console:
            mock_console.print.side_effect = lambda *a, **kw: printed.append(a)
            render_metric_bars(metrics, title="TestTitle")
        assert any("TestTitle" in str(args) for args in printed)

    def test_sparkline_in_output(self) -> None:
        from navig.ui.bars import render_metric_bars
        metrics = [self._metric(sparkline="▂▃▅▇")]
        printed = []
        with patch("navig.ui.bars.console") as mock_console:
            mock_console.print.side_effect = lambda *a, **kw: printed.append(str(a))
            render_metric_bars(metrics)
        assert any("▂▃▅▇" in s for s in printed)


class TestRenderSparklines:
    def _metric(self, label="CPU", value="50%", fill=0.5, sparkline=None, color="cyan"):
        from navig.ui.models import Metric
        return Metric(label=label, value=value, bar_fill=fill,
                      sparkline=sparkline, color=color)

    def test_skips_metrics_without_sparkline(self) -> None:
        from navig.ui.bars import render_sparklines
        metrics = [self._metric()]  # no sparkline
        with patch("navig.ui.bars.console") as mock_console:
            render_sparklines(metrics)
        mock_console.print.assert_not_called()

    def test_renders_sparkline_metrics(self) -> None:
        from navig.ui.bars import render_sparklines
        metrics = [self._metric(sparkline="▂▄▆")]
        with patch("navig.ui.bars.console") as mock_console:
            render_sparklines(metrics)
        assert mock_console.print.called


# ---------------------------------------------------------------------------
# navig/permissions/rule_parser.py
# ---------------------------------------------------------------------------

class TestNormaliseTool:
    def test_lowercases(self) -> None:
        from navig.permissions.rule_parser import _normalise_tool
        assert _normalise_tool("Bash") == "bash"

    def test_strips_tool_suffix(self) -> None:
        from navig.permissions.rule_parser import _normalise_tool
        assert _normalise_tool("BashTool") == "bash"

    def test_strips_tool_suffix_case_insensitive(self) -> None:
        from navig.permissions.rule_parser import _normalise_tool
        assert _normalise_tool("FileTool") == "file"

    def test_wildcard_unchanged(self) -> None:
        from navig.permissions.rule_parser import _normalise_tool
        assert _normalise_tool("*") == "*"

    def test_empty_string_returns_wildcard(self) -> None:
        from navig.permissions.rule_parser import _normalise_tool
        # "tool" suffix stripped from "tool" leaves "" → "*"
        result = _normalise_tool("tool")
        assert result == "*"


class TestParseRuleSpec:
    def test_importable(self) -> None:
        from navig.permissions.rule_parser import parse_rule_spec
        assert callable(parse_rule_spec)

    def test_invalid_action_returns_none(self) -> None:
        from navig.permissions.rule_parser import parse_rule_spec
        assert parse_rule_spec("run", "Bash(ls)") is None

    def test_empty_spec_returns_none(self) -> None:
        from navig.permissions.rule_parser import parse_rule_spec
        assert parse_rule_spec("allow", "") is None
        assert parse_rule_spec("allow", "   ") is None

    def test_wildcard_spec(self) -> None:
        from navig.permissions.rule_parser import parse_rule_spec
        from navig.permissions.rules import RuleAction
        rule = parse_rule_spec("allow", "*")
        assert rule is not None
        assert rule.action == RuleAction.ALLOW
        assert rule.tool == "*"
        assert rule.pattern == "*"

    def test_simple_tool_spec(self) -> None:
        from navig.permissions.rule_parser import parse_rule_spec
        from navig.permissions.rules import RuleAction
        rule = parse_rule_spec("deny", "Bash(rm -rf /tmp/*)")
        assert rule is not None
        assert rule.action == RuleAction.DENY
        assert rule.tool == "bash"
        assert rule.pattern == "rm -rf /tmp/*"

    def test_tool_suffix_normalised(self) -> None:
        from navig.permissions.rule_parser import parse_rule_spec
        rule = parse_rule_spec("allow", "BashTool(git commit:*)")
        assert rule is not None
        assert rule.tool == "bash"

    def test_allow_action(self) -> None:
        from navig.permissions.rule_parser import parse_rule_spec
        from navig.permissions.rules import RuleAction
        rule = parse_rule_spec("ALLOW", "File(read.txt)")
        assert rule is not None
        assert rule.action == RuleAction.ALLOW

    def test_deny_action(self) -> None:
        from navig.permissions.rule_parser import parse_rule_spec
        from navig.permissions.rules import RuleAction
        rule = parse_rule_spec("DENY", "File(delete.txt)")
        assert rule is not None
        assert rule.action == RuleAction.DENY

    def test_source_preserved(self) -> None:
        from navig.permissions.rule_parser import parse_rule_spec
        rule = parse_rule_spec("allow", "Bash(ls)", source="global")
        assert rule is not None
        assert rule.source == "global"

    def test_fallback_glob_no_parens(self) -> None:
        from navig.permissions.rule_parser import parse_rule_spec
        # No parens → fallback: tool="*", pattern=spec
        rule = parse_rule_spec("allow", "rm -rf")
        assert rule is not None
        assert rule.tool == "*"
        assert rule.pattern == "rm -rf"
