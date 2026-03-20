"""
Telegram Markdown Formatter — navig gateway channel module.

Converts standard Markdown (headings, lists, hr, blockquotes, bold/italic)
to Telegram-compatible Unicode markup via a deterministic regex pipeline.

No LLM calls — all transformations are single-pass regex substitutions.

Per-user preferences (heading symbols, bullet style, output format) are
persisted in a lightweight SQLite store (`formatter.db`) using the shared
``navig.storage.engine.Engine``.

Usage::

    from navig.gateway.channels.telegram_formatter import MarkdownFormatter, FormatterStore

    store = FormatterStore()
    prefs = store.get(user_id=123)
    formatter = MarkdownFormatter()
    output = formatter.convert("# Hello\\n- item\\n---", prefs)
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# Symbol pool (user-selectable per heading level)
# ─────────────────────────────────────────────────────────────────

SYMBOL_POOL: list[str] = [
    "■", "◼", "▪", "▪️", "▫", "▫️", "◻", "◾", "◽",
    "🔹", "🔸", "🔶", "🔷", "•", "◼️", "˙", "∘", "·",
]

# Default heading symbols matching the spec table
_DEFAULT_H1 = "■"
_DEFAULT_H2 = "◼"
_DEFAULT_H3 = "▪️"
_DEFAULT_H4 = "▫️"
_DEFAULT_BULLET = "•"
_DEFAULT_BLOCKQUOTE = "❝"
_DEFAULT_HR = "─────────────"

# Numbered list style options
NUMBERED_STYLE_EMOJI = "emoji"       # 1️⃣ 2️⃣ …
NUMBERED_STYLE_PLAIN = "plain"       # 1. 2. …
NUMBERED_STYLE_ROMAN = "roman"       # i. ii. …

OUTPUT_FORMAT_MDV2 = "mdv2"
OUTPUT_FORMAT_PLAIN = "plain"
OUTPUT_FORMAT_HTML = "html"

_EMOJI_DIGITS: dict[int, str] = {
    1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣",
    6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟",
}
_ROMAN: list[tuple[int, str]] = [
    (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"),
    (100, "c"), (90, "xc"), (50, "l"), (40, "xl"),
    (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
]


def _to_roman(n: int) -> str:
    result = ""
    for val, sym in _ROMAN:
        while n >= val:
            result += sym
            n -= val
    return result


# ─────────────────────────────────────────────────────────────────
# FormatterPrefs dataclass
# ─────────────────────────────────────────────────────────────────

@dataclass
class FormatterPrefs:
    """Per-user formatting preferences."""

    h1_symbol: str = _DEFAULT_H1
    h2_symbol: str = _DEFAULT_H2
    h3_symbol: str = _DEFAULT_H3
    h4_symbol: str = _DEFAULT_H4
    bullet_style: str = _DEFAULT_BULLET
    numbered_style: str = NUMBERED_STYLE_EMOJI   # emoji | plain | roman
    output_format: str = OUTPUT_FORMAT_PLAIN     # plain | mdv2 | html
    blockquote_symbol: str = _DEFAULT_BLOCKQUOTE
    hr_symbol: str = _DEFAULT_HR

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "FormatterPrefs":
        try:
            data = json.loads(raw)
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except Exception:
            return cls()


# ─────────────────────────────────────────────────────────────────
# FormatterStore — SQLite persistence via navig Engine
# ─────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS formatter_prefs (
    user_id    INTEGER PRIMARY KEY,
    prefs_json TEXT    NOT NULL,
    updated_at REAL    NOT NULL DEFAULT 0
);
"""


class FormatterStore:
    """
    Lightweight SQLite store for per-user FormatterPrefs.

    Uses the shared ``navig.storage.engine.Engine`` if available;
    falls back to a plain ``sqlite3`` connection so the module is
    usable even when navig-core is not fully installed.
    """

    def __init__(self, db_path: Optional[str] = None):
        from navig.platform.paths import data_dir

        if db_path is None:
            runtime_dir = data_dir()
            runtime_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(runtime_dir / "formatter.db")
        self._db_path = db_path
        self._ensure_schema()

    def _connect(self):
        try:
            from navig.storage.engine import Engine
            return Engine().connect(self._db_path)
        except Exception:
            import sqlite3
            return sqlite3.connect(self._db_path, check_same_thread=False)

    def _ensure_schema(self) -> None:
        try:
            conn = self._connect()
            conn.execute(_SCHEMA)
            conn.commit()
        except Exception as exc:
            logger.warning("FormatterStore schema init failed: %s", exc)

    def get(self, user_id: int) -> FormatterPrefs:
        """Return stored prefs for *user_id*, or defaults if not set."""
        try:
            conn = self._connect()
            cur = conn.execute(
                "SELECT prefs_json FROM formatter_prefs WHERE user_id = ?", (user_id,)
            )
            row = cur.fetchone()
            if row:
                return FormatterPrefs.from_json(row[0])
        except Exception as exc:
            logger.warning("FormatterStore.get failed: %s", exc)
        return FormatterPrefs()

    def save(self, user_id: int, prefs: FormatterPrefs) -> None:
        """Persist *prefs* for *user_id* (upsert)."""
        try:
            conn = self._connect()
            conn.execute(
                """
                INSERT INTO formatter_prefs (user_id, prefs_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    prefs_json = excluded.prefs_json,
                    updated_at = excluded.updated_at
                """,
                (user_id, prefs.to_json(), time.time()),
            )
            conn.commit()
        except Exception as exc:
            logger.warning("FormatterStore.save failed: %s", exc)


