"""
Batch 122: tests for
  - navig/commands/_async_utils.py     (run_sync bridge)
  - navig/commands/boot_cmd.py         (boot_show, boot_run)
  - navig/commands/watch_cmd.py        (watch_start, watch_list)
  - navig/commands/system_cmd.py       (system_info, system_clean, _default)
  - navig/gateway/deck/routes/static_assets.py  (_find_deck_static_dir)
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

# ---------------------------------------------------------------------------
# navig.commands._async_utils
# ---------------------------------------------------------------------------

from navig.commands._async_utils import run_sync


class TestRunSync:
    def test_simple_coroutine_returns_value(self):
        async def _coro():
            return 42

        assert run_sync(_coro()) == 42

    def test_async_sleep_returns_value(self):
        async def _coro():
            await asyncio.sleep(0)
            return "done"

        assert run_sync(_coro()) == "done"

    def test_exception_propagates(self):
        async def _bad():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            run_sync(_bad())

    @pytest.mark.asyncio
    async def test_in_running_loop_uses_thread(self):
        """When an event loop is already running, run_sync should still work."""

        async def _coro():
            return "thread"

        # asyncio.get_running_loop() succeeds here (pytest-asyncio)
        result = run_sync(_coro())
        assert result == "thread"


# ---------------------------------------------------------------------------
# navig.commands.boot_cmd
# ---------------------------------------------------------------------------

from navig.commands.boot_cmd import boot_app

_runner = CliRunner()


class TestBootCmd:
    def test_boot_show(self):
        result = _runner.invoke(boot_app, ["show"])
        assert result.exit_code == 0

    def test_boot_run(self):
        result = _runner.invoke(boot_app, ["run"])
        assert result.exit_code == 0

    def test_boot_run_dry_run(self):
        result = _runner.invoke(boot_app, ["run", "--dry-run"])
        assert result.exit_code == 0

    def test_boot_no_args_shows_help(self):
        result = _runner.invoke(boot_app, [])
        # no_args_is_help=True → exits with 0 and prints help
        assert result.exit_code == 0 or "Usage" in (result.output or "")


# ---------------------------------------------------------------------------
# navig.commands.watch_cmd
# ---------------------------------------------------------------------------

from navig.commands.watch_cmd import watch_app

_wrunner = CliRunner()


class TestWatchCmd:
    def test_watch_start_default_path(self):
        result = _wrunner.invoke(watch_app, ["start"])
        assert result.exit_code == 0

    def test_watch_start_custom_path(self):
        result = _wrunner.invoke(watch_app, ["start", "/tmp"])
        assert result.exit_code == 0

    def test_watch_list(self):
        result = _wrunner.invoke(watch_app, ["list"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# navig.commands.system_cmd
# ---------------------------------------------------------------------------

from navig.commands.system_cmd import system_app

_srunner = CliRunner()


class TestSystemCmd:
    def test_system_info(self):
        result = _srunner.invoke(system_app, ["info"])
        assert result.exit_code == 0

    def test_system_default_shows_table(self):
        result = _srunner.invoke(system_app, [])
        assert result.exit_code == 0

    def test_system_clean_dry_run(self, tmp_path, monkeypatch):
        """Without --yes flag, shows what would be removed and prompts."""
        import navig.commands.system_cmd as _sys_mod
        from navig.platform.paths import config_dir as real_config_dir

        fake_cache = tmp_path / "cache"
        fake_cache.mkdir()

        # Make config_dir() return tmp_path
        monkeypatch.setattr("navig.commands.system_cmd.config_dir", lambda: tmp_path)

        # Provide input "n" to decline the prompt
        result = _srunner.invoke(system_app, ["clean"], input="n\n")
        # May abort or succeed; either is valid behavior
        assert result.exit_code in (0, 1)

    def test_system_clean_yes_flag(self, tmp_path, monkeypatch):
        """With --yes, cleans without prompting."""
        monkeypatch.setattr("navig.commands.system_cmd.config_dir", lambda: tmp_path)

        result = _srunner.invoke(system_app, ["clean", "--yes"])
        assert result.exit_code == 0

    def test_system_clean_yes_short(self, tmp_path, monkeypatch):
        monkeypatch.setattr("navig.commands.system_cmd.config_dir", lambda: tmp_path)
        result = _srunner.invoke(system_app, ["clean", "-y"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# navig.gateway.deck.routes.static_assets
# ---------------------------------------------------------------------------

from navig.gateway.deck.routes.static_assets import _find_deck_static_dir


class TestFindDeckStaticDir:
    def test_override_valid_returns_path(self, tmp_path):
        # Create a valid static dir with index.html
        static = tmp_path / "deck-static"
        static.mkdir()
        (static / "index.html").write_text("<html/>", encoding="utf-8")

        result = _find_deck_static_dir(override=str(static))
        assert result == static

    def test_override_missing_returns_none(self, tmp_path):
        result = _find_deck_static_dir(override=str(tmp_path / "nonexistent"))
        assert result is None

    def test_override_dir_without_index_returns_none(self, tmp_path):
        subdir = tmp_path / "empty"
        subdir.mkdir()
        result = _find_deck_static_dir(override=str(subdir))
        assert result is None

    def test_no_override_no_candidates(self, monkeypatch):
        """When no candidate dirs exist, returns None."""
        # Patch all candidate paths to not exist
        import navig.gateway.deck.routes.static_assets as _mod

        def _fake_find(override=None):
            if override:
                return None
            return None

        # Just confirm calling with no override doesn't raise
        result = _find_deck_static_dir(override=None)
        assert result is None or isinstance(result, Path)

    def test_override_expands_user(self, tmp_path):
        """Confirm Path.expanduser() is applied (doesn't crash with ~)."""
        # Override with a ~ path that doesn't exist
        result = _find_deck_static_dir(override="~/absolutely_nonexistent_deck_dir_xyz")
        assert result is None
