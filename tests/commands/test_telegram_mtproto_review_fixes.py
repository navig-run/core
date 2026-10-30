"""Review regressions in the Telegram MTProto CLI (navig-telegram plugin).

* ``business deleted`` only calls rows "not announced" when their reason is quiet now.
* ``contacts runs`` shows what a prune run did, not ``0 written, 0 undone``.
* ``login`` names a missing vault engine instead of sending you to re-run setup.
"""

from __future__ import annotations

import typer
from typer.testing import CliRunner


def _app() -> typer.Typer:
    import navig.commands._telegram_mtproto as mod

    app = typer.Typer()
    mod.register(app)
    return app


def _flat(result) -> str:
    return " ".join(result.output.split())


def _deleted_rows():
    return [
        {"date": None, "chat_id": 1, "room_title": "a", "sender_name": "x",
         "text": "hi", "deleted_reason": "expired"},
        {"date": None, "chat_id": 1, "room_title": "a", "sender_name": "x",
         "text": "yo", "deleted_reason": None},
    ]


def _patch_deleted(monkeypatch, quiet: set[str]) -> None:
    from navig.store import telegram_catalog
    from navig.telegram import deletions as dl

    monkeypatch.setattr(telegram_catalog.TelegramCatalogStore, "__init__",
                        lambda self, *a, **k: None)
    monkeypatch.setattr(telegram_catalog.TelegramCatalogStore, "list_deleted",
                        lambda self, **k: _deleted_rows())
    monkeypatch.setattr(dl, "quiet_reasons", lambda: set(quiet))


def test_an_announced_expiry_is_not_called_unannounced(monkeypatch):
    _patch_deleted(monkeypatch, quiet=set())   # `deletions quiet expired off`
    result = CliRunner().invoke(_app(), ["business", "deleted"], obj={})
    assert result.exit_code == 0, result.output
    assert "not announced" not in _flat(result)


def test_a_quiet_expiry_is_still_called_unannounced(monkeypatch):
    _patch_deleted(monkeypatch, quiet={"expired", "wiped"})
    result = CliRunner().invoke(_app(), ["business", "deleted"], obj={})
    assert result.exit_code == 0, result.output
    assert "1 auto-deleted/cleared (not announced)" in _flat(result)


def test_contacts_runs_reports_pruned_count(monkeypatch):
    from navig.telegram import contacts as tgc

    monkeypatch.setattr(tgc, "list_runs", lambda: [
        {"run_id": "r1", "chat": "g", "written": 0, "pruned": 3, "undone": 0}])
    result = CliRunner().invoke(_app(), ["contacts", "runs"], obj={})
    assert result.exit_code == 0, result.output
    assert "3 pruned" in _flat(result)


def test_login_names_a_missing_vault_engine(monkeypatch):
    from navig.telegram import config as tgcfg

    monkeypatch.setattr(tgcfg, "have_api_credentials", lambda: False)
    monkeypatch.setattr(tgcfg, "vault_unavailable_reason",
                        lambda: "No module named 'navig_vault'")
    result = CliRunner().invoke(_app(), ["login", "+33123456789"], obj={})
    assert result.exit_code == 1
    flat = _flat(result)
    assert "navig_vault" in flat
    assert "No api_id/api_hash yet" not in flat


def test_login_without_credentials_still_points_at_setup(monkeypatch):
    from navig.telegram import config as tgcfg

    monkeypatch.setattr(tgcfg, "have_api_credentials", lambda: False)
    monkeypatch.setattr(tgcfg, "vault_unavailable_reason", lambda: None)
    result = CliRunner().invoke(_app(), ["login", "+33123456789"], obj={})
    assert result.exit_code == 1
    assert "navig telegram setup" in _flat(result)
