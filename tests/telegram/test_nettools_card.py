"""DNS and SSL as cards: records in a table, the expiry as a verdict, re-check in place.

Both probes used to answer with aligned `<b>` labels and `<code>` values under a drawn
rule — a shape that hid the two facts worth knowing (no MX record, a certificate 12 days
from expiry) inside the middle of a block. These tests pin the tables, the verdict rule,
the paired buttons and the press that rewrites the message instead of stacking a second
probe into the chat.
"""

from __future__ import annotations

import socket

import pytest

from navig.telegram import nettools_card as nc


class _RichChannel:
    def __init__(self) -> None:
        self.rich: list[dict] = []
        self.plain: list[dict] = []

    async def send_rich_message(self, chat_id, **kw):
        self.rich.append({"chat_id": chat_id, **kw})
        return {"message_id": 21}

    async def send_message(self, chat_id, text, **kw):
        self.plain.append({"chat_id": chat_id, "text": text, **kw})
        return {"message_id": 22}


def _labels(rows) -> list[str]:
    return [button["text"] for row in rows or () for button in row]


# --- DNS -------------------------------------------------------------------


def test_each_record_is_a_row_so_a_long_txt_wraps_instead_of_running_off() -> None:
    card = nc.dns_card(
        "naturalpad.fr",
        ipv4=["104.21.2.3", "172.67.1.1"],
        ipv6=["2606:4700::1"],
        mx=[(20, "mx2.ovh.net"), (10, "mx1.ovh.net")],
        txt=["v=spf1 include:ovh.net ~all"],
    )
    html = card.rich_html()
    assert "<th>Type</th><th>Value</th>" in html
    assert "<td><b>A</b></td><td><code>104.21.2.3</code></td>" in html
    assert "<td><b>AAAA</b></td><td><code>2606:4700::1</code></td>" in html
    # MX sorts by preference, which is the order mail servers try them in.
    assert html.index("mx1.ovh.net") < html.index("mx2.ovh.net")
    assert "<code>v=spf1 include:ovh.net ~all</code>" in html


def test_a_missing_mx_record_is_a_verdict_not_an_absence_to_notice() -> None:
    card = nc.dns_card("naturalpad.fr", ipv4=["104.21.2.3"], mx=[], txt=["v=spf1 -all"])
    assert "<aside>No MX record" in card.rich_html()


def test_a_missing_spf_record_is_named_after_the_mx_check_passes() -> None:
    card = nc.dns_card("naturalpad.fr", ipv4=["1.2.3.4"], mx=[(10, "mx.example")], txt=[])
    assert "No SPF record" in card.rich_html()


def test_a_complete_name_gets_no_warning_sentence() -> None:
    card = nc.dns_card(
        "naturalpad.fr", ipv4=["1.2.3.4"], mx=[(10, "mx.example")], txt=["v=spf1 -all"]
    )
    html = card.rich_html()
    assert "<aside>" not in html
    assert "No MX" not in html and "No SPF" not in html


def test_the_two_probes_are_paired_on_each_card() -> None:
    card = nc.dns_card("naturalpad.fr", ipv4=["1.2.3.4"], mx=[(1, "m")], txt=["v=spf1 -all"])
    labels = _labels(card.keyboard())
    assert labels == ["🔄 Re-check", "🔐 Certificate", "✖ Close"]
    assert card.keyboard()[0][0]["callback_data"] == "nt:dns:naturalpad.fr"
    assert card.keyboard()[0][1]["callback_data"] == "nt:ssl:naturalpad.fr:443"
    assert card.keyboard()[0][0]["style"] == "primary"


def test_a_failed_lookup_says_why_and_keeps_the_re_check() -> None:
    card = nc.dns_failure_card("nope.invalid", "[Errno -2] Name or service not known")
    html = card.rich_html()
    assert "<aside>" in html and "Name or service not known" in html
    assert "🔄 Re-check" in _labels(card.keyboard())


# --- certificate -----------------------------------------------------------


def test_the_certificate_sheet_states_issuer_dates_and_days_left() -> None:
    card = nc.ssl_card(
        "naturalpad.fr",
        common_name="naturalpad.fr",
        issuer="Let's Encrypt",
        not_before="Jul 1, 2026",
        not_after="Sep 29, 2026",
        days_left=64,
    )
    html = card.rich_html()
    assert "<td>Issued by</td><td>Let&#x27;s Encrypt</td>" in html or "Encrypt" in html
    assert "<td>Days left</td><td><b>64</b></td>" in html
    assert "<h2>🔐 SSL certificate</h2>" in html


