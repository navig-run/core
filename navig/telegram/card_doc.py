"""One card definition, two renders: rich message (Bot API 10.x) and plain HTML.

Why this exists. Every Telegram card in NAVIG is hand-built as an HTML string for
``sendMessage`` — a bold first line, an emoji per row, a bullet list, and a flat
``reply_markup`` keyboard underneath. That ceiling is now much lower than the
medium: ``sendRichMessage`` renders real section headings, tables, foldable
``<details>`` blocks, checkbox lists, pull quotes, footnotes and **buttons inside
the bubble** (``<tg-button-row>``, with ``primary``/``success``/``danger``/``link``
styles and a disabled state).

Two ways to build those cards exist and only one of them survives review:

* build a separate rich card per surface — then the classic fallback and the rich
  card drift, and the drift shows up as "the button that exists only on some
  clients";
* build the card **once** as structure, and render it twice.

This module is the second way. A :class:`Card` is a title, a meta line, a list of
:class:`Node` blocks, button rows and a footer; :func:`rich_html` and
:func:`classic_html` are two projections of that one structure, and
:func:`keyboard` turns the same buttons into the classic inline-keyboard rows.

Degradation is deliberate, never lossy-by-accident:

    heading   → <h1..h6>              → bold line
    bullets   → <ul><li>              → "• " lines
    tasks     → checkbox list         → "☑ " / "☐ " lines
    table     → <table bordered>      → aligned <pre> grid
    details   → <details>             → bold "▸ summary" + expandable blockquote
    quote     → <blockquote>          → <blockquote> (expandable keeps folding)
    divider   → <hr/>                 → a rule of em dashes
    buttons   → <tg-button-row>       → reply_markup rows (styles carried over)

Text is HTML-escaped by default, so a title carrying ``<script>`` or an operator's
``a & b`` cannot break the parse mode — the same class of bug that used to arrive
as literal ``<b>`` tags after a silent retry without ``parse_mode``. Pass
:class:`Raw` when the innerHTML is already trusted markup.

Limits are enforced here, not at the transport: rich bodies fold at 32 768 UTF-8
characters, classic ``sendMessage`` rejects anything past 4 096 UTF-16, so
:func:`classic_html` truncates on a line boundary and says so.
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

#: Telegram's classic ``sendMessage`` ceiling, in UTF-16 code units.
CLASSIC_LIMIT = 4096
#: Telegram's ``sendRichMessage`` ceiling, in UTF-8 characters.
RICH_LIMIT = 32768

#: Button styles the Bot API accepts on a rich-message button.
STYLES = ("primary", "success", "danger", "link")


def esc(text: str) -> str:
    """HTML-escape *text* for either transport (the same three entities)."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def utf16_len(text: str) -> int:
    """Length of *text* the way Telegram counts it (astral chars are two units)."""
    return len(text.encode("utf-16-le")) // 2


# ── Blocks ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Raw:
    """Markup that is already escaped and trusted — emitted verbatim."""

    html: str


@dataclass(frozen=True)
class Heading:
    """A section heading. *level* 1 is the card title, 2-6 are sections."""

    text: str
    level: int = 3


@dataclass(frozen=True)
class Para:
    """One paragraph. *text* may be a :class:`Raw` to carry inline markup."""

    text: str | Raw = ""
    style: str = ""  # "italic" | "bold" | "" — the paragraph's own emphasis


@dataclass(frozen=True)
class Bullets:
    """An unordered list."""

    items: Sequence[str | Raw] = ()


@dataclass(frozen=True)
class Tasks:
    """A checkbox list — display only: a checkbox carries no callback.

    Items are ``(text, checked)``. Use :class:`Button` when a press must do
    something; a checkbox is state, not an input.
    """

    items: Sequence[tuple[str | Raw, bool]] = ()


@dataclass(frozen=True)
class Table:
    """A grid. *header* is optional; every row is padded/truncated to its width."""

    header: Sequence[str] = ()
    rows: Sequence[Sequence[str | Raw]] = ()
    caption: str = ""
    striped: bool = True
    #: A key/value sheet reads better without rules; a real grid needs them.
    bordered: bool = True


