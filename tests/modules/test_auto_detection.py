"""Unit tests for modules/auto_detection.py — _categorize_error, _calculate_averages."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from navig.modules.auto_detection import AutoDetection


def _make_ad(tmp_path: Path) -> AutoDetection:
    assistant = MagicMock()
    assistant.ai_context_dir = tmp_path
    assistant.assistant_config = {}
    return AutoDetection(assistant)


# ---------------------------------------------------------------------------
# _categorize_error
# ---------------------------------------------------------------------------

class TestAutoCategorizeError:
    def test_permission_denied(self, tmp_path):
        ad = _make_ad(tmp_path)
        assert ad._categorize_error("permission denied: /var/lib") == "permission"

    def test_access_denied(self, tmp_path):
        ad = _make_ad(tmp_path)
        assert ad._categorize_error("Access denied to remote") == "permission"

    def test_forbidden(self, tmp_path):
        ad = _make_ad(tmp_path)
        assert ad._categorize_error("403 Forbidden") == "permission"

    def test_connection_refused(self, tmp_path):
        ad = _make_ad(tmp_path)
        assert ad._categorize_error("Connection refused 0.0.0.0:6379") == "network"

    def test_timeout(self, tmp_path):
        ad = _make_ad(tmp_path)
        assert ad._categorize_error("request timeout after 10s") == "network"

    def test_unreachable(self, tmp_path):
        ad = _make_ad(tmp_path)
        assert ad._categorize_error("Network unreachable") == "network"

    def test_disk_full(self, tmp_path):
        ad = _make_ad(tmp_path)
        assert ad._categorize_error("disk full: no space left") == "resource_exhaustion"

    def test_oom(self, tmp_path):
        ad = _make_ad(tmp_path)
        assert ad._categorize_error("OOM killer invoked") == "resource_exhaustion"

    def test_out_of_memory(self, tmp_path):
        ad = _make_ad(tmp_path)
        assert ad._categorize_error("out of memory: cannot allocate") == "resource_exhaustion"

    def test_not_found(self, tmp_path):
        ad = _make_ad(tmp_path)
        assert ad._categorize_error("bash: python3: not found") == "dependency_missing"

    def test_no_such_file(self, tmp_path):
        ad = _make_ad(tmp_path)
        assert ad._categorize_error("No such file or directory") == "dependency_missing"

    def test_missing(self, tmp_path):
        ad = _make_ad(tmp_path)
        assert ad._categorize_error("missing package: libssl") == "dependency_missing"

    def test_syntax_error(self, tmp_path):
        ad = _make_ad(tmp_path)
        assert ad._categorize_error("SyntaxError: invalid syntax") == "syntax"

    def test_parse_error(self, tmp_path):
        ad = _make_ad(tmp_path)
        assert ad._categorize_error("parse error near line 42") == "syntax"

    def test_config(self, tmp_path):
        ad = _make_ad(tmp_path)
        assert ad._categorize_error("bad configuration file") == "configuration"

    def test_unknown(self, tmp_path):
        ad = _make_ad(tmp_path)
        assert ad._categorize_error("segmentation fault") == "unknown"

    def test_case_insensitive(self, tmp_path):
        ad = _make_ad(tmp_path)
        assert ad._categorize_error("TIMEOUT EXCEEDED") == "network"

    def test_empty_is_unknown(self, tmp_path):
        ad = _make_ad(tmp_path)
        assert ad._categorize_error("") == "unknown"


# ---------------------------------------------------------------------------
# _calculate_averages
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now().isoformat()


def _ago_iso(minutes: int = 0, hours: int = 0, days: int = 0) -> str:
    return (datetime.now() - timedelta(minutes=minutes, hours=hours, days=days)).isoformat()


class TestCalculateAverages:
    def test_empty_history_returns_empty(self, tmp_path):
        ad = _make_ad(tmp_path)
        result = ad._calculate_averages([])
        assert result == {}

    def test_returns_three_keys(self, tmp_path):
        ad = _make_ad(tmp_path)
        metrics = [{"timestamp": _now_iso(), "cpu_percent": 50.0, "memory_percent": 40.0, "disk_percent": 30.0}]
        result = ad._calculate_averages(metrics)
        assert "1_hour" in result
        assert "24_hours" in result
        assert "7_days" in result

    def test_recent_entry_in_all_windows(self, tmp_path):
        ad = _make_ad(tmp_path)
        metrics = [{"timestamp": _now_iso(), "cpu_percent": 80.0, "memory_percent": 60.0, "disk_percent": 50.0}]
        result = ad._calculate_averages(metrics)
        assert result["1_hour"]["cpu"] == pytest.approx(80.0)
        assert result["24_hours"]["cpu"] == pytest.approx(80.0)
        assert result["7_days"]["cpu"] == pytest.approx(80.0)

    def test_old_entry_excluded_from_1h_window(self, tmp_path):
        ad = _make_ad(tmp_path)
        metrics = [{"timestamp": _ago_iso(hours=2), "cpu_percent": 99.0, "memory_percent": 90.0, "disk_percent": 70.0}]
        result = ad._calculate_averages(metrics)
        assert result["1_hour"] is None
        assert result["24_hours"] is not None

    def test_averages_computed_correctly(self, tmp_path):
        ad = _make_ad(tmp_path)
        metrics = [
            {"timestamp": _now_iso(), "cpu_percent": 20.0, "memory_percent": 30.0, "disk_percent": 10.0},
            {"timestamp": _now_iso(), "cpu_percent": 40.0, "memory_percent": 50.0, "disk_percent": 30.0},
        ]
        result = ad._calculate_averages(metrics)
        assert result["1_hour"]["cpu"] == pytest.approx(30.0)
        assert result["1_hour"]["memory"] == pytest.approx(40.0)
        assert result["1_hour"]["disk"] == pytest.approx(20.0)

    def test_missing_keys_default_to_zero(self, tmp_path):
        ad = _make_ad(tmp_path)
        metrics = [{"timestamp": _now_iso()}]  # no cpu/mem/disk keys
        result = ad._calculate_averages(metrics)
        assert result["1_hour"]["cpu"] == pytest.approx(0.0)

    def test_old_weekly_entry_excluded(self, tmp_path):
        ad = _make_ad(tmp_path)
        metrics = [{"timestamp": _ago_iso(days=10), "cpu_percent": 50.0, "memory_percent": 25.0, "disk_percent": 10.0}]
        result = ad._calculate_averages(metrics)
        assert result["7_days"] is None
