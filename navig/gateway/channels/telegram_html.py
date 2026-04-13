"""
Telegram HTML formatting utilities.

All Telegram bot messages in NAVIG use ``parse_mode="HTML"`` exclusively.
This module provides helpers to build properly escaped HTML strings for
Telegram's supported HTML tag set and to convert LLM Markdown output to HTML.

Supported Telegram HTML tags (as of Bot API 7.x):
  <b>, <strong>          bold
  <i>, <em>              italic
  <u>, <ins>             underline
  <s>, <strike>, <del>   strikethrough
  <code>                 inline pre-formatted code
  <pre>                  pre-formatted code block
  <a href="…">           inline link / text mention (tg://user?id=…)
  <tg-spoiler>           spoiler
  <blockquote>           blockquote (optional expandable attribute)

Only <, >, & and " must be escaped inside attribute values;
plain text nodes need <, > and & escaped.

Usage::

    from navig.gateway.channels.telegram_html import (
        md_to_html, html_escape, bold, italic, code, pre,
        blockquote, link, mention, underline, strike, spoiler,
    )

    text = md_to_html(llm_response)         # LLM/Markdown → HTML
    greeting = f"Hello {bold('World')}!"    # → "Hello <b>World</b>!"
"""

from __future__ import annotations

import html as _html_module
import re
from datetime import datetime, timezone

__all__ = [
    "html_escape",
    "md_to_html",
    "fmt_dt",
    "bold",
    "italic",
    "underline",
    "strike",
    "spoiler",
    "code",
    "pre",
    "blockquote",
    "link",
    "mention",
]


# ─────────────────────────────────────────────────────────────────────────────
# Primitives
# ─────────────────────────────────────────────────────────────────────────────

def html_escape(text: str) -> str:
    """Escape HTML special characters for safe inclusion in messages.

    Escapes ``&``, ``<``, ``>`` — does *not* escape ``"`` because Telegram
    message bodies are not attribute values.
    """
    return _html_module.escape(str(text), quote=False)


def bold(text: str) -> str:
    """Wrap *text* in ``<b>…</b>``."""
    return f"<b>{text}</b>"


def italic(text: str) -> str:
    """Wrap *text* in ``<i>…</i>``."""
    return f"<i>{text}</i>"


def underline(text: str) -> str:
    """Wrap *text* in ``<u>…</u>``."""
    return f"<u>{text}</u>"


def strike(text: str) -> str:
    """Wrap *text* in ``<s>…</s>``."""
    return f"<s>{text}</s>"


def spoiler(text: str) -> str:
    """Wrap *text* in ``<tg-spoiler>…</tg-spoiler>``."""
    return f"<tg-spoiler>{text}</tg-spoiler>"


def code(text: str) -> str:
    """Inline code — escapes *text* and wraps in ``<code>…</code>``."""
    return f"<code>{html_escape(text)}</code>"


def pre(text: str, lang: str = "") -> str:
    """Pre-formatted code block.

    If *lang* is given, sets the ``class`` attribute to ``"language-{lang}"``
    so Telegram highlights the block on clients that support it.
    """
    escaped = html_escape(text)
    if lang:
        return f'<pre><code class="language-{html_escape(lang)}">{escaped}</code></pre>'
    return f"<pre>{escaped}</pre>"


def blockquote(text: str, *, expandable: bool = False) -> str:
    """Blockquote element.

    When *expandable* is ``True``, adds the ``expandable`` attribute so very
    long quoted passages are collapsed by default in newer Telegram clients.
    """
    if expandable:
        return f"<blockquote expandable>{text}</blockquote>"
    return f"<blockquote>{text}</blockquote>"


def link(display_text: str, url: str) -> str:
    """Inline hyperlink ``<a href="url">text</a>``.

    The *url* is embedded verbatim so callers must ensure it is valid.
    """
    return f'<a href="{url}">{display_text}</a>'


def mention(name: str, user_id: int) -> str:
    """Text mention that links to a Telegram user without needing @username."""
    return f'<a href="tg://user?id={user_id}">{html_escape(name)}</a>'


# ─────────────────────────────────────────────────────────────────────────────
# Date/time helper
# ─────────────────────────────────────────────────────────────────────────────