_formatter_store: Optional[FormatterStore] = None


def get_formatter_store() -> FormatterStore:
    global _formatter_store
    if _formatter_store is None:
        _formatter_store = FormatterStore()
    return _formatter_store


# ─────────────────────────────────────────────────────────────────
# MarkdownFormatter — deterministic conversion pipeline
# ─────────────────────────────────────────────────────────────────

# Regex patterns (compiled once at module load)
_RE_FENCE = re.compile(r"```[\s\S]*?```")
_RE_H4 = re.compile(r"^#### (.+)$", re.MULTILINE)
_RE_H3 = re.compile(r"^### (.+)$", re.MULTILINE)
_RE_H2 = re.compile(r"^## (.+)$", re.MULTILINE)
_RE_H1 = re.compile(r"^# (.+)$", re.MULTILINE)
_RE_HR = re.compile(r"^(?:---|\*\*\*|___)\s*$", re.MULTILINE)
_RE_BOLD = re.compile(r"\*\*(.+?)\*\*")
_RE_ITALIC_UNDER = re.compile(r"(?<!\w)_(.+?)_(?!\w)")
_RE_BLOCKQUOTE = re.compile(r"^> (.+)$", re.MULTILINE)
_RE_NUMBERED = re.compile(r"^(\d+)\. (.+)$", re.MULTILINE)
_RE_BULLET = re.compile(r"^[ \t]*[-*] (.+)$", re.MULTILINE)


class MarkdownFormatter:
    """
    Convert standard Markdown text to Telegram-compatible output.

    Pipeline order (each step is a single-pass regex substitution):
      1. Extract + protect fenced code blocks
      2. Headings H4 → H1 (bottom-up to avoid partial matches)
      3. Horizontal rules
      4. Blockquotes
      5. Bold / italic (passthrough for Telegram MarkdownV2 / HTML)
      6. Numbered lists
      7. Bullet lists
      8. Restore fenced code blocks
    """

    def convert(self, text: str, prefs: Optional[FormatterPrefs] = None) -> str:
        """Convert *text* to Telegram-formatted output."""
        if prefs is None:
            prefs = FormatterPrefs()

        # -- Step 1: protect fenced code blocks ----------------------------
        fences: list[str] = []

        def _stash_fence(m: re.Match) -> str:  # type: ignore[type-arg]
            fences.append(m.group(0))
            return f"\x00FENCE{len(fences) - 1}\x00"

        text = _RE_FENCE.sub(_stash_fence, text)

        # -- Step 2: headings (H4 first to avoid matching H4 as H3) --------
        text = _RE_H4.sub(lambda m: f"{prefs.h4_symbol} {m.group(1)}", text)
        text = _RE_H3.sub(lambda m: f"{prefs.h3_symbol} {m.group(1)}", text)
        text = _RE_H2.sub(lambda m: f"{prefs.h2_symbol} {m.group(1)}", text)
        text = _RE_H1.sub(
            lambda m: f"{prefs.h1_symbol} {m.group(1).upper()}",
            text,
        )

        # -- Step 3: horizontal rules ---------------------------------------
        text = _RE_HR.sub(prefs.hr_symbol, text)

        # -- Step 4: blockquotes -------------------------------------------
        text = _RE_BLOCKQUOTE.sub(
            lambda m: f"{prefs.blockquote_symbol} {m.group(1)}", text
        )

        # -- Step 5: bold / italic -----------------------------------------
        if prefs.output_format == OUTPUT_FORMAT_HTML:
            text = _RE_BOLD.sub(r"<b>\1</b>", text)
            text = _RE_ITALIC_UNDER.sub(r"<i>\1</i>", text)
        else:
            # Telegram MarkdownV2 and plain both use *bold* / _italic_
            text = _RE_BOLD.sub(r"*\1*", text)
            # _italic_ stays as-is (already Telegram-compatible)

        # -- Step 6: numbered lists ----------------------------------------
        _counter: list[int] = [0]

        def _replace_num(m: re.Match) -> str:  # type: ignore[type-arg]
            n = int(m.group(1))
            _counter[0] = n
            content = m.group(2)
            if prefs.numbered_style == NUMBERED_STYLE_EMOJI:
                symbol = _EMOJI_DIGITS.get(n, f"{n}.")
                return f"{symbol} {content}"
            if prefs.numbered_style == NUMBERED_STYLE_ROMAN:
                return f"{_to_roman(n)}. {content}"
            return f"{n}. {content}"

        text = _RE_NUMBERED.sub(_replace_num, text)

        # -- Step 7: bullet lists ------------------------------------------
        text = _RE_BULLET.sub(lambda m: f"{prefs.bullet_style} {m.group(1)}", text)

        # -- Step 8: restore fenced code blocks ----------------------------
        for idx, block in enumerate(fences):
            text = text.replace(f"\x00FENCE{idx}\x00", block)

        return text

    def convert_chunked(
        self, text: str, prefs: Optional[FormatterPrefs] = None, max_chars: int = 4096
    ) -> list[str]:
        """
        Convert and split into Telegram-safe chunks (≤ *max_chars* each).

        Split points are paragraph boundaries; hard-splits only when a
        single paragraph exceeds *max_chars*.
        """
        converted = self.convert(text, prefs)
        if len(converted) <= max_chars:
            return [converted]

        chunks: list[str] = []
        current = ""
        for paragraph in converted.split("\n\n"):
            block = paragraph + "\n\n"
            if len(current) + len(block) > max_chars:
                if current:
                    chunks.append(current.rstrip())
                    current = ""
                if len(block) > max_chars:
                    # Hard-split oversized block
                    for i in range(0, len(block), max_chars):
                        chunks.append(block[i : i + max_chars])
                    continue
            current += block
        if current.strip():
            chunks.append(current.rstrip())
        return chunks


