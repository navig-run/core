"""Cards for a Business chat — rich, but never with a button that needs a callback.

A Business chat is two people: the owner writes through their own account and one contact
writes to it. Telegram does **not** route the contact's button presses to the bot
(`_is_user_authorized` drops them), so a callback button in this chat is a button that works
for one person and silently does nothing for the other. That rule has been honoured here by
giving business cards **no callback buttons at all** — and it also means these cards can use
the two button kinds that carry no callback:

* ``copy_text`` — hand the reader a string to paste (a domain, the next command to type);
* ``url`` — open the Mini App board, where the same list is editable with a real keyboard.

What a rich body buys here is the rest of the kit: a table for the list, a heading, italic
provenance ("added by Sam"), and a footer that states how to act without buttons — all of it
rendered by Telegram, with the classic projection still the fallback for a chat that cannot
show rich messages.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from navig.telegram.card_doc import Button, Card, Para, Raw, Table

_COPY = {
    "en": {
        "todo_title": "To-do",
        "todo_meta": "{open} open · {done} done",
        "empty": "Nothing yet — type todo <text> to add one.",
        "item": "Item",
        "number": "#",
        "when": "When",
        "added_by": "added by {name}",
        "done": "done",
        "hint": "Anyone here types: todo <text> · done N · undo N · del N (owner) · clear",
        "copy_list": "📋 Copy the list",
        "open_board": "📱 Open the board",
        "footer": "Everything works by typing, so the other person can act too.",
        "whois_title": "Whois",
        "domain": "Domain",
        "registrar": "Registrar",
        "created": "Created",
        "expires": "Expires",
        "updated": "Updated",
        "status": "Status",
        "nameservers": "Nameservers",
        "no_data": "No registry answer for that domain.",
        "copy_domain": "📋 Copy the domain",
        "dns_title": "DNS",
        "dns_type": "Type",
        "dns_value": "Value",
        "dns_none": "No records answered for that name.",
        "copy_lookup": "📋 Copy the lookup",
        "ssl_title": "Certificate",
        "ssl_issuer": "Issuer",
        "ssl_subject": "Issued to",
        "ssl_expires": "Expires",
        "ssl_days": "Days left",
        "ssl_sans": "Names",
        "ssl_ok": "The certificate is valid and the handshake succeeded.",
        "ssl_failing": "No valid certificate — a browser would warn a customer here.",
        "ssl_private": "That is this machine's own address; certificates are not checked there.",
        "http_title": "URL check",
        "http_status": "Status",
        "http_final": "Ends at",
        "http_redirects": "Redirects",
        "http_server": "Server",
        "http_type": "Content",
        "http_ok": "The link answered.",
        "http_failing": "The link did not answer.",
        "http_private": "That is a private address; a chat message may not probe this host.",
        "copy_url": "📋 Copy the URL",
        "reminder_title": "Reminder",
        "reminder_when": "When",
        "reminder_repeat": "Repeats",
        "reminder_snooze_hint": "Snooze works for the owner — the other person's presses never reach the bot.",
    },
}


def _t(key: str, **fields: Any) -> str:
    text = _COPY["en"].get(key, "")
    for name, value in fields.items():
        text = text.replace("{" + name + "}", str(value))
    return text


def _esc(value: Any) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _plain_list(items: Iterable[dict[str, Any]]) -> str:
    """The copy-paste form of the list: one line per item, ticked ones first-ish."""
    lines: list[str] = []
    for item in items:
        mark = "x" if item.get("done") else " "
        seq = item.get("list_seq")
        when = f" ({item['when_label']})" if item.get("when_label") else ""
        lines.append(f"[{mark}] #{seq} {item.get('title') or ''}{when}".rstrip())
    return "\n".join(lines)


def todo_card(
    *,
    items: Sequence[dict[str, Any]] = (),
    peer_name: str = "",
    owner_id: int | None = None,
    board_url: str = "",
) -> Card:
    """The shared list as a table, with a copy button and (when a deck exists) a board link."""
    open_items = [t for t in items if not t.get("done")]
    done_items = [t for t in items if t.get("done")]
    card = Card(
        _t("todo_title"),
        glyph="📋",
        meta=_t("todo_meta", open=len(open_items), done=len(done_items)),
    )

    if not items:
        card.add(Para(_t("empty"), style="italic"))
    else:
        rows: list[list[Any]] = []
        for item in open_items:
            title = f"☐ {_esc(item.get('title'))}"
            if (
                peer_name
                and item.get("created_by")
                and owner_id
                and int(item["created_by"]) != int(owner_id)
            ):
                title += f" <i>({_esc(_t('added_by', name=peer_name))})</i>"
            rows.append(
                [
                    Raw(f"<code>#{_esc(item.get('list_seq'))}</code>"),
                    Raw(title),
                    Raw(f"<i>{_esc(item.get('when_label'))}</i>"
                        if item.get("when_label")
                        else "<i>—</i>"),
                ]
            )
        for item in done_items[-5:]:
            rows.append(
                [
                    Raw(f"<code>#{_esc(item.get('list_seq'))}</code>"),
                    Raw(f"<s>☑ {_esc(item.get('title'))}</s>"),
                    Raw(f"<i>{_t('done')}</i>"),
                ]
            )
        card.add(
            Table(
                header=[_t("number"), _t("item"), _t("when")],
                rows=rows,
                bordered=False,
            )
        )

    card.add(Para(_t("hint"), style="italic"))
    card.buttons(Button(_t("copy_list"), copy=_plain_list(open_items + done_items), style="primary"))
    if board_url:
        card.buttons(Button(_t("open_board"), url=board_url))
    card.footer = _t("footer")
    return card


def whois_card(*, domain: str, fields: dict[str, str]) -> Card:
    """A registry answer as a sheet: what was asked, then only the keys that answered."""
    card = Card(_t("whois_title"), glyph="🔎", meta=domain)
    labels = {
        "registrar": _t("registrar"),
        "created": _t("created"),
        "expires": _t("expires"),
        "updated": _t("updated"),
        "status": _t("status"),
        "nameservers": _t("nameservers"),
    }
    rows: list[list[Any]] = [[Raw(_t("domain")), Raw(f"<code>{_esc(domain)}</code>")]]
    for key, label in labels.items():
        value = str(fields.get(key) or "").strip()
        if value:
            rows.append([Raw(label), Raw(_esc(value))])
    if len(rows) == 1:
        card.add(Para(_t("no_data"), style="italic"))
    else:
        card.add(Table(rows=rows, bordered=False))
    card.buttons(Button(_t("copy_domain"), copy=domain, style="primary"))
    return card


def dns_card(*, domain: str, records: Sequence[tuple[str, str]] = ()) -> Card:
    """A DNS answer as a two-column table, one row per record."""
    card = Card(_t("dns_title"), glyph="🌐", meta=domain)
    if records:
        card.add(
            Table(
                header=[_t("dns_type"), _t("dns_value")],
                rows=[
                    [Raw(f"<code>{_esc(kind)}</code>"), Raw(f"<code>{_esc(value)}</code>")]
                    for kind, value in records
                ],
                bordered=False,
            )
        )
    else:
        card.add(Para(_t("dns_none"), style="italic"))
    card.buttons(Button(_t("copy_lookup"), copy=f"{domain}", style="primary"))
    return card


def _verdict(outcome: str, ok_key: str, fail_key: str, private_key: str) -> str:
    if outcome == "ok":
        return _t(ok_key)
    if outcome == "private":
        return _t(private_key)
    return _t(fail_key)


def ssl_card(*, host: str, facts: dict[str, str]) -> Card:
    """Certificate validity for a domain, with the verdict stated instead of implied."""
    outcome = str(facts.get("outcome") or "")
    card = Card(_t("ssl_title"), glyph="🔒", meta=host or str(facts.get("host") or ""))
    rows: list[list[Any]] = []
    for key, label in (
        ("subject", _t("ssl_subject")),
        ("issuer", _t("ssl_issuer")),
        ("not_after", _t("ssl_expires")),
        ("days_left", _t("ssl_days")),
        ("sans", _t("ssl_sans")),
    ):
        value = str(facts.get(key) or "").strip()
        if value:
            rows.append([Raw(label), Raw(_esc(value))])
    if rows:
        card.add(Table(rows=rows, bordered=False))
    if outcome == "private":
        card.add(Para(_t("ssl_private"), style="italic"))
    elif outcome != "ok":
        detail = str(facts.get("error") or "").strip()
        card.add(Para(_t("ssl_failing"), style="italic"))
        if detail:
            card.add(Para(Raw(f"<code>{_esc(detail)}</code>")))
    else:
        card.add(Para(_t("ssl_ok"), style="italic"))
    card.buttons(Button(_t("copy_domain"), copy=host, style="primary"))
    return card


def http_card(*, url: str, facts: dict[str, str]) -> Card:
    """What a link actually does: status, where it ends up, how many hops, what serves it."""
    outcome = str(facts.get("outcome") or "")
    card = Card(_t("http_title"), glyph="🔗", meta=str(facts.get("final_url") or url or ""))
    rows: list[list[Any]] = []
    if facts.get("status"):
        reason = str(facts.get("reason") or "").strip()
        value = f"{facts['status']} {reason}".strip()
        rows.append([Raw(_t("http_status")), Raw(f"<b>{_esc(value)}</b>")])
    for key, label in (
        ("final_url", _t("http_final")),
        ("redirects", _t("http_redirects")),
        ("server", _t("http_server")),
        ("content_type", _t("http_type")),
    ):
        value = str(facts.get(key) or "").strip()
        if value:
            rows.append([Raw(label), Raw(f"<code>{_esc(value)}</code>")])
    if rows:
        card.add(Table(rows=rows, bordered=False))
    if outcome == "private":
        card.add(Para(_t("http_private"), style="italic"))
    elif outcome != "ok":
        card.add(Para(_t("http_failing"), style="italic"))
        detail = str(facts.get("error") or "").strip()
        if detail:
            card.add(Para(Raw(f"<code>{_esc(detail)}</code>")))
    else:
        card.add(Para(_t("http_ok"), style="italic"))
    card.buttons(Button(_t("copy_url"), copy=str(facts.get("url") or url), style="primary"))
    return card


def reminder_card(*, text: str, when: str = "", repeat: str = "") -> Card:
    """A due reminder: the message, when it was due, how it repeats, and the owner's buttons.

    The keyboard is attached by the caller (``card_transport.send_card(..., keyboard=rows)``)
    so the snooze/stop payloads are exactly the ones the text version used.
    """
    card = Card(_t("reminder_title"), glyph="⏰", meta=when)
    card.add(Para(Raw(_esc(text))))
    rows: list[list[Any]] = []
    if when:
        rows.append([Raw(_t("reminder_when")), Raw(f"<b>{_esc(when)}</b>")])
    if repeat:
        rows.append([Raw(_t("reminder_repeat")), Raw(_esc(repeat))])
    if rows:
        card.add(Table(rows=rows, bordered=False))
    card.footer = _t("reminder_snooze_hint")
    return card


__all__ = ["dns_card", "http_card", "reminder_card", "ssl_card", "todo_card", "whois_card"]
