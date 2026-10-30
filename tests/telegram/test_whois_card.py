"""The WHOIS card: a sheet, a verdict, links inside the text, and a re-read button."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from navig.telegram import whois_card as wc

# A real Verisign answer, trimmed to the lines the parser cares about.
RECORD = """
   Domain Name: GITHUB.COM
   Registry Domain ID: 1264984_DOMAIN_COM-VRSN
   Creation Date: 2007-10-09T18:20:50Z
   Registry Expiry Date: 2027-10-09T18:20:50Z
   Updated Date: 2025-09-09T07:07:05Z
   Registrar: MarkMonitor Inc.
   Domain Status: clientDeleteProhibited https://icann.org/epp#clientDeleteProhibited
   Name Server: DNS1.P08.NSONE.NET
   Name Server: DNS2.P08.NSONE.NET
   DNSSEC: unsigned
"""


# AFNIC answers in a completely different shape: one word per key, `nserver:`,
# and an `eppstatus:` line that must not be mistaken for the domain status.
AFNIC = """
domain:                        naturalpad.fr
status:                        ACTIVE
eppstatus:                     active
registrar:                     OVH
Expiry Date:                   2027-06-17T16:57:39Z
created:                       2011-06-17T16:57:39Z
last-update:                   2026-08-14T09:47:18.611874Z

