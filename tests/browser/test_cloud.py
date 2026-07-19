"""Stage 4 — the cloud anti-detect browser bridge (endpoint build + token redaction)."""

from __future__ import annotations

from navig.browser import cloud as cl
from navig.browser.cloud import CloudBridge


def test_token_appended_as_query():
    b = CloudBridge("wss://host/cdp", token="secret")
    assert b._cdp_endpoint == "wss://host/cdp?token=secret"


def test_token_not_duplicated_when_present():
    b = CloudBridge("wss://host/cdp?token=orig", token="secret")
    # existing token param is kept (setdefault), not overwritten/duplicated
    assert b._cdp_endpoint == "wss://host/cdp?token=orig"


def test_no_token_leaves_endpoint_untouched():
    b = CloudBridge("wss://host/cdp")
    assert b._cdp_endpoint == "wss://host/cdp"


def test_display_endpoint_redacts_token():
    b = CloudBridge("wss://host/cdp", token="supersecret")
    disp = b._display_endpoint()
    assert "supersecret" not in disp
    assert "token=%2A%2A%2A" in disp or "token=***" in disp


def test_custom_token_param():
    b = CloudBridge("wss://host/cdp", token="k", token_param="apiKey")
    assert "apiKey=k" in b._cdp_endpoint
    assert "k" not in b._display_endpoint().split("apiKey=")[1]


def test_connection_hint_mentions_cloud(monkeypatch):
    b = CloudBridge("wss://host/cdp", token="k")
    hint = b._connection_hint()
    assert "cloud" in hint.lower() and "token" in hint.lower()


# ── from_config / enabled ─────────────────────────────────────────────────────

def test_from_config_none_when_unset(monkeypatch):
    monkeypatch.setattr(cl, "cloud_config", lambda: {})
    assert CloudBridge.from_config() is None
    assert cl.cloud_enabled() is False


def test_from_config_builds_and_prefers_env_token(monkeypatch):
    monkeypatch.setattr(cl, "cloud_config", lambda: {"endpoint": "wss://h/cdp", "token": "cfgtok"})
    monkeypatch.setenv(cl._TOKEN_ENV, "envtok")
    b = CloudBridge.from_config()
    assert b is not None
    assert "envtok" in b._cdp_endpoint  # env wins over config plaintext
    assert "envtok" not in b._display_endpoint()


def test_cloud_enabled_true_when_endpoint_set(monkeypatch):
    monkeypatch.setattr(cl, "cloud_config", lambda: {"endpoint": "wss://h/cdp"})
    assert cl.cloud_enabled() is True
