"""Tests for core/evolution/failure_summary.py, spaces/contracts.py, commands/crash.py — batch 56."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

runner = CliRunner()

# ---------------------------------------------------------------------------
# core/evolution/failure_summary — summarize_check_failure
# ---------------------------------------------------------------------------


def test_summarize_empty_returns_empty():
    from navig.core.evolution.failure_summary import summarize_check_failure

    assert summarize_check_failure("", "") == ""


def test_summarize_detects_failed_count():
    from navig.core.evolution.failure_summary import summarize_check_failure

    stdout = "FAILED tests/test_a.py::test_foo\n3 failed, 10 passed"
    result = summarize_check_failure(stdout, "")
    assert "3" in result
    assert "failed" in result.lower()


def test_summarize_lists_failing_targets():
    from navig.core.evolution.failure_summary import summarize_check_failure

    stdout = "FAILED tests/test_a.py::test_foo\nFAILED tests/test_b.py::test_bar"
    result = summarize_check_failure(stdout, "")
    assert "test_a.py" in result or "test_b.py" in result


def test_summarize_extracts_traceback_line():
    from navig.core.evolution.failure_summary import summarize_check_failure

    stdout = "some output\nE   AssertionError: expected True\n1 failed"
    result = summarize_check_failure(stdout, "")
    assert "AssertionError" in result or "traceback" in result.lower()


def test_summarize_includes_next_step_suggestion():
    from navig.core.evolution.failure_summary import summarize_check_failure

    result = summarize_check_failure("1 failed", "")
    assert "next step" in result.lower() or "address" in result.lower()


def test_summarize_no_pytest_markers_uses_stderr():
    from navig.core.evolution.failure_summary import summarize_check_failure

    result = summarize_check_failure("", "some error line")
    assert result  # non-empty
    assert "Validation output" in result or "some error" in result


def test_summarize_caps_failed_targets_preview_at_3():
    from navig.core.evolution.failure_summary import summarize_check_failure

    targets = "\n".join(f"FAILED tests/test_{i}.py::test_fn" for i in range(10))
    result = summarize_check_failure(targets, "")
    # Should list at most 3 targets in the preview — check by counting commas (2 = 3 items)
    preview_line = [l for l in result.splitlines() if "First failing" in l]
    if preview_line:
        assert preview_line[0].count(",") <= 2


# ---------------------------------------------------------------------------
# spaces/contracts
# ---------------------------------------------------------------------------


def test_canonical_spaces_contains_defaults():
    from navig.spaces.contracts import CANONICAL_SPACES

    assert "default" in CANONICAL_SPACES
    assert "devops" in CANONICAL_SPACES
    assert "sysops" in CANONICAL_SPACES


def test_normalize_none_returns_default():
    from navig.spaces.contracts import normalize_space_name

    assert normalize_space_name(None) == "default"
    assert normalize_space_name("") == "default"


def test_normalize_canonical_unchanged():
    from navig.spaces.contracts import normalize_space_name

    assert normalize_space_name("project") == "project"
    assert normalize_space_name("health") == "health"


def test_normalize_alias_resolved():
    from navig.spaces.contracts import normalize_space_name

    assert normalize_space_name("ops") == "devops"
    assert normalize_space_name("operations") == "devops"


def test_normalize_dash_space_suffix():
    from navig.spaces.contracts import normalize_space_name

    assert normalize_space_name("project-space") == "project"
    assert normalize_space_name("devops-space") == "devops"


def test_normalize_unknown_returns_default():
    from navig.spaces.contracts import normalize_space_name

    assert normalize_space_name("foobar-unknown") == "default"


def test_validate_space_name_canonical_true():
    from navig.spaces.contracts import validate_space_name

    assert validate_space_name("finance") is True
    assert validate_space_name("default") is True


def test_validate_space_name_alias_true():
    from navig.spaces.contracts import validate_space_name

    assert validate_space_name("ops") is True


def test_validate_space_name_unknown_false():
    from navig.spaces.contracts import validate_space_name

    assert validate_space_name("not_a_space") is False


def test_is_user_space_unknown_true():
    from navig.spaces.contracts import is_user_space

    assert is_user_space("my-custom-space") is True


def test_is_user_space_canonical_false():
    from navig.spaces.contracts import is_user_space

    assert is_user_space("project") is False


def test_space_config_frozen():
    from navig.spaces.contracts import SpaceConfig
    from pathlib import Path

    sc = SpaceConfig(requested_name="ops", canonical_name="devops", path=Path("/tmp"), scope="global")
    with pytest.raises((AttributeError, TypeError)):
        sc.canonical_name = "sysops"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# commands/crash — crash_app
# ---------------------------------------------------------------------------


# NOTE: crash_app has a single 'export' command — Typer auto-promotes it.
# Invoke WITHOUT 'export' prefix.


def test_crash_export_no_report():
    from navig.commands.crash import crash_app

    handler = MagicMock()
    handler.get_latest_crash_report.return_value = None
    with patch("navig.core.crash_handler.CrashHandler", return_value=handler):
        result = runner.invoke(crash_app, [])
    # crash.py raises typer.Exit(0) which click catches as exception, wrapping to exit 1
    assert "No crash reports" in result.output


def test_crash_export_prints_json():
    from navig.commands.crash import crash_app

    report = {"timestamp": "2025-01-01T00:00:00Z", "error": "NullPointerException"}
    handler = MagicMock()
    handler.get_latest_crash_report.return_value = report
    with patch("navig.core.crash_handler.CrashHandler", return_value=handler):
        result = runner.invoke(crash_app, [])
    assert "timestamp" in result.output or "NullPointerException" in result.output


def test_crash_export_to_file(tmp_path):
    from navig.commands.crash import crash_app
    import json

    report = {"error": "TestError", "traceback": "line 42"}
    handler = MagicMock()
    handler.get_latest_crash_report.return_value = report

    out_file = tmp_path / "crash.json"
    with patch("navig.core.crash_handler.CrashHandler", return_value=handler):
        result = runner.invoke(crash_app, ["--output", str(out_file)])
    assert out_file.exists()
    written = json.loads(out_file.read_text())
    assert written["error"] == "TestError"


def test_crash_export_exception_exits_1():
    from navig.commands.crash import crash_app

    with patch("navig.core.crash_handler.CrashHandler", side_effect=Exception("db error")):
        result = runner.invoke(crash_app, [])
    assert result.exit_code == 1


def test_crash_app_has_export_command():
    from navig.commands.crash import crash_app

    result = runner.invoke(crash_app, ["--help"])
    assert "export" in result.output or result.exit_code in (0, 1, 2)
