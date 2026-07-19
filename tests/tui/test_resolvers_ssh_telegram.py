"""Regression: the TUI SSH + Telegram badges read the real config source.

Both read the WRONG source, so they always showed "missing" even when configured —
the same source-mismatch class as the scheduler/task-queue badges (#352):

- `resolve_ssh` read a `hosts:` key from `config.yaml`, but SSH hosts are per-host
  YAML files under `config_dir()/hosts` (+ legacy `apps/`). `Config.list_hosts()` is
  the canonical lister `navig host list` uses.
- `resolve_telegram` read a flat `TELEGRAM_BOT_TOKEN` config key, but the token lives
  in the vault (vault-first) or under `telegram.bot_token`. `resolve_telegram_bot_token()`
  is the one canonical resolver.
"""

from __future__ import annotations

import navig.core as core_mod
import navig.messaging.secrets as secrets_mod
from navig.tui.resolvers import resolve_ssh, resolve_telegram

# ── SSH ────────────────────────────────────────────────────────────────────


class _FakeConfig:
    """Stand-in for the Config singleton — only the method resolve_ssh uses."""

    hosts: list = []

    def list_hosts(self):
        return list(_FakeConfig.hosts)


def test_ssh_counts_hosts_from_host_manager(monkeypatch):
    _FakeConfig.hosts = ["prod", "staging"]
    monkeypatch.setattr(core_mod, "Config", _FakeConfig)

    badge = resolve_ssh()

    assert badge.status == "ok"
    assert "2 hosts active" in badge.detail


def test_ssh_missing_when_no_hosts(monkeypatch):
    _FakeConfig.hosts = []
    monkeypatch.setattr(core_mod, "Config", _FakeConfig)

    badge = resolve_ssh()

    assert badge.status == "missing"


# ── Telegram ───────────────────────────────────────────────────────────────


def test_telegram_ok_when_token_resolves(monkeypatch):
    monkeypatch.setattr(secrets_mod, "resolve_telegram_bot_token", lambda *a, **k: "123:ABC")

    badge = resolve_telegram()

    assert badge.status == "ok"
    assert "configured" in badge.detail


def test_telegram_missing_when_no_token(monkeypatch):
    monkeypatch.setattr(secrets_mod, "resolve_telegram_bot_token", lambda *a, **k: "")

    badge = resolve_telegram()

    assert badge.status == "missing"
