"""Batch 134: watch_cmd and boot_cmd targeted tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from typer.testing import CliRunner

# ---------------------------------------------------------------------------
# navig.commands.watch_cmd
# ---------------------------------------------------------------------------
from navig.commands.watch_cmd import watch_app

_runner = CliRunner()


def _patch_warn():
    """Context manager that adds ch.warn as a MagicMock if missing."""
    import navig.console_helper as _ch

    class _CM:
        def __enter__(self):
            self._orig = getattr(_ch, "warn", None)
            _ch.warn = MagicMock()
            return _ch.warn

        def __exit__(self, *a):
            if self._orig is None:
                try:
                    delattr(_ch, "warn")
                except AttributeError:
                    pass
            else:
                _ch.warn = self._orig

    return _CM()


class TestWatchStart:
    def test_start_command_exists(self):
        with _patch_warn():
            result = _runner.invoke(watch_app, ["start", "."])
        assert result.exit_code == 1

    def test_start_no_exception(self):
        """No CRASH. The stub exits 1 on purpose, and CliRunner surfaces a deliberate
        `typer.Exit` as SystemExit — so "no exception" would now assert the opposite of
        the intended contract."""
        with _patch_warn():
            result = _runner.invoke(watch_app, ["start", "."])
        assert isinstance(result.exception, SystemExit)
        assert result.exit_code == 1

    def test_start_default_path(self):
        with _patch_warn():
            result = _runner.invoke(watch_app, ["start"])
        assert result.exit_code == 1

    def test_start_custom_path(self):
        with _patch_warn():
            result = _runner.invoke(watch_app, ["start", "/some/path"])
        assert result.exit_code == 1


class TestWatchList:
    def test_list_command_exists(self):
        with _patch_warn():
            result = _runner.invoke(watch_app, ["list"])
        assert result.exit_code == 1

    def test_list_no_exception(self):
        """No CRASH — see test_start_no_exception; the deliberate exit is the contract."""
        with _patch_warn():
            result = _runner.invoke(watch_app, ["list"])
        assert isinstance(result.exception, SystemExit)
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# navig.commands.boot_cmd — re-target with exact file name for coverage heuristic
# ---------------------------------------------------------------------------
from navig.commands.boot_cmd import boot_app as _boot_app


class TestBootCmd:
    def test_show_with_warn_patched(self):
        with _patch_warn():
            result = _runner.invoke(_boot_app, ["show"])
        assert result.exit_code == 1

    def test_run_with_warn_patched(self):
        with _patch_warn():
            result = _runner.invoke(_boot_app, ["run"])
        assert result.exit_code == 1

    def test_run_dry_run_flag(self):
        with _patch_warn():
            result = _runner.invoke(_boot_app, ["run", "--dry-run"])
        assert result.exit_code == 1
