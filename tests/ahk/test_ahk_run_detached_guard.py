"""Regression: run_detached must not crash when the AHK executable is absent."""
from pathlib import Path
from navig.adapters.automation.ahk import AHKAdapter


def test_run_detached_returns_zero_when_executable_missing() -> None:
    adapter = AHKAdapter.__new__(AHKAdapter)
    adapter._executable = None
    assert adapter.run_detached(Path("noop.ahk")) == 0
