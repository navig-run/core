"""Batch 52 — hermetic unit tests.

Modules covered:
- navig.plans.frontmatter        (parse_frontmatter, parse_frontmatter_with_body,
                                   render_frontmatter, first_h1, _safe_read)
- navig.gateway.channels.types   (ContextMessage, MessageMetadata TypedDicts)
- navig.connectors.google_calendar.mappers
                                  (_parse_event_time, calendar_event_to_resource)
- navig.ui.formatters            (render_kv_diagnostics, render_command_row,
                                   render_section_divider)
- navig.ui.diff                  (diff_lines_from_text, render_diff_preview)
- navig.ui.models                (DiffLine, DiffPreview, StatusChip, Metric,
                                   CauseScore, ActionItem, SummaryResult)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


# ──────────────────────────────────────────────────────────────────────────────
# navig.plans.frontmatter
# ──────────────────────────────────────────────────────────────────────────────


class TestParseFrontmatter:
    """parse_frontmatter — dict extraction from --- blocks."""

    def _import(self):
        from navig.plans.frontmatter import parse_frontmatter

        return parse_frontmatter

    def test_basic_single_key(self):
        parse_frontmatter = self._import()
        text = "---\ntitle: Hello World\n---\nbody text"
        result = parse_frontmatter(text)
        assert result == {"title": "Hello World"}

    def test_multiple_keys(self):
        parse_frontmatter = self._import()
        text = "---\ntitle: My Plan\nstatus: active\nowner: alice\n---\n"
        result = parse_frontmatter(text)
        assert result["title"] == "My Plan"
        assert result["status"] == "active"
        assert result["owner"] == "alice"

    def test_no_frontmatter_returns_empty(self):
        parse_frontmatter = self._import()
        assert parse_frontmatter("just plain text") == {}

    def test_empty_string_returns_empty(self):
        parse_frontmatter = self._import()
        assert parse_frontmatter("") == {}

    def test_value_with_colon(self):
        parse_frontmatter = self._import()
        text = "---\nurl: https://example.com/path\n---\n"
        result = parse_frontmatter(text)
        # split on first colon only — value should include the remainder
        assert result["url"] == "https://example.com/path"

    def test_key_stripped(self):
        parse_frontmatter = self._import()
        text = "---\n  spaced_key : value\n---\n"
        result = parse_frontmatter(text)
        assert "spaced_key" in result

    def test_line_without_colon_skipped(self):
        parse_frontmatter = self._import()
        text = "---\ntitle: Hello\njust-a-tag\n---\n"
        result = parse_frontmatter(text)
        assert "title" in result
        assert "just-a-tag" not in result


class TestParseFrontmatterWithBody:
    """parse_frontmatter_with_body — returns (dict, body) tuple."""

    def _import(self):
        from navig.plans.frontmatter import parse_frontmatter_with_body

        return parse_frontmatter_with_body

    def test_returns_tuple(self):
        fn = self._import()
        fm, body = fn("---\ntitle: T\n---\nbody here")
        assert isinstance(fm, dict)
        assert isinstance(body, str)

    def test_body_is_remainder(self):
        fn = self._import()
        _, body = fn("---\ntitle: T\n---\nbody here")
        assert "body here" in body

    def test_dict_populated(self):
        fn = self._import()
        fm, _ = fn("---\ntitle: Test\nstatus: draft\n---\ncontent")
        assert fm["title"] == "Test"
        assert fm["status"] == "draft"

    def test_no_frontmatter(self):
        fn = self._import()
        fm, body = fn("no frontmatter here")
        assert fm == {}
        assert body == "no frontmatter here"

    def test_empty_string(self):
        fn = self._import()
        fm, body = fn("")
        assert fm == {}
        assert body == ""

    def test_frontmatter_only(self):
        fn = self._import()
        fm, body = fn("---\ntitle: Only\n---\n")
        assert fm["title"] == "Only"
        assert body == ""


class TestRenderFrontmatter:
    """render_frontmatter — dict → --- block."""

    def _import(self):
        from navig.plans.frontmatter import render_frontmatter

        return render_frontmatter

    def test_produces_dashes(self):
        render = self._import()
        result = render({"title": "Hello"})
        assert result.startswith("---")
        assert "---" in result[3:]

    def test_key_value_present(self):
        render = self._import()
        result = render({"status": "active", "owner": "bob"})
        assert "status: active" in result
        assert "owner: bob" in result

    def test_empty_dict(self):
        render = self._import()
        result = render({})
        assert "---" in result

    def test_trailing_newline(self):
        render = self._import()
        result = render({"k": "v"})
        assert result.endswith("\n")

    def test_round_trip(self):
        """render then parse should recover original dict."""
        from navig.plans.frontmatter import parse_frontmatter, render_frontmatter

        original = {"title": "My Doc", "status": "draft"}
        rendered = render_frontmatter(original)
        recovered = parse_frontmatter(rendered)
        assert recovered == original


class TestFirstH1:
    """first_h1 — first # heading extractor."""

    def _import(self):
        from navig.plans.frontmatter import first_h1

        return first_h1

    def test_simple(self):
        fn = self._import()
        assert fn("# Hello World\nsome text") == "Hello World"

    def test_skips_h2(self):
        fn = self._import()
        assert fn("## Section\n# Real Title") == "Real Title"

    def test_no_heading(self):
        fn = self._import()
        assert fn("just text") == ""

    def test_empty_string(self):
        fn = self._import()
        assert fn("") == ""

    def test_strips_whitespace(self):
        fn = self._import()
        assert fn("#   Padded Title  ") == "Padded Title"

    def test_frontmatter_plus_heading(self):
        fn = self._import()
        text = "---\ntitle: meta\n---\n# Markdown Title\nBody"
        assert fn(text) == "Markdown Title"


