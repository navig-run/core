"""
Telegram HTML formatting utilities.

All Telegram bot messages in NAVIG use ``parse_mode="HTML"`` exclusively.
This module provides helpers to build properly escaped HTML strings for
Telegram's supported HTML tag set and to convert LLM Markdown output to HTML.

Supported Telegram HTML tags (through Bot API 10.2, July 2026):
  <b>, <strong>          bold
  <i>, <em>              italic
  <u>, <ins>             underline
  <s>, <strike>, <del>   strikethrough
  <code>                 inline pre-formatted code
  <pre>                  pre-formatted code block
  <a href="…">           inline link / text mention (tg://user?id=…)
  <tg-spoiler>           spoiler
  <blockquote>           blockquote
  <blockquote expandable> collapsible quote — folds behind a native "Show More"

``md_to_html`` emits ``<blockquote expandable>`` for long or ``>!``-marked quotes.
For document-grade output (tables, section headings, dividers, collapsible detail
blocks) and the 32,768-char / "Show More" message capacity, use the rich-message
API instead — ``TelegramChannel.send_rich_message`` (Bot API 10.2 ``sendRichMessage``).
``split_html_for_telegram`` keeps formatting tags balanced when an HTML body must be
split across the classic 4,096-char ``sendMessage`` limit.

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
    "split_html_for_telegram",
    "MAX_MESSAGE_UTF16",
]

# Classic Bot API sendMessage/editMessageText text limit (UTF-16 code units).
# Rich messages (sendRichMessage, Bot API 10.2) carry far more (32,768) and are the
# vehicle for long document-grade replies — see TelegramChannel.send_rich_message.
MAX_MESSAGE_UTF16 = 4096


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
    blockquote_expand = False  # a leading "> !" marker forces an expandable quote

    def _flush_blockquote() -> None:
        nonlocal blockquote_expand
        if blockquote_buf:
            joined = "\n".join(blockquote_buf)
            # Fold long quotes behind Telegram's native "Show More" (Bot API 10.2):
            # an explicit "> !" marker, or a quote big enough to be worth collapsing.
            expand = blockquote_expand or joined.count("\n") >= 3 or len(joined) >= 300
            lines_out.append(blockquote(joined, expandable=expand))
            blockquote_buf.clear()
        blockquote_expand = False

    for line in text.split("\n"):
        stripped = line.lstrip()

        # Blockquote  "> text"  (a leading "> !" forces an expandable quote)
        if stripped.startswith("&gt;"):
            rest = stripped[4:]  # drop the escaped ">"
            forced = rest[:1] == "!"
            if forced:
                rest = rest[1:]
            if forced or rest == "" or rest[:1] == " ":
                if forced:
                    blockquote_expand = True
                blockquote_buf.append(rest[1:] if rest[:1] == " " else rest)
                continue
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


# ─────────────────────────────────────────────────────────────────────────────
# Tag-safe splitting for over-length HTML messages
# ─────────────────────────────────────────────────────────────────────────────

# Telegram formatting tags whose open/close balance must survive a split boundary.
_TG_BALANCE_TAGS = frozenset({
    "pre", "code", "blockquote", "b", "strong", "i", "em",
    "u", "ins", "s", "strike", "del", "tg-spoiler", "a",
})
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][\w-]*)([^>]*)>")


def _open_tag_stack(chunk: str) -> list[tuple[str, str]]:
    """Return the stack of still-open ``(tag_name, full_open_tag)`` at end of *chunk*.

    Only Telegram formatting tags are tracked; unknown tags are ignored. A closing
    tag pops the most recent matching open, tolerating mismatched/overlapping nesting.
    """
    stack: list[tuple[str, str]] = []
    for m in _TAG_RE.finditer(chunk):
        closing, name = m.group(1), m.group(2).lower()
        if name not in _TG_BALANCE_TAGS:
            continue
        if closing:
            for i in range(len(stack) - 1, -1, -1):
                if stack[i][0] == name:
                    del stack[i]
                    break
        else:
            stack.append((name, m.group(0)))
    return stack


def split_html_for_telegram(
    html: str, *, max_utf16: int = MAX_MESSAGE_UTF16
) -> list[str]:
    """Split *html* into chunks under *max_utf16* UTF-16 units, keeping tags balanced.

    Breaks at newline boundaries where possible (via
    :func:`navig.gateway.channels.base.utf16_safe_split`), then closes any Telegram
    formatting tag left open at a boundary and re-opens it at the start of the next
    chunk — so no chunk is sent with a half-open ``<pre>``/``<blockquote>``/``<b>`` …
    tag that would trip Telegram's HTML parser. Empty input returns ``[]``.
    """
    from navig.gateway.channels.base import utf16_safe_split

    if not html:
        return []
    # Reserve a little headroom so re-opened tags + closers can't push a chunk over.
    budget = max(256, max_utf16 - 128)
    raw = utf16_safe_split(html, max_utf16=budget)
    if len(raw) <= 1:
        return [html] if html else []

    out: list[str] = []
    reopen: list[str] = []
    for piece in raw:
        chunk = "".join(reopen) + piece
        stack = _open_tag_stack(chunk)
        closer = "".join(f"</{name}>" for name, _ in reversed(stack))
        out.append(chunk + closer)
        reopen = [full for _, full in stack]
    return out