# ─────────────────────────────────────────────────────────────────
# Settings keyboard helpers
# ─────────────────────────────────────────────────────────────────

def build_formatter_settings_keyboard(
    prefs: FormatterPrefs,
) -> list[list[dict]]:
    """
    Build an inline keyboard for the /format settings panel.

    Returns a list-of-lists-of-dicts suitable for Telegram's
    ``reply_markup.inline_keyboard``.
    """
    rows: list[list[dict]] = []

    # Row: Heading symbols
    rows.append([
        {"text": f"H1: {prefs.h1_symbol}", "callback_data": "fmt:h1"},
        {"text": f"H2: {prefs.h2_symbol}", "callback_data": "fmt:h2"},
        {"text": f"H3: {prefs.h3_symbol}", "callback_data": "fmt:h3"},
        {"text": f"H4: {prefs.h4_symbol}", "callback_data": "fmt:h4"},
    ])

    # Row: Bullet + numbered style
    rows.append([
        {"text": f"Bullet: {prefs.bullet_style}", "callback_data": "fmt:bullet"},
        {"text": f"Nums: {prefs.numbered_style}", "callback_data": "fmt:nums"},
    ])

    # Row: Output format
    fmt_labels = {
        OUTPUT_FORMAT_PLAIN: "📄 Plain",
        OUTPUT_FORMAT_MDV2: "✏️ MDv2",
        OUTPUT_FORMAT_HTML: "🌐 HTML",
    }
    rows.append([
        {"text": f"Format: {fmt_labels.get(prefs.output_format, prefs.output_format)}", "callback_data": "fmt:outfmt"},
        {"text": "✅ Done", "callback_data": "fmt:done"},
    ])

    return rows


def build_symbol_picker_keyboard(
    heading_level: str, current: str
) -> list[list[dict]]:
    """
    Build a symbol picker keyboard for heading level *heading_level*
    (``h1`` / ``h2`` / ``h3`` / ``h4``).
    """
    rows: list[list[dict]] = []
    row: list[dict] = []
    for sym in SYMBOL_POOL:
        marker = "✓ " if sym == current else ""
        row.append({
            "text": f"{marker}{sym}",
            "callback_data": f"fmt:sym:{heading_level}:{sym}",
        })
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([{"text": "⬅ Back", "callback_data": "fmt:back"}])
    return rows


def build_bullet_picker_keyboard(current: str) -> list[list[dict]]:
    """Pick bullet style from a compact set."""
    options = ["•", "▪", "◾", "🔹", "▸", "›", "—"]
    row = []
    for opt in options:
        marker = "✓ " if opt == current else ""
        row.append({"text": f"{marker}{opt}", "callback_data": f"fmt:bul:{opt}"})
    return [row, [{"text": "⬅ Back", "callback_data": "fmt:back"}]]


def build_numbered_picker_keyboard(current: str) -> list[list[dict]]:
    options = [
        (NUMBERED_STYLE_EMOJI, "1️⃣ Emoji"),
        (NUMBERED_STYLE_PLAIN, "1. Plain"),
        (NUMBERED_STYLE_ROMAN, "i. Roman"),
    ]
    row = [
        {"text": ("✓ " if val == current else "") + label, "callback_data": f"fmt:numstyle:{val}"}
        for val, label in options
    ]
    return [row, [{"text": "⬅ Back", "callback_data": "fmt:back"}]]


def build_outfmt_picker_keyboard(current: str) -> list[list[dict]]:
    options = [
        (OUTPUT_FORMAT_PLAIN, "📄 Plain"),
        (OUTPUT_FORMAT_MDV2, "✏️ MdV2"),
        (OUTPUT_FORMAT_HTML, "🌐 HTML"),
    ]
    row = [
        {"text": ("✓ " if val == current else "") + label, "callback_data": f"fmt:of:{val}"}
        for val, label in options
    ]
    return [row, [{"text": "⬅ Back", "callback_data": "fmt:back"}]]
