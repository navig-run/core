"""notify_operator — process-agnostic operator DM (used by mailroom plugins + cron)."""

from __future__ import annotations

import pytest

from navig.messaging import notify_operator as mod

pytestmark = pytest.mark.unit


class _FakeCM:
    def __init__(self, cfg):
        self.global_config = cfg


def test_escape_html_keeps_quotes_escapes_angles():
    assert mod.escape_html('a <b> & "c"') == 'a &lt;b&gt; &amp; "c"'


def test_truncate_for_telegram():
    assert mod.truncate_for_telegram("x" * 10, limit=10) == "x" * 10
    out = mod.truncate_for_telegram("x" * 5000)
    assert len(out) == mod.TELEGRAM_TEXT_LIMIT
    assert out.endswith("…")


def test_resolve_chat_prefers_allowed_users(monkeypatch):
    import navig.config as cfg_mod

    monkeypatch.setattr(
        cfg_mod, "get_config_manager", lambda: _FakeCM({"telegram": {"allowed_users": [42, 7]}})
    )
    assert mod.resolve_operator_chat_id() == "42"


def test_resolve_chat_falls_back_to_uid(monkeypatch):
    import navig.config as cfg_mod
    import navig.messaging.secrets as secrets_mod

    monkeypatch.setattr(cfg_mod, "get_config_manager", lambda: _FakeCM({"telegram": {}}))
    monkeypatch.setattr(secrets_mod, "resolve_telegram_uid", lambda raw_config=None: "99")
    assert mod.resolve_operator_chat_id() == "99"


def test_notify_returns_false_without_chat(monkeypatch):
    monkeypatch.setattr(mod, "resolve_operator_chat_id", lambda: None)
    assert mod.notify_operator("hello") is False


def test_notify_sends_and_reports_true(monkeypatch):
    import navig.commands.telegram as tg

    sent = {}

    def fake_send(*, target, message, parse_mode="Markdown", resolve_only=False, host=""):
        sent.update(target=target, message=message, parse_mode=parse_mode)
        return int(target)

    monkeypatch.setattr(tg, "telegram_send", fake_send)
    assert mod.notify_operator("<b>hi</b>", chat_id="123") is True
    assert sent == {"target": "123", "message": "<b>hi</b>", "parse_mode": "HTML"}


def test_notify_swallows_send_errors(monkeypatch):
    import navig.commands.telegram as tg

    def boom(**kw):
        raise RuntimeError("no network")

    monkeypatch.setattr(tg, "telegram_send", boom)
    assert mod.notify_operator("x", chat_id="1") is False


def test_notify_ignores_empty_text(monkeypatch):
    assert mod.notify_operator("   ", chat_id="1") is False
