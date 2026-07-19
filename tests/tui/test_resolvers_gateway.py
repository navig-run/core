"""Regression: the TUI Gateway badge reads the real configured-channel source.

`resolve_gateway` read a `channels` map from navig.json — a field nothing
populates — so the badge ALWAYS said "no channels configured" even with a bot set
up. It now uses `configured_channels()` (navig.messaging.channel_config), the
shared, config-only source of truth `navig gateway status` mirrors.
"""

from __future__ import annotations

import navig.messaging.channel_config as cc_mod
import navig.messaging.secrets as secrets_mod
import navig.tui.resolvers as res_mod
from navig.messaging.channel_config import configured_channels
from navig.tui.resolvers import resolve_gateway

# ── configured_channels helper ──────────────────────────────────────────────


def _mock_tg_from_config(monkeypatch):
    """resolve_telegram_bot_token reads config.telegram.bot_token (no vault/env)."""
    monkeypatch.setattr(
        secrets_mod,
        "resolve_telegram_bot_token",
        lambda cfg=None: ((cfg or {}).get("telegram") or {}).get("bot_token", ""),
    )


def test_configured_channels_empty(monkeypatch):
    _mock_tg_from_config(monkeypatch)
    assert configured_channels({}) == []


def test_configured_channels_telegram(monkeypatch):
    _mock_tg_from_config(monkeypatch)
    assert configured_channels({"telegram": {"bot_token": "123:ABC"}}) == ["Telegram"]


def test_configured_channels_each_kind(monkeypatch):
    _mock_tg_from_config(monkeypatch)
    assert configured_channels({"discord": {"bot_token": "x"}}) == ["Discord"]
    assert configured_channels({"discord": {"token": "x"}}) == ["Discord"]
    assert configured_channels({"comms": {"matrix": {"access_token": "x"}}}) == ["Matrix"]
    assert configured_channels({"matrix": {"access_token": "x"}}) == ["Matrix"]
    assert configured_channels({"whatsapp": {"enabled": True}}) == ["WhatsApp"]
    assert configured_channels({"bridges": {"whatsapp": {"enabled": True}}}) == ["WhatsApp"]
    assert configured_channels({"email": {"smtp_host": "smtp.x"}}) == ["Email"]
    assert configured_channels({"smtp": {"host": "smtp.x"}}) == ["Email"]


def test_configured_channels_order(monkeypatch):
    _mock_tg_from_config(monkeypatch)
    cfg = {
        "email": {"smtp_host": "s"},
        "telegram": {"bot_token": "t"},
        "discord": {"bot_token": "d"},
        "matrix": {"access_token": "m"},
        "whatsapp": {"enabled": True},
    }
    assert configured_channels(cfg) == ["Telegram", "Matrix", "Discord", "WhatsApp", "Email"]


def test_configured_channels_ignores_blank(monkeypatch):
    _mock_tg_from_config(monkeypatch)
    # Present-but-empty sections must not count as configured.
    assert configured_channels({"discord": {"bot_token": ""}, "email": {"smtp_host": ""}}) == []


# ── resolve_gateway ─────────────────────────────────────────────────────────


def test_gateway_missing_when_no_channels(monkeypatch):
    monkeypatch.setattr(cc_mod, "configured_channels", lambda *a, **k: [])
    badge = resolve_gateway()
    assert badge.status == "missing"
    assert "no channels" in badge.detail


def test_gateway_ok_with_channels(monkeypatch):
    monkeypatch.setattr(cc_mod, "configured_channels", lambda *a, **k: ["Telegram"])
    monkeypatch.setattr(res_mod, "_count_recent_errors", lambda *a, **k: 0)
    badge = resolve_gateway()
    assert badge.status == "ok"
    assert "Telegram" in badge.detail


def test_gateway_warns_on_recent_errors(monkeypatch):
    monkeypatch.setattr(cc_mod, "configured_channels", lambda *a, **k: ["Telegram", "Discord", "Matrix"])
    monkeypatch.setattr(res_mod, "_count_recent_errors", lambda *a, **k: 2)
    badge = resolve_gateway()
    assert badge.status == "warn"
    assert "2 errors in last hour" in badge.detail
    assert badge.detail.startswith("Telegram, Discord…")  # active[:2] + ellipsis
