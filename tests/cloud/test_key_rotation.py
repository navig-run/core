"""Rotating ``deck.api_key`` must never silently break reachability.

The key's sha256 IS the Lighthouse tenant (the Durable Object the edge routes to) and
its raw value is embedded in the Mini App link, so a rotation moves the bot's mailbox.
Every *stored* derivative left behind keeps addressing a tenant nothing is attached to —
and the edge ACCEPTS those POSTs (queues them, acks 202), so nothing errors and the
traffic is simply gone. That is how the Business bot went 100% deaf with every light
green (#132/#138).

These tests pin the contract:
  • what we OWN is repaired automatically (webhook + Mini App button);
  • what lives in systems we CANNOT reach (a website's ingest snippet, a Twilio console)
    is surfaced to the operator with its new URL — never silently left broken.
"""

from __future__ import annotations

import pytest

from navig.cloud import api_key_hash, rotation

EDGE = "https://lh.example.workers.dev"
KEY = "navig_live_key"


class FakeConfig:
    def __init__(self, **values):
        self._v = values

    def get(self, key, default=None):
        return self._v.get(key, default)


def _cfg(**over):
    base = {
        "cloud.mode": "lighthouse",
        "cloud.lighthouse_url": EDGE,
        "deck.api_key": KEY,
    }
    base.update(over)
    return FakeConfig(**base)


# ── what we own: repaired automatically ──────────────────────────────────────


def test_rotation_repoints_webhook_and_menu_button(monkeypatch):
    """Both owned derivatives must be re-pointed — the webhook (or the bot is deaf)
    AND the Mini App button (or the deck seeds a dead key and renders blank)."""
    from navig.commands import lighthouse

    monkeypatch.setattr(lighthouse, "configure_telegram_webhook", lambda edge: f"{edge}/tg/x")
    monkeypatch.setattr(rotation, "repoint_menu_button", lambda cfg=None: True)

    fixed, failed = rotation.repoint_owned(_cfg())

    assert "Telegram webhook" in fixed
    assert "Mini App menu button" in fixed
    assert failed == []


def test_one_broken_repoint_does_not_abort_the_other(monkeypatch):
    """A rotation must never hard-fail: if Telegram is briefly down for the button,
    the webhook must still be re-pointed (that one is the difference between a
    working bot and a dead one)."""
    from navig.commands import lighthouse

    monkeypatch.setattr(lighthouse, "configure_telegram_webhook", lambda edge: f"{edge}/tg/x")

    def _boom(cfg=None):
        raise RuntimeError("telegram unreachable")

    monkeypatch.setattr(rotation, "repoint_menu_button", _boom)

    fixed, failed = rotation.repoint_owned(_cfg())

    assert "Telegram webhook" in fixed, "the critical repoint must still happen"
    assert any("Mini App" in f for f in failed), "the failure must be REPORTED, not swallowed"


def test_repoint_is_a_noop_outside_lighthouse(monkeypatch):
    from navig.commands import lighthouse

    called: list[str] = []
    monkeypatch.setattr(lighthouse, "configure_telegram_webhook", lambda e: called.append(e))

    fixed, failed = rotation.repoint_owned(_cfg(**{"cloud.mode": "direct"}))

    assert (fixed, failed, called) == ([], [], []), "no edge tenant → nothing is tenant-bound"


# ── what we do NOT own: surfaced, never silently left broken ─────────────────


def test_website_ingest_urls_are_surfaced_with_the_new_tenant(monkeypatch):
    """The ingest URL is pasted into the USER'S WEBSITE. We cannot fix it — but the
    operator must be handed the new one, or their site drops events into a dead queue."""
    from navig.notify import signals

    monkeypatch.setattr(signals, "list_sources", lambda: [{"name": "shop"}, {"name": "blog"}])

    manual = dict(rotation.manual_repoint_urls(_cfg(**{"adapters.sms": None})))

    tenant = api_key_hash(KEY)
    assert manual["website ingest 'shop'"] == f"{EDGE}/ingest/{tenant}/shop"
    assert manual["website ingest 'blog'"] == f"{EDGE}/ingest/{tenant}/blog"


def test_sms_console_url_surfaced_only_when_sms_is_enabled(monkeypatch):
    from navig.notify import signals

    monkeypatch.setattr(signals, "list_sources", lambda: [])

    on = rotation.manual_repoint_urls(_cfg(**{"adapters.sms": {"enabled": True}}))
    assert on == [("SMS provider console", f"{EDGE}/sms/{api_key_hash(KEY)}")]

    off = rotation.manual_repoint_urls(_cfg(**{"adapters.sms": {"enabled": False}}))
    assert off == [], "a disabled adapter must not nag the operator"


@pytest.mark.parametrize("raw", ["false", "0", "off", "no"])
def test_string_false_does_not_enable_sms(monkeypatch, raw):
    """`navig config set x false` stores the STRING "false" — truthy in Python. A
    documented sharp edge that has bitten this codebase before."""
    from navig.notify import signals

    monkeypatch.setattr(signals, "list_sources", lambda: [])

    assert rotation.manual_repoint_urls(_cfg(**{"adapters.sms": {"enabled": raw}})) == []


def test_nothing_to_report_outside_lighthouse():
    assert rotation.manual_repoint_urls(_cfg(**{"cloud.mode": "direct"})) == []


# ── the two builders of the Mini App link must never drift apart ─────────────


def test_menu_button_url_matches_miniapp_format(monkeypatch):
    """`rotation.menu_button_url` builds the link from an explicit cfg (so it is
    testable and free of hidden global reads); `miniapp._connect_url` builds it from
    global config. They must produce the SAME url — otherwise a rotation would
    "repair" the button to a URL the deck doesn't actually understand."""
    import navig.config
    from navig.commands import miniapp

    deck = "https://deck.example.workers.dev"
    key = "navig_key/with+chars"

    # _connect_url imports get_config_manager INSIDE the function, so it must be
    # patched at its source module — patching miniapp.get_config_manager is a no-op.
    monkeypatch.setattr(
        navig.config,
        "get_config_manager",
        lambda: type("CM", (), {"global_config": {"deck": {"api_key": key}}})(),
    )

    mine = rotation.menu_button_url(_cfg(**{"deck.public_url": deck, "deck.api_key": key}))
    theirs = miniapp._connect_url(deck)

    assert mine == theirs, "the Mini App link format drifted between the two builders"


def test_menu_button_url_is_empty_without_a_deck(monkeypatch):
    """A user who never deployed the deck has no button — repointing must be a no-op,
    not a crash or a bogus URL."""
    assert rotation.menu_button_url(_cfg(**{"deck.public_url": "", "cloud.public_url": ""})) == ""
    assert rotation.repoint_menu_button(_cfg(**{"deck.public_url": ""})) is False