class TestSafeRead:
    """_safe_read — UTF-8 file reader returning '' on failure."""

    def _import(self):
        from navig.plans.frontmatter import _safe_read

        return _safe_read

    def test_reads_file(self, tmp_path):
        fn = self._import()
        f = tmp_path / "doc.md"
        f.write_text("hello world", encoding="utf-8")
        assert fn(f) == "hello world"

    def test_missing_file_returns_empty(self, tmp_path):
        fn = self._import()
        result = fn(tmp_path / "nonexistent.md")
        assert result == ""


# ──────────────────────────────────────────────────────────────────────────────
# navig.gateway.channels.types
# ──────────────────────────────────────────────────────────────────────────────


class TestContextMessage:
    """ContextMessage TypedDict — basic construction and keys."""

    def _import(self):
        from navig.gateway.channels.types import ContextMessage

        return ContextMessage

    def test_can_construct(self):
        ContextMessage = self._import()
        msg = ContextMessage(role="user", content="hello")
        assert msg["role"] == "user"
        assert msg["content"] == "hello"

    def test_assistant_role(self):
        ContextMessage = self._import()
        msg = ContextMessage(role="assistant", content="I can help")
        assert msg["role"] == "assistant"

    def test_is_dict_subclass(self):
        ContextMessage = self._import()
        msg = ContextMessage(role="user", content="x")
        assert isinstance(msg, dict)


class TestMessageMetadata:
    """MessageMetadata TypedDict — optional fields, partial construction."""

    def _import(self):
        from navig.gateway.channels.types import MessageMetadata

        return MessageMetadata

    def test_empty_is_valid(self):
        MessageMetadata = self._import()
        meta = MessageMetadata()
        assert isinstance(meta, dict)

    def test_set_chat_and_user_id(self):
        MessageMetadata = self._import()
        meta = MessageMetadata(chat_id=123, user_id=456, username="alice")
        assert meta["chat_id"] == 123
        assert meta["user_id"] == 456
        assert meta["username"] == "alice"

    def test_partial_fields(self):
        MessageMetadata = self._import()
        meta = MessageMetadata(is_group=True, session_key="123:456")
        assert meta["is_group"] is True
        assert meta["session_key"] == "123:456"

    def test_tier_override(self):
        MessageMetadata = self._import()
        meta = MessageMetadata(tier_override="big")
        assert meta["tier_override"] == "big"

    def test_context_messages(self):
        from navig.gateway.channels.types import ContextMessage, MessageMetadata

        msgs = [ContextMessage(role="user", content="hi")]
        meta = MessageMetadata(context_messages=msgs)
        assert len(meta["context_messages"]) == 1
        assert meta["context_messages"][0]["role"] == "user"


# ──────────────────────────────────────────────────────────────────────────────
# navig.connectors.google_calendar.mappers
# ──────────────────────────────────────────────────────────────────────────────


class TestParseEventTime:
    """_parse_event_time — handles dateTime and date fields."""

    def _import(self):
        from navig.connectors.google_calendar.mappers import _parse_event_time

        return _parse_event_time

    def test_datetime_field(self):
        fn = self._import()
        result = fn({"dateTime": "2024-06-01T10:00:00+02:00"})
        assert "2024-06-01" in result

    def test_date_field_all_day(self):
        fn = self._import()
        result = fn({"date": "2024-12-25"})
        assert "2024-12-25" in result

    def test_empty_dict_returns_something(self):
        fn = self._import()
        result = fn({})
        assert isinstance(result, str)
        assert len(result) > 0

    def test_prefers_datetime_over_date(self):
        fn = self._import()
        result = fn({"dateTime": "2024-06-01T10:00:00Z", "date": "2024-06-01"})
        assert "T" in result  # isoformat includes time

    def test_invalid_datetime_returns_raw(self):
        fn = self._import()
        result = fn({"dateTime": "not-a-date"})
        assert isinstance(result, str)


