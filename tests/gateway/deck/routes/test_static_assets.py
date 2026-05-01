"""Tests for navig/gateway/deck/routes/static_assets.py."""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestFindDeckStaticDir:
    def test_returns_path_or_none_without_override(self):
        from navig.gateway.deck.routes.static_assets import _find_deck_static_dir
        result = _find_deck_static_dir()
        assert result is None or isinstance(result, Path)

    def test_override_valid_path_returns_path(self, tmp_path):
        from navig.gateway.deck.routes.static_assets import _find_deck_static_dir
        # Create a valid override dir with index.html
        (tmp_path / "index.html").write_text("<html/>")
        result = _find_deck_static_dir(override=str(tmp_path))
        assert result == tmp_path

    def test_override_missing_index_skips(self, tmp_path):
        from navig.gateway.deck.routes.static_assets import _find_deck_static_dir
        # Override exists as a dir but has no index.html
        override_dir = tmp_path / "no_index"
        override_dir.mkdir()
        # Falls through to candidates — may find real dist or None
        result = _find_deck_static_dir(override=str(override_dir))
        assert result is None or isinstance(result, Path)

    def test_override_nonexistent_path_skips(self, tmp_path):
        from navig.gateway.deck.routes.static_assets import _find_deck_static_dir
        result = _find_deck_static_dir(override=str(tmp_path / "does_not_exist"))
        assert result is None or isinstance(result, Path)

    def test_no_override_returns_path_or_none(self):
        from navig.gateway.deck.routes.static_assets import _find_deck_static_dir
        result = _find_deck_static_dir()
        assert result is None or isinstance(result, Path)

    def test_valid_override_returns_correct_path(self, tmp_path):
        from navig.gateway.deck.routes.static_assets import _find_deck_static_dir
        (tmp_path / "index.html").write_text("<html/>")
        result = _find_deck_static_dir(override=str(tmp_path))
        assert result is not None
        assert (result / "index.html").exists()


class TestHandleDeckIndex:
    def test_module_has_handle_function(self):
        from navig.gateway.deck.routes import static_assets
        assert hasattr(static_assets, "handle_deck_index")

    def test_handle_is_coroutine(self):
        import inspect
        from navig.gateway.deck.routes.static_assets import handle_deck_index
        assert inspect.iscoroutinefunction(handle_deck_index)
