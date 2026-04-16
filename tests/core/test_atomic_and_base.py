"""Tests for atomic_write_text and the UTF-16 gateway base helpers.

Covers:
  - navig.core.yaml_io.atomic_write_text()
  - navig.gateway.channels.base.utf16_len()
  - navig.gateway.channels.base.utf16_safe_split()
  - navig.gateway.channels.base.BasePlatformAdapter (abstract interface)
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from navig.core.yaml_io import atomic_write_text
from navig.gateway.channels.base import BasePlatformAdapter, utf16_len, utf16_safe_split


# ---------------------------------------------------------------------------
# atomic_write_text
# ---------------------------------------------------------------------------

class TestAtomicWriteText:
    def test_basic_write(self, tmp_path: Path):
        target = tmp_path / "notes.md"
        atomic_write_text(target, "# Hello\n\nWorld")
        assert target.read_text(encoding="utf-8") == "# Hello\n\nWorld"

    def test_creates_parent_dirs(self, tmp_path: Path):
        target = tmp_path / "sub" / "dir" / "file.md"
        atomic_write_text(target, "content")
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "content"

    def test_overwrites_existing_file(self, tmp_path: Path):
        target = tmp_path / "file.md"
        atomic_write_text(target, "v1")
        atomic_write_text(target, "v2")
        assert target.read_text(encoding="utf-8") == "v2"

    def test_no_temp_file_left_over(self, tmp_path: Path):
        target = tmp_path / "notes.md"
        atomic_write_text(target, "clean")
        temp_files = [f for f in tmp_path.iterdir() if ".navig~" in f.name]
        assert len(temp_files) == 0

    def test_empty_string(self, tmp_path: Path):
        target = tmp_path / "empty.md"
        atomic_write_text(target, "")
        assert target.read_text(encoding="utf-8") == ""

    def test_unicode_content(self, tmp_path: Path):
        target = tmp_path / "unicode.md"
        content = "日本語テスト 🎉 ñoño"
        atomic_write_text(target, content)
        assert target.read_text(encoding="utf-8") == content

    def test_custom_encoding(self, tmp_path: Path):
        target = tmp_path / "latin.txt"
        content = "café naïve résumé"
        atomic_write_text(target, content, encoding="latin-1")
        assert target.read_text(encoding="latin-1") == content


# ---------------------------------------------------------------------------
# utf16_len
# ---------------------------------------------------------------------------

class TestUtf16Len:
    def test_ascii(self):
        assert utf16_len("hello") == 5

    def test_bmp_non_ascii(self):
        # é is U+00E9 — one BMP code point = 1 UTF-16 unit
        assert utf16_len("café") == 4

    def test_emoji_counts_as_two(self):
        # 😀 is U+1F600 — outside BMP, needs surrogate pair = 2 UTF-16 units
        assert utf16_len("\U0001F600") == 2

    def test_mixed_bmp_and_supplementary(self):
        # "hi 😀" → 2 (hi) + 1 (space) + 2 (emoji) = 5
        assert utf16_len("hi \U0001F600") == 5

    def test_empty_string(self):
        assert utf16_len("") == 0

    def test_japanese(self):
        # 日 is U+65E5 — BMP → 1 unit
        assert utf16_len("日本語") == 3

    def test_equivalence_with_encode(self):
        text = "Hello 🌍 World"
        assert utf16_len(text) == len(text.encode("utf-16-le")) // 2


# ---------------------------------------------------------------------------
# utf16_safe_split
# ---------------------------------------------------------------------------

class TestUtf16SafeSplit:
    def test_short_text_is_single_chunk(self):
        chunks = utf16_safe_split("hello", max_utf16=100)
        assert chunks == ["hello"]

    def test_empty_returns_empty_list(self):
        assert utf16_safe_split("") == []

    def test_splits_at_max_boundary(self):
        text = "a" * 200
        chunks = utf16_safe_split(text, max_utf16=100)
        assert len(chunks) == 2
        for chunk in chunks:
            assert utf16_len(chunk) <= 100

    def test_all_chunks_fit(self):
        text = "word " * 1000
        max_utf16 = 200
        chunks = utf16_safe_split(text, max_utf16=max_utf16)
        for chunk in chunks:
            assert utf16_len(chunk) <= max_utf16

    def test_content_preserved(self):
        text = "Hello World! " * 50
        chunks = utf16_safe_split(text, max_utf16=100)
        assert "".join(chunks) == text

    def test_prefers_newline_break(self):
        text = "line one\nline two\nline three\nline four"
        # Force a split somewhere in the middle
        chunks = utf16_safe_split(text, max_utf16=20)
        # Each chunk should ideally not start mid-word (newline preference)
        for chunk in chunks:
            assert utf16_len(chunk) <= 20

    def test_emoji_heavy_text_fits(self):
        # Each emoji = 2 UTF-16 units; 50 emoji = 100 units
        text = "\U0001F600" * 60  # 120 units
        chunks = utf16_safe_split(text, max_utf16=100)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert utf16_len(chunk) <= 100

    def test_max_chars_respected(self):
        text = "a" * 200
        chunks = utf16_safe_split(text, max_utf16=9999, max_chars=50)
        for chunk in chunks:
            assert len(chunk) <= 50


# ---------------------------------------------------------------------------
# BasePlatformAdapter (abstract interface)
# ---------------------------------------------------------------------------

class TestBasePlatformAdapter:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            BasePlatformAdapter()  # type: ignore[abstract]

    def test_concrete_subclass_works(self):
        class MockAdapter(BasePlatformAdapter):
            platform_name = "mock"

            async def send_text(self, chat_id, text, *, parse_mode=None, reply_to=None):
                return "mock_msg_id"

            async def edit_message(self, chat_id, message_id, new_text, *, parse_mode=None):
                return True

            async def delete_message(self, chat_id, message_id):
                return True

        adapter = MockAdapter()
        assert adapter.platform_name == "mock"
        assert adapter.max_message_utf16 == 4096  # default

    def test_measure_delegates_to_utf16_len(self):
        class MockAdapter(BasePlatformAdapter):
            async def send_text(self, *a, **kw):
                return ""

            async def edit_message(self, *a, **kw):
                return True

            async def delete_message(self, *a, **kw):
                return True

        adapter = MockAdapter()
        text = "Hi \U0001F600"
        assert adapter.measure(text) == utf16_len(text)

    def test_split_for_platform_uses_max_utf16(self):
        class LimitedAdapter(BasePlatformAdapter):
            max_message_utf16 = 20

            async def send_text(self, *a, **kw):
                return ""

            async def edit_message(self, *a, **kw):
                return True

            async def delete_message(self, *a, **kw):
                return True

        adapter = LimitedAdapter()
        text = "word " * 10  # 50 chars, well over 20 UTF-16 units
        chunks = adapter.split_for_platform(text)
        for chunk in chunks:
            assert utf16_len(chunk) <= 20