@dataclass(frozen=True)
class Details:
    """A collapsed block. Rich folds natively; classic folds inside a quote."""

    summary: str
    body: Sequence["Node"] = ()
    open: bool = False


@dataclass(frozen=True)
class Quote:
    """A block quotation, optionally expandable and credited."""

    text: str | Raw = ""
    cite: str = ""
    expandable: bool = False


@dataclass(frozen=True)
class PullQuote:
    """A pull quote — set apart, unindented, and credited underneath.

    Rich messages have a real element for this (``<aside>``), which reads as a
    callout rather than a quotation, and is the shape a card's headline insight
    wants: today's anchor, this week's number, the one line worth remembering.
    Classic HTML has no ``<aside>``, so the same words arrive in a block quote.
    """

    text: str | Raw = ""
    cite: str = ""


@dataclass(frozen=True)
class Code:
    """A preformatted block. *language* only decorates the rich render."""

    text: str
    language: str = ""


@dataclass(frozen=True)
class Numbered:
    """An ordered list. *type* is ``a``/``A``/``i``/``I`` — rich only."""

    items: Sequence[str | Raw] = ()
    start: int = 1
    type: str = ""  # noqa: A003 — the HTML attribute's own name
    reversed: bool = False


@dataclass(frozen=True)
class Math:
    """A formula in LaTeX. Rich renders it typeset; classic keeps the source."""

    source: str
    block: bool = False


@dataclass(frozen=True)
class Map:
    """A place. Rich draws a real map; classic links out to OpenStreetMap."""

    lat: float
    long: float
    zoom: int = 14
    caption: str = ""

    def url(self) -> str:
        return (
            "https://www.openstreetmap.org/"
            f"?mlat={self.lat}&mlon={self.long}#map={self.zoom}/{self.lat}/{self.long}"
        )


@dataclass(frozen=True)
class Media:
    """One attachment. *kind* is photo / video / audio / animation / document.

    Media may only be a block of its own (the Bot API forbids it inline) and only
    over HTTP(S): a ``file://`` path renders as nothing at all on the client.
    """

    src: str
    kind: str = "photo"
    caption: str = ""
    credit: str = ""
    spoiler: bool = False


@dataclass(frozen=True)
class Gallery:
    """Several attachments: a collage grid, or a slideshow the reader swipes."""

    sources: Sequence[str] = ()
    slideshow: bool = False
    caption: str = ""


@dataclass(frozen=True)
class Time:
    """A timestamp the client localises — "in 2h" for every reader, in their zone.

    *fmt* uses Telegram's date-time pattern (``wDT`` = weekday, date, time).
    """

    unix: int
    fmt: str = "wDT"
    text: str = ""


@dataclass(frozen=True)
class Link:
    """An inline link — real in both projections, unlike most rich-only nodes."""

    text: str
    url: str


@dataclass(frozen=True)
class Anchor:
    """A named target for an in-document link (``Link`` with ``#name``)."""

    name: str


@dataclass(frozen=True)
class Divider:
    """A horizontal rule."""


@dataclass(frozen=True)
class Stat:
    """One ``label · value`` line — the card's compact key/value row."""

    label: str
    value: str
    glyph: str = ""


Node = (
    Heading
    | Para
    | Bullets
    | Numbered
    | Tasks
    | Table
    | Details
    | Quote
    | PullQuote
    | Divider
    | Stat
    | Code
    | Math
    | Map
    | Media
    | Gallery
    | Time
    | Link
    | Anchor
)

#: The alignments a rich-message button row accepts.
ALIGNS = ("left", "center", "right")


# ── Inline helpers ───────────────────────────────────────────────────────────


def link(url: str, text: str) -> Raw:
    """An inline link, escaped for both transports."""
    return Raw(f'<a href="{esc(url)}">{esc(text)}</a>')


def spoiler(text: str) -> Raw:
    """Text the reader must tap to reveal — for a verdict the number would spoil."""
    return Raw(f"<tg-spoiler>{esc(text)}</tg-spoiler>")


