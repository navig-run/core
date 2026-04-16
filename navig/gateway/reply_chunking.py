"""
navig/gateway/reply_chunking.py
────────────────────────────────
Mode-typed text chunking pipeline for channel replies.

Long outbound messages (especially Telegram, which caps at 4 096 bytes) must
be split before delivery.  Previously every handler had its own ad-hoc split
logic; this module provides a single canonical entry point.

Usage::

    from navig.gateway.reply_chunking import ChunkMode, chunk_text

    parts = chunk_text(long_text, ChunkMode.MARKDOWN_BLOCKS, limit=4096)
    for part in parts:
        await bot.send_message(chat_id, part)

``ChunkMode`` controls the granularity at which the text is split:

WORDS
    Split on whitespace.  Smallest granularity; always produces chunks
    ≤ *limit* bytes.
SENTENCES
    Split on sentence boundaries (``'. '``, ``'! '``, ``'? '``).  Falls
    back to word-splitting for sentences longer than *limit*.
PARAGRAPHS
    Split on blank lines.  Falls back to sentence-splitting for paragraphs
    longer than *limit*.
MARKDOWN_BLOCKS
    Preserve fenced code blocks (````` ``` … ``` `````) and block-quotes
    (``> …``) intact where possible.  Falls back to paragraph-splitting for
    blocks that exceed *limit*.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Sequence

# ──────────────────────────────────────────────────────────────────────────────
# Constants (single source of truth)
# ──────────────────────────────────────────────────────────────────────────────

# Telegram hard limit (bytes); callers may override per channel declaration
TELEGRAM_TEXT_LIMIT: int = 4_096

# Regex for fenced code-block delimiters (``` or ~~~)
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})", re.MULTILINE)
# Sentence-ending patterns (period/bang/question before whitespace or EOL)
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────


class ChunkMode(str, Enum):
    """Granularity at which long text is split for channel delivery."""

    WORDS = "words"
    SENTENCES = "sentences"
    PARAGRAPHS = "paragraphs"
    MARKDOWN_BLOCKS = "markdown_blocks"


def chunk_text(
    text: str,
    mode: ChunkMode = ChunkMode.PARAGRAPHS,
    limit: int = TELEGRAM_TEXT_LIMIT,
) -> list[str]:
    """Split *text* into a list of strings each ≤ *limit* characters.

    Parameters
    ----------
    text:
        The message text to split.
    mode:
        Splitting strategy (see :class:`ChunkMode`).
    limit:
        Maximum character count per chunk (not byte count; callers that care
        about bytes should use ``len(chunk.encode())`` after splitting).
        Must be ≥ 1.

    Returns
    -------
    list[str]
        Non-empty list of strings.  Each item has ``len(item) ≤ limit``
        (guaranteed for all modes).  The list is empty only when *text*
        is empty.

    Raises
    ------
    ValueError:
        If *limit* < 1.
    """
    if limit < 1:
        raise ValueError(f"chunk_text: limit must be ≥ 1, got {limit}")
    if not text:
        return []

    dispatch = {
        ChunkMode.WORDS: _chunk_words,
        ChunkMode.SENTENCES: _chunk_sentences,
        ChunkMode.PARAGRAPHS: _chunk_paragraphs,
        ChunkMode.MARKDOWN_BLOCKS: _chunk_markdown_blocks,
    }
    return dispatch[mode](text, limit)


# ──────────────────────────────────────────────────────────────────────────────
# Mode implementations
# ──────────────────────────────────────────────────────────────────────────────


def _chunk_words(text: str, limit: int) -> list[str]:
    """Greedy word-level chunking — guaranteed to stay within *limit*."""
    chunks: list[str] = []
    current_parts: list[str] = []
    current_len = 0

    for word in text.split():
        # +1 for the space separator (except for first word in chunk)
        needed = len(word) + (1 if current_parts else 0)
        if current_len + needed > limit:
            if current_parts:
                chunks.append(" ".join(current_parts))
            # A single word longer than limit: hard-split it
            if len(word) > limit:
                chunks.extend(_hard_split(word, limit))
                current_parts = []
                current_len = 0
            else:
                current_parts = [word]
                current_len = len(word)
        else:
            current_parts.append(word)
            current_len += needed

    if current_parts:
        chunks.append(" ".join(current_parts))
    return chunks


def _chunk_sentences(text: str, limit: int) -> list[str]:
    """Sentence-level chunking with word-split fallback."""
    raw_sentences = _SENTENCE_END_RE.split(text)
    return _pack_segments(raw_sentences, separator=" ", limit=limit,
                          fallback=_chunk_words)


def _chunk_paragraphs(text: str, limit: int) -> list[str]:
    """Paragraph-level chunking (blank-line delimited) with sentence fallback."""
    paragraphs = re.split(r"\n{2,}", text)
    return _pack_segments(paragraphs, separator="\n\n", limit=limit,
                          fallback=_chunk_sentences)


def _chunk_markdown_blocks(text: str, limit: int) -> list[str]:
    """Markdown-aware chunking that preserves fenced code blocks intact."""
    logical_blocks = _split_markdown_blocks(text)
    return _pack_segments(logical_blocks, separator="\n\n", limit=limit,
                          fallback=_chunk_paragraphs)


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────


def _pack_segments(
    segments: Sequence[str],
    separator: str,
    limit: int,
    fallback,
) -> list[str]:
    """Greedily pack *segments* into chunks separated by *separator*.

    When an individual segment exceeds *limit*, *fallback* is called on it
    to produce sub-chunks.
    """
    chunks: list[str] = []
    current_parts: list[str] = []
    current_len = 0
    sep_len = len(separator)

    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        if len(seg) > limit:
            # Flush current buffer first
            if current_parts:
                chunks.append(separator.join(current_parts))
                current_parts = []
                current_len = 0
            chunks.extend(fallback(seg, limit))
            continue
        # +sep_len for the separator (except at the start)
        needed = len(seg) + (sep_len if current_parts else 0)
        if current_len + needed > limit:
            if current_parts:
                chunks.append(separator.join(current_parts))
            current_parts = [seg]
            current_len = len(seg)
        else:
            current_parts.append(seg)
            current_len += needed

    if current_parts:
        chunks.append(separator.join(current_parts))
    return chunks


def _hard_split(text: str, limit: int) -> list[str]:
    """Split *text* mechanically at *limit* boundaries (last resort)."""
    return [text[i:i + limit] for i in range(0, len(text), limit)]


def _split_markdown_blocks(text: str) -> list[str]:
    """Segment *text* at fenced code-block and block-quote boundaries.

    Returns a list of logical blocks: each code-fence block is a single
    element; prose between fences is returned intact (callers can further
    split it).
    """
    lines = text.splitlines(keepends=True)
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    fence_char = ""

    for line in lines:
        m = _FENCE_RE.match(line)
        if not in_fence and m:
            # Start of a fenced block — flush prose, begin fence accumulation
            if current:
                blocks.append("".join(current))
                current = []
            in_fence = True
            fence_char = m.group(1)
            current.append(line)
        elif in_fence:
            current.append(line)
            # Closing fence: same or longer delimiter
            if _FENCE_RE.match(line) and line.strip().startswith(fence_char):
                blocks.append("".join(current))
                current = []
                in_fence = False
                fence_char = ""
        else:
            current.append(line)

    if current:
        blocks.append("".join(current))

    # Return paragraph-split prose blocks but keep code blocks atomic
    result: list[str] = []
    for block in blocks:
        stripped = block.strip()
        if stripped.startswith(("```", "~~~")):
            result.append(stripped)
        else:
            # Further split on blank lines
            for para in re.split(r"\n{2,}", stripped):
                p = para.strip()
                if p:
                    result.append(p)
    return result