class TestCalendarEventToResource:
    """calendar_event_to_resource — full mapping to Resource."""

    def _import(self):
        from navig.connectors.google_calendar.mappers import calendar_event_to_resource

        return calendar_event_to_resource

    def _make_event(self, **overrides):
        base = {
            "id": "evt001",
            "summary": "Team Meeting",
            "description": "Weekly sync",
            "start": {"dateTime": "2024-06-01T10:00:00Z"},
            "end": {"dateTime": "2024-06-01T11:00:00Z"},
            "attendees": [{"email": "a@x.com"}, {"email": "b@x.com"}],
            "location": "Zoom",
            "htmlLink": "https://calendar.google.com/event?eid=evt001",
            "status": "confirmed",
            "organizer": {"email": "org@x.com"},
        }
        base.update(overrides)
        return base

    def test_id_mapped(self):
        fn = self._import()
        r = fn(self._make_event())
        assert r.id == "evt001"

    def test_source_is_google_calendar(self):
        fn = self._import()
        r = fn(self._make_event())
        assert r.source == "google_calendar"

    def test_title_from_summary(self):
        fn = self._import()
        r = fn(self._make_event(summary="Stand-up"))
        assert r.title == "Stand-up"

    def test_no_summary_fallback(self):
        fn = self._import()
        event = self._make_event()
        del event["summary"]
        r = fn(event)
        assert r.title == "(no title)"

    def test_preview_from_description(self):
        fn = self._import()
        r = fn(self._make_event(description="A long description"))
        assert "A long description" in r.preview

    def test_preview_truncated_at_200(self):
        fn = self._import()
        long_desc = "x" * 300
        r = fn(self._make_event(description=long_desc))
        assert len(r.preview) <= 200

    def test_url_from_html_link(self):
        fn = self._import()
        r = fn(self._make_event())
        assert "calendar.google.com" in r.url

    def test_attendee_emails_in_metadata(self):
        fn = self._import()
        r = fn(self._make_event())
        assert "a@x.com" in r.metadata["attendees"]
        assert "b@x.com" in r.metadata["attendees"]

    def test_organizer_in_metadata(self):
        fn = self._import()
        r = fn(self._make_event())
        assert r.metadata["organizer"] == "org@x.com"

    def test_location_in_metadata(self):
        fn = self._import()
        r = fn(self._make_event())
        assert r.metadata["location"] == "Zoom"

    def test_all_day_flag_false_for_datetime(self):
        fn = self._import()
        r = fn(self._make_event())
        assert r.metadata["all_day"] is False

    def test_all_day_flag_true_for_date_only(self):
        fn = self._import()
        r = fn(self._make_event(start={"date": "2024-06-01"}, end={"date": "2024-06-01"}))
        assert r.metadata["all_day"] is True

    def test_recurring_false_by_default(self):
        fn = self._import()
        r = fn(self._make_event())
        assert r.metadata["recurring"] is False

    def test_recurring_true_when_field_present(self):
        fn = self._import()
        r = fn(self._make_event(recurringEventId="base_event_id"))
        assert r.metadata["recurring"] is True

    def test_resource_type_is_event(self):
        from navig.connectors.types import ResourceType

        fn = self._import()
        r = fn(self._make_event())
        assert r.resource_type == ResourceType.EVENT

    def test_empty_event_defaults(self):
        fn = self._import()
        r = fn({})
        assert r.id == ""
        assert r.source == "google_calendar"
        assert r.title == "(no title)"


# ──────────────────────────────────────────────────────────────────────────────
# navig.ui.formatters
# ──────────────────────────────────────────────────────────────────────────────


