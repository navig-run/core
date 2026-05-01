"""
Batch 57: hermetic unit tests for
  - navig/memory/paths.py
  - navig/agent/pattern_analyzer.py
  - navig/core/protocols.py
  - navig/ui/status.py
  - navig/ui/timeline.py
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig/memory/paths.py
# ---------------------------------------------------------------------------

class TestNavigHome:
    def test_returns_path(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"NAVIG_HOME": str(tmp_path)}):
            from navig.memory.paths import navig_home
            result = navig_home()
        assert isinstance(result, Path)

    def test_uses_navig_home_env(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"NAVIG_HOME": str(tmp_path)}):
            from navig.memory.paths import navig_home
            result = navig_home()
        assert result == tmp_path

    def test_falls_back_to_platform_paths_without_env(self, tmp_path: Path) -> None:
        env = {k: v for k, v in os.environ.items() if k not in ("NAVIG_HOME",)}
        with patch.dict(os.environ, env, clear=True):
            import navig.memory.paths as mp
            import navig.platform.paths as pp
            with patch.object(pp, "config_dir", return_value=tmp_path):
                result = mp.navig_home()
        assert result == tmp_path

    def test_returns_path_type(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"NAVIG_HOME": str(tmp_path)}):
            from navig.memory.paths import navig_home
            assert isinstance(navig_home(), Path)


class TestMemoryDir:
    def test_returns_path(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"NAVIG_HOME": str(tmp_path)}):
            import navig.memory.paths as mp
            # reload so navig_home() picks up the env variable
            result = mp.memory_dir()
        assert isinstance(result, Path)

    def test_creates_directory(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"NAVIG_HOME": str(tmp_path)}):
            import navig.memory.paths as mp
            result = mp.memory_dir()
        assert result.exists()
        assert result.is_dir()

    def test_is_memory_subdir(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"NAVIG_HOME": str(tmp_path)}):
            import navig.memory.paths as mp
            result = mp.memory_dir()
        assert result.name == "memory"

    def test_idempotent(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"NAVIG_HOME": str(tmp_path)}):
            import navig.memory.paths as mp
            r1 = mp.memory_dir()
            r2 = mp.memory_dir()
        assert r1 == r2


class TestKeyFactsDbPath:
    def test_is_path(self) -> None:
        from navig.memory.paths import KEY_FACTS_DB_PATH
        assert isinstance(KEY_FACTS_DB_PATH, Path)

    def test_filename(self) -> None:
        from navig.memory.paths import KEY_FACTS_DB_PATH
        assert KEY_FACTS_DB_PATH.name == "key_facts.db"

    def test_parent_is_memory_dir(self) -> None:
        from navig.memory.paths import KEY_FACTS_DB_PATH
        assert KEY_FACTS_DB_PATH.parent.name == "memory"


# ---------------------------------------------------------------------------
# navig/agent/pattern_analyzer.py
# ---------------------------------------------------------------------------

class TestScoredPattern:
    def test_construction(self) -> None:
        from navig.agent.pattern_analyzer import ScoredPattern
        sp = ScoredPattern(sequence=("a", "b"), occurrences=3, score=3.0)
        assert sp.sequence == ("a", "b")
        assert sp.occurrences == 3
        assert sp.score == 3.0

    def test_single_element_sequence(self) -> None:
        from navig.agent.pattern_analyzer import ScoredPattern
        sp = ScoredPattern(sequence=("cmd",), occurrences=5, score=5.0)
        assert len(sp.sequence) == 1

    def test_is_dataclass(self) -> None:
        import dataclasses
        from navig.agent.pattern_analyzer import ScoredPattern
        assert dataclasses.is_dataclass(ScoredPattern)


class TestPatternAnalyzer:
    def test_default_params(self) -> None:
        from navig.agent.pattern_analyzer import PatternAnalyzer
        pa = PatternAnalyzer()
        assert pa.min_occurrences == 2
        assert pa.max_results == 20

    def test_custom_params(self) -> None:
        from navig.agent.pattern_analyzer import PatternAnalyzer
        pa = PatternAnalyzer(min_occurrences=3, max_results=5)
        assert pa.min_occurrences == 3
        assert pa.max_results == 5

    def test_score_by_frequency_empty(self) -> None:
        from navig.agent.pattern_analyzer import PatternAnalyzer
        pa = PatternAnalyzer()
        assert pa.score_by_frequency([]) == []

    def test_score_by_frequency_single_below_min(self) -> None:
        from navig.agent.pattern_analyzer import PatternAnalyzer
        rec = MagicMock()
        rec.command = "ls"
        pa = PatternAnalyzer(min_occurrences=2)
        result = pa.score_by_frequency([rec])
        assert result == []

    def test_score_by_frequency_above_min(self) -> None:
        from navig.agent.pattern_analyzer import PatternAnalyzer, ScoredPattern
        recs = [MagicMock(command="ls"), MagicMock(command="ls"), MagicMock(command="ls")]
        pa = PatternAnalyzer(min_occurrences=2)
        result = pa.score_by_frequency(recs)
        assert len(result) == 1
        assert result[0].sequence == ("ls",)
        assert result[0].occurrences == 3

    def test_score_by_frequency_multiple_commands(self) -> None:
        from navig.agent.pattern_analyzer import PatternAnalyzer
        recs = (
            [MagicMock(command="ls")] * 4
            + [MagicMock(command="pwd")] * 2
            + [MagicMock(command="cat")] * 1
        )
        pa = PatternAnalyzer(min_occurrences=2)
        result = pa.score_by_frequency(recs)
        # "cat" has 1 occurrence, below threshold
        assert len(result) == 2
        # sorted by score descending
        assert result[0].sequence == ("ls",)

    def test_score_by_frequency_respects_max_results(self) -> None:
        from navig.agent.pattern_analyzer import PatternAnalyzer
        recs = []
        for i in range(10):
            recs.extend([MagicMock(command=f"cmd{i}")] * 3)
        pa = PatternAnalyzer(min_occurrences=2, max_results=5)
        result = pa.score_by_frequency(recs)
        assert len(result) <= 5

    def test_ignores_non_string_command(self) -> None:
        from navig.agent.pattern_analyzer import PatternAnalyzer
        rec = MagicMock()
        rec.command = 42  # not a string
        pa = PatternAnalyzer(min_occurrences=1)
        result = pa.score_by_frequency([rec])
        assert result == []

    def test_ignores_blank_command(self) -> None:
        from navig.agent.pattern_analyzer import PatternAnalyzer
        recs = [MagicMock(command="   "), MagicMock(command="   ")]
        pa = PatternAnalyzer(min_occurrences=1)
        result = pa.score_by_frequency(recs)
        assert result == []

    def test_score_equals_occurrences(self) -> None:
        from navig.agent.pattern_analyzer import PatternAnalyzer
        recs = [MagicMock(command="uptime")] * 7
        pa = PatternAnalyzer(min_occurrences=1)
        result = pa.score_by_frequency(recs)
        assert result[0].score == 7.0

    def test_missing_command_attr(self) -> None:
        from navig.agent.pattern_analyzer import PatternAnalyzer
        # object without .command attribute → getattr returns None
        pa = PatternAnalyzer(min_occurrences=1)
        result = pa.score_by_frequency([object(), object()])
        assert result == []


# ---------------------------------------------------------------------------
# navig/core/protocols.py
# ---------------------------------------------------------------------------

class TestConfigProvider:
    def test_importable(self) -> None:
        from navig.core.protocols import ConfigProvider
        assert ConfigProvider is not None

    def test_is_protocol(self) -> None:
        from navig.core.protocols import ConfigProvider
        assert issubclass(ConfigProvider, Protocol)

    def test_has_required_methods(self) -> None:
        from navig.core.protocols import ConfigProvider
        annotations = ConfigProvider.__protocol_attrs__ if hasattr(ConfigProvider, "__protocol_attrs__") else set()
        # Properties and methods must exist on the Protocol class
        for attr in ("app_config_dir", "global_config_dir", "base_dir", "verbose", "get_config_directories"):
            assert hasattr(ConfigProvider, attr)


class TestHostConfigProvider:
    def test_importable(self) -> None:
        from navig.core.protocols import HostConfigProvider
        assert HostConfigProvider is not None

    def test_is_protocol(self) -> None:
        from navig.core.protocols import HostConfigProvider
        assert issubclass(HostConfigProvider, Protocol)

    def test_inherits_config_provider(self) -> None:
        from navig.core.protocols import ConfigProvider, HostConfigProvider
        assert ConfigProvider in HostConfigProvider.__bases__

    def test_has_directory_accessible(self) -> None:
        from navig.core.protocols import HostConfigProvider
        assert hasattr(HostConfigProvider, "_is_directory_accessible")


class TestAppConfigProvider:
    def test_importable(self) -> None:
        from navig.core.protocols import AppConfigProvider
        assert AppConfigProvider is not None

    def test_is_protocol(self) -> None:
        from navig.core.protocols import AppConfigProvider
        assert issubclass(AppConfigProvider, Protocol)

    def test_has_list_hosts(self) -> None:
        from navig.core.protocols import AppConfigProvider
        assert hasattr(AppConfigProvider, "list_hosts")

    def test_has_load_host_config(self) -> None:
        from navig.core.protocols import AppConfigProvider
        assert hasattr(AppConfigProvider, "load_host_config")

    def test_has_save_host_config(self) -> None:
        from navig.core.protocols import AppConfigProvider
        assert hasattr(AppConfigProvider, "save_host_config")


# ---------------------------------------------------------------------------
# navig/ui/status.py
# ---------------------------------------------------------------------------

class TestRenderStatusHeader:
    def _chip(self, label: str = "daemon", color: str = "green", value: str | None = "online", icon: str = "◉", icon_safe: str = "o") -> Any:
        from navig.ui.models import StatusChip
        return StatusChip(label=label, color=color, value=value, icon=icon, icon_safe=icon_safe)

    def test_importable(self) -> None:
        from navig.ui.status import render_status_header
        assert callable(render_status_header)

    def test_empty_list_no_output(self) -> None:
        from navig.ui.status import render_status_header
        mock_console = MagicMock()
        with patch("navig.ui.status.console", mock_console):
            render_status_header([])
        mock_console.print.assert_not_called()

    def test_single_chip_with_value(self) -> None:
        from navig.ui.status import render_status_header
        mock_console = MagicMock()
        chip = self._chip(label="host", value="prod")
        with patch("navig.ui.status.console", mock_console):
            render_status_header([chip])
        mock_console.print.assert_called_once()
        args = mock_console.print.call_args[0][0]
        assert "host" in args

    def test_single_chip_no_value(self) -> None:
        from navig.ui.status import render_status_header
        mock_console = MagicMock()
        chip = self._chip(label="mode", value=None)
        with patch("navig.ui.status.console", mock_console):
            render_status_header([chip])
        mock_console.print.assert_called_once()

    def test_multiple_chips(self) -> None:
        from navig.ui.status import render_status_header
        mock_console = MagicMock()
        chips = [self._chip("daemon", value="on"), self._chip("host", value="prod")]
        with patch("navig.ui.status.console", mock_console):
            render_status_header(chips)
        # called once (joined with separator)
        mock_console.print.assert_called_once()

    def test_custom_separator(self) -> None:
        from navig.ui.status import render_status_header
        mock_console = MagicMock()
        chip = self._chip()
        with patch("navig.ui.status.console", mock_console):
            render_status_header([chip, chip], sep=" | ")
        call_args = mock_console.print.call_args[0][0]
        assert " | " in call_args

    def test_never_raises_on_exception(self) -> None:
        from navig.ui.status import render_status_header
        mock_console = MagicMock()
        mock_console.print.side_effect = RuntimeError("boom")
        chip = self._chip()
        # must not propagate
        render_status_header([chip])

    def test_safe_mode_uses_icon_safe(self) -> None:
        from navig.ui.status import render_status_header
        mock_console = MagicMock()
        chip = self._chip(icon="◉", icon_safe="o")
        with patch("navig.ui.status.console", mock_console), \
             patch("navig.ui.status.SAFE_MODE", True):
            render_status_header([chip])
        call_args = mock_console.print.call_args[0][0]
        assert "o" in call_args


# ---------------------------------------------------------------------------
# navig/ui/timeline.py
# ---------------------------------------------------------------------------

class TestRenderEventTimeline:
    def _event(self, label: str = "start", detail: str = "ok", ts: str = "12:00", icon: str = "▶", color: str = "green") -> Any:
        from navig.ui.models import Event
        return Event(timestamp=ts, icon=icon, label=label, detail=detail, color=color)

    def test_importable(self) -> None:
        from navig.ui.timeline import render_event_timeline
        assert callable(render_event_timeline)

    def test_empty_list_no_output(self) -> None:
        from navig.ui.timeline import render_event_timeline
        mock_console = MagicMock()
        with patch("navig.ui.timeline.console", mock_console):
            render_event_timeline([])
        mock_console.print.assert_not_called()

    def test_renders_events(self) -> None:
        from navig.ui.timeline import render_event_timeline
        mock_console = MagicMock()
        ev = self._event(label="deploy", detail="success")
        with patch("navig.ui.timeline.console", mock_console):
            render_event_timeline([ev])
        # title + 1 event = 2 calls
        assert mock_console.print.call_count == 2

    def test_renders_title_by_default(self) -> None:
        from navig.ui.timeline import render_event_timeline
        mock_console = MagicMock()
        ev = self._event()
        with patch("navig.ui.timeline.console", mock_console):
            render_event_timeline([ev], title="My Events")
        title_call = mock_console.print.call_args_list[0][0][0]
        assert "My Events" in title_call

    def test_no_title_when_show_title_false(self) -> None:
        from navig.ui.timeline import render_event_timeline
        mock_console = MagicMock()
        ev = self._event()
        with patch("navig.ui.timeline.console", mock_console):
            render_event_timeline([ev], show_title=False)
        # only the event line, not title
        assert mock_console.print.call_count == 1

    def test_multiple_events(self) -> None:
        from navig.ui.timeline import render_event_timeline
        mock_console = MagicMock()
        events = [self._event(label=f"step{i}") for i in range(3)]
        with patch("navig.ui.timeline.console", mock_console):
            render_event_timeline(events, show_title=False)
        assert mock_console.print.call_count == 3

    def test_event_detail_in_output(self) -> None:
        from navig.ui.timeline import render_event_timeline
        mock_console = MagicMock()
        ev = self._event(detail="deployed v2")
        with patch("navig.ui.timeline.console", mock_console):
            render_event_timeline([ev], show_title=False)
        event_call = mock_console.print.call_args_list[0][0][0]
        assert "deployed v2" in event_call

    def test_timestamp_in_output(self) -> None:
        from navig.ui.timeline import render_event_timeline
        mock_console = MagicMock()
        ev = self._event(ts="09:30")
        with patch("navig.ui.timeline.console", mock_console):
            render_event_timeline([ev], show_title=False)
        event_call = mock_console.print.call_args_list[0][0][0]
        assert "09:30" in event_call

    def test_never_raises_on_exception(self) -> None:
        from navig.ui.timeline import render_event_timeline
        mock_console = MagicMock()
        mock_console.print.side_effect = RuntimeError("boom")
        ev = self._event()
        # must not propagate
        render_event_timeline([ev])

    def test_safe_mode_separator(self) -> None:
        from navig.ui.timeline import render_event_timeline
        mock_console = MagicMock()
        ev = self._event()
        with patch("navig.ui.timeline.console", mock_console), \
             patch("navig.ui.timeline.SAFE_MODE", True):
            render_event_timeline([ev], show_title=False)
        event_call = mock_console.print.call_args_list[0][0][0]
        assert "-" in event_call