@pytest.mark.parametrize(
    ("days", "expected", "callout"),
    [
        (-1, "expired", True),
        (0, "expired", True),
        (5, "renew it now", True),
        (14, "renew it now", True),
        (21, "renew soon", False),
        (90, "days left", False),
    ],
)
def test_the_expiry_verdict_is_a_rule_not_a_vibe(days, expected, callout) -> None:
    card = nc.ssl_card("example.com", common_name="example.com", days_left=days)
    html = card.rich_html()
    assert expected in html
    assert ("<aside>" in html) is callout


def test_the_subject_alternative_names_are_a_bullet_list() -> None:
    card = nc.ssl_card(
        "naturalpad.fr",
        common_name="naturalpad.fr",
        sans=["naturalpad.fr", "www.naturalpad.fr", "api.naturalpad.fr"],
        days_left=90,
    )
    html = card.rich_html()
    assert "<p><b>SANs</b> (3 names)</p>" in html
    assert "<li><code>www.naturalpad.fr</code></li>" in html


def test_the_sans_cap_says_how_many_it_did_not_print() -> None:
    names = [f"n{i}.example.com" for i in range(9)]
    html = nc.ssl_card("example.com", common_name="example.com", sans=names, days_left=90).rich_html()
    assert "+3 more" in html


def test_an_unreachable_host_is_a_failure_card_with_the_port_in_the_meta() -> None:
    card = nc.ssl_failure_card("example.com", "handshake timed out", port=8443)
    html = card.rich_html()
    assert "example.com:8443" in html
    assert "<aside>handshake timed out</aside>" in html
    assert card.keyboard()[0][0]["callback_data"] == "nt:ssl:example.com:8443"


# --- callbacks -------------------------------------------------------------


def test_a_callback_carries_the_probe_the_domain_and_the_port() -> None:
    assert nc.parse_callback("nt:dns:example.com:443") == ("dns", "example.com", 443)
    assert nc.parse_callback("nt:ssl:example.com:8443") == ("ssl", "example.com", 8443)


@pytest.mark.parametrize(
    "data",
    ["", "nt:dns:x", "wh:R:example.com", "nt:bogus:x:443", "nt:ssl:x:notaport", "nt:ssl:x:0"],
)
def test_a_malformed_callback_is_refused(data) -> None:
    assert nc.parse_callback(data) is None


def test_the_classic_projection_keeps_the_words_and_drops_the_rich_tags() -> None:
    classic = nc.ssl_card(
        "example.com", common_name="example.com", issuer="ACME", days_left=3
    ).classic_html()
    assert "example.com" in classic and "ACME" in classic
    for tag in ("<h2", "<table", "<tg-button-row", "<footer", "<aside"):
        assert tag not in classic


# --- the handlers ----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_typed_dns_command_lands_as_one_card(monkeypatch):
    from navig.gateway.channels.telegram import TelegramChannel

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.21.2.3", 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700::1", 0, 0, 0)),
        ],
    )
    channel = TelegramChannel(bot_token="1:FAKE")
    recorder = _RichChannel()
    channel.send_rich_message = recorder.send_rich_message
    channel.send_message = recorder.send_message

    await channel._handle_dns(1, 2, text="/dns naturalpad.fr")

    assert len(recorder.rich) == 1
    html = recorder.rich[0]["html"]
    assert "<h2>🌍 DNS lookup</h2>" in html
    assert "104.21.2.3" in html
    assert "<tg-button-row" in html


@pytest.mark.asyncio
async def test_a_press_on_the_dns_card_rewrites_it_in_place(monkeypatch):
    from navig.gateway.channels.telegram import TelegramChannel
    from navig.telegram import card_transport

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.21.2.3", 0)),
        ],
    )
    channel = TelegramChannel(bot_token="1:FAKE")
    recorder = _RichChannel()
    channel.send_rich_message = recorder.send_rich_message
    channel.send_message = recorder.send_message

    seen: list[tuple[int, int]] = []

    async def _edit_card(chan, chat_id, message_id, card, *, keyboard=None):
        seen.append((chat_id, message_id))
        return True

    monkeypatch.setattr(card_transport, "edit_card", _edit_card)

    await channel._handle_dns(1, 2, text="/dns naturalpad.fr", message_id=88)

    assert seen == [(1, 88)]
    assert not recorder.rich


@pytest.mark.asyncio
async def test_an_unresolvable_name_still_answers_with_a_card(monkeypatch):
    def _fail(host, port):
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", _fail)
    from navig.gateway.channels.telegram import TelegramChannel

    channel = TelegramChannel(bot_token="1:FAKE")
    recorder = _RichChannel()
    channel.send_rich_message = recorder.send_rich_message
    channel.send_message = recorder.send_message

    await channel._handle_dns(1, 2, text="/dns nope.invalid")

    assert len(recorder.rich) == 1
    assert "Name or service not known" in recorder.rich[0]["html"]


