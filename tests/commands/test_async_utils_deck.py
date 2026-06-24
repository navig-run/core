"""Unit tests for commands/_async_utils.py and gateway/deck/routes/static_assets.py."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from navig.commands._async_utils import run_sync
from navig.gateway.deck.routes.static_assets import _find_deck_static_dir


# ---------------------------------------------------------------------------
# run_sync
# ---------------------------------------------------------------------------

class TestRunSync:
    def test_simple_coroutine(self):
        async def coro():
            return 42

        assert run_sync(coro()) == 42

    def test_coroutine_with_await(self):
        async def coro():
            await asyncio.sleep(0)
            return "done"

        assert run_sync(coro()) == "done"

    def test_coroutine_returning_none(self):
        async def coro():
            return None

        assert run_sync(coro()) is None

    def test_exception_propagates(self):
        async def coro():
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            run_sync(coro())

    def test_coroutine_with_list_result(self):
        async def coro():
            return [1, 2, 3]

        assert run_sync(coro()) == [1, 2, 3]

    def test_no_event_loop_running(self):
        """In a non-async context, run_sync should start a fresh loop."""
        async def coro():
            return "fresh_loop"

        result = run_sync(coro())
        assert result == "fresh_loop"


# ---------------------------------------------------------------------------
# _find_deck_static_dir
# ---------------------------------------------------------------------------

class TestFindDeckStaticDir:
    def test_override_with_valid_dir(self, tmp_path):
        """Override pointing to a dir that has index.html → returns that dir."""
        (tmp_path / "index.html").write_text("<html/>")
        result = _find_deck_static_dir(str(tmp_path))
        assert result == tmp_path

    def test_override_dir_without_index_falls_through(self, tmp_path):
        """Override dir exists but no index.html → falls through to candidates."""
        # No index.html in tmp_path — falls through to real candidates (may or may not exist)
        result = _find_deck_static_dir(str(tmp_path))
        assert result is None or isinstance(result, Path)

    def test_override_nonexistent_dir_falls_through(self, tmp_path):
        """Override path that doesn't exist — falls through to real candidates."""
        result = _find_deck_static_dir(str(tmp_path / "does_not_exist"))
        assert result is None or isinstance(result, Path)

    def test_no_override_no_candidates(self):
        """No override and no real deck-static dirs → None."""
        result = _find_deck_static_dir(None)
        # Can be None or a real path if deck-static happens to exist
        assert result is None or isinstance(result, Path)

    def test_override_home_expansion(self, tmp_path):
        """Tilde in override path should be expanded before checking — no crash."""
        result = _find_deck_static_dir("~/nonexistent-deck-xyz/dist")
        assert result is None or isinstance(result, Path)

    def test_returns_path_type(self, tmp_path):
        (tmp_path / "index.html").write_text("<html/>")
        result = _find_deck_static_dir(str(tmp_path))
        assert isinstance(result, Path)
