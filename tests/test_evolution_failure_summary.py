from __future__ import annotations

from pathlib import Path

from navig.core.evolution.failure_summary import summarize_check_failure
from navig.core.evolution.fix import FixEvolver


def test_summarize_check_failure_extracts_pytest_signals():
    stdout = """
=========================== short test summary info ===========================
FAILED tests/test_math.py::test_add - AssertionError: expected 2
============================== 1 failed in 0.12s =============================
"""
    stderr = "E   AssertionError: expected 2"

    summary = summarize_check_failure(stdout, stderr)

    assert "Failed tests: 1" in summary
    assert "tests/test_math.py::test_add" in summary
    assert "AssertionError: expected 2" in summary


def test_fix_evolver_includes_failure_summary_on_check_failure(tmp_path: Path, monkeypatch):
    target = tmp_path / "sample.py"
    target.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    class _Result:
        returncode = 1
        stdout = (
            "FAILED tests/test_math.py::test_add - AssertionError: expected 2\n1 failed in 0.08s\n"
        )
        stderr = "E   AssertionError: expected 2"

    def fake_run(*args, **kwargs):
        return _Result()

    monkeypatch.setattr("subprocess.run", fake_run)

    evolver = FixEvolver(target, check_command="pytest {file}")
    validation = evolver._validate("```python\ndef add(a, b):\n    return a + b\n```", context=None)

    assert validation is not None
    assert "Failure Summary:" in validation
    assert "Failed tests: 1" in validation
    assert evolver.last_failure_summary