class TestRenderKvDiagnostics:
    """render_kv_diagnostics — aligned key→value output."""

    def _import(self):
        from navig.ui.formatters import render_kv_diagnostics

        return render_kv_diagnostics

    def test_empty_list_does_nothing(self):
        fn = self._import()
        mock_console = MagicMock()
        with patch("navig.ui.formatters.console", mock_console):
            fn([])
        mock_console.print.assert_not_called()

    def test_renders_pairs(self):
        fn = self._import()
        mock_console = MagicMock()
        with patch("navig.ui.formatters.console", mock_console):
            fn([("host", "prod"), ("status", "ok")])
        assert mock_console.print.call_count == 2

    def test_renders_title_when_given(self):
        fn = self._import()
        mock_console = MagicMock()
        with patch("navig.ui.formatters.console", mock_console):
            fn([("k", "v")], title="Section")
        # one extra call for title
        assert mock_console.print.call_count == 2

    def test_survives_console_exception(self):
        fn = self._import()
        mock_console = MagicMock()
        mock_console.print.side_effect = RuntimeError("crash")
        with patch("navig.ui.formatters.console", mock_console):
            fn([("key", "val")])  # should not raise

    def test_label_width_applied(self):
        fn = self._import()
        calls = []
        mock_console = MagicMock()
        mock_console.print.side_effect = lambda txt: calls.append(txt)
        with patch("navig.ui.formatters.console", mock_console):
            fn([("short", "val")], label_width=30)
        assert any("short" in c for c in calls)


class TestRenderCommandRow:
    """render_command_row — padded label + cyan command."""

    def _import(self):
        from navig.ui.formatters import render_command_row

        return render_command_row

    def test_renders_label_and_cmd(self):
        fn = self._import()
        calls = []
        mock_console = MagicMock()
        mock_console.print.side_effect = lambda txt: calls.append(txt)
        with patch("navig.ui.formatters.console", mock_console):
            fn("deploy", "navig flow run deploy")
        assert any("deploy" in c for c in calls)
        assert any("navig flow run deploy" in c for c in calls)

    def test_with_description(self):
        fn = self._import()
        calls = []
        mock_console = MagicMock()
        mock_console.print.side_effect = lambda txt: calls.append(txt)
        with patch("navig.ui.formatters.console", mock_console):
            fn("init", "navig init", description="Initialize project")
        assert any("Initialize project" in c for c in calls)

    def test_survives_console_exception(self):
        fn = self._import()
        mock_console = MagicMock()
        mock_console.print.side_effect = RuntimeError("boom")
        with patch("navig.ui.formatters.console", mock_console):
            fn("label", "cmd")  # should not raise

    def test_no_description(self):
        fn = self._import()
        mock_console = MagicMock()
        with patch("navig.ui.formatters.console", mock_console):
            fn("lbl", "cmd")
        mock_console.print.assert_called_once()


class TestRenderSectionDivider:
    """render_section_divider — thin horizontal rule."""

    def _import(self):
        from navig.ui.formatters import render_section_divider

        return render_section_divider

    def test_with_title(self):
        fn = self._import()
        mock_console = MagicMock()
        with patch("navig.ui.formatters.console", mock_console):
            fn("My Section")
        mock_console.rule.assert_called_once()

    def test_without_title(self):
        fn = self._import()
        mock_console = MagicMock()
        with patch("navig.ui.formatters.console", mock_console):
            fn()
        mock_console.rule.assert_called_once()

    def test_survives_console_exception(self):
        fn = self._import()
        mock_console = MagicMock()
        mock_console.rule.side_effect = RuntimeError("crash")
        with patch("navig.ui.formatters.console", mock_console):
            fn("title")  # should not raise


# ──────────────────────────────────────────────────────────────────────────────
# navig.ui.diff
# ──────────────────────────────────────────────────────────────────────────────


class TestDiffLinesFromText:
    """diff_lines_from_text — generates DiffLine list from two strings."""

    def _import(self):
        from navig.ui.diff import diff_lines_from_text

        return diff_lines_from_text

    def test_identical_texts_no_adds_removes(self):
        fn = self._import()
        lines = fn("hello\nworld", "hello\nworld")
        ops = [l.op for l in lines]
        assert "add" not in ops
        assert "remove" not in ops

    def test_addition_detected(self):
        fn = self._import()
        lines = fn("line1", "line1\nline2")
        ops = [l.op for l in lines]
        assert "add" in ops

    def test_removal_detected(self):
        fn = self._import()
        lines = fn("line1\nline2", "line1")
        ops = [l.op for l in lines]
        assert "remove" in ops

    def test_returns_diff_line_objects(self):
        from navig.ui.models import DiffLine

        fn = self._import()
        lines = fn("a", "b")
        for line in lines:
            assert isinstance(line, DiffLine)

    def test_empty_strings(self):
        fn = self._import()
        lines = fn("", "")
        assert isinstance(lines, list)

    def test_content_stripped_of_leading_marker(self):
        fn = self._import()
        lines = fn("before", "after")
        for line in lines:
            if line.op == "add":
                assert not line.content.startswith("+")
            if line.op == "remove":
                assert not line.content.startswith("-")