# --- the resolver's output -------------------------------------------------

LINUX_MX = (
    "Server:\t\t127.0.0.53\n"
    "Address:\t127.0.0.53#53\n"
    "\n"
    "Non-authoritative answer:\n"
    "naturalpad.fr\tmail exchanger = 1 smtp.google.com.\n"
    "\n"
    "Authoritative answers can be found from:\n"
)
WINDOWS_MX = "naturalpad.fr  MX preference = 10, mail exchanger = mx1.ovh.net"
TXT_OUTPUT = (
    'naturalpad.fr\ttext = "facebook-domain-verification=qj2bju455rf77v5jl9e87snfbakm1j"\n'
    'naturalpad.fr\ttext = "v=spf1 include:_spf.google.com include:spf.brevo.com ~all"\n'
    'naturalpad.fr\ttext = "brevo-code:ffe17590b3ee5d37a2ba628b2037bd5f"\n'
)


def test_the_linux_resolver_shape_is_read() -> None:
    """`mail exchanger = 1 host` — no "preference" word, which the old regex demanded."""
    assert nc.parse_nslookup_mx(LINUX_MX) == [(1, "smtp.google.com")]


def test_the_windows_resolver_shape_is_read_too() -> None:
    assert nc.parse_nslookup_mx(WINDOWS_MX) == [(10, "mx1.ovh.net")]


def test_a_record_free_answer_parses_to_nothing() -> None:
    assert nc.parse_nslookup_mx("Non-authoritative answer:\n") == []
    assert nc.parse_nslookup_txt("Non-authoritative answer:\n") == []


def test_every_txt_value_is_kept_with_its_quotes_stripped() -> None:
    values = nc.parse_nslookup_txt(TXT_OUTPUT)
    assert len(values) == 3
    assert "v=spf1 include:_spf.google.com include:spf.brevo.com ~all" in values
    assert not any(value.startswith('"') or value.endswith('"') for value in values)


def test_a_real_mx_and_spf_leave_the_card_silent() -> None:
    html = nc.dns_card(
        "naturalpad.fr",
        ipv4=["104.21.2.3"],
        mx=nc.parse_nslookup_mx(LINUX_MX),
        txt=nc.parse_nslookup_txt(TXT_OUTPUT),
    ).rich_html()
    assert "<aside>" not in html
    assert "<code>smtp.google.com</code>" in html


@pytest.mark.parametrize(
    ("mx_checked", "txt_checked", "forbidden"),
    [
        (False, False, ("cannot receive mail", "No SPF record")),
        (False, True, ("cannot receive mail",)),
        (True, False, ("No SPF record",)),
    ],
)
def test_a_probe_that_did_not_run_never_claims_the_domain_lacks_the_record(
    mx_checked, txt_checked, forbidden
) -> None:
    """`nslookup` missing is a fact about the host, not about the domain."""
    html = nc.dns_card(
        "naturalpad.fr",
        ipv4=["104.21.2.3"],
        mx=[],
        txt=[],
        mx_checked=mx_checked,
        txt_checked=txt_checked,
    ).rich_html()
    for claim in forbidden:
        assert claim not in html


@pytest.mark.asyncio
async def test_a_missing_resolver_produces_no_false_absence(monkeypatch):
    from navig.gateway.channels.telegram import TelegramChannel

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.21.2.3", 0))],
    )

    async def _no_nslookup(*args, **kwargs):
        raise FileNotFoundError("nslookup")

    import asyncio as _asyncio

    monkeypatch.setattr(_asyncio, "create_subprocess_exec", _no_nslookup)
    channel = TelegramChannel(bot_token="1:FAKE")
    recorder = _RichChannel()
    channel.send_rich_message = recorder.send_rich_message
    channel.send_message = recorder.send_message

    await channel._handle_dns(1, 2, text="/dns naturalpad.fr")

    html = recorder.rich[0]["html"]
    assert "cannot receive mail" not in html
    assert "<code>104.21.2.3</code>" in html


def test_the_channel_can_reach_both_probes() -> None:
    import inspect

    from navig.gateway.channels.telegram import TelegramChannel

    for name in ("_handle_dns", "_handle_ssl"):
        params = inspect.signature(getattr(TelegramChannel, name)).parameters
        assert "message_id" in params, name


def test_the_nt_prefix_is_owned_by_the_net_tools_extension() -> None:
    from navig.gateway.channels.telegram_extensions import EXTENSIONS

    owner = next(e for e in EXTENSIONS if "nt:" in e.callback_prefixes)
    assert owner.id == "nettools"
    assert "dns" in owner.commands and "ssl" in owner.commands
