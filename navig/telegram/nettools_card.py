"""Two network probes as cards: DNS records and a certificate's life.

`/dns <domain>` and `/ssl <domain>` answered with an aligned block of `<b>` labels and
`<code>` values separated by a drawn rule. Nothing in it said whether the answer was
*normal* — a domain with no MX record looked exactly like one that delivers mail — and
re-checking meant retyping the command.

These builders take the probe's facts as plain data and render them: a table whose rows
are records (so a long TXT wraps instead of pushing the line off screen), a verdict only
when there is something worth saying, and the two probes paired as buttons on each card.
"""

from __future__ import annotations

from typing import Any

from navig.core.i18n import t
from navig.telegram.card_doc import Bullets, Button, Card, Para, PullQuote, Raw, Table, esc

_FALLBACK = {
    "nt.dns.title": "DNS lookup",
    "nt.dns.a": "A",
    "nt.dns.aaaa": "AAAA",
    "nt.dns.mx": "MX",
    "nt.dns.txt": "TXT",
    "nt.dns.type": "Type",
    "nt.dns.value": "Value",
    "nt.dns.empty": "The name resolved to nothing at all.",
    "nt.dns.no_mail": "No MX record — this domain cannot receive mail.",
    "nt.dns.no_spf": "No SPF record in TXT — senders can claim this domain.",
    "nt.ssl.title": "SSL certificate",
    "nt.ssl.issuer": "Issued by",
    "nt.ssl.cn": "Common name",
    "nt.ssl.from": "Valid from",
    "nt.ssl.to": "Expires",
    "nt.ssl.days": "Days left",
    "nt.ssl.sans": "SANs",
    "nt.ssl.expired": "The certificate has expired.",
    "nt.ssl.soon": "Expires in {days} days — renew it now.",
    "nt.ssl.renew": "Expires in {days} days — renew soon.",
    "nt.ssl.ok": "A valid certificate, {days} days left.",
    "nt.recheck": "🔄 Re-check",
    "nt.dns_btn": "🌍 DNS",
    "nt.ssl_btn": "🔐 Certificate",
    "nt.close": "✖ Close",
    "nt.hint": "Re-check in place, or open the other probe on the same name.",
    "nt.partner": "Same name, the other probe",
}

SOON_DAYS = 14
WARN_DAYS = 30
MAX_ADDRESSES = 8
MAX_TXT = 3
MAX_TEXT_WIDTH = 72


def _label(key: str, fallback: str = "") -> str:
    """A locale string when the install has one, the English default otherwise.

    `navig.core.i18n.t` returns the KEY when the catalogue has no entry, so a missing
    translation is detected by identity, not by falsiness.
    """
    try:
        value = t(key)
    except Exception:  # noqa: BLE001 — a missing catalogue is not a failed probe
        value = ""
    if not value or value == key:
        return fallback or _FALLBACK.get(key, key)
    return value


def _clip(text: str, width: int = MAX_TEXT_WIDTH) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= width else text[: width - 1] + "…"


# --- callbacks -------------------------------------------------------------


def dns_callback(domain: str) -> str:
    return f"nt:dns:{domain}"[:64]


def ssl_callback(domain: str, port: int = 443) -> str:
    return f"nt:ssl:{domain}:{port}"[:64]


def parse_callback(data: str) -> tuple[str, str, int] | None:
    """``nt:ssl:example.com:443`` -> ``("ssl", "example.com", 443)``."""
    parts = (data or "").split(":")
    if len(parts) != 4 or parts[0] != "nt":
        return None
    kind, domain, port_text = parts[1], parts[2], parts[3]
    if kind not in {"dns", "ssl"} or not domain:
        return None
    try:
        port = int(port_text)
    except ValueError:
        return None
    if not 1 <= port <= 65535:
        return None
    return kind, domain, port


def _buttons(card: Card, *, domain: str, port: int, kind: str) -> Card:
    recheck = Button(
        _label("nt.recheck", "🔄 Re-check"),
        data=dns_callback(domain) if kind == "dns" else ssl_callback(domain, port),
        style="primary",
    )
    partner = (
        Button(_label("nt.ssl_btn", "🔐 Certificate"), data=ssl_callback(domain, port))
        if kind == "dns"
        else Button(
            _label("nt.dns_btn", "🌍 DNS"), data=dns_callback(domain)
        )
    )
    card.buttons(recheck, partner)
    card.buttons(Button(_label("nt.close", "✖ Close"), data="nt:close:x:0"))
    card.footer = _label("nt.hint", "Re-check in place, or open the other probe on the same name.")
    return card


# --- DNS -------------------------------------------------------------------


