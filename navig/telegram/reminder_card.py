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
            rest = text[m.end() :].strip()
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


def _label(key: str, fallback: str) -> str:
    """A button label from the locale catalog, or *fallback* when it is missing."""
    try:
        from navig.core import i18n

        text = i18n.t(key)
    except Exception:  # noqa: BLE001 — a missing catalog must not cost the buttons
        return fallback
    return fallback if text in (key, "") else text


def keyboard(reminder_id: int) -> list[list[dict]]:
    """The two things you can do with a ping without leaving the chat.

    Done completes the row; +1 h pushes the same row an hour out (``store.snooze_reminder``)
    so it comes back instead of being lost — the two actions the operator reaches for
    when a reminder lands at a bad moment.
    """
    rid = int(reminder_id)
    return [
        [
            {"text": _label("reminder.button.done", "✅ Done"), "callback_data": f"rem:done:{rid}"},
            {
                "text": _label("reminder.button.snooze", "⏰ +1 h"),
                "callback_data": f"rem:snooze:{rid}",
            },
        ]
    ]


def ping_card(header_html: str, msg: str, *, due_unix: int | None = None):
    """The same reminder as a card — one definition, both renders project it.

    *header_html* is trusted (a locale string). The headline becomes the card's
    title, the "when" its meta line, and the note stays a paragraph, so the rich
    render gets a real heading instead of a bold line inside running text.
    *due_unix* (the row's ``remind_at``) adds a live countdown, which is the one
    thing a static "in 2 h" string cannot do: Telegram keeps it current.
    """
    from navig.telegram.card_doc import Card, Heading, Para, Raw, Table

    title, when, details = _parts(msg)
    # The header is a caption, not a stanza: "⏰ Reminder" on its own line above the
    # headline said what the notification already says. It rides the meta line with
    # the "when", and only when it names something the title does not.
    label = _plain(header_html)
    meta = " · ".join(bit for bit in ((label if title and label not in title else ""), when) if bit)
    # A ping arrives alone in the conversation, so it owns the screen and earns the
    # display size — unlike a detail card opened inside a list.
    card = Card(title or label or "⏰", meta=meta, big=True)
    if due_unix:
        # Inline, not a nested paragraph: Para already wraps its text in <p>, and the
        # doubled tag rendered as a blank line above the countdown.
        card.add(Para(Raw(f'⏰ <tg-time unix="{int(due_unix)}" format="R">due</tg-time>')))
    if details:
        rows = _labelled_rows(details)
        if rows:
            card.add(Heading(_label("reminder.heading.details", "Details"), 4))
            card.add(Table(rows=rows, bordered=False))
        else:
            card.add(Para(Raw(_details_html(details))))
    return card


def _labelled_rows(details: str) -> list[list[str]]:
    """``[[label, value], …]`` when a pasted note is a set of ``LABEL: value`` lines.

    The operator's appointment reminders carry a clinic, a doctor, what to bring and a
    file path; stacked as a table they read at a glance, where a run-on paragraph does
    not. A note with no such structure returns ``[]`` and stays prose.
    """
    text = (details or "").strip()
    if not text:
        return []
    if "\n" in text:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    else:
        if len(_LABEL_RE.findall(text)) < 2:
            return []
        lines = [
            ln.strip()
            for ln in _LABEL_RE.sub(lambda m: f"\n{m.group('label').strip()}: ", text).splitlines()
            if ln.strip()
        ]
    rows: list[list[str]] = []
    for line in lines:
        head, sep, tail = line.partition(":")
        if sep and tail.strip() and len(head.strip()) <= 24:
            rows.append([head.strip(), tail.strip()])
        elif rows:
            rows[-1][1] = f"{rows[-1][1]} {line}".strip()
        else:
            return []
    return rows if len(rows) >= 2 else []


def _details_html(details: str) -> str:
    from navig.gateway.channels.telegram_html import md_to_html

    return md_to_html(labelled_lines(details))


def _plain(header_html: str) -> str:
    """A locale header without its markup, for use as a card title."""
    return re.sub(r"<[^>]+>", "", header_html or "").strip()


def render(header_html: str, msg: str) -> str:
    """The Telegram HTML for one reminder. *header_html* is trusted (a locale string)."""
    from navig.gateway.channels.telegram_html import html_escape, md_to_html

    title, when, details = _parts(msg)
    # Same rule as the card: the caption joins the "when" line instead of taking a
    # paragraph of its own above the headline.
    label = _plain(header_html)
    meta_plain = " · ".join(
        bit for bit in ((label if title and label not in title else ""), when) if bit
    )
    lines = []
    if title:
        lines.append(f"<b>{html_escape(title)}</b>")
    if meta_plain:
        lines.append(f"<i>{html_escape(meta_plain)}</i>")
    if details:
        lines.append("")
        lines.append(md_to_html(labelled_lines(details)))
    return "\n".join(lines)


def state_card(header_html: str, msg: str, *, state: str, due_unix: int | None = None):
    """The same ping AFTER a button was pressed — same words, the outcome appended.

    The message the operator tapped is the message that has to change: a fresh reply
    would leave the live card sitting there with buttons that no longer mean anything.
    The keyboard is dropped because both actions are spent.
    """
    from dataclasses import replace

    card = ping_card(header_html, msg, due_unix=due_unix)
    meta = " · ".join(bit for bit in (card.meta, state) if bit)
    return replace(card, meta=meta, rows=())


def render_rich(header_html: str, msg: str, *, due_unix: int | None = None) -> str:
    """The same reminder as a rich message — heading, live countdown, key/value sheet."""
    return ping_card(header_html, msg, due_unix=due_unix).rich_html()
