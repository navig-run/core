"""
Base platform adapter for NAVIG gateway channels.

Provides:
- :class:`BasePlatformAdapter` — abstract interface that every channel
  driver (Telegram, Matrix, CLI, …) should implement.
- UTF-16 length helpers for platforms that measure message size in UTF-16
  code units (e.g. Telegram Bot API).
- :func:`utf16_safe_split` — reliable chunker that respects both a character
  budget and a UTF-16 code-unit budget.

Background
----------
Telegram counts message length in UTF-16 code units, not Unicode code points.
Characters in the Basic Multilingual Plane (U+0000–U+FFFF) cost 1 unit;
Supplementary Plane characters (emoji, some CJK, U+10000+) cost 2 units.
Using ``len(text)`` to split overestimates capacity for BMP-only text and
underestimates it for messages heavy with emoji — leading to silent truncation
or API errors.

Usage::

    from navig.gateway.channels.base import utf16_len, utf16_safe_split

    chunks = utf16_safe_split(long_message, max_utf16=4096)
"""

from __future__ import annotations

import abc
from typing import Iterator, Optional, Sequence


# ---------------------------------------------------------------------------
# UTF-16 length helpers
# ---------------------------------------------------------------------------

def utf16_len(text: str) -> int:
    """Return the number of UTF-16 code units required to encode *text*.

    Single BMP characters count as 1 unit; supplementary-plane characters
    (U+10000 and above — most emoji, rare CJK) count as 2 units (surrogate pair).

    This matches how the Telegram Bot API measures ``text`` / ``caption``
    field lengths.

    Parameters
    ----------
    text:
        Unicode string to measure.

    Returns
    -------
    int
        UTF-16 code-unit count (equivalent to ``len(text.encode('utf-16-le')) // 2``).

    Examples::

        utf16_len("hello")          # 5 — all BMP
        utf16_len("\U0001F600")     # 2 — emoji is a surrogate pair
        utf16_len("hi \U0001F600") # 5  (2 + 1 + 2)
    """
    return len(text.encode("utf-16-le")) // 2


def utf16_safe_split(
    text: str,
    *,
    max_utf16: int = 4096,
    max_chars: Optional[int] = None,
    prefer_newline: bool = True,
) -> list[str]:
    """Split *text* into chunks that fit within *max_utf16* UTF-16 code units.

    Parameters
    ----------
    text:
        The text to split.
    max_utf16:
        Maximum UTF-16 code units per chunk (default: 4096, Telegram limit).
    max_chars:
        Optional additional character count limit per chunk.
    prefer_newline:
        When ``True`` (default), the splitter tries to break at the nearest
        ``\\n`` backwards from the split point rather than mid-word.

    Returns
    -------
    list[str]
        Ordered list of non-empty chunks.  Empty input returns ``[]``.
    """
    if not text:
        return []

    chunks: list[str] = []
    remaining = text

    while remaining:
        if utf16_len(remaining) <= max_utf16 and (
            max_chars is None or len(remaining) <= max_chars
        ):
            chunks.append(remaining)
            break

        # Binary-search for the largest prefix that fits.
        lo, hi = 1, len(remaining)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            candidate = remaining[:mid]
            fits = utf16_len(candidate) <= max_utf16
            if max_chars is not None:
                fits = fits and len(candidate) <= max_chars
            if fits:
                lo = mid
            else:
                hi = mid - 1

        split_at = lo

        # Prefer to break at a newline so chunks stay paragraph-aligned.
        if prefer_newline and split_at > 1:
            nl_pos = remaining.rfind("\n", 0, split_at)
            if nl_pos > 0:
                split_at = nl_pos + 1  # include the newline in the preceding chunk

        chunk = remaining[:split_at]
        remaining = remaining[split_at:]

        if chunk:
            chunks.append(chunk)

    return chunks


# ---------------------------------------------------------------------------
# Abstract base adapter
# ---------------------------------------------------------------------------

class BasePlatformAdapter(abc.ABC):
    """Minimal interface that every NAVIG gateway channel must satisfy.

    Concrete drivers (Telegram, Matrix, CLI, etc.) should subclass this and
    implement all abstract methods.  The gateway engine calls only methods
    defined here so new channels can be added without touching the core.

    Attributes
    ----------
    platform_name:
        Short identifier used in logs and ``SessionSource`` context
        (e.g. ``"telegram"``, ``"matrix"``, ``"cli"``).
    max_message_utf16:
        Per-message character budget in UTF-16 code units.  Defaults to
        4096 (Telegram MarkdownV2 limit).  Override in subclasses.
    """

    platform_name: str = "unknown"
    max_message_utf16: int = 4096

    # -- Sending ------------------------------------------------------------

    @abc.abstractmethod
    async def send_text(
        self,
        chat_id: str,
        text: str,
        *,
        parse_mode: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> str:
        """Send *text* to *chat_id* and return the delivered message id.

        Implementations must split *text* if it exceeds the platform limit
        (use :func:`utf16_safe_split` for UTF-16-counting platforms).
        """

    @abc.abstractmethod
    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        new_text: str,
        *,
        parse_mode: Optional[str] = None,
    ) -> bool:
        """Edit an existing message.  Returns ``True`` on success."""

    @abc.abstractmethod
    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        """Delete a message.  Returns ``True`` on success."""

    # -- Typing / reactions -------------------------------------------------

    async def send_typing(self, chat_id: str) -> None:
        """Send a typing indicator.  No-op by default."""

    # -- Helpers ------------------------------------------------------------

    def split_for_platform(self, text: str) -> list[str]:
        """Convenience wrapper: split *text* using this adapter's UTF-16 limit."""
        return utf16_safe_split(text, max_utf16=self.max_message_utf16)

    def measure(self, text: str) -> int:
        """Return the effective message length for this platform."""
        return utf16_len(text)
