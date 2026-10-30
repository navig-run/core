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


def test_outside_lighthouse_only_the_menu_button_is_repointed(monkeypatch):
    """No edge tenant → no webhook to move; but the Mini App button embeds the raw key
    in direct/cloudflared/Tailscale installs too, so it must still be re-pointed.

    Review: https://github.com/navig-run/labs/pull/145#discussion_r3567469433
    """
    from navig.commands import lighthouse

    called: list[str] = []
    monkeypatch.setattr(lighthouse, "configure_telegram_webhook", lambda e: called.append(e))
    monkeypatch.setattr(rotation, "repoint_menu_button", lambda cfg=None: True)

    fixed, failed = rotation.repoint_owned(_cfg(**{"cloud.mode": "direct"}))

    assert called == [], "no edge tenant → the webhook is not tenant-bound"
    assert (fixed, failed) == (["Mini App menu button"], [])


def test_direct_mode_rotation_calls_set_chat_menu_button(monkeypatch):
    calls = _chat_buttons(monkeypatch, {})
    cfg = _cfg(**{"cloud.mode": "direct", "deck.public_url": "https://d.example"})

    fixed, failed = rotation.repoint_owned(cfg)

    assert "Mini App menu button" in fixed and failed == []
    sets = [b for m, b in calls if m == "setChatMenuButton"]
    assert sets and f"key={KEY}" in sets[0]["menu_button"]["web_app"]["url"]


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


# ── per-chat overrides: the button a user actually taps ──────────────────────
#
# A PER-CHAT menu button wins over the bot default for that one chat. The operator's
# own chat carried a stale keyless one (`deck.navig.run/`), so every launch sent no
# Bearer and the edge 401'd before the brain saw a request — "Session expired" on
# every launch — while every repair and check only ever touched the DEFAULT.


def _chat_buttons(monkeypatch, per_chat: dict[int, dict]):
    """Stub Telegram: getChatMenuButton answers per chat; record every set call."""
    from navig.commands import miniapp

    calls: list[tuple[str, dict]] = []

    def _call(token, method, body=None, *, timeout=10.0):
        body = body or {}
        calls.append((method, body))
        if method == "getChatMenuButton":
            return {"ok": True, "result": per_chat.get(body.get("chat_id"), {"type": "default"})}
        return {"ok": True, "result": True}

    monkeypatch.setattr(miniapp, "_tg_call", _call)
    monkeypatch.setattr(miniapp, "_bot_token", lambda: "TOKEN")
    return calls


def _webapp(url: str) -> dict:
    return {"type": "web_app", "text": "x", "web_app": {"url": url}}


def test_keyless_per_chat_override_is_found_and_reset(monkeypatch):
    calls = _chat_buttons(monkeypatch, {111: _webapp("https://deck.navig.run/")})
    cfg = _cfg(**{"telegram.allowed_users": [111]})

    stale = rotation.stale_chat_menu_buttons(cfg)
    assert stale == [(111, "https://deck.navig.run")]

    assert rotation.clear_stale_chat_menu_buttons(cfg) == [111]
    resets = [b for m, b in calls if m == "setChatMenuButton"]
    assert resets == [{"chat_id": 111, "menu_button": {"type": "default"}}]


def test_retired_key_per_chat_override_is_stale(monkeypatch):
    _chat_buttons(monkeypatch, {111: _webapp("https://d.example/connect?key=OLD_KEY&v=1")})
    assert rotation.stale_chat_menu_buttons(_cfg(**{"telegram.allowed_users": [111]})) == [
        (111, "https://d.example")
    ]


def test_healthy_overrides_and_non_webapp_buttons_are_left_alone(monkeypatch):
    calls = _chat_buttons(
        monkeypatch,
        {
            111: _webapp(f"https://d.example/connect?key={KEY}&v=1"),  # carries the live key
            222: {"type": "default"},                                  # inherits the default
            333: {"type": "commands"},                                 # deliberate non-app button
        },
    )
    cfg = _cfg(**{"telegram.allowed_users": "111, 222, 333"})  # config-set string spelling

    assert rotation.stale_chat_menu_buttons(cfg) == []
    assert rotation.clear_stale_chat_menu_buttons(cfg) == []
    assert not [m for m, _ in calls if m == "setChatMenuButton"]
    # all three chats were actually inspected — the "nothing stale" is not vacuous
    assert sorted(
        {b["chat_id"] for m, b in calls if m == "getChatMenuButton" and "chat_id" in b}
    ) == [111, 222, 333]


def test_keyed_override_off_the_current_default_is_stale(monkeypatch):
    """A keyed per-chat button on an old origin or bundle still shadows the new default."""
    current = f"https://d.example/connect?key={KEY}&v=NEW"
    _chat_buttons(
        monkeypatch,
        {
            None: _webapp(current),                                       # the bot default
            111: _webapp(f"https://old.example/connect?key={KEY}&v=NEW"),  # old origin
            222: _webapp(f"https://d.example/connect?key={KEY}&v=OLD"),    # stale bundle
            333: _webapp(current),                                        # matches: healthy
        },
    )
    cfg = _cfg(**{"telegram.allowed_users": [111, 222, 333]})
    assert rotation.stale_chat_menu_buttons(cfg) == [
        (111, "https://old.example"),
        (222, "https://d.example"),
    ]
    assert rotation.clear_stale_chat_menu_buttons(cfg) == [111, 222]


