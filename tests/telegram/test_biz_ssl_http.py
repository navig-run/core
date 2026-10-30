"""Tests for the business ssl/http cards and the private-target guard."""

from __future__ import annotations

import pytest

from navig.telegram import biz_card, biz_lookups

SSL_OK = {
    "outcome": "ok",
    "host": "example.com",
    "subject": "example.com",
    "issuer": "Let's Encrypt",
    "not_after": "Jan 12 09:00:00 2027 GMT",
    "days_left": "77",
    "sans": "2",
}

HTTP_OK = {
    "outcome": "ok",
    "url": "http://example.com",
    "status": "200",
    "reason": "OK",
    "final_url": "https://example.com/",
    "redirects": "1",
    "server": "cloudflare",
    "content_type": "text/html; charset=utf-8",
}


def _callbacks(card) -> list[str]:
    return [
        btn.get("callback_data")
        for row in (card.keyboard() or [])
        for btn in row
        if btn.get("callback_data")
    ]


# ── the cards ────────────────────────────────────────────────────────────────


def test_an_ssl_card_states_the_verdict_and_the_dates() -> None:
    card = biz_card.ssl_card(host="example.com", facts=SSL_OK)
    html = card.rich_html()
    assert "<h2>🔒 Certificate</h2>" in html
    assert "<td>Issuer</td><td>Let&#x27;s Encrypt</td>" in html or "Let" in html
    assert "Jan 12 09:00:00 2027 GMT" in html
    assert "77" in html
    assert "valid and the handshake succeeded" in html


def test_a_failed_ssl_card_says_a_customer_would_be_warned() -> None:
    card = biz_card.ssl_card(host="broken.test", facts={"outcome": "failed", "error": "timeout"})
    html = card.rich_html()
    assert "browser would warn" in html
    assert "timeout" in html


def test_a_private_ssl_target_is_refused_rather_than_probed() -> None:
    html = biz_card.ssl_card(host="127.0.0.1", facts={"outcome": "private"}).rich_html()
    assert "own address" in html


def test_an_http_card_shows_where_the_link_ends_up() -> None:
    card = biz_card.http_card(url="http://example.com", facts=HTTP_OK)
    html = card.rich_html()
    assert "<h2>🔗 URL check</h2>" in html
    assert "<b>200 OK</b>" in html
    assert "https://example.com/" in html
    assert "cloudflare" in html
    assert "The link answered" in html


def test_an_unreachable_url_is_an_answer_not_a_crash() -> None:
    card = biz_card.http_card(
        url="https://nope.test", facts={"outcome": "failed", "url": "https://nope.test", "error": "dns"}
    )
    html = card.rich_html()
    assert "did not answer" in html
    assert "dns" in html


def test_a_private_http_target_is_refused() -> None:
    html = biz_card.http_card(
        url="http://169.254.169.254/latest", facts={"outcome": "private"}
    ).rich_html()
    assert "may not probe this host" in html


def test_the_new_cards_carry_only_copy_buttons() -> None:
    for card in (
        biz_card.ssl_card(host="example.com", facts=SSL_OK),
        biz_card.http_card(url="https://example.com", facts=HTTP_OK),
    ):
        assert _callbacks(card) == []
        assert any(
            btn.get("copy_text") for row in card.keyboard() for btn in row
        )


def test_the_new_cards_keep_their_classic_projection_clean() -> None:
    for card in (
        biz_card.ssl_card(host="example.com", facts=SSL_OK),
        biz_card.http_card(url="https://example.com", facts=HTTP_OK),
    ):
        classic = card.classic_html()
        for tag in ("<h2", "<table", "<aside", "<footer"):
            assert tag not in classic, (tag, classic[:160])


# ── the guard ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "localhost",
        "10.0.0.5",
        "192.168.1.1",
        "172.16.4.1",
        "169.254.169.254",
        "::1",
    ],
)
def test_private_and_metadata_hosts_are_recognised(host: str) -> None:
    assert biz_lookups.is_private_target(host) is True


@pytest.mark.parametrize("host", ["example.com", "172.32.0.1", "navig.run"])
def test_public_hosts_are_not(host: str) -> None:
    assert biz_lookups.is_private_target(host) is False


@pytest.mark.asyncio
async def test_ssl_info_refuses_a_private_host_without_a_handshake(monkeypatch) -> None:
    def boom(*a, **k):
        raise AssertionError("a private host must not be dialled")

    monkeypatch.setattr(biz_lookups, "_ssl_probe", boom)
    assert (await biz_lookups.ssl_info("127.0.0.1"))["outcome"] == "private"


@pytest.mark.asyncio
async def test_http_info_refuses_the_metadata_address(monkeypatch) -> None:
    import aiohttp

    def boom(*a, **k):
        raise AssertionError("the metadata address must not be fetched")

    monkeypatch.setattr(aiohttp, "ClientSession", boom)
    facts = await biz_lookups.http_info("http://169.254.169.254/latest/meta-data")
    assert facts["outcome"] == "private"


@pytest.mark.asyncio
async def test_ssl_info_says_invalid_for_a_bare_word(monkeypatch) -> None:
    def boom(*a, **k):
        raise AssertionError("no handshake for a bare word")

    monkeypatch.setattr(biz_lookups, "_ssl_probe", boom)
    assert (await biz_lookups.ssl_info("localhost"))["outcome"] in {"invalid", "private"}
