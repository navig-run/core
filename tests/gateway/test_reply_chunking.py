"""
tests/gateway/test_reply_chunking.py
─────────────────────────────────────
Tests for navig.gateway.reply_chunking (Item 7).

Imports only navig.gateway.reply_chunking — no server, no Telegram.
"""
from __future__ import annotations

import pytest

from navig.gateway.reply_chunking import (
    TELEGRAM_TEXT_LIMIT,
    ChunkMode,
    _chunk_paragraphs,
    _chunk_sentences,
    _chunk_words,
    _hard_split,
    _split_markdown_blocks,
    chunk_text,
)


# ──────────────────────────────────────────────────────────────────────────────
# Basic contract
# ──────────────────────────────────────────────────────────────────────────────


class TestChunkTextContract:
    def test_empty_returns_empty(self):
        assert chunk_text("") == []

    def test_short_text_returns_single_chunk(self):
        result = chunk_text("hello world", limit=100)
        assert result == ["hello world"]

    def test_all_chunks_within_limit(self):
        long = " ".join(["word"] * 500)
        for mode in ChunkMode:
            chunks = chunk_text(long, mode=mode, limit=40)
            for chunk in chunks:
                assert len(chunk) <= 40, f"mode={mode}, chunk={chunk!r}"

    def test_invalid_limit_raises(self):
        with pytest.raises(ValueError, match="limit must be"):
            chunk_text("text", limit=0)

    def test_telegram_default_limit(self):
        # Default limit attribute is exported and roughly correct
        assert TELEGRAM_TEXT_LIMIT == 4096


# ──────────────────────────────────────────────────────────────────────────────
# WORDS mode
# ──────────────────────────────────────────────────────────────────────────────


class TestChunkWords:
    def test_fits_in_one(self):
        assert _chunk_words("hello world", 100) == ["hello world"]

    def test_splits_on_boundary(self):
        # "aa bb cc" with limit=5 → ["aa bb", "cc"] (aa+space+bb=5)
        result = _chunk_words("aa bb cc", limit=5)
        assert result == ["aa bb", "cc"]

    def test_single_long_word_hard_split(self):
        result = _chunk_words("abcdefgh", limit=3)
        assert result == ["abc", "def", "gh"]

    def test_leading_trailing_spaces(self):
        result = _chunk_words("  hello  world  ", limit=100)
        assert result == ["hello world"]


# ──────────────────────────────────────────────────────────────────────────────
# SENTENCES mode
# ──────────────────────────────────────────────────────────────────────────────


class TestChunkSentences:
    def test_two_sentences_fit(self):
        text = "First sentence. Second sentence."
        result = _chunk_sentences(text, limit=100)
        assert len(result) == 1

    def test_two_sentences_split(self):
        text = "First sentence. Second sentence."
        # Each sentence is ~16 chars — force split at 20
        result = _chunk_sentences(text, limit=20)
        assert all(len(c) <= 20 for c in result)
        assert len(result) >= 2

    def test_joined_text_correct(self):
        text = "A. B. C."
        result = _chunk_sentences(text, limit=100)
        combined = " ".join(result)
        assert "A" in combined and "B" in combined and "C" in combined


# ──────────────────────────────────────────────────────────────────────────────
# PARAGRAPHS mode
# ──────────────────────────────────────────────────────────────────────────────


class TestChunkParagraphs:
    def test_two_paragraphs_together(self):
        text = "Para one.\n\nPara two."
        result = _chunk_paragraphs(text, limit=100)
        assert len(result) == 1
        assert "Para one" in result[0]

    def test_two_paragraphs_split(self):
        # Each paragraph is ~9 chars; force limit low enough to split
        text = "Para one.\n\nPara two."
        result = _chunk_paragraphs(text, limit=12)
        assert len(result) == 2

    def test_blank_lines_stripped(self):
        text = "\n\nHello\n\n"
        result = _chunk_paragraphs(text, limit=100)
        assert result == ["Hello"]


# ──────────────────────────────────────────────────────────────────────────────
# MARKDOWN_BLOCKS mode
# ──────────────────────────────────────────────────────────────────────────────


class TestChunkMarkdownBlocks:
    def _call(self, text, limit=4096):
        return chunk_text(text, mode=ChunkMode.MARKDOWN_BLOCKS, limit=limit)

    def test_code_block_kept_intact(self):
        text = "Intro\n\n```python\nx = 1\n```\n\nOutro"
        result = self._call(text)
        # All code fence content should appear in some chunk
        combined = "\n".join(result)
        assert "```python" in combined
        assert "x = 1" in combined

    def test_prose_paragraphs_split(self):
        para_a = "A " * 50   # 100 chars
        para_b = "B " * 50   # 100 chars
        text = f"{para_a.strip()}\n\n{para_b.strip()}"
        result = self._call(text, limit=120)
        assert all(len(c) <= 120 for c in result)
        assert len(result) >= 2

    def test_empty_text(self):
        assert self._call("") == []


# ──────────────────────────────────────────────────────────────────────────────
# _split_markdown_blocks helper
# ──────────────────────────────────────────────────────────────────────────────


class TestSplitMarkdownBlocks:
    def test_plain_prose(self):
        blocks = _split_markdown_blocks("Hello\n\nWorld")
        assert "Hello" in blocks
        assert "World" in blocks

    def test_fence_is_single_block(self):
        text = "Prose\n\n```\ncode here\n```\n\nMore prose"
        blocks = _split_markdown_blocks(text)
        fence_blocks = [b for b in blocks if b.startswith("```")]
        assert len(fence_blocks) == 1
        assert "code here" in fence_blocks[0]

    def test_tilde_fence(self):
        text = "~~~\ncode\n~~~"
        blocks = _split_markdown_blocks(text)
        fence_blocks = [b for b in blocks if b.startswith("~~~")]
        assert len(fence_blocks) == 1


# ──────────────────────────────────────────────────────────────────────────────
# _hard_split
# ──────────────────────────────────────────────────────────────────────────────


class TestHardSplit:
    def test_even_split(self):
        assert _hard_split("123456", 2) == ["12", "34", "56"]

    def test_uneven_split(self):
        assert _hard_split("12345", 2) == ["12", "34", "5"]

    def test_single_chunk(self):
        assert _hard_split("abc", 10) == ["abc"]


# ──────────────────────────────────────────────────────────────────────────────
# Round-trip integrity
# ──────────────────────────────────────────────────────────────────────────────


class TestRoundTrip:
    """Re-joining chunks must recover the original words in order."""

    LONG_TEXT = " ".join([f"word{i}" for i in range(200)])

    def test_words_roundtrip(self):
        chunks = chunk_text(self.LONG_TEXT, mode=ChunkMode.WORDS, limit=50)
        combined = " ".join(chunks)
        assert combined == self.LONG_TEXT

    def test_sentences_roundtrip(self):
        text = "Hello world. This is a test. Another sentence here."
        chunks = chunk_text(text, mode=ChunkMode.SENTENCES, limit=20)
        combined = " ".join(chunks)
        # All words from original appear in combined
        for word in ["Hello", "test", "sentence"]:
            assert word in combined