def test_groups_are_skipped_and_the_key_never_leaks_into_the_report(monkeypatch):
    calls = _chat_buttons(monkeypatch, {111: _webapp("https://d.example/connect?key=OLD_KEY")})
    stale = rotation.stale_chat_menu_buttons(_cfg(**{"telegram.allowed_users": [111, -100123]}))
    assert [c for c, _ in stale] == [111]
    assert all("OLD_KEY" not in origin and "key=" not in origin for _, origin in stale)
    assert all(b.get("chat_id") != -100123 for _, b in calls)


def test_repoint_menu_button_also_sweeps_per_chat_overrides(monkeypatch):
    """Re-pointing only the DEFAULT repairs nothing for a user whose chat overrides it."""
    calls = _chat_buttons(monkeypatch, {111: _webapp("https://deck.navig.run/")})
    cfg = _cfg(**{"deck.public_url": "https://d.example", "telegram.allowed_users": [111]})

    assert rotation.repoint_menu_button(cfg) is True
    sets = [b for m, b in calls if m == "setChatMenuButton"]
    assert "chat_id" not in sets[0]                     # the default, first
    assert {"chat_id": 111, "menu_button": {"type": "default"}} in sets


def test_a_rejected_menu_button_repoint_is_reported_as_failed(monkeypatch):
    """Telegram's `ok: false` must not collapse into the silent "not applicable" False.

    Review: https://github.com/navig-run/labs/pull/145#discussion_r3567469435
    """
    from navig.commands import lighthouse, miniapp

    monkeypatch.setattr(lighthouse, "configure_telegram_webhook", lambda edge: f"{edge}/tg/x")
    monkeypatch.setattr(miniapp, "_bot_token", lambda: "TOKEN")
    monkeypatch.setattr(
        miniapp, "_tg_call", lambda *a, **k: {"ok": False, "description": "x"}
    )

    fixed, failed = rotation.repoint_owned(_cfg(**{"deck.public_url": "https://d.example"}))

    assert "Mini App menu button (x)" in failed
    assert "Mini App menu button" not in fixed


# ── remote NAVIG OS: devices hold the OLD tenant as their relay address ───────


def test_paired_os_devices_are_surfaced_for_re_pairing(monkeypatch):
    from navig.notify import signals
    from navig.store import os_devices

    monkeypatch.setattr(signals, "list_sources", lambda: [])

    class _Store:
        def list_devices(self):
            return [{"id": "d1"}]

    monkeypatch.setattr(os_devices, "get_os_device_store", lambda: _Store())
    manual = dict(rotation.manual_repoint_urls(_cfg(**{"adapters.sms": None})))
    assert "paired NAVIG OS devices" in manual


def test_no_devices_means_nothing_to_say(monkeypatch):
    from navig.notify import signals
    from navig.store import os_devices

    monkeypatch.setattr(signals, "list_sources", lambda: [])

    class _Store:
        def list_devices(self):
            return []

    monkeypatch.setattr(os_devices, "get_os_device_store", lambda: _Store())
    assert "paired NAVIG OS devices" not in dict(rotation.manual_repoint_urls(_cfg(**{"adapters.sms": None})))


def test_retire_reports_a_missing_daemon_as_none_not_an_error(monkeypatch):
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead_port = s.getsockname()[1]
    monkeypatch.setattr("navig.gateway_client.gateway_base_url", lambda: f"http://127.0.0.1:{dead_port}")
    assert rotation.retire_os_relay(timeout=0.5) is None


def test_cloud_key_rotate_retires_the_relay_while_the_old_key_is_live(monkeypatch):
    """Only the OLD key's uplink reaches the OLD tenant, so retire must run first."""
    from typer.testing import CliRunner

    from navig.cloud import deck_key
    from navig.commands import cloud

    class _Cfg(FakeConfig):
        def set(self, key, value, scope="global"):
            self._v[key] = value

        def save(self, scope="global"):
            pass

    cfg = _Cfg(**{"deck.api_key": KEY})
    key_when_retired: list[str] = []
    monkeypatch.setattr(cloud, "_config", lambda: cfg)
    monkeypatch.setattr(rotation, "retire_os_relay", lambda: key_when_retired.append(cfg.get("deck.api_key")) or True)
    monkeypatch.setattr(deck_key, "backup", lambda key: True)
    monkeypatch.setattr(cloud, "_repoint_telegram_webhook", lambda c: None)

    result = CliRunner().invoke(cloud.app, ["key", "--rotate"], input="yes\n")
    assert result.exit_code == 0, result.output
    assert key_when_retired == [KEY], "retire must run while the old key is still the live one"
    assert cfg.get("deck.api_key") != KEY