class TestRenderDiffPreview:
    """render_diff_preview — renders only in debug mode."""

    def _import(self):
        from navig.ui.diff import render_diff_preview

        return render_diff_preview

    def _make_diff(self, lines=None):
        from navig.ui.models import DiffLine, DiffPreview

        if lines is None:
            lines = [DiffLine(op="add", content="new line")]
        return DiffPreview(title="test diff", lines=lines)

    def test_skipped_when_debug_false_and_no_env(self):
        fn = self._import()
        mock_console = MagicMock()
        with patch("navig.ui.diff.console", mock_console):
            with patch.dict("os.environ", {"NAVIG_DEBUG": "0"}):
                fn(self._make_diff(), debug=False)
        mock_console.print.assert_not_called()

    def test_renders_when_debug_true(self):
        fn = self._import()
        mock_console = MagicMock()
        with patch("navig.ui.diff.console", mock_console):
            fn(self._make_diff(), debug=True)
        assert mock_console.print.call_count >= 1

    def test_renders_when_navig_debug_env(self):
        fn = self._import()
        mock_console = MagicMock()
        with patch("navig.ui.diff.console", mock_console):
            with patch.dict("os.environ", {"NAVIG_DEBUG": "1"}):
                fn(self._make_diff(), debug=False)
        assert mock_console.print.call_count >= 1

    def test_empty_diff_no_render(self):
        fn = self._import()
        from navig.ui.models import DiffPreview

        mock_console = MagicMock()
        with patch("navig.ui.diff.console", mock_console):
            fn(DiffPreview(title="empty", lines=[]), debug=True)
        mock_console.print.assert_not_called()

    def test_max_lines_respected(self):
        fn = self._import()
        from navig.ui.models import DiffLine, DiffPreview

        many_lines = [DiffLine(op="add", content=f"line {i}") for i in range(20)]
        diff = DiffPreview(title="big", lines=many_lines)
        mock_console = MagicMock()
        calls = []
        mock_console.print.side_effect = lambda t: calls.append(t)
        with patch("navig.ui.diff.console", mock_console):
            fn(diff, debug=True, max_lines=5)
        # should show "… X more lines" at end
        assert any("more lines" in str(c) for c in calls)

    def test_survives_console_exception(self):
        fn = self._import()
        mock_console = MagicMock()
        mock_console.print.side_effect = RuntimeError("crash")
        with patch("navig.ui.diff.console", mock_console):
            fn(self._make_diff(), debug=True)  # should not raise


# ──────────────────────────────────────────────────────────────────────────────
# navig.ui.models
# ──────────────────────────────────────────────────────────────────────────────


class TestUiModels:
    """Basic construction tests for navig.ui.models dataclasses."""

    def test_status_chip_defaults(self):
        from navig.ui.models import StatusChip

        chip = StatusChip(icon="●", icon_safe="*", label="active")
        assert chip.color == "white"
        assert chip.value is None

    def test_status_chip_with_value(self):
        from navig.ui.models import StatusChip

        chip = StatusChip(icon="●", icon_safe="*", label="cpu", value="45%", color="cyan")
        assert chip.value == "45%"
        assert chip.color == "cyan"

    def test_metric_defaults(self):
        from navig.ui.models import Metric

        m = Metric(label="memory", value="2.1 GB", bar_fill=0.65)
        assert m.color == "cyan"
        assert m.sparkline is None

    def test_cause_score_defaults(self):
        from navig.ui.models import CauseScore

        cs = CauseScore(confidence=85, description="OOM killer triggered")
        assert cs.severity == "info"

    def test_diff_line_ops(self):
        from navig.ui.models import DiffLine

        for op in ("add", "remove", "context"):
            dl = DiffLine(op=op, content="line content")
            assert dl.op == op

    def test_diff_preview_empty_lines(self):
        from navig.ui.models import DiffPreview

        dp = DiffPreview(title="patch")
        assert dp.lines == []

    def test_diff_preview_with_lines(self):
        from navig.ui.models import DiffLine, DiffPreview

        lines = [DiffLine(op="add", content="new")]
        dp = DiffPreview(title="my diff", lines=lines)
        assert len(dp.lines) == 1

    def test_action_item_defaults(self):
        from navig.ui.models import ActionItem

        ai = ActionItem(index=1, description="restart service")
        assert ai.risk == "low"
        assert ai.estimated_value is None

    def test_summary_result(self):
        from navig.ui.models import SummaryResult

        sr = SummaryResult(
            root_cause="memory leak",
            recommendation="restart pod",
            confidence=90,
        )
        assert sr.confidence == 90
        assert sr.action_prompt is None
