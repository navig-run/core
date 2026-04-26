"""Tests for navig.ui.models — typed UI dataclasses."""

from __future__ import annotations

import pytest

from navig.ui.models import (
    ActionItem,
    CauseScore,
    DiffLine,
    DiffPreview,
    Event,
    Metric,
    StatusChip,
    SummaryResult,
)


class TestStatusChip:
    def test_required_fields(self):
        chip = StatusChip(icon="✓", icon_safe="[ok]", label="Status")
        assert chip.icon == "✓"
        assert chip.icon_safe == "[ok]"
        assert chip.label == "Status"

    def test_defaults(self):
        chip = StatusChip(icon="x", icon_safe="x", label="L")
        assert chip.value is None
        assert chip.color == "white"

    def test_with_value(self):
        chip = StatusChip(icon="●", icon_safe="*", label="CPU", value="42%", color="yellow")
        assert chip.value == "42%"
        assert chip.color == "yellow"


class TestMetric:
    def test_required_fields(self):
        m = Metric(label="cpu", value="45%", bar_fill=0.45)
        assert m.label == "cpu"
        assert m.value == "45%"
        assert m.bar_fill == 0.45

    def test_defaults(self):
        m = Metric(label="mem", value="2GB", bar_fill=0.5)
        assert m.sparkline is None
        assert m.color == "cyan"

    def test_with_sparkline(self):
        m = Metric(label="net", value="100MB", bar_fill=0.3, sparkline="▁▂▃▄")
        assert m.sparkline == "▁▂▃▄"

    def test_bar_fill_range(self):
        for v in [0.0, 0.5, 1.0]:
            m = Metric(label="x", value="y", bar_fill=v)
            assert 0.0 <= m.bar_fill <= 1.0


class TestCauseScore:
    def test_required_fields(self):
        c = CauseScore(confidence=85, description="High CPU from process X")
        assert c.confidence == 85
        assert "CPU" in c.description

    def test_default_severity(self):
        c = CauseScore(confidence=50, description="test")
        assert c.severity == "info"

    def test_custom_severity(self):
        c = CauseScore(confidence=95, description="disk full", severity="critical")
        assert c.severity == "critical"


class TestEvent:
    def test_required_fields(self):
        e = Event(timestamp="10:00", icon="⚠", label="Warn", detail="High load")
        assert e.timestamp == "10:00"
        assert e.label == "Warn"

    def test_default_color(self):
        e = Event(timestamp="t", icon="i", label="l", detail="d")
        assert e.color == "white"

    def test_custom_color(self):
        e = Event(timestamp="t", icon="!", label="Error", detail="crash", color="red")
        assert e.color == "red"


class TestActionItem:
    def test_required_fields(self):
        a = ActionItem(index=1, description="Restart nginx")
        assert a.index == 1
        assert a.description == "Restart nginx"

    def test_defaults(self):
        a = ActionItem(index=1, description="x")
        assert a.estimated_value is None
        assert a.risk == "low"

    def test_with_all_fields(self):
        a = ActionItem(index=3, description="Scale DB", estimated_value="high", risk="medium")
        assert a.estimated_value == "high"
        assert a.risk == "medium"


class TestDiffLine:
    def test_add_op(self):
        d = DiffLine(op="add", content="+ new line")
        assert d.op == "add"

    def test_remove_op(self):
        d = DiffLine(op="remove", content="- old line")
        assert d.op == "remove"

    def test_context_op(self):
        d = DiffLine(op="context", content=" ctx")
        assert d.op == "context"


class TestDiffPreview:
    def test_title_required(self):
        preview = DiffPreview(title="Config changes")
        assert preview.title == "Config changes"

    def test_default_empty_lines(self):
        preview = DiffPreview(title="t")
        assert preview.lines == []

    def test_with_lines(self):
        lines = [DiffLine(op="add", content="new"), DiffLine(op="remove", content="old")]
        preview = DiffPreview(title="diff", lines=lines)
        assert len(preview.lines) == 2

    def test_lines_default_factory_independent(self):
        a = DiffPreview(title="a")
        b = DiffPreview(title="b")
        a.lines.append(DiffLine(op="add", content="x"))
        assert b.lines == []


class TestSummaryResult:
    def test_required_fields(self):
        s = SummaryResult(root_cause="OOM", recommendation="Add memory", confidence=90)
        assert s.root_cause == "OOM"
        assert s.confidence == 90

    def test_default_action_prompt(self):
        s = SummaryResult(root_cause="x", recommendation="y", confidence=50)
        assert s.action_prompt is None

    def test_with_action_prompt(self):
        s = SummaryResult(
            root_cause="disk",
            recommendation="clean up",
            confidence=80,
            action_prompt="Run cleanup now?",
        )
        assert s.action_prompt == "Run cleanup now?"