def dns_rows(
    *,
    ipv4: list[str] | tuple[str, ...] = (),
    ipv6: list[str] | tuple[str, ...] = (),
    mx: list[tuple[int, str]] | tuple[tuple[int, str], ...] = (),
    txt: list[str] | tuple[str, ...] = (),
) -> list[list[Any]]:
    """One row per record: the type in the first column, the value in the second."""
    rows: list[list[Any]] = []
    for ip in list(ipv4)[:MAX_ADDRESSES]:
        rows.append([Raw(f"<b>{_label('nt.dns.a', 'A')}</b>"), Raw(f"<code>{esc(ip)}</code>")])
    for ip in list(ipv6)[:MAX_ADDRESSES]:
        rows.append([Raw(f"<b>{_label('nt.dns.aaaa', 'AAAA')}</b>"), Raw(f"<code>{esc(ip)}</code>")])
    for pref, host in sorted(mx, key=lambda item: item[0]):
        rows.append(
            [
                Raw(f"<b>{_label('nt.dns.mx', 'MX')}</b> {pref}"),
                Raw(f"<code>{esc(host)}</code>"),
            ]
        )
    for value in list(txt)[:MAX_TXT]:
        rows.append([Raw(f"<b>{_label('nt.dns.txt', 'TXT')}</b>"), Raw(f"<code>{esc(_clip(value))}</code>")])
    return rows


def dns_verdict(
    *,
    ipv4: list[str] | tuple[str, ...],
    mx: list[Any],
    txt: list[str] | tuple[str, ...],
    mx_checked: bool = True,
    txt_checked: bool = True,
) -> str:
    """Only the absences worth acting on; a healthy name needs no sentence.

    ``*_checked`` says whether the probe actually ran. A lookup that could not execute
    must stay silent — "No MX record" is a claim about the domain, and a missing
    ``nslookup`` is a claim about this host.
    """
    if not ipv4:
        return _label("nt.dns.empty", "The name resolved to nothing at all.")
    if mx_checked and not mx:
        return _label("nt.dns.no_mail", "No MX record — this domain cannot receive mail.")
    if txt_checked and not any("v=spf" in value.lower() for value in txt):
        return _label("nt.dns.no_spf", "No SPF record in TXT — senders can claim this domain.")
    return ""


def dns_card(
    domain: str,
    *,
    ipv4: list[str] | tuple[str, ...] = (),
    ipv6: list[str] | tuple[str, ...] = (),
    mx: list[tuple[int, str]] | tuple[tuple[int, str], ...] = (),
    txt: list[str] | tuple[str, ...] = (),
    mx_checked: bool = True,
    txt_checked: bool = True,
) -> Card:
    rows = dns_rows(ipv4=ipv4, ipv6=ipv6, mx=mx, txt=txt)
    card = Card(f"🌍 {_label('nt.dns.title', 'DNS lookup')}", meta=domain)
    if rows:
        card.add(
            Table(
                header=[_label("nt.dns.type", "Type"), _label("nt.dns.value", "Value")],
                rows=rows,
                bordered=False,
            )
        )
    verdict = dns_verdict(
        ipv4=ipv4, mx=mx, txt=txt, mx_checked=mx_checked, txt_checked=txt_checked
    )
    if verdict:
        card.add(PullQuote(verdict))
    return _buttons(card, domain=domain, port=443, kind="dns")


# --- parsing `nslookup` (no sockets, so it can be tested on fixture text) ---

_MX_PATTERNS = (
    # Linux: `naturalpad.fr\tmail exchanger = 1 smtp.google.com.`
    r"exchanger\s*=\s*(\d+)\s+(\S+)",
    # Windows: `MX preference = 10, mail exchanger = mx.example.com`
    r"preference\s*=\s*(\d+).*?exchanger\s*=\s*(\S+)",
    # Bare `MX = 1 smtp.google.com` shape used by some resolvers.
    r"\bMX\s*=\s*(\d+)\s+(\S+)",
)
_TXT_PATTERN = r"\btext\s*=\s*\"(.*)\"\s*$"


def parse_nslookup_mx(text: str) -> list[tuple[int, str]]:
    """MX records from `nslookup -type=MX` output, ordered by preference."""
    import re

    found: list[tuple[int, str]] = []
    for line in (text or "").splitlines():
        for pattern in _MX_PATTERNS:
            match = re.search(pattern, line, re.I)
            if match:
                host = match.group(2).rstrip(".").strip()
                if host and (int(match.group(1)), host) not in found:
                    found.append((int(match.group(1)), host))
                break
    return sorted(found, key=lambda item: item[0])


