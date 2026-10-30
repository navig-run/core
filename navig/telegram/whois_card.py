"""A WHOIS record as a card — a sheet, a verdict, and links you can press.

`/whois` used to print a divider of `<code>─────</code>` and then every field as
an icon line with a monospace value underneath.  That is a wall of text on a
phone, and the one thing a reader wants — *how long have I got* — was a sentence
buried at the bottom.

This module keeps the parsing (WHOIS servers all answer differently) and hands
the reader a card: the dates and registrar as a sheet, the name servers as a
list, the expiry as a callout, the authoritative record as an inline link, and
buttons that open the site or re-read the record.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Sequence

from navig.telegram.card_doc import (
    Bullets,
    Button,
    Card,
    Para,
    PullQuote,
    Raw,
    Table,
    esc,
    link,
)

# (label, regex). Every registry spells these differently — Verisign indents
# ``Creation Date``, AFNIC answers ``created:`` and ``nserver:`` — so each pattern
# is anchored at the start of a line. Without the anchor ``eppstatus:`` answers a
# question about ``status:`` and the card shows a field nobody asked for.
FIELDS: tuple[tuple[str, str], ...] = (
    ("Registered", r"^\s*(?:Creation Date|Created(?: On)?|Registered(?: On)?|created)\s*:\s*(.+)"),
    (
        "Expires",
        r"^\s*(?:Registry Expiry Date|Expiry Date|Expiration Date|Expir(?:y|ation) Date)\s*:\s*(.+)",
    ),
    ("Updated", r"^\s*(?:Updated Date|last-update|last-modified|changed)\s*:\s*(.+)"),
    ("Registrar", r"^\s*(?:Registrar(?: Name)?|sponsoring-registrar)\s*:\s*(.+)"),
    ("Status", r"^\s*(?:Domain Status|status)\s*:\s*(\S+)"),
    ("DNSSEC", r"^\s*dnssec\s*:\s*(.+)"),
    ("Registrant", r"^\s*(?:Registrant Organization|registrant-organization)\s*:\s*(.+)"),
    ("Registrant email", r"^\s*Registrant Email\s*:\s*(.+)"),
)

NAME_SERVER = r"^\s*(?:nserver|name\s*server)\s*:\s*(.+)"

REDACTED = frozenset({"", "not disclosed", "redacted for privacy", "redacted"})

DATE_LABELS = ("Registered", "Expires", "Updated")

# Icons for the sheet rows: a date is a date, whatever it is called.
_ICONS = {
    "Registered": "📅",
    "Expires": "⏳",
    "Updated": "🔄",
    "Registrar": "🏢",
    "Status": "🔒",
    "DNSSEC": "🛡",
    "Registrant": "👤",
    "Registrant email": "📧",
    "Days left": "📆",
}


def parse_record(raw: str) -> tuple[dict[str, str], list[str]]:
    """Pull the fields that matter out of a registry answer.

    Returns ``(fields, name_servers)``; fields keep registry order so a card reads
    the same way twice.
    """
    fields: dict[str, str] = {}
    servers: list[str] = []
    for line in str(raw).splitlines():
        name_server = re.search(NAME_SERVER, line, re.I)
        if name_server:
            server = name_server.group(1).strip().lower().rstrip(".")
            if server and server not in servers:
                servers.append(server)
            continue
        for label, pattern in FIELDS:
            match = re.search(pattern, line, re.I)
            if not match:
                continue
            value = match.group(1).strip()
            if value.lower() in REDACTED or label in fields:
                break
            if label == "Status":
                # "clientTransferProhibited https://icann.org/epp#clientTransferProhibited"
                value = re.split(r"\s+https?://", value)[0].strip()
            fields[label] = value
            break
    return fields, servers


def human_date(value: str) -> str:
    """``1997-09-15T04:00:00Z`` or ``1997-09-15`` → ``Sep 15, 1997``."""
    head = str(value).strip().split("T")[0].split(" ")[0]
    try:
        moment = datetime.strptime(head, "%Y-%m-%d")
    except ValueError:
        return str(value).strip()
    return f"{moment.strftime('%b')} {moment.day}, {moment.year}"


def days_until(value: str, *, now: datetime | None = None) -> int | None:
    """Whole days between now and an ISO-ish date, or ``None`` if unparsable."""
    head = str(value).strip().split("T")[0]
    try:
        moment = datetime.strptime(head, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    reference = now or datetime.now(timezone.utc)
    return (moment - reference).days


def verdict(days: int | None) -> tuple[str, bool]:
    """The one sentence about expiry, and whether it wants attention."""
    if days is None:
        return "", False
    if days <= 0:
        return "❌ This domain has expired — the site may already be offline.", True
    if days <= 30:
        return f"⚠️ Expires in {days} days — renew before it lapses.", True
    if days <= 90:
        return f"📆 Expires in {days} days.", False
    years, rest = divmod(days, 365)
    if years:
        return f"✅ Registered for another {years}y {rest}d.", False
    return f"✅ Registered for another {days} days.", False


def whois_card(
    domain: str,
    fields: dict[str, str],
    servers: Sequence[str] = (),
    *,
    days: int | None = None,
    raw: str = "",
) -> Card:
    """The record as a card: sheet, name servers, expiry verdict, links."""
    host = esc(domain)
    card = Card("WHOIS", glyph="🌍", meta=host, big=True)

    rows: list[list[str | Raw]] = []
    for label in DATE_LABELS:
        if label in fields:
            rows.append([f"{_ICONS.get(label, '')} {label}".strip(), Raw(f"<b>{esc(human_date(fields[label]))}</b>")])
    if days is not None:
        rows.append(
            [
                "Days left",
                Raw(f"<b>{days}</b>" if days > 0 else "<b>expired</b>"),
            ]
        )
    for label, value in fields.items():
        if label in DATE_LABELS:
            continue
        short = value if len(value) <= 80 else value[:77] + "…"
        rows.append([f"{_ICONS.get(label, '')} {label}".strip(), esc(short)])
    if rows:
        card.add(Table(rows=rows, bordered=False))

    note, urgent = verdict(days)
    if note:
        # A callout for the urgent case, plain italics otherwise: a card where
        # everything shouts is a card where nothing does.
        card.add(PullQuote(note) if urgent else Para(note, style="italic"))

    if servers:
        # Bullets escapes plain strings, so a monospace server name has to arrive
        # as Raw — otherwise the reader sees the literal `<code>` tag.
        listed = Bullets([Raw(f"<code>{esc(server)}</code>") for server in servers[:6]])
        card.add(listed)

    if not rows:
        kept = [line for line in str(raw).splitlines() if line.strip()][:12]
        if kept:
            card.add(Para("The registry answered in a shape this card does not parse:", style="italic"))

    # Links live inside the text here: the record itself, and the site the domain
    # serves. `link` is the kit's inline anchor, not a button, and it hands back a
    # Raw — so the sentence is assembled from its html rather than formatted.
    site = link(f"https://{domain}", "Open the site")
    record = link(f"https://rdap.org/domain/{domain}", "RDAP record")
    card.add(Para(Raw(f"{site.html} · {record.html}"), style="italic"))

    card.footer = f"Query answered by the registry for .{domain.rsplit('.', 1)[-1]}"
    card.buttons(
        Button("🌐 Open site", url=f"https://{domain}"),
        Button("📋 RDAP", url=f"https://rdap.org/domain/{domain}"),
    )
    card.buttons(Button("🔄 Re-read", data=refresh_payload(domain), style="primary"))
    return card


def refresh_payload(domain: str) -> str:
    """Callback data that re-reads *domain* and redraws the message it came from.

    The domain travels in the payload, so a press needs no server-side state and
    the pressed message is the one that gets rewritten.
    """
    return f"wh:R:{domain}"


def parse_refresh(cb_data: str) -> str | None:
    """``wh:R:example.com`` → ``example.com``; anything else → ``None``."""
    parts = str(cb_data).split(":", 2)
    if len(parts) != 3 or parts[0] != "wh" or parts[1] != "R":
        return None
    return parts[2].strip() or None


def failure_card(domain: str, reason: str) -> Card:
    """A failed lookup is still a card — the reader gets a sentence, not a stack."""
    card = Card("WHOIS", glyph="🌍", meta=esc(domain), big=True)
    card.add(PullQuote(f"The registry did not answer: {esc(reason)}"))
    fallback = link(f"https://rdap.org/domain/{domain}", "Try the RDAP record")
    card.add(
        Para(
            Raw(
                f"{fallback.html} — port 43 may be blocked on this network."
            ),
            style="italic",
        )
    )
    card.buttons(Button("📋 RDAP", url=f"https://rdap.org/domain/{domain}"))
    return card


__all__ = [
    "DATE_LABELS",
    "FIELDS",
    "NAME_SERVER",
    "REDACTED",
    "days_until",
    "failure_card",
    "human_date",
    "parse_record",
    "parse_refresh",
    "refresh_payload",
    "verdict",
    "whois_card",
]