def mark(text: str) -> Raw:
    """Highlighted text: the one figure the reader should not miss."""
    return Raw(f"<mark>{esc(text)}</mark>")


def code(text: str) -> Raw:
    """Inline monospace, for a path, a flag or a callback id."""
    return Raw(f"<code>{esc(text)}</code>")


def bold(text: str) -> Raw:
    return Raw(f"<b>{esc(text)}</b>")


def italic(text: str) -> Raw:
    return Raw(f"<i>{esc(text)}</i>")


def sub(text: str) -> Raw:
    return Raw(f"<sub>{esc(text)}</sub>")


def sup(text: str) -> Raw:
    return Raw(f"<sup>{esc(text)}</sup>")


def math(source: str) -> Raw:
    """An inline formula."""
    return Raw(f"<tg-math>{esc(source)}</tg-math>")


# ── Buttons ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Button:
    """One button. Exactly one of *data* / *url* / *copy* / *query* decides a press.

    ``style`` is one of :data:`STYLES`; the Bot API allows ``link`` only for a
    callback button (a URL button is already a link), so it is dropped there.
    """

    label: str
    data: str = ""
    url: str = ""
    copy: str = ""
    query: str = ""
    style: str = ""
    disabled: bool = False

    def __post_init__(self) -> None:
        if self.style and self.style not in STYLES:
            raise ValueError(f"unknown button style {self.style!r}; want one of {STYLES}")
        if self.style == "link" and self.url:
            raise ValueError("style 'link' is only allowed on a callback button")
        actions = [value for value in (self.data, self.url, self.copy, self.query) if value]
        if len(actions) > 1:
            raise ValueError("a button does one thing: data, url, copy or query — not several")

    # -- classic ------------------------------------------------------------

    def classic(self) -> dict[str, Any]:
        """This button as an ``InlineKeyboardButton`` (``reply_markup`` row cell).

        A disabled button keeps ``callback_data`` on purpose: ``disabled`` is a
        Bot API 10.3 field, and a server that predates it rejects a button with no
        action outright — which would take the whole keyboard down with it. The
        ``noop`` callback is the safety net (the dispatcher answers it silently),
        and on a current server the button is never pressed at all.
        """
        button: dict[str, Any] = {"text": self.label}
        if self.disabled:
            button["callback_data"] = "noop:disabled"
            button["disabled"] = {}
            return button
        if self.data:
            button["callback_data"] = self.data
        elif self.url:
            button["url"] = self.url
        elif self.copy:
            button["copy_text"] = {"text": self.copy}
        elif self.query:
            button["switch_inline_query"] = self.query
        # `link` is a rich-message style only. An inline-keyboard button accepts
        # primary/success/danger and answers 400 "Invalid button style
        # specified" to `link`, which takes the whole keyboard down with it.
        if self.style and self.style != "link":
            button["style"] = self.style
        return button

    # -- rich ---------------------------------------------------------------

    def rich(self) -> str:
        """This button as a ``<tg-button>`` element for a rich message body."""
        attrs: list[str] = []
        if self.disabled:
            attrs.append('type="disabled"')
        elif self.data:
            attrs.append('type="callback_data"')
            attrs.append(f'data="{esc(self.data)}"')
        elif self.url:
            attrs.append('type="url"')
            attrs.append(f'url="{esc(self.url)}"')
        elif self.copy:
            attrs.append('type="copy_text"')
            attrs.append(f'text="{esc(self.copy)}"')
        elif self.query:
            attrs.append('type="switch_inline_query"')
            attrs.append(f'query="{esc(self.query)}"')
        else:
            attrs.append('type="disabled"')
        if self.style:
            attrs.append(f'style="{self.style}"')
        glyph = esc(self.label)
        return f"<tg-button {' '.join(attrs)}>{glyph}</tg-button>"


# ── Card ─────────────────────────────────────────────────────────────────────


