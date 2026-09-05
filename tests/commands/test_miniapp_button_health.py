"""`miniapp_button_health` — is the Mini App button serving the deck we deployed?

Telegram caches a Mini App by URL and ignores ``Cache-Control``, so the ``v=``
cache-bust on the button URL is the ONLY thing that makes a client re-fetch after a
deploy. A button that never changed pins every client to an old bundle while the
deploy, the edge and the uplink all report healthy — which is exactly how this install
ended up on a months-old deck that could not reach the brain at all.

Every branch here is a state that produced, or would hide, that failure.
"""

from __future__ import annotations

import pytest

from navig.commands import miniapp as m


def _config(
    monkeypatch,
    *,
    public_url: str = "https://deck.example.dev",
    sig: str = "abc123",
    api_key: str = "K",
):
    """Point `miniapp_button_health` at a synthetic deck config.

    `api_key` defaults to "K" to match the `key=K` the button URLs below carry: a
    real install always has one, and the health check compares the button's key
    against it (a button left on a RETIRED key resolves to an edge tenant with no
    brain, while its origin and v= both still look perfect).
    """
    values = {"deck.public_url": public_url, "deck.bundle_sig": sig, "deck.api_key": api_key}

    class _CM:
        def get(self, key, default=None):
            return values.get(key, default)

    monkeypatch.setattr("navig.config.ConfigManager", lambda *a, **k: _CM())


def _button(monkeypatch, url: str | None, *, ok: bool = True, description: str = "", btype: str = "web_app"):
    """Stub the live getChatMenuButton response."""
    monkeypatch.setattr(m, "_bot_token", lambda: "TOKEN")

    def _call(token, method, body=None, *, timeout=10.0):
        assert method == "getChatMenuButton"
        if not ok:
            return {"ok": False, "description": description}
        result: dict = {"type": btype}
        if btype == "web_app":
            result["web_app"] = {"url": url}
        return {"ok": True, "result": result}

    monkeypatch.setattr(m, "_tg_call", _call)


def test_current_bundle_is_healthy(monkeypatch):
    _config(monkeypatch)
    _button(monkeypatch, "https://deck.example.dev/connect?key=K&v=abc123")
    ok, detail, warn = m.miniapp_button_health()
    assert ok is True
    assert warn is False
    assert "abc123" in detail


def test_missing_cache_bust_is_a_hard_error(monkeypatch):
    """The live defect: a button URL with no `v=` never changes, so Telegram never
    re-fetches. This is a ✗, not a ⚠ — it silently breaks every client."""
    _config(monkeypatch)
    _button(monkeypatch, "https://deck.example.dev/connect?key=K")
    ok, detail, warn = m.miniapp_button_health()
    assert ok is False
    assert warn is False  # a real defect, not a could-not-verify
    assert "cache-bust" in detail
    assert "navig miniapp register" in detail


def test_stale_cache_bust_is_a_hard_error(monkeypatch):
    _config(monkeypatch, sig="new999")
    _button(monkeypatch, "https://deck.example.dev/connect?key=K&v=old111")
    ok, detail, warn = m.miniapp_button_health()
    assert ok is False
    assert warn is False
    assert "old111" in detail and "new999" in detail


def test_origin_mismatch_is_a_hard_error(monkeypatch):
    """A button pointing at a different deck entirely — e.g. a retired deployment."""
    _config(monkeypatch, public_url="https://navig-deck.studio.workers.dev")
    _button(monkeypatch, "https://deck.navig.run/connect?key=K&v=abc123")
    ok, detail, warn = m.miniapp_button_health()
    assert ok is False
    assert warn is False
    assert "deck.navig.run" in detail
    assert "navig-deck.studio.workers.dev" in detail


@pytest.mark.parametrize(
    "setup",
    [
        pytest.param("no_token", id="no-bot-token"),
        pytest.param("tg_failed", id="telegram-call-failed"),
        pytest.param("no_button", id="no-button-set"),
        pytest.param("no_sig", id="no-bundle-sig-recorded"),
    ],
)
def test_could_not_verify_is_a_warning_never_a_pass(monkeypatch, setup):
    """`ok=True` must mean "verified healthy", never "I could not look" — a green tick
    over an unknown actively tells the operator not to investigate."""
    _config(monkeypatch, sig="abc123" if setup != "no_sig" else "")
    if setup == "no_token":
        monkeypatch.setattr(m, "_bot_token", lambda: "")
    elif setup == "tg_failed":
        _button(monkeypatch, None, ok=False, description="Unauthorized")
    elif setup == "no_button":
        _button(monkeypatch, None, btype="default")
    else:
        _button(monkeypatch, "https://deck.example.dev/connect?key=K")

    ok, _detail, warn = m.miniapp_button_health()
    assert ok is False
    assert warn is True


def test_no_deck_deployed_yields_no_row(monkeypatch):
    """Nothing deployed → the question does not apply; don't invent a row for it."""
    _config(monkeypatch, public_url="")
    monkeypatch.setattr(m, "_bot_token", lambda: "TOKEN")
    assert m.miniapp_button_health() is None


def test_never_raises_when_config_is_unreadable(monkeypatch):
    """A health check that crashes its caller is worse than one that reports unknown."""
    def _boom(*a, **k):
        raise RuntimeError("config unreadable")

    monkeypatch.setattr("navig.config.ConfigManager", _boom)
    ok, detail, warn = m.miniapp_button_health()
    assert ok is False and warn is True
    assert "COULD NOT VERIFY" in detail


def test_a_button_on_a_retired_key_is_a_hard_error(monkeypatch):
    """Rotation moves the tenant. `deck.api_key`'s sha256 IS the lighthouse Durable
    Object, so a button still carrying the OLD key sends every Mini App session to a
    tenant with no uplink — while the origin and the v= cache-bust both still match,
    so every other check here passes. That combination is why this needs its own row."""
    _config(monkeypatch, api_key="NEW_KEY")
    _button(monkeypatch, "https://deck.example.dev/connect?key=OLD_KEY&v=abc123")
    ok, detail, warn = m.miniapp_button_health()
    assert ok is False
    assert warn is False, "a wrong tenant is a definite failure, not could-not-verify"
    assert "register" in detail
    # The check compares; it must never echo either key.
    assert "OLD_KEY" not in detail and "NEW_KEY" not in detail, detail


def test_a_button_with_no_key_is_a_hard_error(monkeypatch):
    _config(monkeypatch)
    _button(monkeypatch, "https://deck.example.dev/connect?v=abc123")
    ok, detail, warn = m.miniapp_button_health()
    assert ok is False
    assert warn is False
    assert "key" in detail.lower()


def test_no_api_key_in_config_is_could_not_verify_not_a_pass(monkeypatch):
    """Same rule as everywhere else here: a check that could not run is a ⚠, never a ✓."""
    _config(monkeypatch, api_key="")
    _button(monkeypatch, "https://deck.example.dev/connect?key=K&v=abc123")
    ok, detail, warn = m.miniapp_button_health()
    assert ok is False
    assert warn is True, "unknown must not render as a green tick"
