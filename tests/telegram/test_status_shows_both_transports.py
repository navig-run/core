"""`navig telegram status` must report the user account, not just the bot.

Two commands claimed the name "status" on the same Typer app. Typer keeps the
last registration, so the bot one shadowed the MTProto one for ~6 months and the
user-account state was unreachable — the exact information missing when the
vault engine disappeared and every credential read as unset.
"""
from __future__ import annotations

import navig.commands.telegram as tgcmd
from navig.telegram import status as tgstatus


def test_only_one_command_claims_the_status_name():
    """A second registration would silently shadow this one again."""
    names = [c.name or c.callback.__name__
             for c in tgcmd.telegram_app.registered_commands]
    assert names.count("status") == 1


def test_collect_reports_a_missing_vault_engine_distinctly(monkeypatch):
    from navig.telegram import config as tgcfg
    monkeypatch.setattr(tgcfg, "vault_unavailable_reason",
                        lambda: "requires the navig-vault engine")
    monkeypatch.setattr(tgcfg, "have_api_credentials", lambda: False)
    monkeypatch.setattr(tgcfg, "is_logged_in", lambda: False)
    st = tgstatus.collect()
    assert "navig-vault" in st["vault_error"]


def test_collect_never_raises_even_when_the_vault_explodes(monkeypatch):
    """Status is what you run when things are broken; it must not break too."""
    from navig.telegram import config as tgcfg

    def boom():
        raise RuntimeError("vault on fire")
    monkeypatch.setattr(tgcfg, "vault_unavailable_reason", boom)
    monkeypatch.setattr(tgcfg, "have_api_credentials", boom)
    st = tgstatus.collect()
    assert st["credentials"] is False


def test_a_healthy_account_reports_credentials_and_login(monkeypatch):
    from navig.telegram import config as tgcfg
    monkeypatch.setattr(tgcfg, "vault_unavailable_reason", lambda: None)
    monkeypatch.setattr(tgcfg, "have_api_credentials", lambda: True)
    monkeypatch.setattr(tgcfg, "is_logged_in", lambda: True)
    st = tgstatus.collect()
    assert st["credentials"] and st["logged_in"] and st["vault_error"] is None


def test_probe_account_returns_none_instead_of_raising(monkeypatch):
    from navig.telegram import user_client

    async def boom():
        raise RuntimeError("unauthorized")
    monkeypatch.setattr(user_client, "whoami", boom)
    assert tgstatus.probe_account() is None


def test_the_renderer_names_the_engine_when_the_vault_is_missing(monkeypatch, capsys):
    monkeypatch.setattr(tgstatus, "collect", lambda: {
        "telethon": True, "vault_error": "requires the navig-vault engine",
        "credentials": False, "logged_in": False, "me": None, "authorized": None,
    })
    tgcmd._render_user_account_status()
    out = capsys.readouterr().out
    assert "navig-vault" in out
    assert "probably intact" in out
    assert "telegram setup" in out          # only as the thing NOT to do


def test_the_renderer_says_setup_when_credentials_are_genuinely_absent(monkeypatch, capsys):
    monkeypatch.setattr(tgstatus, "collect", lambda: {
        "telethon": True, "vault_error": None, "credentials": False,
        "logged_in": False, "me": None, "authorized": None,
    })
    tgcmd._render_user_account_status()
    out = capsys.readouterr().out
    assert "navig telegram setup" in out
    assert "navig-vault" not in out


def _stub_bot_side(monkeypatch, token):
    import navig.config as navig_config
    from navig.messaging import secrets

    class _CM:
        def _load_global_config(self):
            return {"telegram": {}}

    monkeypatch.setattr(navig_config, "get_config_manager", lambda *a, **k: _CM())
    monkeypatch.setattr(secrets, "resolve_telegram_bot_token", lambda cfg=None: token)
    monkeypatch.setattr(secrets, "resolve_telegram_uid", lambda cfg=None: None)


_LOGGED_IN = {
    "telethon": True, "vault_error": None, "credentials": True,
    "logged_in": True, "me": None, "authorized": None,
}


def test_status_renders_the_user_account_even_without_a_bot_token(monkeypatch):
    """A missing bot token used to return before the MTProto section rendered."""
    from typer.testing import CliRunner

    _stub_bot_side(monkeypatch, "")
    monkeypatch.setattr(tgstatus, "collect", lambda: dict(_LOGGED_IN))
    monkeypatch.setattr(tgstatus, "probe_account",
                        lambda: {"id": 42, "username": "someone"})
    result = CliRunner().invoke(tgcmd.telegram_app, ["status"])
    assert "Bot token missing" in result.output
    assert "User Account (MTProto)" in result.output
    assert "someone" in result.output
    assert result.exit_code == 0


def test_an_unauthorized_session_makes_status_exit_nonzero(monkeypatch):
    """A broken session must not read as healthy to a script."""
    from typer.testing import CliRunner

    _stub_bot_side(monkeypatch, "123456:ABCDEFGHIJKLMNOP")
    monkeypatch.setattr(tgstatus, "collect", lambda: dict(_LOGGED_IN))
    monkeypatch.setattr(tgstatus, "probe_account", lambda: None)
    result = CliRunner().invoke(tgcmd.telegram_app, ["status"])
    assert "NOT authorized" in result.output
    assert result.exit_code == 1


def test_renderer_reports_health(monkeypatch):
    monkeypatch.setattr(tgstatus, "collect", lambda: dict(_LOGGED_IN))
    monkeypatch.setattr(tgstatus, "probe_account", lambda: None)
    assert tgcmd._render_user_account_status() is False
    monkeypatch.setattr(tgstatus, "probe_account", lambda: {"id": 1, "name": "x"})
    assert tgcmd._render_user_account_status() is True