@dataclass
class Card:
    """A card: title, optional meta line, blocks, button rows, footer.

    ``big`` promotes the title to the display size (``<h1>``). It is off by
    default on purpose: a screen of equal ``<h1>``s reads as shouting, and on a
    phone the largest heading eats the first screenful before any content. The
    title of a card is a section heading (``<h2>``); only a card that *is* the
    screen — a briefing, a report — asks for the display size.
    """

    title: str = ""
    glyph: str = ""
    meta: str = ""
    blocks: list[Node] = field(default_factory=list)
    rows: list[list[Button]] = field(default_factory=list)
    aligns: list[str] = field(default_factory=list)
    footer: str = ""
    big: bool = False

    # -- construction (chainable) -------------------------------------------

    def add(self, *nodes: Node) -> "Card":
        self.blocks.extend(nodes)
        return self

    def buttons(self, *row: Button, align: str = "left") -> "Card":
        """Add a button row. *align* matters only to the rich render."""
        if row:
            if align not in ALIGNS:
                raise ValueError(f"unknown align {align!r}; want one of {ALIGNS}")
            self.rows.append(list(row))
            self.aligns.append(align)
        return self

    # -- render: rich -------------------------------------------------------

    def rich_html(self) -> str:
        """The card as rich-message HTML (headings, tables, in-bubble buttons)."""
        out: list[str] = []
        if self.title:
            head = esc(self.title)
            level = 1 if self.big else 2
            out.append(
                f"<h{level}>{self.glyph} {head}</h{level}>"
                if self.glyph
                else f"<h{level}>{head}</h{level}>"
            )
        if self.meta:
            out.append(f"<p><i>{esc(self.meta)}</i></p>")
        out.extend(_rich_node(node) for node in self.blocks)
        if self.rows:
            aligns = self.aligns or ["left"] * len(self.rows)
            for row, align in zip(self.rows, aligns):
                cells = "".join(button.rich() for button in row)
                out.append(f'<tg-button-row align="{align}">{cells}</tg-button-row>')
        if self.footer:
            out.append(f"<footer>{esc(self.footer)}</footer>")
        return "\n".join(part for part in out if part).strip()

    # -- render: classic ----------------------------------------------------

    def classic_html(self, limit: int = CLASSIC_LIMIT - 96) -> str:
        """The same card as ``sendMessage`` HTML, truncated at *limit* UTF-16 units.

        The default leaves room for the truncation notice, so the result always
        fits Telegram's 4 096 ceiling.
        """
        out: list[str] = []
        if self.title:
            head = esc(self.title)
            out.append(f"{self.glyph} <b>{head}</b>" if self.glyph else f"<b>{head}</b>")
        if self.meta:
            out.append(f"<i>{esc(self.meta)}</i>")
        out.extend(_classic_node(node) for node in self.blocks)
        if self.footer:
            out.append(f"<i>{esc(self.footer)}</i>")
        text = "\n\n".join(part for part in out if part).strip()
        return _truncate(text, limit)

    # -- render: keyboard ---------------------------------------------------

    def keyboard(self) -> list[list[dict[str, Any]]] | None:
        """The button rows as a classic inline keyboard, or ``None`` when empty.

        Rich messages carry their buttons in the body; this is the same layout for
        the fallback transport, so a rejected rich send never loses the actions.
        """
        rows = [[button.classic() for button in row] for row in self.rows if row]
        return rows or None


# ── Rich projection ──────────────────────────────────────────────────────────


def _rich_inline(value: str | Raw, style: str = "") -> str:
    html = value.html if isinstance(value, Raw) else esc(value)
    if style == "italic":
        return f"<i>{html}</i>"
    if style == "bold":
        return f"<b>{html}</b>"
    return html


