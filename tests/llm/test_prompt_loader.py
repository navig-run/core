"""Hermetic unit tests for navig.prompt_loader."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


def _write_prompt(directory: Path, slug: str, content: str) -> Path:
    """Write a prompt file under directory/prompts/<slug>.md."""
    parts = slug.split("/")
    folder = directory / "prompts" / Path(*parts).parent if "/" in slug else directory / "prompts"
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / (parts[-1] + ".md")
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# helpers for cache management
# ---------------------------------------------------------------------------


def _clear_cache():
    from navig.prompt_loader import load_prompt

    load_prompt.cache_clear()


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


class TestLoadPrompt:
    def setup_method(self):
        _clear_cache()

    def teardown_method(self):
        _clear_cache()

    def test_missing_file_returns_warning_string(self, tmp_path):
        from navig.prompt_loader import load_prompt

        with patch("navig.prompt_loader.builtin_store_dir", return_value=tmp_path):
            result = load_prompt("nonexistent_slug")

        assert result == "Warning: Prompt nonexistent_slug not found."

    def test_plain_content_returned(self, tmp_path):
        from navig.prompt_loader import load_prompt

        _write_prompt(tmp_path, "hello", "Hello world prompt")

        with patch("navig.prompt_loader.builtin_store_dir", return_value=tmp_path):
            result = load_prompt("hello")

        assert result == "Hello world prompt"

    def test_frontmatter_stripped(self, tmp_path):
        from navig.prompt_loader import load_prompt

        content = "---\ntitle: Test\nversion: 1\n---\nActual prompt body"
        _write_prompt(tmp_path, "with_fm", content)

        with patch("navig.prompt_loader.builtin_store_dir", return_value=tmp_path):
            result = load_prompt("with_fm")

        assert result == "Actual prompt body"
        assert "title:" not in result

    def test_incomplete_frontmatter_returns_full_content(self, tmp_path):
        from navig.prompt_loader import load_prompt

        # Only one --- delimiter; should fall through to plain content
        content = "---\nno closing delimiter\njust content"
        _write_prompt(tmp_path, "partial_fm", content)

        with patch("navig.prompt_loader.builtin_store_dir", return_value=tmp_path):
            result = load_prompt("partial_fm")

        assert "no closing delimiter" in result

    def test_cache_returns_same_object_on_second_call(self, tmp_path):
        from navig.prompt_loader import load_prompt

        _write_prompt(tmp_path, "cached_slug", "Cached content")

        with patch("navig.prompt_loader.builtin_store_dir", return_value=tmp_path) as mock_dir:
            r1 = load_prompt("cached_slug")
            r2 = load_prompt("cached_slug")

        # Both calls return identical result; builtin_store_dir called once only
        assert r1 == r2
        assert mock_dir.call_count == 1

    def test_nested_slug_resolves(self, tmp_path):
        from navig.prompt_loader import load_prompt

        _write_prompt(tmp_path, "browser/vision", "Browser vision prompt")

        with patch("navig.prompt_loader.builtin_store_dir", return_value=tmp_path):
            result = load_prompt("browser/vision")

        assert result == "Browser vision prompt"