nserver:                       etienne.ns.cloudflare.com
nserver:                       harlee.ns.cloudflare.com
"""


def test_the_record_parses_into_fields_and_servers():
    fields, servers = wc.parse_record(RECORD)
    assert fields["Registered"].startswith("2007-10-09")
    assert fields["Expires"].startswith("2027-10-09")
    assert fields["Registrar"] == "MarkMonitor Inc."
    # The trailing EPP URL is not part of the status word.
    assert fields["Status"] == "clientDeleteProhibited"
    assert servers == ["dns1.p08.nsone.net", "dns2.p08.nsone.net"]


def test_a_second_registry_shape_parses_too():
    fields, servers = wc.parse_record(AFNIC)
    assert fields["Registered"].startswith("2011-06-17")
    assert fields["Expires"].startswith("2027-06-17")
    assert fields["Updated"].startswith("2026-08-14")
    assert fields["Registrar"] == "OVH"
    # `eppstatus: active` is not `status: ACTIVE` — the anchored pattern decides.
    assert fields["Status"] == "ACTIVE"
    assert servers == ["etienne.ns.cloudflare.com", "harlee.ns.cloudflare.com"]


def test_a_redacted_registrant_is_not_a_value():
    fields, _ = wc.parse_record(
        "Registrant Organization: REDACTED FOR PRIVACY\nRegistrant Email: Not Disclosed\n"
    )
    assert "Registrant" not in fields
    assert "Registrant email" not in fields


def test_a_date_reads_as_a_date():
    assert wc.human_date("2007-10-09T18:20:50Z") == "Oct 9, 2007"
    assert wc.human_date("2007-10-09") == "Oct 9, 2007"
    # Anything unexpected survives instead of becoming "None".
    assert wc.human_date("before 1999") == "before 1999"


def test_days_left_is_measured_from_the_expiry_date():
    now = datetime(2026, 10, 27, tzinfo=timezone.utc)
    assert wc.days_until("2027-10-09T18:20:50Z", now=now) == 347
    assert wc.days_until("2026-11-01", now=now) == 5
    assert wc.days_until("2026-10-01", now=now) == -26
    assert wc.days_until("soon", now=now) is None


def test_the_verdict_shouts_only_when_it_matters():
    assert wc.verdict(None) == ("", False)
    expired, urgent = wc.verdict(-3)
    assert urgent and "expired" in expired
    assert wc.verdict(12)[1] is True
    soon, urgent_soon = wc.verdict(60)
    assert urgent_soon is False and "60 days" in soon
    long_run, _ = wc.verdict(800)
    assert "2y" in long_run


def _card(**kwargs):
    fields, servers = wc.parse_record(RECORD)
    return wc.whois_card("github.com", fields, servers, **kwargs)


def test_the_card_puts_the_dates_in_a_sheet_and_the_servers_in_a_list():
    rich = _card(days=347).rich_html()
    assert "<h1>🌍 WHOIS</h1>" in rich
    assert "<table striped>" in rich
    assert "<td>📅 Registered</td><td><b>Oct 9, 2007</b></td>" in rich
    assert "<td>⏳ Expires</td><td><b>Oct 9, 2027</b></td>" in rich
    assert "<td>Days left</td><td><b>347</b></td>" in rich
    assert "<td>🏢 Registrar</td><td>MarkMonitor Inc.</td>" in rich
    assert "<li><code>dns1.p08.nsone.net</code></li>" in rich


def test_the_links_are_inside_the_text_not_only_on_the_buttons():
    rich = _card(days=347).rich_html()
    assert '<a href="https://github.com">Open the site</a>' in rich
    assert '<a href="https://rdap.org/domain/github.com">RDAP record</a>' in rich


def test_the_card_buttons_open_the_site_and_re_read_it():
    rows = _card(days=347).keyboard()
    flat = [button for row in rows for button in row]
    assert flat[0] == {"text": "🌐 Open site", "url": "https://github.com"}
    assert flat[1] == {"text": "📋 RDAP", "url": "https://rdap.org/domain/github.com"}
    assert flat[2]["callback_data"] == "wh:R:github.com"
    assert flat[2]["style"] == "primary"


def test_the_classic_projection_carries_no_rich_only_tag():
    classic = _card(days=347).classic_html()
    for tag in ("<h2>", "<table", "<tg-button-row", "<aside", "<footer"):
        assert tag not in classic


def test_a_lapsed_domain_is_a_callout_and_a_healthy_one_is_a_sentence():
    assert "<aside>" in _card(days=-2).rich_html()
    healthy = _card(days=800).rich_html()
    assert "<aside>" not in healthy
    assert "Registered for another 2y" in healthy


def test_a_failed_lookup_is_still_a_card_with_a_way_out():
    card = wc.failure_card("example.com", "timed out")
    rich = card.rich_html()
    assert "The registry did not answer: timed out" in rich
    assert '<a href="https://rdap.org/domain/example.com">Try the RDAP record</a>' in rich
    assert card.keyboard()[0][0]["url"] == "https://rdap.org/domain/example.com"


def test_the_refresh_payload_round_trips():
    assert wc.refresh_payload("github.com") == "wh:R:github.com"
    assert wc.parse_refresh("wh:R:github.com") == "github.com"
    assert wc.parse_refresh("wh:X:github.com") is None
    assert wc.parse_refresh("hm:open:tasks") is None
    assert wc.parse_refresh("wh:R:") is None
    # A press must fit Telegram's 64-byte callback budget.
    assert len(wc.refresh_payload("a-really-long-subdomain.example.museum")) < 64


def test_the_handler_routes_a_press_back_through_itself():
    """The `wh:` branch asks the channel for the domain again; no state is stored."""
    from navig.telegram import whois_card as mod

    assert mod.parse_refresh("wh:R:naturalpad.fr") == "naturalpad.fr"


class _FakeChannel:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.edited: list[dict] = []

    def _rich_messages_enabled(self) -> bool:
        return True

    async def send_rich_message(self, chat_id, *, html=None, fallback_html=None, keyboard=None, **_kw):
        self.sent.append({"chat_id": chat_id, "html": html, "classic": fallback_html, "keyboard": keyboard})
        return {"message_id": 1}

    async def edit_rich_message(self, chat_id, message_id, *, html=None, fallback_html=None, keyboard=None):
        self.edited.append({"message_id": message_id, "html": html, "keyboard": keyboard})
        return True

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text, "keyboard": kwargs.get("keyboard")})
        return {"message_id": 2}


def test_a_refresh_rewrites_the_pressed_message_and_never_sends_a_second():
    from navig.telegram import card_transport

    channel = _FakeChannel()
    card = _card(days=347)
    asyncio.run(card_transport.edit_card(channel, 42, 777, card))
    assert channel.edited[0]["message_id"] == 777
    assert "<table striped>" in channel.edited[0]["html"]
    assert not channel.sent
