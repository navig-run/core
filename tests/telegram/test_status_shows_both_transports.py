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
