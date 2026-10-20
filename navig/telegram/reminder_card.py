"""How a reminder reads when it lands in Telegram.

A reminder row stores one plain string (``message``). The poller used to send
``header + html.escape(message)`` — so a todo ping arrived as
``⏰ Rappel`` / ``⏰ Dentist — 1 hour before (due today 11:40)`` (two alarm clocks,
the time buried in brackets), and a pasted appointment arrived as one unbroken
120-character blob with its "Fiche: …" path and motive run together.

:func:`render` turns the same string into a small card — bold title, an italic
"when" line, then any details — without changing what is stored, so every row
already in the table (old format, leading ``⏰`` and all) renders too.
"""

from __future__ import annotations

import re

#: A headline longer than this is cut at a word boundary with "…".
HEADLINE_MAX = 60
#: Below this, a message is short enough to stay a single line.
_SPLIT_ABOVE = 80
#: A boundary closer to the start than this would leave a useless stub
#: ("Dr." → "Dr"), so it is skipped in favour of the next one.
_MIN_HEAD = 8

_LEADING_CLOCK_RE = re.compile("^\\s*⏰️?\\s*")
# Where a pasted message's subject ends: a dash or colon between words, a newline,
# or a sentence end.
_BOUNDARY_RE = re.compile(r"\s+[-–—]\s+|:\s+|\n|(?<=[.!?])\s+")
# The tail `pim.reminders.reminder_text` writes after " — ", in EVERY locale:
# "1 hour before (due today 11:40)" · "за 3 дн (Пн 26 окт · 10:30)" ·
# "c'est l'heure (lun 12:00)". Only the parenthesised "when" is structural — the
# words around it are translated, so matching the English "due" missed fr/ru.
# The lead is capped so a pasted note ending in "(…)" is not mistaken for a ping.
_LEAD_RE = re.compile(r"^(?P<lead>[^()]{1,40}?)\s+\((?P<when>[^()]+)\)$")
# A capitalised label inside a pasted note ("LE MOTIF:", "EMPORTER:", "Fiche:")
# right after a sentence end — each starts its own line so a dense note reads as
# a short list instead of one paragraph.
_LABEL_RE = re.compile(r"(?<=[.!?])\s+(?P<label>[A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÖØ-öø-ÿ0-9 ]{1,24}?):\s+")


def shorten(text: str, limit: int = HEADLINE_MAX) -> str:
    """*text* cut to *limit* characters at a word boundary, with "…" when cut."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space >= limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:-–—.") + "…"


def split_headline(text: str, limit: int = HEADLINE_MAX) -> tuple[str, str]:
    """``(headline, rest)``: the subject of a pasted message and everything after it.

    Cut at the first dash / colon / line / sentence boundary that leaves a real
    headline, else at *limit* characters on a word boundary. *rest* is "" when the
    whole text fits as the headline.
    """
    text = (text or "").strip()
    for m in _BOUNDARY_RE.finditer(text):
        head = text[: m.start()].strip(" ,;:-–—")
        if len(head) >= _MIN_HEAD:
            rest = text[m.end():].strip()
            if len(head) <= limit:
                return head, rest
            break
    if len(text) <= limit:
        return text, ""
    head = shorten(text, limit)
    return head, text


def _parts(msg: str) -> tuple[str, str, str]:
    """``(title, when, details)`` of a stored reminder message."""
    text = _LEADING_CLOCK_RE.sub("", msg or "").strip()
    first, _, more = text.partition("\n")
    first = first.strip()

    if " — " in first:
        title, tail = first.rsplit(" — ", 1)
        tail = tail.strip()
        m = _LEAD_RE.match(tail)
        if m:
            when = f"{m.group('lead').strip()} · {m.group('when').strip()}"
            return title.strip(), when, more.strip()

    if more.strip():
        return first, "", more.strip()
    if len(first) > _SPLIT_ABOVE:
        head, rest = split_headline(first)
        return head, "", rest
    return first, "", ""


def labelled_lines(details: str) -> str:
    """Break a one-paragraph note at its ``LABEL:`` markers, bolding each label.

    Only when the note has two or more labels — one colon in prose is not a
    structure worth reshaping. Returns markdown for ``md_to_html``.
    """
    if "\n" in details or len(_LABEL_RE.findall(details)) < 2:
        return details
    return _LABEL_RE.sub(lambda m: f"\n**{m.group('label').strip()}:** ", details).strip()


def render(header_html: str, msg: str) -> str:
    """The Telegram HTML for one reminder. *header_html* is trusted (a locale string)."""
    from navig.gateway.channels.telegram_html import html_escape, md_to_html

    title, when, details = _parts(msg)
    lines = [header_html] if header_html else []
    if title:
        lines.append(f"<b>{html_escape(title)}</b>")
    if when:
        lines.append(f"<i>{html_escape(when)}</i>")
    if details:
        lines.append("")
        lines.append(md_to_html(labelled_lines(details)))
    return "\n".join(lines)