def fmt_dt(ts: int) -> str:
    """Format a Unix timestamp as ``"1 Jan 2026, 14:30 UTC"``."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%-d %b %Y, %H:%M UTC")


# ─────────────────────────────────────────────────────────────────────────────
# Markdown → HTML converter
# ─────────────────────────────────────────────────────────────────────────────

def md_to_html(text: str) -> str:
    """Convert LLM-generated Markdown to Telegram-safe HTML.

    Handles the most common patterns produced by language models:

    * ``**bold**`` / ``__bold__``  → ``<b>bold</b>``
    * ``*italic*`` / ``_italic_``  → ``<i>italic</i>``
    * ``~~strike~~``               → ``<s>strike</s>``
    * `` `inline code` ``          → ``<code>inline code</code>``
    * `` ```lang↵…↵``` ``          → ``<pre><code class="language-lang">…</code></pre>``
    * ``# H1`` / ``## H2`` / ``### H3`` → ``<b>heading</b>``
    * ``> quote``                  → ``<blockquote>quote</blockquote>``
    * ``* item`` / ``- item``       → ``• item``
    * ``+ sub-item``               → ``  ◦ sub-item``
    * ``[text](url)``              → ``<a href="url">text</a>``

    Already-escaped HTML entities and non-Markdown content pass through
    safely because the function HTML-escapes the source before applying tags.

    Special handling:
    - Fenced code blocks are extracted *before* escaping so their content
      is preserved verbatim (only the block delimiters are consumed).
    - Inline ``**bold**`` is processed before ``*italic*`` so the stronger
      pattern wins.
    """
    if not text:
        return ""

    text = str(text)

    # ── 1. Extract fenced code blocks before any escaping ──────────────────
    #    Replace each ```block``` with a placeholder, store original content.
    code_blocks: list[str] = []

    def _store_code_block(m: re.Match) -> str:
        lang = (m.group(1) or "").strip()
        content = m.group(2)
        escaped_content = _html_module.escape(content, quote=False)
        if lang:
            block = f'<pre><code class="language-{html_escape(lang)}">{escaped_content}</code></pre>'
        else:
            block = f"<pre>{escaped_content}</pre>"
        code_blocks.append(block)
        return f"\x00CODE{len(code_blocks) - 1}\x00"

    text = re.sub(
        r"```([^\n`]*)\n(.*?)```",
        _store_code_block,
        text,
        flags=re.DOTALL,
    )

    # ── 2. HTML-escape everything that remains ──────────────────────────────
    text = _html_module.escape(text, quote=False)

    # ── 3. Line-level transforms ────────────────────────────────────────────
    lines_out: list[str] = []
    blockquote_buf: list[str] = []

    def _flush_blockquote() -> None:
        if blockquote_buf:
            joined = "\n".join(blockquote_buf)
            lines_out.append(f"<blockquote>{joined}</blockquote>")
            blockquote_buf.clear()

    for line in text.split("\n"):
        stripped = line.lstrip()

        # Blockquote  > text
        if stripped.startswith("&gt; ") or stripped == "&gt;":
            # html.escape already turned > into &gt;
            blockquote_buf.append(stripped[5:] if stripped.startswith("&gt; ") else "")
            continue
        else:
            _flush_blockquote()

        # ATX headings  # / ## / ###
        heading_m = re.match(r"^(#{1,3})\s+(.*)", stripped)
        if heading_m:
            lines_out.append(f"<b>{heading_m.group(2).strip()}</b>")
            continue

        # Bullet  * text  /  - text
        if re.match(r"^[*\-]\s+", stripped):
            lines_out.append(f"\u2022 {stripped[2:].strip()}")
            continue

        # Sub-bullet  + text
        if re.match(r"^\+\s+", stripped):
            lines_out.append(f"  \u25e6 {stripped[2:].strip()}")
            continue

        lines_out.append(line)

    _flush_blockquote()
    text = "\n".join(lines_out)

    # ── 4. Inline transforms ────────────────────────────────────────────────
    # Fenced inline code  `code`
    text = re.sub(r"`([^`\n]+?)`", lambda m: f"<code>{m.group(1)}</code>", text)

    # Bold  **text** or __text__  (must come before italic)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text, flags=re.DOTALL)

    # Italic  *text* or _text_  (after bold consumed double markers)
    text = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!_)_([^_\n]+?)_(?!_)", r"<i>\1</i>", text)

    # Strikethrough  ~~text~~
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)

    # Links  [text](url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

    # ── 5. Restore fenced code blocks ──────────────────────────────────────
    for i, block in enumerate(code_blocks):
        text = text.replace(f"\x00CODE{i}\x00", block)

    # ── 6. Clean up whitespace ──────────────────────────────────────────────
    # Collapse 3+ consecutive blank lines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip("\n")


# ─────────────────────────────────────────────────────────────────────────────
# V1 Markdown → HTML (for legacy V1 syntax used in bot UI strings)
# ─────────────────────────────────────────────────────────────────────────────

def v1_to_html(text: str) -> str:
    """Convert Telegram Markdown V1 syntax to HTML.

    Handles:
    * ``*bold*``         → ``<b>bold</b>``
    * `` `code` ``       → ``<code>code</code>``
    * ``_italic_``       → ``<i>italic</i>``
    * ``[text](url)``    → ``<a href="url">text</a>``

    Does NOT html-escape the text first — callers are responsible for
    ensuring that non-V1 portions are already safe. This is intentional
    so that partially-HTML strings (e.g. with emoji or existing entity
    references) can be upgraded cleanly.
    """
    # Bold *text*
    text = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<b>\1</b>", text)
    # Inline code `text`
    text = re.sub(r"`([^`\n]+?)`", lambda m: f"<code>{m.group(1)}</code>", text)
    # Italic _text_
    text = re.sub(r"(?<!_)_([^_\n]+?)_(?!_)", r"<i>\1</i>", text)
    # Links [text](url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text
