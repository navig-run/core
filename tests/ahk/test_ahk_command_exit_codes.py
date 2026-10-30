"""Regression: a failed AHK operation must exit non-zero, not print an error and exit 0.

Review F0593: kill / start / notify / volume / mute printed ``ch.error`` on an adapter
result with ``success=False`` and then fell off the function with exit code 0.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

ahk = pytest.importorskip("navig_ahk.commands.ahk")
from typer.testing import CliRunner  # noqa: E402

_FAIL = SimpleNamespace(success=False, stderr="adapter said no")


class _FailingAdapter:
    def kill_process(self, *a, **kw):
        return _FAIL

    def start_process(self, *a, **kw):
        return _FAIL

    def show_notification(self, *a, **kw):
        return _FAIL

    def set_volume(self, *a, **kw):
        return _FAIL

    def mute(self, *a, **kw):
        return _FAIL


@pytest.mark.parametrize(
    "argv",
    [
        ["kill", "notepad.exe", "--force"],
        ["start", "notepad.exe"],
        ["notify", "hello"],
        ["volume", "40"],
        ["mute"],
    ],
)
def test_failed_operation_exits_nonzero(monkeypatch, argv):
    monkeypatch.setattr(ahk, "_get_adapter", lambda: _FailingAdapter())
    result = CliRunner().invoke(ahk.ahk_app, argv)
    assert result.exit_code == 1, result.output
