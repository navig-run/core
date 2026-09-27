"""Every web_app button the bot sends must open the deck WITH its access key.

The Mini App reaches the brain through the Lighthouse edge, which routes by
``sha256(deck.api_key)`` taken from the Bearer the deck sends. The deck only learns
that key from a ``/connect?key=…`` entry link. A bare deck URL therefore sends no
Bearer, and the edge answers a bare 401 before the brain sees anything — the deck
shows "Session expired" on every launch and no reload can fix it.

``_get_deck_url`` returned the raw legacy ``telegram.deck_url`` (keyless), and
``_register_commands`` pushed it as the bot's DEFAULT button on every boot, racing
``navig miniapp deploy``, which owns that button. These run on a REAL
``TelegramChannel``: its "mixin" methods are delegated with ``self`` = the channel,
which does not inherit the mixin at runtime, so a helper the mixin calls must also be
delegated on the channel — a missing one is an AttributeError here, not a pass.
(``test_telegram_channel_calls_resolve`` does not see this path; verified by removing
the delegator, which left it green.)
"""

from __future__ import annotations

import pytest

from navig.gateway.channels.telegram import TelegramChannel

KEYED = "https://deck.example.dev/connect?key=K&v=sig1"


def _config(monkeypatch, values: dict) -> None:
    class _CM:
        def get(self, key, default=None):
            return values.get(key, default)

    monkeypatch.setattr("navig.config.ConfigManager", lambda *a, **k: _CM())
    monkeypatch.setattr(
        "navig.commands.miniapp._connect_url",
        lambda base, version="": f"{base}/connect?key=K&v={version}",
    )


def _channel() -> TelegramChannel:
    ch = TelegramChannel.__new__(TelegramChannel)  # no network, no config reads
    return ch


def test_deck_url_is_the_keyed_entry_link_for_a_miniapp_deck(monkeypatch):
    _config(monkeypatch, {"deck.public_url": "https://deck.example.dev", "deck.bundle_sig": "sig1"})
    assert _channel()._get_deck_url() == KEYED


def test_no_miniapp_deck_falls_back_to_legacy_deck_url(monkeypatch, tmp_path):
    _config(monkeypatch, {})
    cfg = tmp_path / "config.yaml"
    cfg.write_text("telegram:\n  deck_url: https://legacy.example/\n", encoding="utf-8")
    monkeypatch.setattr(
        "navig.gateway.channels.telegram_commands.global_config_path", lambda: cfg
    )
    monkeypatch.chdir(tmp_path)  # no project-local .navig/config.yaml
    assert _channel()._get_deck_url() == "https://legacy.example/"


@pytest.mark.asyncio
async def test_register_commands_does_not_clobber_the_miniapp_button(monkeypatch):
    """`navig miniapp deploy` owns the menu button (key + v= cache-bust). A second
    writer on every boot is a race that the deploy can lose."""
    _config(monkeypatch, {"deck.public_url": "https://deck.example.dev", "deck.bundle_sig": "sig1"})
    ch = _channel()
    calls: list[str] = []

    async def _api_call(method, payload=None):
        calls.append(method)
        return {"ok": True}

    ch._api_call = _api_call
    await ch._register_commands()
    assert "setMyCommands" in calls  # the command list is still registered
    assert "setChatMenuButton" not in calls


@pytest.mark.asyncio
async def test_deck_command_sends_the_keyed_link(monkeypatch):
    _config(monkeypatch, {"deck.public_url": "https://deck.example.dev", "deck.bundle_sig": "sig1"})
    ch = _channel()
    sent: list[dict] = []

    async def _send(chat_id, text, parse_mode=None, keyboard=None, **kw):
        sent.append({"keyboard": keyboard})

    ch.send_message = _send
    await ch._handle_deck(111)
    url = sent[0]["keyboard"][0][0]["web_app"]["url"]
    assert url == KEYED