def parse_nslookup_txt(text: str) -> list[str]:
    """Quoted TXT values from `nslookup -type=TXT` output, duplicates dropped."""
    import re

    found: list[str] = []
    for line in (text or "").splitlines():
        match = re.search(_TXT_PATTERN, line)
        if not match:
            continue
        value = match.group(1)
        if value and value not in found:
            found.append(value)
    return found


def dns_failure_card(domain: str, reason: str) -> Card:
    card = Card(f"🌍 {_label('nt.dns.title', 'DNS lookup')}", meta=domain)
    card.add(PullQuote(f"{_label('nt.dns.empty', 'The name resolved to nothing at all.')} {esc(reason)}"))
    return _buttons(card, domain=domain, port=443, kind="dns")


# --- certificate -----------------------------------------------------------


def ssl_verdict(days_left: int | None) -> tuple[str, bool]:
    """``(sentence, needs_a_callout)`` — silence is the healthy case."""
    if days_left is None:
        return "", False
    if days_left <= 0:
        return _label("nt.ssl.expired", "The certificate has expired."), True
    if days_left <= SOON_DAYS:
        return _label("nt.ssl.soon", "Expires in {days} days — renew it now.").replace("{days}", str(days_left)), True
    if days_left <= WARN_DAYS:
        return _label("nt.ssl.renew", "Expires in {days} days — renew soon.").replace("{days}", str(days_left)), False
    return _label("nt.ssl.ok", "A valid certificate, {days} days left.").replace("{days}", str(days_left)), False


def ssl_card(
    domain: str,
    *,
    port: int = 443,
    common_name: str = "",
    issuer: str = "",
    not_before: str = "",
    not_after: str = "",
    sans: list[str] | tuple[str, ...] = (),
    days_left: int | None = None,
) -> Card:
    card = Card(
        f"🔐 {_label('nt.ssl.title', 'SSL certificate')}",
        meta=f"{domain}:{port}",
    )
    sheet: list[list[Any]] = []
    if common_name:
        sheet.append([Raw(_label("nt.ssl.cn", "Common name")), Raw(f"<code>{esc(common_name)}</code>")])
    if issuer:
        sheet.append([Raw(_label("nt.ssl.issuer", "Issued by")), Raw(esc(issuer))])
    if not_before:
        sheet.append([Raw(_label("nt.ssl.from", "Valid from")), Raw(esc(not_before))])
    if not_after:
        sheet.append([Raw(_label("nt.ssl.to", "Expires")), Raw(esc(not_after))])
    if days_left is not None:
        sheet.append(
            [Raw(_label("nt.ssl.days", "Days left")), Raw(f"<b>{days_left}</b>")]
        )
    if sheet:
        card.add(Table(rows=sheet, bordered=False))

    if sans:
        shown = [Raw(f"<code>{esc(name)}</code>") for name in list(sans)[:6]]
        if len(sans) > 6:
            shown.append(Raw(f"<i>+{len(sans) - 6} more</i>"))
        card.add(Para(Raw(f"<b>{_label('nt.ssl.sans', 'SANs')}</b> ({len(sans)} names)")))
        card.add(Bullets(shown))

    verdict, urgent = ssl_verdict(days_left)
    if verdict:
        if urgent:
            card.add(PullQuote(verdict))
        else:
            card.add(Para(verdict, style="italic"))
    return _buttons(card, domain=domain, port=port, kind="ssl")


def ssl_failure_card(domain: str, reason: str, *, port: int = 443) -> Card:
    card = Card(f"🔐 {_label('nt.ssl.title', 'SSL certificate')}", meta=f"{domain}:{port}")
    card.add(PullQuote(esc(reason)))
    return _buttons(card, domain=domain, port=port, kind="ssl")


def build_dns(domain: str, **kwargs: Any) -> tuple[str, str, list[list[dict]] | None]:
    card = dns_card(domain, **kwargs)
    return card.classic_html(), card.rich_html(), card.keyboard()


def build_ssl(domain: str, **kwargs: Any) -> tuple[str, str, list[list[dict]] | None]:
    card = ssl_card(domain, **kwargs)
    return card.classic_html(), card.rich_html(), card.keyboard()


__all__ = [
    "MAX_ADDRESSES",
    "MAX_TXT",
    "SOON_DAYS",
    "WARN_DAYS",
    "build_dns",
    "build_ssl",
    "dns_callback",
    "dns_card",
    "dns_failure_card",
    "dns_rows",
    "dns_verdict",
    "parse_callback",
    "parse_nslookup_mx",
    "parse_nslookup_txt",
    "ssl_callback",
    "ssl_card",
    "ssl_failure_card",
    "ssl_verdict",
]