def _rich_node(node: Node) -> str:
    if isinstance(node, Heading):
        level = min(max(node.level, 1), 6)
        return f"<h{level}>{esc(node.text)}</h{level}>"
    if isinstance(node, Para):
        return f"<p>{_rich_inline(node.text, node.style)}</p>"
    if isinstance(node, Bullets):
        items = "".join(f"<li>{_rich_inline(item)}</li>" for item in node.items)
        return f"<ul>{items}</ul>" if items else ""
    if isinstance(node, Tasks):
        cells = []
        for item, checked in node.items:
            box = '<input type="checkbox" checked>' if checked else '<input type="checkbox">'
            cells.append(f"<li>{box}{_rich_inline(item)}</li>")
        return f"<ul>{''.join(cells)}</ul>" if cells else ""
    if isinstance(node, Table):
        return _rich_table(node)
    if isinstance(node, Details):
        flag = " open" if node.open else ""
        body = "".join(_rich_node(child) for child in node.body)
        return f"<details{flag}><summary>{esc(node.summary)}</summary>{body}</details>"
    if isinstance(node, Quote):
        flag = " expandable" if node.expandable else ""
        cite = f"<cite>{esc(node.cite)}</cite>" if node.cite else ""
        return f"<blockquote{flag}>{_rich_inline(node.text)}{cite}</blockquote>"
    if isinstance(node, PullQuote):
        cite = f"<cite>{esc(node.cite)}</cite>" if node.cite else ""
        return f"<aside>{_rich_inline(node.text)}{cite}</aside>"
    if isinstance(node, Divider):
        return "<hr/>"
    if isinstance(node, Stat):
        glyph = f"{node.glyph} " if node.glyph else ""
        return f"<p>{glyph}<b>{esc(node.label)}</b> · {esc(node.value)}</p>"
    if isinstance(node, Code):
        lang = f' class="language-{esc(node.language)}"' if node.language else ""
        return f"<pre><code{lang}>{esc(node.text)}</code></pre>"
    if isinstance(node, Numbered):
        attrs = ""
        if node.start != 1:
            attrs += f' start="{node.start}"'
        if node.type:
            attrs += f' type="{esc(node.type)}"'
        if node.reversed:
            attrs += " reversed"
        items = "".join(f"<li>{_rich_inline(item)}</li>" for item in node.items)
        return f"<ol{attrs}>{items}</ol>" if items else ""
    if isinstance(node, Math):
        if node.block:
            return f"<tg-math-block>{esc(node.source)}</tg-math-block>"
        return f"<p><tg-math>{esc(node.source)}</tg-math></p>"
    if isinstance(node, Map):
        pin = f'<tg-map lat="{node.lat}" long="{node.long}" zoom="{node.zoom}"/>'
        if node.caption:
            return f"<figure>{pin}<figcaption>{esc(node.caption)}</figcaption></figure>"
        return pin
    if isinstance(node, Media):
        return _rich_media(node)
    if isinstance(node, Gallery):
        tag = "tg-slideshow" if node.slideshow else "tg-collage"
        cells = "".join(
            _rich_media(Media(src=src, kind=_kind(src)), bare=True) for src in node.sources
        )
        caption = f"<figcaption>{esc(node.caption)}</figcaption>" if node.caption else ""
        return f"<{tag}>{cells}{caption}</{tag}>"
    if isinstance(node, Time):
        label = esc(node.text) if node.text else esc(_stamp(node.unix))
        return f'<p><tg-time unix="{int(node.unix)}" format="{esc(node.fmt)}">{label}</tg-time></p>'
    if isinstance(node, Link):
        return f'<p><a href="{esc(node.url)}">{esc(node.text)}</a></p>'
    if isinstance(node, Anchor):
        return f'<a name="{esc(node.name)}"></a>'
    raise TypeError(f"not a card node: {node!r}")


def _cell_text(value: str | Raw) -> str:
    """One cell as plain text — the classic grid needs plain text to align it."""
    raw = value.html if isinstance(value, Raw) else str(value)
    return _html.unescape(re.sub(r"<[^>]+>", "", raw))


def _rich_table(node: Table) -> str:
    attrs = ""
    if node.bordered:
        attrs += " bordered"
    if node.striped:
        attrs += " striped"
    caption = f"<caption>{esc(node.caption)}</caption>" if node.caption else ""
    head = ""
    if node.header:
        cells = "".join(f"<th>{_rich_inline(cell)}</th>" for cell in node.header)
        head = f"<tr>{cells}</tr>"
    body = "".join(
        "<tr>" + "".join(f"<td>{_rich_inline(cell)}</td>" for cell in row) + "</tr>"
        for row in node.rows
    )
    return f"<table{attrs}>{caption}{head}{body}</table>"


