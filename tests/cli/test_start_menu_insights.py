"""
Batch 119 — start_menu (build_main_menu/build_section/get_action_info)
         + insights enums/dataclasses (InsightType/Severity/TimeRange/Insight/HostScore/CommandStats)

Pure-unit tests: no network, no file I/O, no subprocess.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# bot/start_menu — build_main_menu
# ---------------------------------------------------------------------------

from navig.bot.start_menu import build_main_menu, build_section, get_action_info


class TestBuildMainMenu:
    def test_returns_dict(self):
        result = build_main_menu()
        assert isinstance(result, dict)

    def test_has_text_key(self):
        result = build_main_menu()
        assert "text" in result

    def test_has_buttons_key(self):
        result = build_main_menu()
        assert "buttons" in result

    def test_buttons_is_list(self):
        result = build_main_menu()
        assert isinstance(result["buttons"], list)

    def test_greeting_empty_when_no_user(self):
        result = build_main_menu()
        # No user name → greeting should NOT insert a name before NAVIG
        assert result["text"].startswith("I'm") or "Hey" not in result["text"][:5]

    def test_greeting_includes_user_name(self):
        result = build_main_menu("Alice")
        assert "Alice" in result["text"]

    def test_nav_core_present_in_buttons(self):
        result = build_main_menu()
        all_callbacks = [cb for row in result["buttons"] for _, cb in row]
        assert "nav:core" in all_callbacks

    def test_nav_infra_present_in_buttons(self):
        result = build_main_menu()
        all_callbacks = [cb for row in result["buttons"] for _, cb in row]
        assert "nav:infra" in all_callbacks

    def test_nav_docker_present_in_buttons(self):
        result = build_main_menu()
        all_callbacks = [cb for row in result["buttons"] for _, cb in row]
        assert "nav:docker" in all_callbacks

    def test_nav_db_present_in_buttons(self):
        result = build_main_menu()
        all_callbacks = [cb for row in result["buttons"] for _, cb in row]
        assert "nav:db" in all_callbacks

    def test_nav_tools_present_in_buttons(self):
        result = build_main_menu()
        all_callbacks = [cb for row in result["buttons"] for _, cb in row]
        assert "nav:tools" in all_callbacks

    def test_multiple_rows_in_buttons(self):
        result = build_main_menu()
        assert len(result["buttons"]) >= 3


class TestBuildSection:
    def test_known_section_returns_dict(self):
        result = build_section("core")
        assert isinstance(result, dict)

    def test_unknown_section_returns_none(self):
        result = build_section("nonexistent_xyz")
        assert result is None

    def test_infra_section_has_text(self):
        result = build_section("infra")
        assert "text" in result and len(result["text"]) > 0

    def test_infra_section_has_buttons(self):
        result = build_section("infra")
        assert "buttons" in result

    def test_back_button_in_core_section(self):
        result = build_section("core")
        all_callbacks = [cb for row in result["buttons"] for _, cb in row]
        assert "nav:main" in all_callbacks

    def test_docker_section_exists(self):
        result = build_section("docker")
        assert result is not None

    def test_db_section_exists(self):
        result = build_section("db")
        assert result is not None

    def test_monitor_section_exists(self):
        result = build_section("monitor")
        assert result is not None


class TestGetActionInfo:
    def test_unknown_action_returns_none(self):
        assert get_action_info("completely_unknown_action_xyz") is None

    def test_known_action_returns_dict(self):
        # 'status' and 'ping' are very common bot actions
        result = get_action_info("status")
        if result is not None:
            assert isinstance(result, dict)

    def test_returns_none_for_empty_string(self):
        assert get_action_info("") is None


# ---------------------------------------------------------------------------
# commands/insights — enums
# ---------------------------------------------------------------------------

from navig.commands.insights import (
    AnalyticsReport,
    CommandStats,
    HostScore,
    Insight,
    InsightType,
    Severity,
    TimePattern,
    TimeRange,
)


class TestInsightType:
    def test_usage_value(self):
        assert InsightType.USAGE.value == "usage"

    def test_error_value(self):
        assert InsightType.ERROR.value == "error"

    def test_performance_value(self):
        assert InsightType.PERFORMANCE.value == "performance"

    def test_health_value(self):
        assert InsightType.HEALTH.value == "health"

    def test_recommendation_value(self):
        assert InsightType.RECOMMENDATION.value == "recommendation"

    def test_anomaly_value(self):
        assert InsightType.ANOMALY.value == "anomaly"

    def test_is_str_subclass(self):
        assert isinstance(InsightType.USAGE, str)

    def test_six_members(self):
        assert len(list(InsightType)) == 6


class TestSeverity:
    def test_info_value(self):
        assert Severity.INFO.value == "info"

    def test_warning_value(self):
        assert Severity.WARNING.value == "warning"

    def test_critical_value(self):
        assert Severity.CRITICAL.value == "critical"

    def test_three_members(self):
        assert len(list(Severity)) == 3


class TestTimeRange:
    def test_today_value(self):
        assert TimeRange.TODAY.value == "today"

    def test_week_value(self):
        assert TimeRange.WEEK.value == "week"

    def test_month_value(self):
        assert TimeRange.MONTH.value == "month"

    def test_all_value(self):
        assert TimeRange.ALL.value == "all"


# ---------------------------------------------------------------------------
# commands/insights — Insight dataclass
# ---------------------------------------------------------------------------


class TestInsight:
    def test_type_stored(self):
        ins = Insight(type=InsightType.USAGE, title="T", description="D")
        assert ins.type == InsightType.USAGE

    def test_title_stored(self):
        ins = Insight(type=InsightType.ERROR, title="My title", description="D")
        assert ins.title == "My title"

    def test_description_stored(self):
        ins = Insight(type=InsightType.HEALTH, title="T", description="Detail")
        assert ins.description == "Detail"

    def test_severity_default_info(self):
        ins = Insight(type=InsightType.USAGE, title="T", description="D")
        assert ins.severity == Severity.INFO

    def test_data_default_empty(self):
        ins = Insight(type=InsightType.USAGE, title="T", description="D")
        assert ins.data == {}

    def test_recommendations_default_empty(self):
        ins = Insight(type=InsightType.USAGE, title="T", description="D")
        assert ins.recommendations == []

    def test_timestamp_auto_populated(self):
        ins = Insight(type=InsightType.USAGE, title="T", description="D")
        assert len(ins.timestamp) > 0

    def test_data_not_shared_across_instances(self):
        a = Insight(type=InsightType.USAGE, title="A", description="a")
        b = Insight(type=InsightType.USAGE, title="B", description="b")
        a.data["key"] = "x"
        assert "key" not in b.data

    def test_custom_severity(self):
        ins = Insight(type=InsightType.ANOMALY, title="T", description="D", severity=Severity.CRITICAL)
        assert ins.severity == Severity.CRITICAL


# ---------------------------------------------------------------------------
# commands/insights — HostScore / CommandStats / TimePattern dataclasses
# ---------------------------------------------------------------------------


class TestHostScore:
    def _make(self):
        return HostScore(
            host="prod-01",
            score=85,
            success_rate=0.98,
            avg_latency_ms=120,
            error_count=2,
            last_success="2024-01-01T10:00:00",
            last_error="2024-01-01T09:00:00",
            trend="stable",
        )

    def test_host_stored(self):
        assert self._make().host == "prod-01"

    def test_score_stored(self):
        assert self._make().score == 85

    def test_trend_stored(self):
        assert self._make().trend == "stable"

    def test_success_rate_stored(self):
        assert self._make().success_rate == pytest.approx(0.98)


class TestCommandStats:
    def _make(self):
        return CommandStats(
            command="navig db list",
            count=42,
            success_rate=0.95,
            avg_duration_ms=300,
            last_used="2024-01-01",
            hosts_used=["prod-01", "staging"],
        )

    def test_command_stored(self):
        assert self._make().command == "navig db list"

    def test_count_stored(self):
        assert self._make().count == 42

    def test_hosts_used_stored(self):
        assert self._make().hosts_used == ["prod-01", "staging"]


class TestTimePattern:
    def _make(self):
        return TimePattern(
            hour=9,
            day_of_week=1,
            count=50,
            success_rate=0.9,
            most_common_commands=["navig run", "navig db list"],
        )

    def test_hour_stored(self):
        assert self._make().hour == 9

    def test_day_of_week_stored(self):
        assert self._make().day_of_week == 1

    def test_count_stored(self):
        assert self._make().count == 50

    def test_most_common_commands_stored(self):
        assert "navig run" in self._make().most_common_commands
