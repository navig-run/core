"""A missing vault engine must not masquerade as missing credentials.

navig-vault was absent from an environment once; every credential read returned
None, the CLI said "api_id/api_hash are not set. Run `navig telegram setup`",
and the advertised repair would have re-authenticated over credentials that were
present and intact the whole time.
"""
from __future__ import annotations

import pytest

from navig.telegram import config as tgcfg
from navig.telegram import user_client as uc


def test_reason_is_none_when_the_vault_opens(monkeypatch):
    monkeypatch.setattr(tgcfg, "_vault", lambda: object())
    assert tgcfg.vault_unavailable_reason() is None


def test_a_missing_engine_is_reported(monkeypatch):
    def boom():
        raise ImportError("requires the navig-vault engine")
    monkeypatch.setattr(tgcfg, "_vault", boom)
    assert "navig-vault" in (tgcfg.vault_unavailable_reason() or "")


def test_a_locked_vault_is_not_reported_as_missing(monkeypatch):
    """Locked is a different problem; per-label handling already covers it."""
    def boom():
        raise RuntimeError("vault is locked")
    monkeypatch.setattr(tgcfg, "_vault", boom)
    assert tgcfg.vault_unavailable_reason() is None


def test_build_client_blames_the_engine_not_the_user(monkeypatch):
    monkeypatch.setattr(uc, "require_telethon", lambda: None)
    monkeypatch.setattr(uc.config, "get_api_id", lambda: None)
    monkeypatch.setattr(uc.config, "get_api_hash", lambda: None)
    monkeypatch.setattr(uc.config, "vault_unavailable_reason",
                        lambda: "requires the navig-vault engine")
    with pytest.raises(uc.TelegramNotConfigured) as e:
        uc.build_client()
    msg = str(e.value)
    assert "navig-vault" in msg
    assert "probably intact" in msg
    assert "telegram setup" not in msg.lower().split("rather than")[0]


def test_genuinely_absent_credentials_still_say_run_setup(monkeypatch):
    monkeypatch.setattr(uc, "require_telethon", lambda: None)
    monkeypatch.setattr(uc.config, "get_api_id", lambda: None)
    monkeypatch.setattr(uc.config, "get_api_hash", lambda: None)
    monkeypatch.setattr(uc.config, "vault_unavailable_reason", lambda: None)
    with pytest.raises(uc.TelegramNotConfigured, match="navig telegram setup"):
        uc.build_client()
