"""
Batch 84 — navig/core/renderer.py
Tests for BlockType enum, progress_bar, renderBlock (stdout capture).
"""
import pytest

from navig.core.renderer import (
    BlockType,
    DIVIDER,
    _STYLES,
    progress_bar,
    renderBlock,
    renderMetric,
)


# ---------------------------------------------------------------------------
# BlockType enum
# ---------------------------------------------------------------------------


class TestBlockTypeEnum:
    def test_error_value(self):
        assert BlockType.ERROR == "ERROR"

    def test_success_value(self):
        assert BlockType.SUCCESS == "SUCCESS"

    def test_warning_value(self):
        assert BlockType.WARNING == "WARNING"

    def test_info_value(self):
        assert BlockType.INFO == "INFO"

    def test_all_expected_types_exist(self):
        expected = {"CONNECT", "FETCH", "METRICS", "ROOT_CAUSE", "FIX",
                    "ACTION", "CONFIRM", "INFO", "WARNING", "ERROR", "SUCCESS"}
        actual = {bt.value for bt in BlockType}
        assert expected == actual

    def test_count(self):
        assert len(list(BlockType)) == 11


# ---------------------------------------------------------------------------
# _STYLES coverage
# ---------------------------------------------------------------------------


class TestStyles:
    def test_all_block_types_have_style(self):
        for bt in BlockType:
            assert bt in _STYLES

    def test_style_has_label(self):
        for bt in BlockType:
            assert _STYLES[bt].label != ""

    def test_style_label_matches_block_type(self):
        for bt in BlockType:
            assert _STYLES[bt].label == bt.value


# ---------------------------------------------------------------------------
# DIVIDER constant
# ---------------------------------------------------------------------------


class TestDivider:
    def test_is_string(self):
        assert isinstance(DIVIDER, str)

    def test_nonempty(self):
        assert len(DIVIDER) > 0


# ---------------------------------------------------------------------------
# progress_bar
# ---------------------------------------------------------------------------


class TestProgressBar:
    def test_returns_string(self):
        assert isinstance(progress_bar(50, 100), str)

    def test_contains_percentage(self):
        result = progress_bar(50, 100)
        assert "50.0%" in result

    def test_zero_total_zero_percent(self):
        result = progress_bar(0, 0)
        assert "0.0%" in result

    def test_full_100_percent(self):
        result = progress_bar(100, 100)
        assert "100.0%" in result

    def test_over_total_capped_at_100(self):
        result = progress_bar(200, 100)
        assert "100.0%" in result

    def test_25_percent(self):
        result = progress_bar(25, 100)
        assert "25.0%" in result

    def test_width_parameter_affects_output(self):
        narrow = progress_bar(50, 100, width=5)
        wide = progress_bar(50, 100, width=40)
        # wider bar should be a longer string
        assert len(wide) > len(narrow)

    def test_default_width_present(self):
        result = progress_bar(10, 100)
        assert result  # non-empty


# ---------------------------------------------------------------------------
# renderBlock (stdout capture via capsys)
# ---------------------------------------------------------------------------


class TestRenderBlock:
    def test_title_in_output(self, capsys):
        renderBlock(BlockType.INFO, "test-title")
        out = capsys.readouterr().out
        assert "test-title" in out

    def test_label_in_output(self, capsys):
        renderBlock(BlockType.ERROR, "oops")
        out = capsys.readouterr().out
        assert "ERROR" in out

    def test_body_in_output_when_provided(self, capsys):
        renderBlock(BlockType.SUCCESS, "done", body="Operation completed")
        out = capsys.readouterr().out
        assert "Operation completed" in out

    def test_no_body_no_crash(self, capsys):
        renderBlock(BlockType.WARNING, "heads up")
        out = capsys.readouterr().out
        assert "heads up" in out

    def test_multiline_body_all_lines_present(self, capsys):
        renderBlock(BlockType.INFO, "title", body="line1\nline2\nline3")
        out = capsys.readouterr().out
        assert "line1" in out
        assert "line2" in out
        assert "line3" in out

    def test_trailing_newline_present(self, capsys):
        renderBlock(BlockType.INFO, "x")
        out = capsys.readouterr().out
        assert out.endswith("\n")


# ---------------------------------------------------------------------------
# renderMetric (stdout capture via capsys)
# ---------------------------------------------------------------------------


class TestRenderMetric:
    def test_metric_name_in_output(self, capsys):
        renderMetric("cpu_usage", 70, 100)
        out = capsys.readouterr().out
        assert "cpu_usage" in out

    def test_values_in_output(self, capsys):
        renderMetric("disk", 40, 100)
        out = capsys.readouterr().out
        assert "40" in out
        assert "100" in out

    def test_unit_in_output(self, capsys):
        renderMetric("memory", 512, 1024, unit="MB")
        out = capsys.readouterr().out
        assert "MB" in out

    def test_zero_total_no_crash(self, capsys):
        renderMetric("nothing", 0, 0)
        out = capsys.readouterr().out
        assert "nothing" in out