_VIDEO_EXT = (".mp4", ".m4v", ".mov", ".webm", ".gif")
_AUDIO_EXT = (".mp3", ".m4a", ".aac", ".flac", ".wav")
_VOICE_EXT = (".ogg", ".opus")


def _kind(src: str) -> str:
    """Guess an attachment's kind from its URL, the way the client will.

    Matched on the whole URL, not the path: a CDN puts the format inside a query
    (``…/photo-1506?w=900`` has no extension at all) or behind a signed suffix.
    """
    url = src.lower()
    if any(ext in url for ext in _VOICE_EXT):
        return "voice"
    if any(ext in url for ext in _VIDEO_EXT):
        return "video"
    if any(ext in url for ext in _AUDIO_EXT):
        return "audio"
    if any(ext in url for ext in (".jpg", ".jpeg", ".png", ".webp", "images.", "/photo")):
        return "photo"
    return "document"


def _stamp(unix: int) -> str:
    """A timestamp for the classic render, which cannot localise it."""
    return datetime.fromtimestamp(int(unix), tz=timezone.utc).strftime("%a %d %b %H:%M UTC")


def _rich_media(node: Media, *, bare: bool = False) -> str:
    kind = node.kind if node.kind != "auto" else _kind(node.src)
    spoiler = " tg-spoiler" if node.spoiler else ""
    src = esc(node.src)
    if kind == "document":
        tag = f'<tg-document src="{src}"></tg-document>'
    elif kind == "video":
        tag = f'<video src="{src}"{spoiler}></video>'
    elif kind in {"audio", "voice"}:
        tag = f'<audio src="{src}"></audio>'
    else:
        tag = f'<img src="{src}"{spoiler}/>'
    if bare:
        return tag
    caption = ""
    if node.caption:
        cite = f"<cite>{esc(node.credit)}</cite>" if node.credit else ""
        caption = f"<figcaption>{esc(node.caption)}{cite}</figcaption>"
    elif node.credit:
        caption = f"<figcaption><cite>{esc(node.credit)}</cite></figcaption>"
    return f"<figure>{tag}{caption}</figure>" if caption else tag


# ── Classic projection ───────────────────────────────────────────────────────
def _classic_inline(value: str | Raw, style: str = "") -> str:
    html = value.html if isinstance(value, Raw) else esc(value)
    if style == "italic":
        return f"<i>{html}</i>"
    if style == "bold":
        return f"<b>{html}</b>"
    return html


