"""The EXPLORE_Q marker: suggested follow-up questions at the end of a reply.

A reply asked for suggestions ends with a line like::

    EXPLORE_Q: Who directed this? | Other Fincher films? | Full cast list?

Surfaces render the questions as buttons and never show the marker itself. It
lives here, not in the Telegram gateway, because the agent's own conversation
history must strip it too: a history full of raw "EXPLORE_Q: …" lines teaches the
model to write it on turns that never asked for it.
"""

from __future__ import annotations

import re

_EXPLORE_Q_RE = re.compile(
    # The MARKER only — what follows it is parsed by _parse_explore_items().
    # As instructed, or decorated / mangled the way models actually write it:
    #   **EXPLORE_Q:** · `EXPLORE_Q:` · <b>EXPLORE_Q:</b> · _EXPLORE_Q_: · Explore_Q:
    #   EXPLORE\_Q: (markdown-escaped underscore) · EXPLORE_Q : (space before colon)
    #   EXPLORE_Q： (fullwidth colon — CJK / some Cyrillic keyboards)
    # ⚠ Case-insensitive, and every caller uses THIS regex — the old case-sensitive
    # `"EXPLORE_Q:" in text` pre-checks disagreed with it, so `Explore_Q:` leaked raw.
    r"\n*(?<![A-Za-z0-9])(?:\\?[*_`]+|<[a-z]+>)*EXPLORE\\?_Q(?:\\?[*_`]+|</[a-z]+>)*"
    r"[ \t]*[:：](?:\\?[*_`]+|</[a-z]+>)*[ \t]*",
    re.IGNORECASE,
)
#: Longest text accepted as ONE explore item — anything longer is prose the model
#: wrote after the marker line, not a button label.
_EXPLORE_ITEM_MAX_CHARS: int = 150
_EXPLORE_BULLET_RE = re.compile(r"^(?:[-*•◦]|\d{1,2}[.)])\s+")
_EXPLORE_Q_MAX: int = 5  # max explore buttons rendered per response


def _clean_explore_item(item: str) -> str:
    item = _EXPLORE_BULLET_RE.sub("", item.strip())
    item = item.strip().strip("*_`\\").strip()
    # Models echo the placeholder brackets from the instruction: "[Who directed it?]".
    if item.startswith("[") and item.endswith("]"):
        item = item[1:-1].strip()
    return item


def _parse_explore_items(rest: str) -> list[str]:
    """Items after the marker: ``a | b | c`` on its line, or one per following line."""
    lines = [ln.strip() for ln in rest.replace("\r", "").split("\n")]
    items: list[str] = []

    def _take(line: str) -> None:
        for raw in re.split(r"[|｜]", line):
            item = _clean_explore_item(raw)
            if item and len(item) <= _EXPLORE_ITEM_MAX_CHARS:
                items.append(item)

    if lines and lines[0]:
        _take(lines[0])
    if len(items) < 2:
        # "EXPLORE_Q:" alone on its line, the items listed underneath.
        for line in lines[1:]:
            if len(items) >= _EXPLORE_Q_MAX:
                break
            if line:
                _take(line)
    return items[:_EXPLORE_Q_MAX]


def extract_explore_questions(text: str) -> tuple[str, list[str]]:
    """Strip the EXPLORE_Q marker and return (cleaned_text, [question, ...]).

    The marker and EVERYTHING after it is removed from the visible text (the
    instruction says nothing follows it, and a half-parsed tail is worse than none),
    and the questions are returned so they can be shown as explore inline buttons.
    Items may also sit one per line under a bare ``EXPLORE_Q:`` line.
    """
    if not text:
        return text or "", []
    m = _EXPLORE_Q_RE.search(text)
    if not m:
        return text, []
    cleaned = text[: m.start()].rstrip()
    return cleaned, _parse_explore_items(text[m.end():])


def strip_explore_marker(text: str) -> str:
    """The visible text with any EXPLORE_Q marker (and what follows it) removed.

    For send paths that render no explore buttons (/think, /explain, CODE, a CLI
    reply) and for stored history: the marker is a rendering instruction and must
    never reach the user or the model's own context.
    """
    try:
        return extract_explore_questions(text)[0]
    except Exception:  # noqa: BLE001 — a formatting helper must never block a reply
        return text
