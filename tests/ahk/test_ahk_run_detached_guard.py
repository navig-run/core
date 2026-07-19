"""Regression: run_detached must not crash when the AHK executable is absent.

Before the fix it referenced ``self.executable`` (AttributeError) and would
``NoneType.exists()`` crash; it must return 0 (no-op) instead.
"""
from pathlib import Path

from navig.adapters.automation.ahk import AHKAdapter


def test_run_detached_returns_zero_when_executable_missing() -> None:
    adapter = AHKAdapter.__new__(AHKAdapter)   # bypass discovery
    adapter._executable = None                 # simulate "AHK not installed"
    assert adapter.run_detached(Path("noop.ahk")) == 0
