"""Only the Brain's owner may point the bot at a webhook.

The deploy path used to re-run ``setWebhook`` on every machine — how a Windows
install reclaimed a bot the outpost was polling. These tests pin the guard.
"""

from __future__ import annotations

import pytest

from navig.brain import authority, guard
from navig.commands import lighthouse


def test_single_machine_install_without_authority_keeps_old_behaviour(monkeypatch):
    monkeypatch.setattr(authority, "configured", lambda: False)
    assert lighthouse.webhook_may_be_set() == (True, "")


def test_owner_may_set_the_webhook(monkeypatch):
    monkeypatch.setattr(authority, "configured", lambda: True)
    monkeypatch.setattr(guard, "is_owner", lambda **kwargs: True)
    assert lighthouse.webhook_may_be_set() == (True, "")


def test_non_owner_is_refused(monkeypatch):
    monkeypatch.setattr(authority, "configured", lambda: True)
    monkeypatch.setattr(guard, "is_owner", lambda **kwargs: False)
    allowed, why = lighthouse.webhook_may_be_set()
    assert allowed is False
    assert "not the Brain owner" in why


def test_unconfirmable_ownership_is_refused(monkeypatch):
    monkeypatch.setattr(authority, "configured", lambda: True)

    def boom(**kwargs):
        raise RuntimeError("authority unreachable")

    monkeypatch.setattr(guard, "is_owner", boom)
    allowed, why = lighthouse.webhook_may_be_set()
    assert allowed is False
    assert "could not be confirmed" in why


def test_configure_refuses_before_any_telegram_call(monkeypatch):
    import navig.messaging.secrets as secrets

    monkeypatch.setattr(secrets, "resolve_telegram_bot_token", lambda: "123:abc")
    monkeypatch.setattr(lighthouse, "webhook_may_be_set", lambda: (False, "not the owner"))
    posts: list[object] = []
    monkeypatch.setattr("requests.post", lambda *a, **k: posts.append(a) or object())

    with pytest.raises(RuntimeError, match="not set"):
        lighthouse.configure_telegram_webhook("https://navig-lighthouse.example")
    assert posts == []
