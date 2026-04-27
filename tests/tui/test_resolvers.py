"""
Hermetic unit tests for navig.tui.resolvers — StatusBadge

Covers:
- StatusBadge dataclass field storage
- StatusBadge.color property for each status
- StatusBadge.symbol property (default icon per status, custom icon override)
"""

import pytest

from navig.tui.resolvers import StatusBadge


# ─────────────────────────────────────────────────────────────
# StatusBadge fields
# ─────────────────────────────────────────────────────────────


class TestStatusBadgeFields:
    def test_required_fields(self):
        badge = StatusBadge(label="AI Provider", status="ok")
        assert badge.label == "AI Provider"
        assert badge.status == "ok"

    def test_defaults(self):
        badge = StatusBadge(label="x", status="ok")
        assert badge.detail == ""
        assert badge.icon == ""
        assert badge.deep_link == ""

    def test_optional_fields(self):
        badge = StatusBadge(
            label="Telegram",
            status="warn",
            detail="Token missing",
            icon="🤖",
            deep_link="/settings/telegram",
        )
        assert badge.detail == "Token missing"
        assert badge.icon == "🤖"
        assert badge.deep_link == "/settings/telegram"


# ─────────────────────────────────────────────────────────────
# StatusBadge.color
# ─────────────────────────────────────────────────────────────


class TestStatusBadgeColor:
    @pytest.mark.parametrize("status, expected_color", [
        ("ok", "#10b981"),
        ("warn", "#f59e0b"),
        ("error", "#ef4444"),
        ("missing", "#64748b"),
    ])
    def test_known_statuses(self, status, expected_color):
        badge = StatusBadge(label="X", status=status)
        assert badge.color == expected_color

    def test_unknown_status_default_color(self):
        badge = StatusBadge(label="X", status="unknown")
        assert badge.color == "#64748b"


# ─────────────────────────────────────────────────────────────
# StatusBadge.symbol
# ─────────────────────────────────────────────────────────────


class TestStatusBadgeSymbol:
    @pytest.mark.parametrize("status, expected_symbol", [
        ("ok", "●"),
        ("warn", "◑"),
        ("error", "✖"),
        ("missing", "○"),
    ])
    def test_default_symbols(self, status, expected_symbol):
        badge = StatusBadge(label="X", status=status)
        assert badge.symbol == expected_symbol

    def test_custom_icon_overrides_default(self):
        badge = StatusBadge(label="X", status="ok", icon="★")
        assert badge.symbol == "★"

    def test_unknown_status_symbol(self):
        badge = StatusBadge(label="X", status="custom")
        assert badge.symbol == "?"

    def test_all_known_statuses_have_distinct_symbols(self):
        statuses = ["ok", "warn", "error", "missing"]
        symbols = {StatusBadge(label="X", status=s).symbol for s in statuses}
        assert len(symbols) == 4  # all distinct