def _classic_node(node: Node) -> str:
    if isinstance(node, Heading):
        return f"<b>{esc(node.text)}</b>"
    if isinstance(node, Para):
        return _classic_inline(node.text, node.style)
    if isinstance(node, Bullets):
        return "\n".join(f"• {_classic_inline(item)}" for item in node.items)
    if isinstance(node, Tasks):
        return "\n".join(
            f"{'☑' if checked else '☐'} {_classic_inline(item)}" for item, checked in node.items
        )
    if isinstance(node, Table):
        return _classic_table(node)
    if isinstance(node, Details):
        body = "\n\n".join(filter(None, (_classic_node(child) for child in node.body)))
        summary = f"<b>▸ {esc(node.summary)}</b>"
        return f"{summary}\n<blockquote expandable>{body}</blockquote>" if body else summary
    if isinstance(node, Quote):
        flag = " expandable" if node.expandable else ""
        # `<cite>` exists only in rich messages — classic HTML answers
        # "Unsupported start tag" and the whole send is rejected (400), so the
        # credit becomes an em-dash attribution instead.
        cite = f" — <i>{esc(node.cite)}</i>" if node.cite else ""
        return f"<blockquote{flag}>{_classic_inline(node.text)}{cite}</blockquote>"
    if isinstance(node, PullQuote):
        # No `<aside>` in classic HTML: the callout keeps its shape as a quote.
        cite = f"\n— <i>{esc(node.cite)}</i>" if node.cite else ""
        return f"<blockquote>{_classic_inline(node.text)}{cite}</blockquote>"
    if isinstance(node, Divider):
        return "———"
    if isinstance(node, Stat):
        glyph = f"{node.glyph} " if node.glyph else ""
        return f"{glyph}<b>{esc(node.label)}</b> · {esc(node.value)}"
    if isinstance(node, Code):
        return f"<pre>{esc(node.text)}</pre>"
    if isinstance(node, Numbered):
        return "\n".join(
            f"{index}. {_classic_inline(item)}"
            for index, item in enumerate(node.items, start=node.start)
        )
    if isinstance(node, Math):
        # No TeX in classic HTML: the source stays readable in monospace.
        return f"<code>{esc(node.source)}</code>"
    if isinstance(node, Map):
        label = esc(node.caption) if node.caption else f"{node.lat}, {node.long}"
        return f'📍 <a href="{esc(node.url())}">{label}</a>'
    if isinstance(node, Media):
        label = esc(node.caption) if node.caption else esc(node.src.rsplit("/", 1)[-1])
        return f'<a href="{esc(node.src)}">{label}</a>'
    if isinstance(node, Gallery):
        links = [
            f'• <a href="{esc(src)}">{esc(src.rsplit("/", 1)[-1])}</a>' for src in node.sources
        ]
        caption = f"<i>{esc(node.caption)}</i>\n" if node.caption else ""
        return caption + "\n".join(links)
    if isinstance(node, Time):
        return esc(node.text) if node.text else esc(_stamp(node.unix))
    if isinstance(node, Link):
        return f'<a href="{esc(node.url)}">{esc(node.text)}</a>'
    if isinstance(node, Anchor):
        return ""  # an anchor is a rich-message target; classic has nowhere to land
    raise TypeError(f"not a card node: {node!r}")


def _classic_table(node: Table) -> str:
    """A table as an aligned ``<pre>`` grid — the only grid classic HTML has."""
    rows: list[list[str]] = []
    if node.header:
        rows.append([_cell_text(cell) for cell in node.header])
    rows.extend([_cell_text(cell) for cell in row] for row in node.rows)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    for row in rows:  # pad ragged rows so the grid stays rectangular
        row.extend([""] * (width - len(row)))
    sizes = [max(len(row[col]) for row in rows) for col in range(width)]
    lines = []
    for index, row in enumerate(rows):
        cells = [row[col].ljust(sizes[col]) for col in range(width)]
        line = "  ".join(cells).rstrip()
        if index == 0 and node.header:
            line = f"<b>{esc(line)}</b>"
        else:
            line = esc(line)
        lines.append(line)
    caption = f"<i>{esc(node.caption)}</i>\n" if node.caption else ""
    return f"{caption}<pre>{chr(10).join(lines)}</pre>"


def _truncate(html: str, limit: int) -> str:
    """Cut *html* to *limit* UTF-16 units on a whitespace boundary, with a notice."""
    if utf16_len(html) <= limit:
        return html
    notice = "\n<i>… (cut — open the deck for the rest)</i>"
    budget = limit - utf16_len(notice)
    out: list[str] = []
    used = 0
    for word in html.split(" "):
        cost = utf16_len(word) + 1
        if used + cost > budget:
            break
        out.append(word)
        used += cost
    cut = " ".join(out)
    # Never leave a tag open: drop back to the last ">" that closes a tag run.
    if cut.rfind("<") > cut.rfind(">"):
        cut = cut[: cut.rfind("<")]
    return cut + notice


__all__ = [
    "ALIGNS",
    "CLASSIC_LIMIT",
    "RICH_LIMIT",
    "STYLES",
    "Anchor",
    "Bullets",
    "Button",
    "Card",
    "Code",
    "Details",
    "Divider",
    "Gallery",
    "Heading",
    "Link",
    "Map",
    "Math",
    "Media",
    "Node",
    "Numbered",
    "Para",
    "PullQuote",
    "Quote",
    "Raw",
    "Stat",
    "Table",
    "Tasks",
    "Time",
    "bold",
    "code",
    "esc",
    "italic",
    "link",
    "mark",
    "math",
    "spoiler",
    "sub",
    "sup",
    "utf16_len",
]
